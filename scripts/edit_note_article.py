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

        # 編集ページを開いてセッション確立
        edit_url = f'https://editor.note.com/notes/{NOTE_KEY}/edit'
        print(f"[P] 編集ページ: {edit_url}")
        page.goto(edit_url, wait_until='networkidle', timeout=30000)
        page.wait_for_timeout(3000)

        # ブラウザ内 cookie を確認（HttpOnly含む全クッキー）
        browser_cookies = context.cookies()
        print(f"[P] ブラウザ cookie: {[c['name'] for c in browser_cookies if 'note.com' in c.get('domain','')]}")

        # CSRF token を meta タグから取得
        csrf = page.evaluate("""() => {
            const m = document.querySelector('meta[name="csrf-token"]');
            if (m) return m.getAttribute('content');
            // window.__reactFiber__ から探すパターン
            if (window.csrfToken) return window.csrfToken;
            return null;
        }""")
        if csrf:
            print(f"[P] CSRF token (meta): {csrf[:20]}...")
            import builtins
            builtins._NOTE_CSRF_TOKEN = csrf

        all_cookies = context.cookies()
        browser.close()

    fresh = {c['name']: c['value']
             for c in all_cookies
             if 'note.com' in c.get('domain', '')}
    print(f"[P] セッション確立完了 ({len(fresh)} cookies)")
    print(f"[P] cookie keys: {list(fresh.keys())}")
    return fresh


def intercept_editor_save(note_key: str, cookies: dict, fixed_body: str) -> bool:
    """
    editor.note.com でのネットワークリクエストを傍受し、
    実際の save API を特定してリプレイする。
    """
    from playwright.sync_api import sync_playwright
    import threading

    print("[intercept] editor ネットワーク傍受開始...")

    captured = {'put_req': None, 'csrf_token': None, 'window_vars': {}}

    def on_request(req):
        method = req.method.upper()
        url = req.url
        # text_notes への PUT/PATCH/POST を傍受
        if method in ('PUT', 'PATCH', 'POST') and 'text_notes' in url:
            print(f"[intercept] {method} {url}")
            captured['put_req'] = {
                'method': method,
                'url': url,
                'headers': dict(req.headers),
                'post_data': req.post_data or '',
            }
        # ヘッダーに csrf が含まれるリクエストを記録
        if any('csrf' in k.lower() or 'xsrf' in k.lower()
               for k in req.headers.keys()):
            for k, v in req.headers.items():
                if 'csrf' in k.lower() or 'xsrf' in k.lower():
                    print(f"[intercept] CSRF header detected: {k}={v[:40]}")
                    captured['csrf_token'] = v

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        storage = {'cookies': [
            {'name': k, 'value': v, 'domain': '.note.com',
             'path': '/', 'httpOnly': False, 'secure': True}
            for k, v in cookies.items()
        ]}
        context = browser.new_context(storage_state=storage, viewport={'width': 1280, 'height': 900})
        page = context.new_page()
        page.on('request', on_request)

        edit_url = f'https://editor.note.com/notes/{note_key}/edit'
        print(f"[intercept] 編集ページ読み込み: {edit_url}")
        page.goto(edit_url, wait_until='networkidle', timeout=40000)
        page.wait_for_timeout(5000)

        # window 変数から CSRF/認証情報を探す
        win_info = page.evaluate("""() => {
            const info = {};
            // meta csrf-token
            const m = document.querySelector('meta[name="csrf-token"]');
            if (m) info.meta_csrf = m.content;
            // window オブジェクトから関連変数を探す
            const keys = Object.keys(window);
            for (const k of keys) {
                const lk = k.toLowerCase();
                if (lk.includes('csrf') || lk.includes('xsrf') || lk.includes('token')) {
                    try { info['win_' + k] = String(window[k]).substring(0, 60); } catch(e) {}
                }
            }
            // __NEXT_DATA__ や __reactFiber__ などから探す
            if (window.__NEXT_DATA__) {
                const nd = window.__NEXT_DATA__;
                if (nd.props && nd.props.csrfToken)
                    info.next_csrf = nd.props.csrfToken;
            }
            return info;
        }""")
        print(f"[intercept] window vars: {win_info}")
        captured['window_vars'] = win_info

        # ProseMirror エディタを見つけてフォーカス、スペースを入力してデリート
        # これで autosave または save button が有効になる可能性がある
        try:
            # エディタの div を見つける
            editor_el = page.locator('.ProseMirror, [contenteditable="true"]').first
            editor_el.click(timeout=5000)
            page.keyboard.press('End')
            page.keyboard.type(' ')
            page.wait_for_timeout(500)
            page.keyboard.press('Backspace')
            page.wait_for_timeout(2000)
            print("[intercept] エディタに trivial edit を入力")
        except Exception as e:
            print(f"[intercept] エディタ操作スキップ: {e}")

        # 保存ボタンを探してクリック (React イベント用に dispatchEvent も試みる)
        save_selectors = [
            'button:has-text("保存")',
            'button:has-text("公開")',
            '[data-testid="publish-button"]',
            'button.o-publishButton',
        ]
        for sel in save_selectors:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=2000):
                    print(f"[intercept] 保存ボタンをクリック: {sel}")
                    # JS dispatchEvent で React イベントを確実に発火
                    btn.dispatch_event('click')
                    page.wait_for_timeout(3000)
                    break
            except Exception:
                pass

        page.wait_for_timeout(5000)

        # 傍受した PUT リクエストがあればリプレイ
        if captured['put_req']:
            req_info = captured['put_req']
            print(f"[intercept] 傍受成功: {req_info['method']} {req_info['url']}")
            print(f"[intercept] headers: {list(req_info['headers'].keys())}")
            print(f"[intercept] body(先頭200): {req_info['post_data'][:200]}")

            # 傍受したリクエストのヘッダーを使って、固定 body で再送信
            import requests as _req
            resp = _req.request(
                method=req_info['method'],
                url=req_info['url'],
                headers=req_info['headers'],
                cookies=cookies,
                data=req_info['post_data'].encode('utf-8') if req_info['post_data'] else None,
                timeout=30,
            )
            print(f"[intercept-replay] status: {resp.status_code}")
            print(f"[intercept-replay] body: {resp.text[:300]}")
            if resp.status_code in (200, 201, 204):
                browser.close()
                print("✅ 傍受リクエストのリプレイで更新成功（テスト）")
                # 実際の更新はここで行う
                pass

        browser.close()

    # 傍受でリクエスト形式が判明した場合、その情報を使って直接 PUT
    csrf = captured.get('csrf_token') or captured['window_vars'].get('meta_csrf')
    if csrf:
        print(f"[intercept] CSRF token 取得: {csrf[:30]}")
        return _api_put_with_csrf(note_key, cookies, fixed_body, csrf)

    return False


def _api_put_with_csrf(note_key: str, cookies: dict, fixed_body: str, csrf_token: str) -> bool:
    """傍受した CSRF トークンを使って PUT を実行する"""
    import requests

    article_id = 166673362  # n6855a6f6aaf0 の numeric ID

    headers = {
        'Accept': 'application/json, text/plain, */*',
        'Content-Type': 'application/json',
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
                      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': f'https://editor.note.com/notes/{note_key}/edit/',
        'Origin': 'https://editor.note.com',
        'X-CSRF-Token': csrf_token,
        'X-Requested-With': 'XMLHttpRequest',
    }
    body = {"text_note": {"body": fixed_body, "status": "published"}}

    resp = requests.put(
        f"https://note.com/api/v1/text_notes/{article_id}",
        headers=headers, cookies=cookies, json=body, timeout=30
    )
    print(f"[csrf-put] status: {resp.status_code}, body: {resp.text[:300]}")
    return resp.status_code in (200, 201, 204)


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

    # CSRF / XSRF トークン付与
    # 1. Playwright が取得した meta csrf-token を優先
    import builtins
    csrf_from_page = getattr(builtins, '_NOTE_CSRF_TOKEN', '')
    if csrf_from_page:
        base_headers['X-CSRF-Token'] = csrf_from_page
        print(f"[API] X-CSRF-Token (meta): {csrf_from_page[:20]}...")
    else:
        # 2. cookie フォールバック
        for xsrf_key in ('XSRF-TOKEN', '_xsrf', 'csrf_token', 'csrftoken'):
            xsrf = cookies.get(xsrf_key, '')
            if xsrf:
                base_headers['X-XSRF-TOKEN'] = xsrf
                print(f"[API] XSRF-TOKEN ({xsrf_key}): {xsrf[:20]}...")
                break
        else:
            print("[WARN] CSRF/XSRF トークンが見つかりません — 422 になる可能性あり")

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
            elif put_resp.status_code in (405, 422):
                print(f"  {put_resp.status_code} — PATCH を試みます")
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

    print("[ERR] 全 PUT エンドポイントで失敗 — ブラウザ内 fetch を試みます")
    return None  # None = try Playwright fetch


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

    # API で直接更新
    result = api_update(NOTE_KEY, cookies)
    if result is True:
        sys.exit(0)

    # フォールバック: ネットワーク傍受で実際の save API を特定
    if result is None:
        import requests as _req
        resp = _req.get(f"https://note.com/api/v3/notes/{NOTE_KEY}", timeout=20)
        if resp.status_code == 200:
            body = resp.json().get('data', {}).get('body', '')
            fixed_body = fix_cross_links_html(body)
            if fixed_body != body:
                ok = intercept_editor_save(NOTE_KEY, cookies, fixed_body)
                if ok:
                    sys.exit(0)

    sys.exit(1)


if __name__ == '__main__':
    main()
