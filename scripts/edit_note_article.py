#!/usr/bin/env python3
"""
note.com 既投稿記事のクロスリンク形式を修正するスクリプト

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


def ss(page, name):
    try:
        page.screenshot(path=str(SCREENSHOT_DIR / f'edit_{name}.png'))
    except Exception:
        pass


def load_storage_state():
    if NOTE_COOKIES_B64:
        decoded = base64.b64decode(NOTE_COOKIES_B64.encode('ascii')).decode('utf-8')
        return json.loads(decoded)
    return None


def insert_content_with_ogp(page, content):
    lines = content.split('\n')
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('https://') or stripped.startswith('http://'):
            page.keyboard.type(stripped, delay=3)
            page.keyboard.press('Enter')
            page.wait_for_timeout(2500)
        else:
            if line:
                page.keyboard.type(line, delay=8)
            page.keyboard.press('Enter')
            page.wait_for_timeout(30)


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


def main():
    if not NOTE_KEY:
        print("[ERR] NOTE_KEY が未設定")
        sys.exit(1)

    storage = load_storage_state()
    if not storage and not (NOTE_EMAIL and NOTE_PASSWORD):
        print("[ERR] 認証情報なし（NOTE_COOKIESまたはNOTE_EMAIL/PASSWORDが必要）")
        sys.exit(1)

    from playwright.sync_api import sync_playwright
    import requests as req_lib

    with sync_playwright() as p:
        ctx_kwargs = {'viewport': {'width': 1280, 'height': 900}}
        if storage:
            ctx_kwargs['storage_state'] = storage

        browser = p.chromium.launch(headless=True)
        context = browser.new_context(**ctx_kwargs)
        page = context.new_page()

        # ネットワーク傍受（API呼び出しを全部記録）
        api_log = []     # [(method, url)]
        put_bodies = {}  # {id_or_key: body_str}
        get_responses = {}  # {id_or_key: (status, body_str)}

        def on_request(req):
            if '/api/' in req.url:
                api_log.append(f"{req.method} {req.url}")
                m_id = re.search(r'/text_notes/(\d+)$', req.url)
                m_key = re.search(r'/text_notes/([a-z0-9]+)$', req.url)
                key = (m_id or m_key)
                if key and req.method == 'PUT':
                    put_bodies[key.group(1)] = req.post_data or ''

        def on_response(resp):
            if '/api/' in resp.url:
                m_id = re.search(r'/text_notes/(\d+)$', resp.url)
                m_key = re.search(r'/text_notes/([a-z0-9]+)$', resp.url)
                key = (m_id or m_key)
                if key and resp.request.method in ('GET', 'PUT'):
                    try:
                        get_responses[key.group(1)] = (resp.status, resp.text())
                    except Exception:
                        pass

        page.on('request', on_request)
        page.on('response', on_response)

        try:
            # ログイン確認
            print("[1] ログイン確認...")
            page.goto('https://note.com', wait_until='domcontentloaded', timeout=30000)
            page.wait_for_timeout(2000)
            if 'login' in page.url:
                if NOTE_EMAIL and NOTE_PASSWORD:
                    page.goto('https://note.com/login', wait_until='domcontentloaded')
                    page.wait_for_timeout(3000)
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
                    if 'login' in page.url:
                        raise RuntimeError("ログイン失敗")
            print(f"  OK: {page.url[:60]}")

            # 編集ページへ
            edit_url = f'https://note.com/notes/{NOTE_KEY}/edit'
            print(f"[2] 編集ページへ: {edit_url}")
            page.goto(edit_url, wait_until='networkidle', timeout=30000)
            page.wait_for_timeout(5000)
            ss(page, '01_edit_loaded')
            print(f"  現在URL: {page.url[:80]}")

            # 「AIと相談」ダイアログがあれば閉じる
            try:
                close_btn = page.locator('button:has-text("×"), button[aria-label="閉じる"], .modal-close, [data-testid="close"]')
                if close_btn.count() > 0:
                    close_btn.first.click()
                    page.wait_for_timeout(500)
                    print("  AIダイアログを閉じました")
            except Exception:
                pass
            # Escapeでオーバーレイを閉じる
            page.keyboard.press('Escape')
            page.wait_for_timeout(500)

            # APIリクエストのログ表示
            print(f"  API呼び出し一覧 ({len(api_log)}件):")
            for call in api_log[:20]:
                print(f"    {call}")
            print(f"  取得したレスポンス keys: {list(get_responses.keys())[:10]}")

            # クロスリンク問題はAPIで確認せず、ソースファイルから確認して修正進める

            # ProseMirrorのテキストノードを直接修正するのは複雑なため、
            # エディタで全選択→削除→正しいコンテンツを再入力する方式を採用
            # まず記事の元テキストコンテンツを data_dir から取得
            data_dir_env = os.environ.get('DATA_DIR', '')
            if data_dir_env:
                note_posted_dir = Path(data_dir_env) / 'note_posted'
                matched = list(note_posted_dir.glob(f'*_{NOTE_KEY[-3:]}*.json'))
                # URL でマッチするファイルを探す
                source_content = None
                source_title = None
                for f in note_posted_dir.glob('*.json'):
                    try:
                        d = json.load(open(f, encoding='utf-8'))
                        if NOTE_KEY in d.get('url', ''):
                            source_content = fix_cross_links(d.get('content', ''))
                            source_title = d.get('title', '')
                            print(f"  ソースファイル: {f.name}")
                            break
                    except Exception:
                        pass

                if not source_content:
                    print("[ERR] note_posted からソースコンテンツが見つかりません")
                    sys.exit(1)
            else:
                print("[ERR] DATA_DIR が未設定")
                sys.exit(1)

            # エディタで本文を全選択して削除し、修正済みコンテンツを再入力
            print("[3] 本文エリアにフォーカス...")
            try:
                page.click('.ProseMirror', timeout=5000)
            except Exception:
                els = page.locator('[contenteditable="true"]').all()
                if len(els) > 1:
                    els[-1].click()
            page.wait_for_timeout(500)

            # 全選択・削除
            print("[4] 全選択→削除...")
            page.keyboard.press('Control+a')
            page.wait_for_timeout(300)
            page.keyboard.press('Delete')
            page.wait_for_timeout(500)
            ss(page, '04_cleared')

            # 修正済みコンテンツを入力
            print("[5] 修正済みコンテンツ入力...")
            insert_content_with_ogp(page, source_content)
            ss(page, '05_content_filled')
            print("  完了")

            # OGP読み込み待機 + エディタのフォーカスを外す
            print("[5.5] OGP読み込み待機...")
            try:
                page.wait_for_load_state('networkidle', timeout=20000)
            except Exception:
                page.wait_for_timeout(5000)
            # エディタ外をクリックしてフォーカスを外す
            page.mouse.click(640, 30)
            page.wait_for_timeout(1000)
            ss(page, '05b_before_publish')

            # 公開ボタンをクリック（モーダルが開くまで最大20秒待機）
            print("[6] 公開に進む...")
            clicked_step6 = False
            for sel in ['button:has-text("公開に進む")', 'button:has-text("公開設定")', 'button:has-text("公開する")']:
                try:
                    page.click(sel, timeout=6000)
                    print(f"  {sel} クリック成功")
                    clicked_step6 = True
                    break
                except Exception:
                    pass

            if not clicked_step6:
                # JavaScriptで強制クリック
                result = page.evaluate("""
                    () => {
                        const buttons = Array.from(document.querySelectorAll('button'));
                        const btn = buttons.find(b => b.textContent.includes('公開に進む') || b.textContent.includes('公開設定'));
                        if (btn) { btn.click(); return btn.textContent.trim(); }
                        return null;
                    }
                """)
                if result:
                    print(f"  JS強制クリック: {result}")
                    clicked_step6 = True

            if clicked_step6:
                # モーダルの確定ボタンが出現するまで待つ
                for sel in ['button:has-text("更新する")', 'button:has-text("公開する")', 'button:has-text("保存する")']:
                    try:
                        page.wait_for_selector(sel, timeout=20000)
                        print(f"  モーダル確認: {sel} が出現")
                        break
                    except Exception:
                        pass

            ss(page, '06_publish_modal')

            # 公開確定
            print("[7] 公開確定...")
            published = False
            for sel in ['button:has-text("更新する")', 'button:has-text("公開する")', 'button:has-text("保存する")']:
                try:
                    page.click(sel, timeout=8000)
                    page.wait_for_timeout(5000)
                    print(f"  {sel} クリック成功")
                    published = True
                    break
                except Exception:
                    pass

            if not published:
                print("  [WARN] 公開確定ボタンが見つかりませんでした")

            ss(page, '07_published')
            print(f"  最終URL: {page.url[:80]}")
            if published:
                print(f"✅ 記事 {NOTE_KEY} の修正完了")
            else:
                print(f"⚠️ 記事 {NOTE_KEY} の公開確定未完了（手動確認が必要）")
                sys.exit(1)

        except Exception as e:
            print(f"[ERR] {e}")
            ss(page, 'error')
            sys.exit(1)
        finally:
            browser.close()


if __name__ == '__main__':
    main()
