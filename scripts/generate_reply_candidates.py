#!/usr/bin/env python3
"""
毎朝のリプライ候補生成スクリプト
X API Bearer Tokenで最新ツイートを検索し、
各投稿に合わせた個別の返信案を生成する。
"""
import json, os, re
from pathlib import Path
from datetime import date, datetime

if os.environ.get('GITHUB_ACTIONS'):
    BASE_DIR = Path(__file__).parent.parent
else:
    BASE_DIR = Path.home() / 'Documents' / 'x-affiliate'

SUGGESTIONS_DIR = BASE_DIR / 'reply_suggestions'
CONFIG_PATH     = BASE_DIR / 'config.json'
SUMMARY_FILE    = os.environ.get('GITHUB_STEP_SUMMARY', '')

# 検索クエリ（X API v2 Basic以上で動作）
SEARCH_QUERIES = [
    '英語学習 -is:retweet lang:ja',
    '外資系転職 英語 -is:retweet lang:ja',
    'キャリアコーチング 転職 -is:retweet lang:ja',
    'TOEIC 外資系 -is:retweet lang:ja',
    '英会話 体験 -is:retweet lang:ja',
    '子ども 英語 オンライン英会話 -is:retweet lang:ja',
]
MAX_PER_QUERY = 10
MIN_LIKES     = 3


def write_summary(text: str):
    if SUMMARY_FILE:
        with open(SUMMARY_FILE, 'a', encoding='utf-8') as f:
            f.write(text + '\n')


def load_credentials():
    if os.environ.get('X_API_KEY'):
        return {
            'api_key':             os.environ['X_API_KEY'],
            'api_secret':          os.environ['X_API_SECRET'],
            'access_token':        os.environ.get('X_ACCESS_TOKEN', ''),
            'access_token_secret': os.environ.get('X_ACCESS_TOKEN_SECRET', ''),
            'bearer_token':        os.environ.get('X_BEARER_TOKEN', ''),
        }
    with open(CONFIG_PATH, encoding='utf-8') as f:
        cfg = json.load(f)
    return cfg['x_api']


# ────────────────────────────────────────────────
# 投稿内容を分析して個別の返信案を生成
# ────────────────────────────────────────────────
# ユーザーペルソナ（固定）
PERSONA = {
    'career':  '外資系IT企業で10年英語を使ってきた',
    'skills':  'PMP・FP保持、ESPP・RSU運用中',
    'insight': '英語とキャリアで選択肢を増やすことの大切さを実感している',
}

# キーワード→返信テンプレート（実体験ベース、複数バリエーション）
# 各キーワードグループに複数パターンを用意してローテーション
REPLY_PATTERNS = {
    ('挫折', '続かない', 'やめた', '失敗'): [
        '週1スクールを3年続けても伸びませんでした。変わったのは「勉強する場所」から「使う場所」に切り替えてからです。頻度より環境でした。',
        '自分も同じで、アプリ・参考書・スクールを繰り返してました。続かない原因は意志じゃなく、使う機会がなかったことでした。',
        '3回挫折して気づいたのは、方法より「英語を使わざるを得ない状況を作るか」でした。正しい環境が先でした。',
    ],
    ('TOEIC', 'スコア', '点数', '試験'): [
        'TOEIC800点取っても外資系会議で詰まりました。ビジネスフレーズ100個を集中的に覚えてから発言が変わった経験があります。',
        '試験英語と実務英語は本当に別物でした。TOEICより「会議で使うフレーズの瞬発力」の差が大きかったです。',
        '外資系10年で感じたのはスコアより「会議で間を置かずに返せるか」が全てだったことです。',
    ],
    ('外資系', '転職', '年収'): [
        '外資転職で一番驚いたのは「自己アピールしないと存在しないのと同じ」という文化でした。スキルより自己主張力が先でした。',
        '転職後に気づいたのは英語力より「どう働きたいか」の軸を持っていた人が早く活躍していたことです。',
        '外資転職後に後悔したのは「なぜ転職するか」を整理しないまま動いたことでした。目的地より方向性が先だったと今は思います。',
    ],
    ('子ども', '英語教育', 'オンライン英会話', '子供'): [
        '外資系の親として、最初の先生で英語好きか嫌いかが決まると実感しました。資格持ちの正社員教師かどうかが一番の判断基準でした。',
        '子どもの英語で失敗したのは教師の質を見ずにサービスを選んだことでした。毎回先生が変わると子どもが安心して話せないんですよね。',
        '3歳から始めた親として、続けられるかどうかは「最初の体験が楽しかったか」だけで決まると思っています。',
    ],
    ('キャリア', '方向性', 'やりたいこと', '迷'): [
        '外資系10年でキャリアに迷ったとき、転職エージェントより先に「何のために働くか」を整理するコーチングが転機でした。',
        '転職活動で一番後悔したのは「どこに行くか」を先に決めたことです。「何のために働くか」が先でした。',
        '同じ状況がありました。求人を見る前に自分の方向性を整理した方が、転職活動の精度が全然違いました。',
    ],
    ('英会話', '話せない', '話す', 'スピーキング'): [
        '週1スクール2年で話せなかった理由は、授業以外で英語に触れる時間がゼロだったからでした。毎日話せる場所を作ってから変わりました。',
        'スピーキングは「量より慣れ」でした。完璧な文法より不完全でも話した回数の方が伸びに直結しました。',
        '外資系で英語を使ってきて確信したのは、アウトプットの場所があるかどうかが全てだということです。',
    ],
    ('IT転職', 'エンジニア', 'プログラミング', 'SIer'): [
        'SIerからIT外資系に転職して一番変わったのは「成果で評価される」文化でした。スキルより自己発信力が先に問われました。',
        'IT転職で後悔したのはエージェントを使う前に市場価値を正確に把握していなかったことです。専門エージェントへの相談が先でした。',
    ],
}

def extract_key_sentence(text: str) -> str:
    """ツイートから核心の一文を抽出"""
    text = re.sub(r'https?://\S+', '', text).strip()
    sentences = re.split(r'[。\n]', text)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 8]
    return sentences[0] if sentences else text[:40]

def generate_individual_reply(tweet_text: str, username: str) -> str:
    """ツイート内容を分析して実体験ベースの返信案を生成"""
    text = tweet_text

    # キーワードマッチング → 複数パターンからいいね数ハッシュでローテーション
    for keywords, patterns in REPLY_PATTERNS.items():
        if any(kw in text for kw in keywords):
            idx = hash(tweet_text) % len(patterns)
            return patterns[idx]

    # デフォルト：外資系経験からの共感
    key_sentence = extract_key_sentence(text)
    return (
        f'外資系IT10年の経験から言うと、これは本質をついていると思います。\n\n'
        f'{key_sentence[:40]}という感覚、自分も転職後に強く持ちました。'
    )


def main():
    import tweepy

    creds = load_credentials()
    bearer = creds.get('bearer_token', '')

    if not bearer:
        print('⚠️ bearer_tokenが未設定。config.json または X_BEARER_TOKEN Secretに追加してください。')
        # APIなしでも手動確認用URLを出力
        _output_manual_guide()
        return

    client = tweepy.Client(bearer_token=bearer, wait_on_rate_limit=True)

    today = date.today().strftime('%Y-%m-%d')
    SUGGESTIONS_DIR.mkdir(exist_ok=True)

    all_candidates = []
    seen_ids = set()

    for query in SEARCH_QUERIES:
        print(f'検索中: {query[:45]}...')
        try:
            resp = client.search_recent_tweets(
                query=query,
                max_results=MAX_PER_QUERY,
                tweet_fields=['public_metrics', 'author_id', 'created_at', 'text'],
                expansions=['author_id'],
                user_fields=['username', 'public_metrics', 'name'],
            )
            if not resp.data:
                print('  結果なし')
                continue

            # ユーザー情報マップ（Tweepy v4: resp.includesはdict）
            users = {}
            if resp.includes:
                inc = resp.includes if isinstance(resp.includes, dict) else (vars(resp.includes) if hasattr(resp.includes, '__dict__') else {})
                for u in inc.get('users', []):
                    users[u.id] = u

            for tweet in resp.data:
                if tweet.id in seen_ids:
                    continue
                seen_ids.add(tweet.id)

                m = tweet.public_metrics or {}
                likes = m.get('like_count', 0)
                if likes < MIN_LIKES:
                    continue

                user = users.get(tweet.author_id)
                uname = getattr(user, 'username', 'unknown') if user else 'unknown'
                followers = 0
                if user and hasattr(user, 'public_metrics') and user.public_metrics:
                    followers = user.public_metrics.get('followers_count', 0)

                reply = generate_individual_reply(tweet.text, uname)

                all_candidates.append({
                    'tweet_id':    str(tweet.id),
                    'tweet_url':   f'https://x.com/i/web/status/{tweet.id}',
                    'author':      f'@{uname}',
                    'followers':   followers,
                    'likes':       likes,
                    'text':        tweet.text[:150],
                    'reply_draft': reply,
                })
                print(f'  @{uname} like={likes} | {tweet.text[:45]}')

        except Exception as e:
            print(f'  エラー: {type(e).__name__}: {str(e)[:120]}')

    # いいね数でソートして上位15件
    all_candidates.sort(key=lambda x: x['likes'], reverse=True)
    top = all_candidates[:15]

    output = {
        'date':       today,
        'generated':  datetime.now().isoformat(),
        'count':      len(top),
        'candidates': top,
    }
    out_file = SUGGESTIONS_DIR / f'{today}.json'
    with open(out_file, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # サマリー出力
    lines = [f'## 今日のリプライ候補 ({today}) — {len(top)}件\n\n']
    for i, c in enumerate(top[:10], 1):
        lines.append(
            f'### {i}. @{c["author"].lstrip("@")} (フォロワー: {c["followers"]:,}人 / いいね: {c["likes"]})\n'
            f'**投稿**: {c["tweet_url"]}\n'
            f'**内容**: {c["text"][:80]}...\n\n'
            f'**返信案**:\n```\n{c["reply_draft"]}\n```\n\n'
        )

    summary = ''.join(lines)
    write_summary(summary)
    print('\n' + summary[:3000])
    print(f'保存: {out_file}')


def _output_manual_guide():
    """bearer_token未設定時の手動ガイド出力"""
    today = date.today().strftime('%Y-%m-%d')
    SUGGESTIONS_DIR.mkdir(exist_ok=True)
    output = {
        'date': today,
        'generated': datetime.now().isoformat(),
        'note': 'bearer_token未設定。config.jsonのbearer_tokenに追加してください。',
        'manual_searches': [
            {'keyword': q, 'url': f'https://x.com/search?q={q.replace(" ","%20")}&f=live'}
            for q in ['英語学習 外資系', 'TOEIC 転職', '英会話 続かない', 'キャリア 30代 転職', '子ども 英語 オンライン']
        ]
    }
    with open(SUGGESTIONS_DIR / f'{today}.json', 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print('bearer_tokenを設定してrun workflowを再実行してください。')


if __name__ == '__main__':
    main()
