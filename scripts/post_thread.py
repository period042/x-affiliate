#!/usr/bin/env python3
"""
スレッド投稿スクリプト（5ツイート連続）
thread_queue/ からファイルを読み込み、返信チェーンで投稿する
"""
import os, json, sys, time
from pathlib import Path
from datetime import datetime

if os.environ.get('GITHUB_ACTIONS'):
    BASE_DIR = Path(__file__).parent.parent
else:
    BASE_DIR = Path.home() / 'Documents' / 'x-affiliate'

DATA_DIR          = Path(os.environ['DATA_DIR']) if os.environ.get('DATA_DIR') else BASE_DIR
THREAD_QUEUE_DIR  = DATA_DIR / 'thread_queue'
THREAD_POSTED_DIR = DATA_DIR / 'thread_posted'
CONFIG_PATH       = BASE_DIR / 'config.json'
SUMMARY_FILE      = os.environ.get('GITHUB_STEP_SUMMARY', '')

MAX_RETRIES      = 3
TWEET_INTERVAL   = 3   # ツイート間隔（秒）


def write_summary(text: str):
    if SUMMARY_FILE:
        with open(SUMMARY_FILE, 'a', encoding='utf-8') as f:
            f.write(text + '\n')


def load_credentials():
    if os.environ.get('X_API_KEY'):
        return {
            'api_key':             os.environ['X_API_KEY'],
            'api_secret':          os.environ['X_API_SECRET'],
            'access_token':        os.environ['X_ACCESS_TOKEN'],
            'access_token_secret': os.environ['X_ACCESS_TOKEN_SECRET'],
        }
    with open(CONFIG_PATH, encoding='utf-8') as f:
        return json.load(f)['x_api']


def get_next_thread():
    threads = sorted(THREAD_QUEUE_DIR.glob('*.json'))
    return threads[0] if threads else None


def main():
    import tweepy

    creds = load_credentials()
    client = tweepy.Client(
        consumer_key=creds['api_key'],
        consumer_secret=creds['api_secret'],
        access_token=creds['access_token'],
        access_token_secret=creds['access_token_secret']
    )

    thread_file = get_next_thread()
    if not thread_file:
        print("スレッドキューが空です。")
        write_summary("## スレッド投稿\n⚠️ スレッドキューが空")
        sys.exit(0)

    with open(thread_file, encoding='utf-8') as f:
        thread = json.load(f)

    tweets = thread.get('tweets', [])
    if not tweets:
        print("ツイートリストが空です。")
        sys.exit(1)

    print(f"スレッド投稿開始: {thread.get('title', thread_file.name)}")
    print(f"ツイート数: {len(tweets)}")

    posted_ids = []
    parent_id  = None

    # auto_fix_content をインポート（X投稿と同じ修正ルールを使用）
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).parent))
    try:
        from post_from_queue import auto_fix_content
    except Exception:
        auto_fix_content = lambda text: (text, [])

    # ── プロフリンク文言チェック（投稿前に全ツイートを検証）──
    import re as _re
    _PROF_PATTERNS = [
        _re.compile(r'プロフのリンク'),
        _re.compile(r'詳しくはプロフ'),
        _re.compile(r'プロフのリンクをご確認'),
        _re.compile(r'→\s*(スタディサプリ|LanCul|CampusTop|POSIWILL|AQUES|ENGREAL)[^\n]*\n(?!https?)'),
    ]
    prof_violations = []
    for idx, t in enumerate(tweets):
        for pat in _PROF_PATTERNS:
            if pat.search(t):
                prof_violations.append(f"tweet[{idx+1}]: {pat.pattern}")
                break
    if prof_violations:
        reason = f"プロフリンク文言を検出（noteリンクへの置換が必要）: {prof_violations}"
        print(f"[SKIP] {reason}")
        write_summary(f"## スレッド投稿\n⚠️ プロフリンク文言のためスキップ\n- {thread_file.name}\n- 理由: {reason}")
        sys.exit(1)

    for i, tweet_text in enumerate(tweets, 1):
        current_text = tweet_text
        print(f"\n[{i}/{len(tweets)}] {current_text[:60]}...")

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                # Tweepy v4: reply はキーワード引数で直接渡す
                if parent_id:
                    response = client.create_tweet(
                        text=current_text,
                        in_reply_to_tweet_id=parent_id,
                        user_auth=True
                    )
                else:
                    response = client.create_tweet(text=current_text, user_auth=True)

                tweet_id = response.data['id']
                parent_id = tweet_id
                posted_ids.append(tweet_id)
                print(f"  ✓ 投稿成功 (試行{attempt}): {tweet_id}")

                if i < len(tweets):
                    time.sleep(TWEET_INTERVAL)
                break

            except tweepy.errors.Forbidden as e:
                # 403: コンテンツ修正して再試行
                print(f"  403 コンテンツ拒否 (試行{attempt}): {e}")
                fixed, changes = auto_fix_content(current_text)
                if fixed != current_text and changes:
                    current_text = fixed
                    print(f"  [自動修正] {changes}")
                    time.sleep(15)
                    continue
                elif attempt >= MAX_RETRIES:
                    print(f"  修正不能 → スレッド{i}番目でスキップ、次のツイートへ")
                    # 失敗ツイートはスキップして続行（スレッドを中断しない）
                    break
                time.sleep(15)

            except Exception as e:
                print(f"  ✗ 投稿失敗 (試行{attempt}/{MAX_RETRIES}): {e}")
                if attempt < MAX_RETRIES:
                    time.sleep(15)
                else:
                    print(f"  スレッド途中で失敗 ({i}番目)")
                    write_summary(
                        f"## スレッド投稿\n❌ {i}番目のツイートで失敗\n"
                        f"- ファイル: `{thread_file.name}`\n"
                        f"- エラー: `{e}`"
                    )
                    sys.exit(1)

    # アーカイブ
    THREAD_POSTED_DIR.mkdir(exist_ok=True)
    thread['posted_at']  = datetime.now().isoformat()
    thread['tweet_ids']  = [str(i) for i in posted_ids]
    thread['thread_url'] = f"https://twitter.com/i/web/status/{posted_ids[0]}"
    with open(THREAD_POSTED_DIR / thread_file.name, 'w', encoding='utf-8') as f:
        json.dump(thread, f, ensure_ascii=False, indent=2)
    thread_file.unlink()

    remaining = len(list(THREAD_QUEUE_DIR.glob('*.json')))
    print(f"\n完了。スレッド先頭URL: {thread['thread_url']}")
    print(f"残りスレッドキュー: {remaining}件")

    write_summary(
        f"## スレッド投稿\n"
        f"✅ {len(tweets)}ツイートのスレッド投稿成功\n"
        f"- ファイル: `{thread_file.name}`\n"
        f"- URL: {thread['thread_url']}\n"
        f"- 残りキュー: {remaining}件"
    )


if __name__ == '__main__':
    main()
