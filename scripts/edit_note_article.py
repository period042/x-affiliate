#!/usr/bin/env python3
"""
note.com 既投稿記事のクロスリンク形式を修正するスクリプト
GET: /api/v3/notes/{key}  (認証不要)
PUT: /api/v1/text_notes/{numeric_id}  (認証必要)
"""
import os, json, base64, sys, re, uuid
from pathlib import Path

NOTE_KEY         = os.environ.get('NOTE_KEY', '')
NOTE_COOKIES_B64 = os.environ.get('NOTE_COOKIES', '')
NOTE_EMAIL       = os.environ.get('NOTE_EMAIL', '')
NOTE_PASSWORD    = os.environ.get('NOTE_PASSWORD', '')
SCREENSHOT_DIR   = Path('/tmp')


def fix_cross_links_html(html_body: str) -> str:
    """
    <p ...>（関連記事: 「title」 URL）</p>
    → <p ...>関連記事：「title」</p>
      <figure embedded-service="note" data-src="URL" ...></figure>
    """
    # （関連記事: 「title」 https://note.com/...）を含む <p> タグを検出
    pattern = (
        r'(<p(?:[^>]*)>)'                        # <p ...>
        r'[（\(]関連記事[:：]\s*'             # （関連記事:
        r'[「\「]([^」\」]+)[」\」]'  # 「title」
        r'\s+(https://note\.com/\S+?)'           # URL
        r'[）\)]'                             # ）
        r'(</p>)'                                # </p>
    )

    def make_embed(m):
        p_open = m.group(1)
        title  = m.group(2).strip()
        url    = m.group(3).rstrip('）)').strip()
        p_close = m.group(4)

        # note key から embedded-content-key を生成
        key_match = re.search(r'/n/(n[0-9a-f]+)', url)
        if key_match:
            note_key_body = key_match.group(1)[1:]   # 先頭の 'n' を除く
            embed_key = f"emb{note_key_body}"
            embedded_service = "note"
        else:
            embed_key = f"emb{uuid.uuid4().hex[:12]}"
            embedded_service = "external-article"

        fig_id = str(uuid.uuid4())
        figure = (
            f'<figure name="{fig_id}" id="{fig_id}" '
            f'data-src="{url}" '
            f'data-identifier="null" '
            f'embedded-service="{embedded_service}" '
            f'embedded-content-key="{embed_key}">'
            f'</figure>'
        )
        print(f"  [fix] 「{title[:30]}」→ {url}")
        return f'{p_open}関連記事：「{title}」{p_close}\n{figure}'

    result = re.sub(pattern, make_embed, html_body)
    return result


def load_cookies() -> dict:
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

        # 編集ページを開いてセッション・XSRF トークンを確立
        edit_url = f'https://editor.note.com/notes/{NOTE_KEY}/edit'
        print(f"[P] 編集ページ: {edit_url}")
        page.goto(edit_url, wait_until='networkidle', timeout=30000)
        page.wait_for_timeout(3000)

        all_cookies = context.cookies()
        browser.close()

    fresh = {c['name']: c['value']
             for c in all_cookies
             if 'note.com' in c.get('domain', '')}
    print(f"[P] セッション確立完了 ({len(fresh)} cookies)")
    print(f"[P] cookie keys: {list(fresh.keys())}")
    return fresh


def api_update(note_key: str, cookies: dict) -> bool:
    import requests

    base_headers = {
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'ja,en-US;q=0.9,en;q=0.8',
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
                      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': f'https://editor.note.com/notes/{note_key}/edit/',
        'Origin': 'https://editor.note.com',
    }

    # XSRF トークン付与 (cookie名バリエーション対応)
    for xsrf_key in ('XSRF-TOKEN', '_xsrf', 'csrf_token', 'csrftoken'):
        xsrf = cookies.get(xsrf_key, '')
        if xsrf:
            base_headers['X-XSRF-TOKEN'] = xsrf
            print(f"[API] XSRF-TOKEN ({xsrf_key}): {xsrf[:20]}...")
            break
    else:
        print("[WARN] XSRF-TOKEN クッキーが見つかりません")

    # --- GET: v3 エンドポイントで記事取得 ---
    get_url = f"https://note.com/api/v3/notes/{note_key}"
    print(f"[API] GET {get_url}")
    resp = requests.get(get_url, headers={
        k: v for k, v in base_headers.items() if k not in ('Origin',)
    }, cookies=cookies, timeout=20)
    print(f"  status: {resp.status_code}")
    if resp.status_code != 200:
        print(f"  error: {resp.text[:600]}")
        return False

    try:
        data = resp.json()
    except Exception:
        print(f"  JSON parse error: {resp.text[:200]}")
        return False

    article = data.get('data', {})
    article_id = article.get('id')
    body = article.get('body', '')
    status = article.get('status', 'published')
    print(f"  id={article_id}, status={status}, body_len={len(body)}")

    # クロスリンクの存在確認
    if '関連記事' not in body:
        print("  変更なし（クロスリンクが見つかりません）")
        return True

    print(f"  クロスリンク候補を検出: {body.count('関連記事')} 件")
    print(f"  クロスリンク前後 (100文字): {body[max(0,body.find('関連記事')-20):body.find('関連記事')+100]}")

    # --- クロスリンク修正 ---
    fixed_body = fix_cross_links_html(body)
    if fixed_body == body:
        print("  正規表現にマッチするクロスリンクなし（手動確認が必要）")
        # デバッグ: 周辺 HTML を出力
        idx = body.find('関連記事')
        print(f"  [DBG] HTML周辺:\n{body[max(0,idx-100):idx+300]}")
        return False

    print("  クロスリンク修正完了")

    if not article_id:
        print("  [ERR] 記事 numeric ID が取得できません")
        return False

    # --- PUT: 複数エンドポイントを順に試す ---
    put_candidates = [
        f"https://note.com/api/v1/text_notes/{article_id}",
        f"https://note.com/api/v2/text_notes/{article_id}",
        f"https://editor.note.com/api/v1/text_notes/{article_id}",
        f"https://note.com/api/v1/text_notes/{note_key}",
    ]

    put_headers = {**base_headers, 'Content-Type': 'application/json'}
    put_body = {
        "text_note": {
            "body": fixed_body,
            "status": status,
        }
    }

    for put_url in put_candidates:
        print(f"[API] PUT {put_url}")
        try:
            put_resp = requests.put(
                put_url,
                cookies=cookies, headers=put_headers,
                json=put_body, timeout=30
            )
            print(f"  status: {put_resp.status_code}")
            print(f"  response: {put_resp.text[:600]}")
            if put_resp.status_code in (200, 201, 204):
                print(f"✅ 記事 {note_key} の更新成功 ({put_url})")
                return True
            elif put_resp.status_code == 405:
                print("  405 Method Not Allowed — PATCH を試みます")
                patch_resp = requests.patch(
                    put_url,
                    cookies=cookies, headers=put_headers,
                    json=put_body, timeout=30
                )
                print(f"  PATCH status: {patch_resp.status_code}")
                print(f"  PATCH response: {patch_resp.text[:400]}")
                if patch_resp.status_code in (200, 201, 204):
                    print(f"✅ 記事 {note_key} の更新成功 (PATCH {put_url})")
                    return True
            elif put_resp.status_code in (401, 403):
                print("  認証エラー — 次のエンドポイントへ")
                continue
        except Exception as e:
            print(f"  例外: {e}")

    print("[ERR] 全 PUT エンドポイントで失敗")
    return False


def main():
    if not NOTE_KEY:
        print("[ERR] NOTE_KEY が未設定")
        sys.exit(1)

    cookies = load_cookies()
    if not cookies and not (NOTE_EMAIL and NOTE_PASSWORD):
        print("[ERR] 認証情報なし（NOTE_COOKIES または NOTE_EMAIL/PASSWORD が必要）")
        sys.exit(1)

    # Playwright でセッション確立（XSRF トークン取得のため）
    try:
        cookies = get_session_via_playwright(cookies)
    except Exception as e:
        print(f"[WARN] Playwright セッション確立失敗: {e}")
        print("  保存済みクッキーで API 試行します")

    success = api_update(NOTE_KEY, cookies)
    if not success:
        sys.exit(1)


if __name__ == '__main__':
    main()
