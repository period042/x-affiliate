#!/usr/bin/env python3
"""drafts/*.json を note.com に下書きとして保存するスクリプト"""
import os, json, base64, sys, time
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')

NOTE_COOKIES_B64 = os.environ.get('NOTE_COOKIES', '')
NOTE_EMAIL       = os.environ.get('NOTE_EMAIL', '')
NOTE_PASSWORD    = os.environ.get('NOTE_PASSWORD', '')

BASE_DIR = (
    Path(__file__).parent.parent
    if os.environ.get('GITHUB_ACTIONS')
    else Path.home() / 'Documents' / '01_ClaudeCode' / 'x-affiliate'
)
DRAFTS_DIR     = BASE_DIR / 'drafts'
SCREENSHOT_DIR = Path('/tmp') if os.environ.get('GITHUB_ACTIONS') else BASE_DIR / 'logs'
LOCAL_COOKIE_PATH = BASE_DIR / 'note_cookies.json'


def ss(page, name):
    try:
        page.screenshot(path=str(SCREENSHOT_DIR / f'{name}.png'))
    except Exception:
        pass


def load_storage_state():
    if NOTE_COOKIES_B64:
        decoded = base64.b64decode(NOTE_COOKIES_B64.encode('ascii')).decode('utf-8')
        return json.loads(decoded)
    if LOCAL_COOKIE_PATH.exists():
        return json.loads(LOCAL_COOKIE_PATH.read_text(encoding='utf-8'))
    return None


def type_content(page, content):
    lines = content.split('\n')
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('https://') or stripped.startswith('http://'):
            page.keyboard.type(stripped, delay=3)
            page.keyboard.press('Enter')
            page.wait_for_timeout(2500)
        elif stripped.startswith('<a ') and 'href=' in stripped:
            # アフィリエイトリンク: URLだけ入力してOGP化 + テキストは次行
            import re
            m = re.search(r'href="([^"]+)"', stripped)
            link_text = re.sub(r'<[^>]+>', '', stripped).strip()
            if m:
                page.keyboard.type(m.group(1), delay=3)
                page.keyboard.press('Enter')
                page.wait_for_timeout(2500)
        else:
            if line:
                page.keyboard.type(line, delay=8)
            page.keyboard.press('Enter')
            page.wait_for_timeout(30)


def save_draft(page, article_path):
    article = json.loads(article_path.read_text(encoding='utf-8'))
    title   = article['title']
    content = article['content']
    print(f"\n=== 下書き保存: {title[:50]}... ===")

    # 新規記事ページへ
    print("[1] 新規記事ページへ...")
    page.goto('https://note.com/notes/new', wait_until='networkidle', timeout=30000)
    page.wait_for_timeout(5000)
    ss(page, f'01_editor_{article_path.stem}')

    if 'login' in page.url:
        raise RuntimeError("ログインが必要")

    # タイトル入力
    print("[2] タイトル入力...")
    for sel in ['[placeholder*="タイトル"]', 'h1[contenteditable]', '.title-input', 'div[data-placeholder*="タイトル"]']:
        try:
            page.click(sel, timeout=3000)
            page.keyboard.type(title, delay=5)
            page.wait_for_timeout(500)
            print(f"  タイトル入力: {sel}")
            break
        except Exception:
            continue

    page.keyboard.press('Enter')
    page.wait_for_timeout(1000)

    # 本文入力
    print("[3] 本文入力...")
    for sel in ['[placeholder*="本文"]', '.ProseMirror', 'div[contenteditable="true"]:not(h1)', '.editor-content']:
        try:
            page.click(sel, timeout=3000)
            print(f"  本文フォーカス: {sel}")
            break
        except Exception:
            continue

    type_content(page, content)
    print("  本文入力完了")
    ss(page, f'03_typed_{article_path.stem}')

    # 自動保存を待つ（note.comは入力後しばらくすると自動保存）
    print("[4] 自動保存待ち (10秒)...")
    page.wait_for_timeout(10000)
    ss(page, f'04_after_wait_{article_path.stem}')

    # 現在のURLを取得（下書きのedit URLになっているはず）
    current_url = page.url
    print(f"  URL: {current_url}")

    # note IDを抽出
    import re
    note_id = None
    m = re.search(r'/notes/new\?d=(\w+)', current_url)
    if not m:
        m = re.search(r'/notes/(\w+)/edit', current_url)
    if m:
        note_id = m.group(1)

    print(f"  下書きID: {note_id or '取得失敗'}")

    # ドラフトのnote_urlを更新
    article['draft_url'] = current_url
    article['draft_id']  = note_id
    article_path.write_text(json.dumps(article, ensure_ascii=False, indent=2), encoding='utf-8')

    return note_id, current_url


def main():
    SCREENSHOT_DIR.mkdir(exist_ok=True)

    draft_files = sorted(DRAFTS_DIR.glob('draft_house_*.json'))
    if not draft_files:
        print("下書きファイルが見つかりません")
        return

    print(f"対象: {[f.name for f in draft_files]}")

    from playwright.sync_api import sync_playwright

    storage_state = load_storage_state()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage']
        )
        ctx_kwargs = dict(
            user_agent=(
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/124.0.0.0 Safari/537.36'
            ),
            viewport={'width': 1280, 'height': 900},
        )
        if storage_state:
            ctx_kwargs['storage_state'] = storage_state
        context = browser.new_context(**ctx_kwargs)
        page = context.new_page()

        # ログイン確認
        print("[0] ログイン確認...")
        page.goto('https://note.com', wait_until='domcontentloaded', timeout=30000)
        page.wait_for_timeout(2000)
        if 'login' in page.url:
            if NOTE_EMAIL and NOTE_PASSWORD:
                page.goto('https://note.com/login', wait_until='domcontentloaded')
                page.wait_for_timeout(3000)
                page.fill('input[type="email"]', NOTE_EMAIL)
                page.fill('input[type="password"]', NOTE_PASSWORD)
                page.click('button[type="submit"]')
                page.wait_for_load_state('networkidle', timeout=20000)
                if 'login' in page.url:
                    raise RuntimeError("ログイン失敗")
            else:
                raise RuntimeError("認証情報なし")
        print(f"  ログイン済み: {page.url[:60]}")

        results = []
        for draft_path in draft_files:
            try:
                note_id, url = save_draft(page, draft_path)
                results.append((draft_path.name, 'OK', url))
            except Exception as e:
                print(f"  エラー: {e}")
                results.append((draft_path.name, 'NG', str(e)))
            time.sleep(3)

        browser.close()

    print("\n=== 結果 ===")
    for fname, status, url in results:
        print(f"[{status}] {fname}: {url}")


if __name__ == '__main__':
    main()
