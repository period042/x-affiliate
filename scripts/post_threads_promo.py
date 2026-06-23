#!/usr/bin/env python3
"""
Threadsにnote記事の宣伝投稿をする。
note_posted/ の公開済み記事からローテーションで選び、
30日以内に宣伝済みの記事は除外して投稿する。
"""
import os, json, time, requests, sys
from pathlib import Path
from datetime import datetime, timedelta

THREADS_USER_ID      = os.environ.get('THREADS_USER_ID', '')
THREADS_ACCESS_TOKEN = os.environ.get('THREADS_ACCESS_TOKEN', '')
SUMMARY_FILE         = os.environ.get('GITHUB_STEP_SUMMARY', '')

if os.environ.get('DATA_DIR'):
    DATA_DIR = Path(os.environ['DATA_DIR'])
elif os.environ.get('GITHUB_ACTIONS'):
    DATA_DIR = Path(__file__).parent.parent / 'data'
else:
    DATA_DIR = Path.home() / 'Documents' / 'x-affiliate-data'

NOTE_POSTED_DIR          = DATA_DIR / 'note_posted'
THREADS_PROMO_POSTED_DIR = DATA_DIR / 'threads_promo_posted'

PROMO_COOLDOWN_DAYS = 30  # 同一記事の再投稿クールダウン（日）


def write_summary(text: str):
    if SUMMARY_FILE:
        with open(SUMMARY_FILE, 'a', encoding='utf-8') as f:
            f.write(text + '\n')


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


def get_available_articles(recently_promoted: set) -> list:
    articles = []
    for f in sorted(NOTE_POSTED_DIR.glob('*.json')):
        try:
            d = json.loads(f.read_text(encoding='utf-8'))
            url = d.get('url', '')
            # note.com の公開URLのみ（editor.note.com は除外）
            if not url.startswith('https://note.com/'):
                continue
            if url in recently_promoted:
                continue
            articles.append({
                'file': f.name,
                'title': d.get('title', ''),
                'content': d.get('content', ''),
                'genre': d.get('genre', ''),
                'url': url,
            })
        except Exception:
            pass
    return articles


def make_promo_text(title: str, content: str, url: str) -> str:
    # 冒頭の有効な段落（見出し・URL・注意書きを除く）を最大120字取得
    hook = ''
    for line in content.split('\n'):
        stripped = line.strip()
        if (stripped
                and not stripped.startswith('#')
                and not stripped.startswith('→')
                and not stripped.startswith('http')
                and not stripped.startswith('※')
                and not stripped.startswith('【')):
            hook = stripped[:120]
            break

    if hook:
        return f"{hook}\n\n{title}\n\n→ 記事はこちら\n{url}"
    else:
        return f"{title}\n\n外資系10年の経験から書きました。\n\n→ 記事はこちら\n{url}"


def create_container(text: str) -> str:
    resp = requests.post(
        f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads",
        params={"media_type": "TEXT", "text": text, "access_token": THREADS_ACCESS_TOKEN},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()['id']


def publish_container(creation_id: str) -> str:
    resp = requests.post(
        f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads_publish",
        params={"creation_id": creation_id, "access_token": THREADS_ACCESS_TOKEN},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()['id']


def main():
    if not THREADS_USER_ID or not THREADS_ACCESS_TOKEN:
        print("[SKIP] THREADS_USER_ID または THREADS_ACCESS_TOKEN が未設定")
        sys.exit(0)

    THREADS_PROMO_POSTED_DIR.mkdir(parents=True, exist_ok=True)

    recently_promoted = get_recently_promoted_urls()
    articles = get_available_articles(recently_promoted)

    if not articles:
        print("[SKIP] 投稿可能な記事がありません（全て30日以内に投稿済みか公開URLなし）")
        write_summary("## Threads宣伝投稿\n⚠️ 投稿可能な記事なし（30日クールダウン中）")
        sys.exit(0)

    # 最も古い（投稿日が早い）記事を選ぶ → ローテーションになる
    article = articles[0]
    promo_text = make_promo_text(article['title'], article['content'], article['url'])

    print(f"投稿記事: {article['title'][:50]}")
    print(f"URL: {article['url']}")
    print(f"本文:\n{promo_text}\n")

    try:
        container_id = create_container(promo_text)
        print(f"[OK] コンテナ作成: {container_id}")
        time.sleep(3)

        threads_id = publish_container(container_id)
        threads_url = f"https://www.threads.net/t/{threads_id}"
        print(f"[OK] Threads投稿成功: {threads_url}")

        record = {
            'note_url':       article['url'],
            'title':          article['title'],
            'threads_post_id': threads_id,
            'threads_url':    threads_url,
            'promo_text':     promo_text,
            'posted_at':      datetime.now().isoformat(),
        }
        fname = datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + article['file']
        with open(THREADS_PROMO_POSTED_DIR / fname, 'w', encoding='utf-8') as f:
            json.dump(record, f, ensure_ascii=False, indent=2)

        write_summary(
            f"## Threads宣伝投稿\n"
            f"✅ 投稿成功: {threads_url}\n"
            f"- 記事: {article['title'][:50]}\n"
            f"- note URL: {article['url']}"
        )

    except requests.HTTPError as e:
        body = e.response.text if e.response is not None else ''
        print(f"[ERR] Threads投稿失敗: {e.response.status_code} {body}")
        write_summary(
            f"## Threads宣伝投稿\n"
            f"❌ 失敗: {e.response.status_code}\n"
            f"- 詳細: {body}"
        )
        sys.exit(1)


if __name__ == '__main__':
    main()
