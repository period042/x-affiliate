#!/usr/bin/env python3
"""note.comの短い記事(1000字未満)を下書きに戻すスクリプト

対象: posted_at が 2026-06-02〜2026-06-06 かつ content < 1000字の記事
方式: Playwright ctx.request (APIRequestContext) を使用
      - ブラウザのCORS制限をバイパス
      - セッションCookieを正しく送付
      - /api/v2/creators/{creator}/contents でクリエイター公開記事リストを取得
      - 照合した numeric_id に対して PUT status:draft を実行
"""
import os
import json
import base64
import re
import sys
import time
from pathlib import Path

NOTE_COOKIES_B64 = os.environ.get('NOTE_COOKIES', '')
SUMMARY_FILE     = os.environ.get('GITHUB_STEP_SUMMARY', '')

BASE_DIR = (
    Path(__file__).parent.parent
    if os.environ.get('GITHUB_ACTIONS')
    else Path.home() / 'Documents' / 'x-affiliate'
)
NOTE_POSTED_DIR   = BASE_DIR / 'note_posted'
LOCAL_COOKIE_PATH = BASE_DIR / 'note_cookies.json'
SCREENSHOT_DIR    = Path('/tmp') if os.environ.get('GITHUB_ACTIONS') else BASE_DIR / 'logs'

SHORT_THRESHOLD = 1000
DATE_FROM       = '2026-06-02'
DATE_TO         = '2026-06-06'
CREATOR_NAME    = 'english_gaishi'


def write_summary(text: str):
    if SUMMARY_FILE:
        with open(SUMMARY_FILE, 'a', encoding='utf-8') as f:
            f.write(text + '\n')


def load_storage_state():
    if NOTE_COOKIES_B64:
        decoded = base64.b64decode(NOTE_COOKIES_B64.encode('ascii')).decode('utf-8')
        return json.loads(decoded)
    if LOCAL_COOKIE_PATH.exists():
        with open(LOCAL_COOKIE_PATH, encoding='utf-8') as f:
            return json.load(f)
    return None


def extract_note_key(data: dict):
    url = data.get('note_url') or data.get('url', '')
    if not url:
        return None
    m = re.search(r'/notes/(n[a-f0-9]{10,})', url)
    if m:
        return m.group(1)
    m = re.search(r'/n/(n[a-f0-9]{10,})', url)
    if m:
        return m.group(1)
    return None


def get_articles_to_privatize():
    articles = []
    for f in sorted(NOTE_POSTED_DIR.glob('*.json')):
        d = json.loads(f.read_text(encoding='utf-8'))
        content_len = len(d.get('content', ''))
        if content_len >= SHORT_THRESHOLD:
            continue
        posted_at = d.get('posted_at', '')[:10] if d.get('posted_at') else ''
        if not (DATE_FROM <= posted_at <= DATE_TO):
            continue
        note_key = extract_note_key(d)
        if note_key:
            articles.append({
                'file':      f.name,
                'title':     d.get('title', ''),
                'chars':     content_len,
                'posted_at': posted_at,
                'note_key':  note_key,
            })
        else:
            print(f'[SKIP] note_key不明: {f.name}')
    return articles


def deduplicate_by_key(articles):
    seen = set()
    result = []
    for a in articles:
        if a['note_key'] not in seen:
            seen.add(a['note_key'])
            result.append(a)
        else:
            print(f'[DUP] キー重複スキップ: {a["file"]} ({a["note_key"]})')
    return result


def get_published_notes(api_ctx, creator: str) -> dict:
    """クリエイターの公開記事を全件取得して {note_key: numeric_id} を返す"""
    published = {}
    page_num = 1
    while True:
        resp = api_ctx.get(
            f'https://note.com/api/v2/creators/{creator}/contents',
            params={'kind': 'note', 'page': page_num, 'per': 100}
        )
        print(f'  [creator API] page={page_num} status={resp.status}')
        if not resp.ok:
            print(f'  [WARN] クリエイターAPI失敗: {resp.status} - {resp.text()[:200]}')
            break
        data = resp.json()
        contents = data.get('data', {}).get('contents', [])
        if not contents:
            break
        for item in contents:
            key = item.get('key') or item.get('noteId') or ''
            nid = item.get('id')
            if key and nid:
                published[key] = nid
        # ページネーション確認
        is_last = data.get('data', {}).get('isLastPage', True)
        total   = data.get('data', {}).get('totalCount', 0)
        print(f'    → {len(contents)}件取得 (累計{len(published)}件, total={total}, isLastPage={is_last})')
        if is_last or len(published) >= total:
            break
        page_num += 1
        time.sleep(0.3)
    return published


def main():
    storage_state = load_storage_state()
    if not storage_state:
        msg = '[ERROR] Cookie未設定。NOTE_COOKIES環境変数またはnote_cookies.jsonが必要です。'
        print(msg)
        write_summary(f'## note非公開化\n❌ {msg}')
        sys.exit(1)

    print('=== note短記事 非公開化 開始 ===')
    print(f'対象期間: {DATE_FROM} 〜 {DATE_TO}')
    print(f'文字数閾値: {SHORT_THRESHOLD}字未満')

    articles = get_articles_to_privatize()
    articles = deduplicate_by_key(articles)
    target_keys = {a['note_key'] for a in articles}
    print(f'非公開化対象: {len(articles)}件（重複除外後）\n')

    SCREENSHOT_DIR.mkdir(exist_ok=True)

    from playwright.sync_api import sync_playwright

    success_privatized  = []
    success_already     = []
    failed              = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage']
        )
        ctx = browser.new_context(
            storage_state=storage_state,
            user_agent=(
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/124.0.0.0 Safari/537.36'
            ),
            viewport={'width': 1280, 'height': 900},
        )
        api = ctx.request  # APIRequestContext - CORSバイパス、Cookie自動送付

        # ── 認証確認 ────────────────────────────────────────────────
        print('[init] 認証確認...')
        me_resp = api.get('https://note.com/api/v1/users/current_user')
        print(f'  current_user: status={me_resp.status}')
        if me_resp.status == 401:
            print('[ERROR] 401 Unauthorized。Cookieが期限切れです。')
            write_summary('## note非公開化\n❌ Cookie期限切れ（401）。save_note_cookies.pyを再実行してください。')
            browser.close()
            sys.exit(1)
        try:
            me_data = me_resp.json()
            uid     = me_data.get('data', {}).get('id')
            uname   = me_data.get('data', {}).get('urlname')
            print(f'  ✅ 認証確認: @{uname} (id={uid})')
        except Exception:
            print(f'  [WARN] current_user JSON パース失敗（ステータス={me_resp.status}）。続行します。')

        # ── クリエイター公開記事リスト取得 ───────────────────────────
        print(f'\n[step1] @{CREATOR_NAME} の公開記事を取得中...')
        published_map = get_published_notes(api, CREATOR_NAME)
        print(f'  公開記事数: {len(published_map)}件')

        # デバッグ: 対象keyが公開リストに含まれるか
        found_in_published  = target_keys & set(published_map.keys())
        absent_in_published = target_keys - set(published_map.keys())
        print(f'  対象キーのうち公開中: {len(found_in_published)}件')
        print(f'  対象キーのうち非公開/不在: {len(absent_in_published)}件')

        # ── 各記事を処理 ─────────────────────────────────────────────
        print(f'\n[step2] 非公開化処理...')
        for art in articles:
            key   = art['note_key']
            title = art['title'][:45]
            print(f'\n[{art["file"]}] {title}... ({art["chars"]}字)')

            # ① 公開リストに無い → すでに非公開
            if key not in published_map:
                print(f'  ✅ すでに非公開（公開リストに不在）')
                success_already.append(art)
                continue

            # ② 公開リストにある → 非公開化
            numeric_id = published_map[key]
            print(f'  公開中 numeric_id={numeric_id} → 非公開化実行')

            put_resp = api.put(
                f'https://note.com/api/v1/text_notes/{numeric_id}',
                data=json.dumps({'status': 'draft'}),
                headers={'Content-Type': 'application/json', 'Accept': 'application/json'}
            )
            print(f'  PUT status={put_resp.status}')

            if put_resp.ok:
                try:
                    pd = put_resp.json()
                    new_status = pd.get('data', {}).get('status', 'unknown')
                    print(f'  ✅ 非公開化完了 → status={new_status}')
                except Exception:
                    print(f'  ✅ 非公開化完了（レスポンスパース不可）')
                success_privatized.append(art)
            else:
                body_preview = put_resp.text()[:300]
                print(f'  ❌ PUT失敗: {body_preview}')
                failed.append(art)

            time.sleep(0.5)

        browser.close()

    # ── 結果レポート ──────────────────────────────────────────────
    print(f'\n=== 完了 ===')
    print(f'✅ 今回非公開化: {len(success_privatized)}件')
    print(f'✅ すでに非公開: {len(success_already)}件')
    print(f'❌ 失敗: {len(failed)}件')

    if failed:
        print('\n失敗した記事:')
        for art in failed:
            print(f'  {art["file"]}: https://note.com/n/{art["note_key"]}')

    summary = [
        '## note記事 非公開化',
        f'- 対象: {len(articles)}件',
        f'- ✅ 今回非公開化: {len(success_privatized)}件',
        f'- ✅ すでに非公開: {len(success_already)}件',
        f'- ❌ 失敗: {len(failed)}件',
    ]
    if failed:
        summary.append('\n### 失敗した記事')
        for art in failed:
            summary.append(f'- [{art["title"][:40]}](https://note.com/n/{art["note_key"]})')
    write_summary('\n'.join(summary))

    if failed:
        sys.exit(1)


if __name__ == '__main__':
    main()
