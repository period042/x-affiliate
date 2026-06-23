#!/usr/bin/env python3
"""X投稿後に同じ内容をThreadsに投稿する。
post.yml / post2.yml の "Post to X" ステップ成功後に呼ばれる想定。
"""
import os
import sys
import json
import time
import requests
from pathlib import Path

THREADS_USER_ID     = os.environ.get('THREADS_USER_ID', '')
THREADS_ACCESS_TOKEN = os.environ.get('THREADS_ACCESS_TOKEN', '')
SUMMARY_FILE        = os.environ.get('GITHUB_STEP_SUMMARY', '')

if os.environ.get('DATA_DIR'):
    POSTED_DIR = Path(os.environ['DATA_DIR']) / 'posted'
elif os.environ.get('GITHUB_ACTIONS'):
    POSTED_DIR = Path(__file__).parent.parent / 'data' / 'posted'
else:
    POSTED_DIR = Path.home() / 'Documents' / 'x-affiliate-data' / 'posted'

# X投稿からThreads投稿までの最大許容時間（秒）
MAX_AGE_SEC = 300


def write_summary(text: str):
    if SUMMARY_FILE:
        with open(SUMMARY_FILE, 'a', encoding='utf-8') as f:
            f.write(text + '\n')


def get_latest_posted() -> tuple[Path, dict] | tuple[None, None]:
    files = sorted(POSTED_DIR.glob('*.json'))
    if not files:
        return None, None
    latest = files[-1]
    # ファイルがMAX_AGE_SEC以内に作成されたものかチェック
    age = time.time() - latest.stat().st_mtime
    if age > MAX_AGE_SEC:
        print(f"[SKIP] 最新のpostedファイルが{int(age)}秒前に作成（{MAX_AGE_SEC}秒超）→ 今回のX投稿なし")
        return None, None
    with open(latest, encoding='utf-8') as f:
        return latest, json.load(f)


def create_container(text: str) -> str:
    resp = requests.post(
        f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads",
        params={
            "media_type": "TEXT",
            "text": text,
            "access_token": THREADS_ACCESS_TOKEN,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()['id']


def publish_container(creation_id: str) -> str:
    resp = requests.post(
        f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads_publish",
        params={
            "creation_id": creation_id,
            "access_token": THREADS_ACCESS_TOKEN,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()['id']


def main():
    if not THREADS_USER_ID or not THREADS_ACCESS_TOKEN:
        print("[SKIP] THREADS_USER_ID または THREADS_ACCESS_TOKEN が未設定")
        sys.exit(0)

    latest_path, post = get_latest_posted()
    if post is None:
        write_summary("## Threads投稿\n- スキップ（直近のX投稿なし）")
        sys.exit(0)

    if post.get('threads_post_id'):
        print(f"[SKIP] すでにThreads投稿済み: {post['threads_post_id']}")
        sys.exit(0)

    content = post.get('content', '').strip()
    if not content:
        print("[SKIP] contentが空")
        sys.exit(0)

    print(f"投稿内容:\n{content}\n")

    try:
        container_id = create_container(content)
        print(f"[OK] コンテナ作成: {container_id}")
        time.sleep(3)

        threads_id = publish_container(container_id)
        threads_url = f"https://www.threads.net/t/{threads_id}"
        print(f"[OK] Threads投稿成功: {threads_url}")

        # posted JSONにthreads_post_idを追記
        post['threads_post_id'] = threads_id
        with open(latest_path, 'w', encoding='utf-8') as f:
            json.dump(post, f, ensure_ascii=False, indent=2)

        write_summary(
            f"## Threads投稿\n"
            f"- 成功: {threads_url}\n"
            f"- ファイル: `{latest_path.name}`"
        )

    except requests.HTTPError as e:
        body = e.response.text if e.response is not None else ''
        print(f"[ERR] Threads投稿失敗: {e.response.status_code} {body}")
        write_summary(
            f"## Threads投稿\n"
            f"- 失敗: {e.response.status_code}\n"
            f"- 詳細: {body}"
        )
        sys.exit(1)


if __name__ == '__main__':
    main()
