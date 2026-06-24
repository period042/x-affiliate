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
    pattern = (
        r'(<p(?:[^>]*)>)'
        r'[（\(]関連記事[:：]\s*'
        r'[「\「]([^」\」]+)[」\」]'
        r'\s+(https://note\.com/\S+?)'
        r'[）\)]'
        r'(</p>)'
    )

    def make_embed(m):
        p_open = m.group(1)
        title  = m.group(2).strip()
        url    = m.group(3).rstrip('）)').strip()
        p_close = m.group(4)

        key_match = re.search(r'/n/(n[0-9a-f]+)', url)
        if key_match:
            note_key_body = key_match.group(1)[1:]
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

        edit_url = f'https://editor.note.com/notes/{NOTE_KEY}/edit'
        print(f"[P] 編集ページ: {edit_url}")
        page.goto(edit_url, wait_until='networkidle', timeout=30000)
        page.wait_for_timeout(3000)

        browser_cookies = context.cookies()
        print(f"[P] ブラウザ cookie: {[c['name'] for c in browser_cookies if 'note.com' in c.get('domain','')]}")

        all_cookies = context.cookies()
        browser.close()

    fresh = {c['name']: c['value']
             for c in all_cookies
             if 'note.com' in c.get('domain', '')}
    print(f"[P] セッション確立完了 ({len(fresh)} cookies)")
    return fresh


def save_and_publish_via_playwright(note_key: str, cookies: dict, fixed_body: str,
                                    article_id: int) -> bool:
    """
    Playwright route interception で text_notes への全書き込みリクエストの
    body を fixed_body に差し替えた上で、公開フロー（下書き保存→公開ボタン→確定）を実行する。
    """
    from playwright.sync_api import sync_playwright
    import requests as _req

    print("[route] draft_save + publish インターセプトで更新を試みます...")

    intercepted = {'draft': 0, 'publish': 0}

    def handle_note_api(route, request):
        url = request.url
        method = request.method
        if method not in ('POST', 'PUT', 'PATCH'):
            route.continue_()
            return
        try:
            data = json.loads(request.post_data or '{}')
            replaced = False
            # top-level body
            if 'body' in data:
                data['body'] = fixed_body
                replaced = True
            # nested text_note.body
            if isinstance(data.get('text_note'), dict) and 'body' in data['text_note']:
                data['text_note']['body'] = fixed_body
                replaced = True
            if replaced:
                label = 'draft' if 'draft_save' in url else 'publish'
                intercepted[label] += 1
                print(f"[route] {method} {label} body置換: {url[:80]}")
                route.continue_(post_data=json.dumps(data, ensure_ascii=False))
            else:
                print(f"[route] {method} パススルー (body なし): {url[:80]}")
                route.continue_()
        except Exception as e:
            print(f"[route] インターセプトエラー: {e}")
            route.continue_()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--no-first-run',
                '--no-default-browser-check',
                '--disable-dev-shm-usage',
            ]
        )
        storage = {'cookies': [
            {'name': k, 'value': v, 'domain': '.note.com',
             'path': '/', 'httpOnly': False, 'secure': True}
            for k, v in cookies.items()
        ]}
        context = browser.new_context(
            storage_state=storage,
            viewport={'width': 1280, 'height': 900},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        )
        page = context.new_page()
        # Playwright の webdriver 検知を無効化
        page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        # 全 API リクエストをログ（デバッグ用）
        def log_request(req):
            if '/api/' in req.url and req.method in ('POST', 'PUT', 'PATCH'):
                print(f"[NET] {req.method} {req.url[:100]}")
        page.on('request', log_request)

        # text_notes への全書き込みをインターセプト
        page.route('**/api/v1/text_notes**', handle_note_api)
        page.route('**/api/v2/text_notes**', handle_note_api)

        edit_url = f'https://editor.note.com/notes/{note_key}/edit'
        print(f"[route] 編集ページ読み込み: {edit_url}")
        page.goto(edit_url, wait_until='networkidle', timeout=40000)
        page.wait_for_timeout(5000)
        page.screenshot(path='/tmp/edit_step1_loaded.png')

        print(f"[route] ページ URL: {page.url}")

        # エディタの末尾にスペースを追加してダーティ状態にする（Backspace しない）
        try:
            editor_el = page.locator('.ProseMirror, [contenteditable="true"]').first
            editor_el.click(timeout=5000)
            page.keyboard.press('End')
            page.keyboard.type(' ')
            page.wait_for_timeout(1000)
            print("[route] trivial edit (space 追加): 完了")
        except Exception as e:
            print(f"[route] エディタ操作スキップ: {e}")

        # 下書き保存ボタンをクリック
        save_clicked = False
        for sel in ['button:has-text("一時保存")', 'button:has-text("保存")', 'button:has-text("下書き保存")', '[aria-label="保存"]']:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=2000):
                    print(f"[route] 保存ボタンクリック: {sel}")
                    btn.dispatch_event('click')
                    save_clicked = True
                    break
            except Exception:
                pass

        if not save_clicked:
            print("[route] 保存ボタンが見つかりません — 自動保存を待機")

        # draft_save が完了するまで待機
        print("[route] draft_save 待機 (15s)...")
        page.wait_for_timeout(15000)
        page.screenshot(path='/tmp/edit_step2_saved.png')
        print(f"[route] この時点の draft インターセプト数: {intercepted['draft']}")
        print(f"[route] draft_save 後 URL: {page.url}")

        # 公開ボタンをクリック
        publish_btn_clicked = False
        for sel in [
            'button:has-text("公開に進む")',
            'button:has-text("更新する")',
            'button:has-text("公開する")',
            'button:has-text("投稿する")',
        ]:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=3000):
                    print(f"[route] 公開ボタンクリック: {sel}")
                    btn.click(timeout=5000)
                    publish_btn_clicked = True
                    print(f"[route] クリック後 URL: {page.url}")
                    break
            except Exception:
                pass

        if not publish_btn_clicked:
            print("[route] 公開ボタンが見つかりません")
            page.screenshot(path='/tmp/edit_step3_no_publish_btn.png')
            btns = page.locator('button').all_text_contents()
            print(f"[route] 現在のボタン一覧: {btns[:20]}")
        else:
            # パネルが開くまで wait_for_selector で待機 (最大15秒)
            confirm_sel = None
            for candidate in ['button:has-text("更新する")', 'button:has-text("公開する")', 'button:has-text("投稿する")']:
                try:
                    page.wait_for_selector(candidate, timeout=15000)
                    # 同名ボタンが複数ある (保存前 + パネル内) 可能性あり
                    btns_found = page.locator(candidate)
                    count = btns_found.count()
                    print(f"[route] {candidate} が {count} 件見つかりました")
                    confirm_sel = candidate
                    break
                except Exception as e:
                    print(f"[route] wait_for_selector ({candidate}): {e}")

            page.screenshot(path='/tmp/edit_step3_modal.png')
            print(f"[route] パネル後 URL: {page.url}")

            # 全ボタン・ロールボタンをスキャン
            all_btns = page.evaluate("""() => {
                const els = document.querySelectorAll('button, [role="button"]');
                return Array.from(els)
                    .map(e => e.innerText.trim())
                    .filter(t => t.length > 0 && t.length < 30);
            }""")
            print(f"[route] 全クリック要素: {all_btns[:30]}")

            confirm_clicked = False
            if confirm_sel:
                try:
                    btns_found = page.locator(confirm_sel)
                    count = btns_found.count()
                    # 2つ以上ある場合は最後（パネル内ボタン）
                    target = btns_found.last if count > 1 else btns_found.first
                    if target.is_visible(timeout=5000):
                        print(f"[route] 確定ボタンクリック: {confirm_sel} (count={count})")
                        target.click(timeout=5000)
                        confirm_clicked = True
                except Exception as e:
                    print(f"[route] 確定ボタンクリックエラー: {e}")

            if not confirm_clicked:
                print("[route] 確定ボタンが見つかりません — browser fetch で PATCH を試みます")
                # Playwright ブラウザ内から PATCH して status を published に変更
                patch_result = page.evaluate(f"""async () => {{
                    try {{
                        const resp = await fetch('https://note.com/api/v1/text_notes/{article_id}', {{
                            method: 'PATCH',
                            credentials: 'include',
                            headers: {{
                                'Content-Type': 'application/json',
                                'Accept': 'application/json, text/plain, */*',
                            }},
                            body: JSON.stringify({{text_note: {{status: 'published'}}}})
                        }});
                        const txt = await resp.text();
                        return {{status: resp.status, body: txt.substring(0, 200)}};
                    }} catch(e) {{ return {{error: String(e)}}; }}
                }}""")
                print(f"[route] browser PATCH result: {patch_result}")

                # fallback: draft_save の is_temp_saved=false でも試す
                publish_save_result = page.evaluate(f"""async () => {{
                    try {{
                        const resp = await fetch('https://note.com/api/v1/text_notes/draft_save?id={article_id}&is_temp_saved=false', {{
                            method: 'POST',
                            credentials: 'include',
                            headers: {{
                                'Content-Type': 'application/json',
                                'Accept': 'application/json, text/plain, */*',
                            }},
                            body: JSON.stringify({{body: {json.dumps(fixed_body)}, status: 'published'}})
                        }});
                        const txt = await resp.text();
                        return {{status: resp.status, body: txt.substring(0, 200)}};
                    }} catch(e) {{ return {{error: String(e)}}; }}
                }}""")
                print(f"[route] browser draft_save(false) result: {publish_save_result}")

            print("[route] 公開処理待機 (25s)...")
            page.wait_for_timeout(25000)
            page.screenshot(path='/tmp/edit_step4_published.png')
            print(f"[route] 公開後 URL: {page.url}")

        browser.close()

    print(f"[route] インターセプト結果: draft={intercepted['draft']}, publish={intercepted['publish']}")

    if intercepted['draft'] == 0 and intercepted['publish'] == 0:
        print("[route] インターセプトなし — 失敗")
        return False

    # 公開記事が実際に更新されたか検証 (最大 6 回 × 5秒)
    import time
    for attempt in range(1, 7):
        time.sleep(5)
        resp = _req.get(f"https://note.com/api/v3/notes/{note_key}", timeout=20)
        if resp.status_code != 200:
            print(f"[route] 試行 {attempt}: GET失敗 (status={resp.status_code})")
            continue
        cur = resp.json().get('data', {}).get('body', '')
        has_embed = 'embedded-service="note"' in cur and 'n71b416f7c92b' in cur
        no_crosslink = '（関連記事' not in cur
        print(f"[route] 試行 {attempt}: body_len={len(cur)}, has_embed={has_embed}, no_crosslink={no_crosslink}")
        if has_embed and no_crosslink:
            print(f"✅ 公開記事更新確認: OGP figure 追加 + クロスリンク削除 (試行 {attempt})")
            return True
        if no_crosslink:
            print(f"✅ 公開記事更新確認: クロスリンク削除済み (試行 {attempt})")
            return True

    print("[route] 検証タイムアウト: 公開記事が更新されず")
    return False


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

    import builtins
    csrf_from_page = getattr(builtins, '_NOTE_CSRF_TOKEN', '')
    if csrf_from_page:
        base_headers['X-CSRF-Token'] = csrf_from_page
    else:
        for xsrf_key in ('XSRF-TOKEN', '_xsrf', 'csrf_token', 'csrftoken'):
            xsrf = cookies.get(xsrf_key, '')
            if xsrf:
                base_headers['X-XSRF-TOKEN'] = xsrf
                break
        else:
            print("[WARN] CSRF/XSRF トークンが見つかりません — 422 になる可能性あり")

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

    if '関連記事' not in body:
        print("  変更なし（クロスリンクが見つかりません）")
        return True

    print(f"  クロスリンク候補を検出: {body.count('関連記事')} 件")
    idx = body.find('関連記事')
    print(f"  [DBG] HTML周辺:\n{body[max(0,idx-50):idx+200]}")

    fixed_body = fix_cross_links_html(body)
    if fixed_body == body:
        print("  正規表現にマッチするクロスリンクなし（手動確認が必要）")
        return False

    print("  クロスリンク修正完了")

    if not article_id:
        print("  [ERR] 記事 numeric ID が取得できません")
        return False

    # PUT を複数フォーマットで試す
    put_candidates = [
        f"https://note.com/api/v1/text_notes/{article_id}",
        f"https://note.com/api/v2/text_notes/{article_id}",
        f"https://editor.note.com/api/v1/text_notes/{article_id}",
        f"https://note.com/api/v1/text_notes/{note_key}",
    ]

    put_headers = {**base_headers, 'Content-Type': 'application/json'}
    # 2種類のボディフォーマットを試す
    put_body_formats = [
        {"text_note": {"body": fixed_body, "status": status}},
        {"body": fixed_body, "status": status},
        {"body": fixed_body},
    ]

    for put_url in put_candidates:
        for body_fmt in put_body_formats:
            print(f"[API] PUT {put_url} body_keys={list(body_fmt.keys())}")
            try:
                put_resp = requests.put(
                    put_url,
                    cookies=cookies, headers=put_headers,
                    json=body_fmt, timeout=30
                )
                print(f"  status: {put_resp.status_code}")
                if put_resp.status_code in (200, 201, 204):
                    print(f"✅ 記事 {note_key} の更新成功 ({put_url})")
                    return True
                elif put_resp.status_code == 422:
                    print(f"  422: {put_resp.text[:200]}")
                elif put_resp.status_code in (401, 403):
                    print(f"  {put_resp.status_code}: 認証エラー")
                    break  # このURLは諦める
            except Exception as e:
                print(f"  例外: {e}")

    print("[ERR] 全 PUT エンドポイントで失敗 — Playwright フォールバックへ")
    return None  # None = Playwright フォールバックを試す


def main():
    if not NOTE_KEY:
        print("[ERR] NOTE_KEY が未設定")
        sys.exit(1)

    cookies = load_cookies()
    if not cookies and not (NOTE_EMAIL and NOTE_PASSWORD):
        print("[ERR] 認証情報なし（NOTE_COOKIES または NOTE_EMAIL/PASSWORD が必要）")
        sys.exit(1)

    try:
        cookies = get_session_via_playwright(cookies)
    except Exception as e:
        print(f"[WARN] Playwright セッション確立失敗: {e}")

    result = api_update(NOTE_KEY, cookies)
    if result is True:
        sys.exit(0)

    # フォールバック: Playwright で draft_save + publish をインターセプト
    if result is None:
        import requests as _req
        resp = _req.get(f"https://note.com/api/v3/notes/{NOTE_KEY}", timeout=20)
        if resp.status_code == 200:
            article_data = resp.json().get('data', {})
            article_id = article_data.get('id')
            body = article_data.get('body', '')
            fixed_body = fix_cross_links_html(body)
            if fixed_body != body and article_id:
                ok = save_and_publish_via_playwright(NOTE_KEY, cookies, fixed_body, article_id)
                if ok:
                    sys.exit(0)

    sys.exit(1)


if __name__ == '__main__':
    main()
