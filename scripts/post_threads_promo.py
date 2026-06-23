#!/usr/bin/env python3
"""
Threadsにnote記事の宣伝投稿をする（高エンゲージメント記事優先 + Claude生成テキスト）

選出ロジック:
  1. note.com 公開APIで全記事のいいね数・閲覧数を取得
  2. スコア = likes*3 + views*0.1（最新30日記事は×1.5ボーナス）
  3. 30日以内に宣伝済みの記事を除外してスコア降順で1件選出

投稿生成:
  - Claude API (claude-haiku-4-5) で記事内容に最適なフォーマットを自動選択
  - フォーマット: スレッド投稿 / 質問投稿 / Before/After / 引用
  - スレッド投稿は Threads reply_to_id で連投
"""
import os, json, time, requests, sys
from pathlib import Path
from datetime import datetime, timedelta

THREADS_USER_ID      = os.environ.get('THREADS_USER_ID', '')
THREADS_ACCESS_TOKEN = os.environ.get('THREADS_ACCESS_TOKEN', '')
ANTHROPIC_API_KEY    = os.environ.get('ANTHROPIC_API_KEY', '')
SUMMARY_FILE         = os.environ.get('GITHUB_STEP_SUMMARY', '')
NOTE_USERNAME        = os.environ.get('NOTE_USERNAME', 'english_gaishi')

if os.environ.get('DATA_DIR'):
    DATA_DIR = Path(os.environ['DATA_DIR'])
elif os.environ.get('GITHUB_ACTIONS'):
    DATA_DIR = Path(__file__).parent.parent / 'data'
else:
    DATA_DIR = Path.home() / 'Documents' / 'x-affiliate-data'

NOTE_POSTED_DIR          = DATA_DIR / 'note_posted'
THREADS_PROMO_POSTED_DIR = DATA_DIR / 'threads_promo_posted'

PROMO_COOLDOWN_DAYS = 30


def write_summary(text: str):
    if SUMMARY_FILE:
        with open(SUMMARY_FILE, 'a', encoding='utf-8') as f:
            f.write(text + '\n')


def fetch_note_stats() -> dict:
    """note.com公開APIから全記事のいいね・閲覧数を取得。{note_key: {likes, views, publish_at}}"""
    stats = {}
    for page in range(1, 10):
        try:
            r = requests.get(
                f'https://note.com/api/v2/creators/{NOTE_USERNAME}/contents',
                params={'kind': 'note', 'page': page, 'per': 100},
                timeout=15,
            )
            if not r.ok:
                break
            contents = r.json().get('data', {}).get('contents', [])
            if not contents:
                break
            for n in contents:
                key = n.get('key', '')
                if key:
                    stats[key] = {
                        'likes': n.get('likeCount', 0) or n.get('like_count', 0) or 0,
                        'views': n.get('readCount', 0) or n.get('read_count', 0) or 0,
                        'publish_at': n.get('publishAt') or n.get('publish_at') or '',
                    }
        except Exception as e:
            print(f'[WARN] stats取得失敗 page={page}: {e}')
            break
    print(f'[INFO] note統計取得: {len(stats)}件')
    return stats


def calc_score(stats_entry: dict) -> float:
    likes = stats_entry.get('likes', 0)
    views = stats_entry.get('views', 0)
    score = likes * 3 + views * 0.1
    publish_at = stats_entry.get('publish_at', '')
    if publish_at:
        try:
            pub = datetime.fromisoformat(publish_at.replace('Z', '+00:00'))
            age_days = (datetime.now(pub.tzinfo) - pub).days
            if age_days <= 30:
                score *= 1.5
        except Exception:
            pass
    return score


def get_recently_promoted_urls() -> set:
    if not THREADS_PROMO_POSTED_DIR.exists():
        return set()
    cutoff = datetime.now() - timedelta(days=PROMO_COOLDOWN_DAYS)
    promoted = set()
    for f in THREADS_PROMO_POSTED_DIR.glob('*.json'):
        try:
            d = json.loads(f.read_text(encoding='utf-8'))
            posted_at = datetime.fromisoformat(d.get('posted_at', '2000-01-01'))
            if posted_at > cutoff:
                promoted.add(d.get('note_url', ''))
        except Exception:
            pass
    return promoted


def get_candidate_articles(note_stats: dict, recently_promoted: set) -> list:
    articles = []
    for f in sorted(NOTE_POSTED_DIR.glob('*.json')):
        try:
            d = json.loads(f.read_text(encoding='utf-8'))
            url = d.get('url', '')
            if not url.startswith('https://note.com/'):
                continue
            if url in recently_promoted:
                continue
            note_key = url.split('/n/')[-1] if '/n/' in url else ''
            stats = note_stats.get(note_key, {})
            articles.append({
                'file':    f.name,
                'title':   d.get('title', ''),
                'content': d.get('content', ''),
                'genre':   d.get('genre', ''),
                'url':     url,
                'likes':   stats.get('likes', 0),
                'views':   stats.get('views', 0),
                'score':   calc_score(stats),
            })
        except Exception:
            pass
    articles.sort(key=lambda x: x['score'], reverse=True)
    return articles


FALLBACK_PROMPT = """記事冒頭の1文 + タイトル + URLのシンプルな形式で投稿テキストを生成"""

GENERATION_PROMPT = """あなたはThreadsの投稿者です。以下のnote記事を宣伝するThreads投稿を1つ作成してください。

## 記事情報
タイトル: {title}
ジャンル: {genre}
いいね数: {likes} / 閲覧数: {views}
記事URL: {url}

## 記事冒頭（最大1200字）
{content_excerpt}

## 投稿フォーマットの選択基準（記事内容に最も合ったものを1つ選ぶ）
- **スレッド投稿**: 記事にステップ・手順・複数の気づきがある場合。
  形式: 1投稿目（hook・問題提起）→ 2投稿目（本論・解決策）→ 3投稿目（まとめ+URL）
  各投稿を「---」で区切って出力する
- **質問投稿**: 共感を得やすいテーマ・読者の悩みに直結する場合。
  問いかけから始め、短い体験談→URL で締める
- **Before/After投稿**: 変化・成長・気づきのストーリーがある場合。
  「〜だった → 今は〜」の構造で書き、URL で締める
- **引用投稿**: 記事の中に金言・印象的な一文がある場合。
  引用（「」で囲む）→ 文脈補足 → URL で締める

## 制約（必ず守る）
- 日本語で書く
- 全角500文字以内（スレッド投稿の場合は各投稿500文字以内・最大3投稿）
- 末尾に必ず記事URLを入れる（URLは {url} を使う）
- ハッシュタグは使わない
- 宣伝臭を出しすぎない・体験談として自然に書く
- 「プロフのリンク」「プロフはこちら」は絶対に使わない

## 出力形式
スレッド投稿なら「---」で区切る。それ以外は投稿テキストのみ出力（説明不要）。"""


def generate_post(article: dict) -> str:
    """Claude APIで投稿テキスト生成。失敗時はフォールバック。"""
    title   = article['title']
    content = article['content']
    url     = article['url']

    if ANTHROPIC_API_KEY:
        prompt = GENERATION_PROMPT.format(
            title=title,
            genre=article.get('genre', ''),
            likes=article['likes'],
            views=article['views'],
            url=url,
            content_excerpt=content[:1200],
        )
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
            msg = client.messages.create(
                model='claude-haiku-4-5-20251001',
                max_tokens=900,
                messages=[{'role': 'user', 'content': prompt}],
            )
            result = msg.content[0].text.strip()
            print(f'[Claude] 生成完了 ({len(result)}字)')
            return result
        except Exception as e:
            print(f'[WARN] Claude API失敗: {e} → フォールバック')

    # フォールバック: 冒頭1文 + タイトル + URL
    hook = next(
        (l.strip()[:120] for l in content.split('\n')
         if l.strip() and not l.strip().startswith(('#', '→', 'http', '※', '【'))),
        ''
    )
    return f"{hook}\n\n{title}\n\n→ 記事はこちら\n{url}" if hook else f"{title}\n\n→ 記事はこちら\n{url}"


def post_single(text: str) -> str:
    """単発投稿 → threads_post_id を返す"""
    r = requests.post(
        f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads",
        params={"media_type": "TEXT", "text": text, "access_token": THREADS_ACCESS_TOKEN},
        timeout=30,
    )
    r.raise_for_status()
    container_id = r.json()['id']
    time.sleep(3)

    r2 = requests.post(
        f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads_publish",
        params={"creation_id": container_id, "access_token": THREADS_ACCESS_TOKEN},
        timeout=30,
    )
    r2.raise_for_status()
    return r2.json()['id']


def post_thread_chain(posts: list) -> list:
    """スレッド連投 → [post_id, ...] を返す"""
    ids = []
    parent_id = None
    for i, text in enumerate(posts, 1):
        params = {"media_type": "TEXT", "text": text, "access_token": THREADS_ACCESS_TOKEN}
        if parent_id:
            params["reply_to_id"] = parent_id
        r = requests.post(
            f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads",
            params=params, timeout=30,
        )
        r.raise_for_status()
        container_id = r.json()['id']
        time.sleep(3)

        r2 = requests.post(
            f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads_publish",
            params={"creation_id": container_id, "access_token": THREADS_ACCESS_TOKEN},
            timeout=30,
        )
        r2.raise_for_status()
        post_id = r2.json()['id']
        ids.append(post_id)
        parent_id = post_id
        print(f"[OK] スレッド{i}/{len(posts)}: {post_id}")
        if i < len(posts):
            time.sleep(5)
    return ids


def main():
    if not THREADS_USER_ID or not THREADS_ACCESS_TOKEN:
        print("[SKIP] THREADS_USER_ID または THREADS_ACCESS_TOKEN が未設定")
        sys.exit(0)

    THREADS_PROMO_POSTED_DIR.mkdir(parents=True, exist_ok=True)

    note_stats = fetch_note_stats()
    recently_promoted = get_recently_promoted_urls()
    articles = get_candidate_articles(note_stats, recently_promoted)

    if not articles:
        print("[SKIP] 投稿可能な記事がありません（全て30日以内に投稿済みか公開URLなし）")
        write_summary("## Threads宣伝投稿\n⚠️ 投稿可能な記事なし（30日クールダウン中）")
        sys.exit(0)

    article = articles[0]
    print(f"選出記事: {article['title'][:50]}")
    print(f"スコア: {article['score']:.1f} (likes={article['likes']}, views={article['views']})")
    print(f"URL: {article['url']}")

    generated = generate_post(article)

    # 「---」区切りでスレッド投稿かを判定
    posts = [p.strip() for p in generated.split('---') if p.strip()]
    is_thread = len(posts) > 1

    print(f"\n{'スレッド' if is_thread else '単発'}投稿 ({len(posts)}投稿):")
    for i, p in enumerate(posts, 1):
        print(f"  [{i}] {p[:100]}...")

    try:
        if is_thread:
            post_ids = post_thread_chain(posts)
            threads_url = f"https://www.threads.net/t/{post_ids[0]}"
        else:
            post_id = post_single(posts[0])
            threads_url = f"https://www.threads.net/t/{post_id}"
            post_ids = [post_id]

        print(f"[OK] Threads投稿成功: {threads_url}")

        record = {
            'note_url':         article['url'],
            'title':            article['title'],
            'likes':            article['likes'],
            'views':            article['views'],
            'score':            article['score'],
            'post_type':        'thread' if is_thread else 'single',
            'threads_post_ids': [str(i) for i in post_ids],
            'threads_url':      threads_url,
            'promo_texts':      posts,
            'posted_at':        datetime.now().isoformat(),
        }
        fname = datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + article['file']
        with open(THREADS_PROMO_POSTED_DIR / fname, 'w', encoding='utf-8') as f:
            json.dump(record, f, ensure_ascii=False, indent=2)

        write_summary(
            f"## Threads宣伝投稿\n"
            f"✅ {'スレッド' if is_thread else '単発'}投稿成功: {threads_url}\n"
            f"- 記事: {article['title'][:50]}\n"
            f"- スコア: {article['score']:.1f} (likes={article['likes']}, views={article['views']})\n"
            f"- note URL: {article['url']}"
        )

    except requests.HTTPError as e:
        body = e.response.text if e.response is not None else ''
        print(f"[ERR] Threads投稿失敗: {e.response.status_code} {body}")
        write_summary(f"## Threads宣伝投稿\n❌ 失敗: {e.response.status_code}\n{body}")
        sys.exit(1)


if __name__ == '__main__':
    main()
