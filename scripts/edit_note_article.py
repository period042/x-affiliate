#!/usr/bin/env python3
"""
note.com 既投稿記事のクロスリンク形式を修正するスクリプト
note.com の内部API (GET/PUT /api/v1/text_notes) を使って直接更新する

使い方:
  NOTE_KEY=n6855a6f6aaf0 python scripts/edit_note_article.py
"""
import os, json, base64, sys, re
from pathlib import Path

NOTE_KEY         = os.environ.get('NOTE_KEY', '')
NOTE_COOKIES_B64 = os.environ.get('NOTE_COOKIES', '')
NOTE_EMAIL       = os.environ.get('NOTE_EMAIL', '')
NOTE_PASSWORD    = os.environ.get('NOTE_PASSWORD', '')
SCREENSHOT_DIR   = Path('/tmp') if os.environ.get('GITHUB_ACTIONS') else Path('/tmp')


def fix_cross_links(content: str) -> str:
    """（関連記事: 「タイトル」 URL）→ 関連記事：「タイトル」\n\nURL"""
    def replace_inline_link(m):
        inner = m.group(1)
        url_match = re.search(r'https://\S+', inner)
        if not url_match:
            return m.group(0)
        url = url_match.group(0)
        text_before = inner[:url_match.start()].strip().rstrip('　 ')
        return f'{text_before}\n\n{url}'
    return re.sub(r'（([^）]*https://note\.com/[^）]*)）', replace_inline_link, content)


def load_cookies() -> dict:
    """NOTE_COOKIES_B64 から note.com 用クッキー辞書を返す"""
    if not NOTE_COOKIES_B64:
        return {}
    try:
        storage = json.loads(base64.b64decode(NOTE_COOKIES_B64.encode('ascii')).decode('utf-8'))
        return {c['name']: c['value']
                for c in storage.get('cookies', [])
                if 'note.com' in c.get('domain', '')}
    except Exception as e:
        print(f"[WARN] クッキーデコード失敗: {e}")
        return {}


def get_session_via_playwright(cookies: dict) -> dict:
    """Playwright でログインして最新セッションクッキーを取得する"""
    from playwright.sync_api import sync_playwright

    print("[P] Playwright でセッション確立中...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx_kwargs = {'viewport': {'width': 1280, 'height': 900}}
        if cookies:
            storage = {'cookies': [
                {'name': k, 'value': v, 'domain': '.note.com',
                 'path': '/', 'httpOnly': False, 'secure': True}
                for k, v in cookies.items()
            ]}
            ctx_kwargs['storage_state'] = storage
        context = browser.new_context(**ctx_kwargs)
        page = context.new_page()

        page.goto('https://note.com', wait_until='domcontentloaded', timeout=30000)
        page.wait_for_timeout(3000)

        if 'login' in page.url and NOTE_EMAIL and NOTE_PASSWORD:
            print("[P] ログイン実行...")
            page.goto('https://note.com/login', wait_until='domcontentloaded')
            page.wait_for_timeout(2000)
            for sel in ['input[type="email"]', 'input[name="email"]']:
                try:
                    page.fill(sel, NOTE_EMAIL, timeout=4000)
                    break
                except Exception:
                    pass
            for sel in ['input[type="password"]', 'input[name="password"]']:
                try:
                    page.fill(sel, NOTE_PASSWORD, timeout=4000)
                    break
                except Exception:
                    pass
            page.click('button[type="submit"]')
            page.wait_for_load_state('networkidle', timeout=20000)

        # 編集ページを1回開いてセッションを確立
        edit_url = f'https://note.com/notes/{NOTE_KEY}/edit'
        page.goto(edit_url, wait_until='networkidle', timeout=30000)
        page.wait_for_timeout(3000)

        # 最新クッキーを取得
        all_cookies = context.cookies()
        browser.close()

    fresh = {c['name']: c['value']
             for c in all_cookies
             if 'note.com' in c.get('domain', '')}
    print(f"[P] セッション確立完了 ({len(fresh)} cookies)")
    return fresh


def api_update(note_key: str, cookies: dict) -> bool:
    """note.com API で記事本文を直接更新する"""
    import requests

    base_headers = {
        'Accept': 'application/json, text/plain, */*',
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
                      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': f'https://editor.note.com/notes/{note_key}/edit/',
        'Origin': 'https://editor.note.com',
    }
    # XSRF トークンをヘッダーに付与
    xsrf = cookies.get('XSRF-TOKEN', '')
    if xsrf:
        base_headers['X-XSRF-TOKEN'] = xsrf

    # --- GET: 記事データ取得 ---
    print(f"[API] GET /api/v1/text_notes/{note_key}")
    resp = requests.get(
        f"https://note.com/api/v1/text_notes/{note_key}",
        cookies=cookies, headers=base_headers, timeout=20
    )
    print(f"  status: {resp.status_code}")
    if resp.status_code != 200:
        print(f"  error: {resp.text[:400]}")
        return False

    try:
        data = resp.json()
    except Exception:
        print(f"  JSON parse error: {resp.text[:200]}")
        return False

    article = data.get('data', {})
    article_id = article.get('id')
    body = article.get('body', '')
    status = article.get('status', '')
    print(f"  id={article_id}, status={status}, body_len={len(body)}")
    print(f"  body(先頭200文字): {body[:200]}")

    if not article_id:
        print("  [ERR] 記事IDが取得できませんでした")
        return False

    # --- クロスリンク修正 ---
    fixed_body = fix_cross_links(body)
    if fixed_body == body:
        print("  変更なし（クロスリンクが見つからないか既に修正済み）")
        return True

    diff_count = body.count('（') - fixed_body.count('（')
    print(f"  クロスリンク {diff_count} 件を修正")

    # --- PUT: 記事本文更新 ---
    print(f"[API] PUT /api/v1/text_notes/{article_id}")
    put_headers = {**base_headers, 'Content-Type': 'application/json'}
    put_body = {"text_note": {"body": fixed_body, "status": status or "published"}}

    put_resp = requests.put(
        f"https://note.com/api/v1/text_notes/{article_id}",
        cookies=cookies, headers=put_headers,
        json=put_body, timeout=30
    )
    print(f"  status: {put_resp.status_code}")
    print(f"  response(先頭300文字): {put_resp.text[:300]}")

    if put_resp.status_code in (200, 201, 204):
        print(f"✅ 記事 {note_key} の更新成功")
        return True
    else:
        print(f"[ERR] PUT 失敗")
        return False


def main():
    if not NOTE_KEY:
        print("[ERR] NOTE_KEY が未設定")
        sys.exit(1)

    # クッキーロード
    cookies = load_cookies()
    if not cookies and not (NOTE_EMAIL and NOTE_PASSWORD):
        print("[ERR] 認証情報なし（NOTE_COOKIESまたはNOTE_EMAIL/PASSWORDが必要）")
        sys.exit(1)

    # Playwright でセッション確立（最新クッキーを取得）
    try:
        cookies = get_session_via_playwright(cookies)
    except Exception as e:
        print(f"[WARN] Playwright セッション確立失敗: {e}")
        print("  保存済みクッキーで API 試行します")

    # API で直接更新
    success = api_update(NOTE_KEY, cookies)
    if not success:
        sys.exit(1)


if __name__ == '__main__':
    main()
