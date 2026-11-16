#!/usr/bin/env python3
"""note.comに記事を自動投稿するPlaywrightスクリプト（Cookie認証方式）"""
import os
import json
import base64
import sys
from pathlib import Path
import re
from datetime import datetime, timedelta, timezone
from article_checks import assert_editor_headings, validate_article_text
from note_hashtags import apply_article_hashtags
from note_eyecatch import select_gallery_image, used_eyecatch_keys

TOKYO = timezone(timedelta(hours=9))
HEADING_LINE = re.compile(r'^#{2,3}\s+\S')

NOTE_COOKIES_B64  = os.environ.get('NOTE_COOKIES', '')
NOTE_EMAIL        = os.environ.get('NOTE_EMAIL', '')
NOTE_PASSWORD     = os.environ.get('NOTE_PASSWORD', '')
NOTE_USERNAME     = os.environ.get('NOTE_USERNAME', 'english_gaishi')
SUMMARY_FILE      = os.environ.get('GITHUB_STEP_SUMMARY', '')


def write_summary(text: str):
    if SUMMARY_FILE:
        with open(SUMMARY_FILE, 'a', encoding='utf-8') as f:
            f.write(text + '\n')

BASE_DIR = (
    Path(__file__).parent.parent
    if os.environ.get('GITHUB_ACTIONS')
    else Path.home() / 'Documents' / 'x-affiliate'
)
DATA_DIR = (
    Path(os.environ['DATA_DIR'])
    if os.environ.get('DATA_DIR')
    else BASE_DIR
)
NOTE_QUEUE_DIR    = DATA_DIR / 'note_queue'
NOTE_POSTED_DIR   = DATA_DIR / 'note_posted'
SCREENSHOT_DIR    = Path('/tmp') if os.environ.get('GITHUB_ACTIONS') else BASE_DIR / 'logs'
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
        with open(LOCAL_COOKIE_PATH, encoding='utf-8') as f:
            return json.load(f)
    return None


def get_next_article():
    today = datetime.now(TOKYO).date()
    for path in sorted(NOTE_QUEUE_DIR.glob('*.json')):
        try:
            # utf-8-sig: BOM付きUTF-8も正しく読む
            d = json.loads(path.read_text(encoding='utf-8-sig'))
            if d.get('paid'):
                continue
            sched = datetime.fromisoformat(d['scheduled_for']).date()
            if sched <= today:
                char_count = len(d.get('content', ''))
                if char_count < 3000:
                    print(f'[SKIP] 文字数不足 {char_count}字 < 3000字: {path.name}')
                    continue
                try:
                    validate_article_text(d.get('content', ''))
                except ValueError as problem:
                    print(f'[SKIP] {problem}: {path.name}')
                    write_summary(f"⚠️ 本文構成に問題があるため投稿スキップ: `{path.name}` {problem}")
                    continue
                return path
        except Exception as e:
            print(f'[WARN] scheduled_for parse failed ({path.name}): {e} → skip')
    return None


def insert_content_with_ogp(page, content):
    """本文をOGPカード付き・[text](url)リンク対応で挿入する"""
    import re as _link_re
    LINK_PATTERN = _link_re.compile(r'\[([^\]]+)\]\((https?://[^)]+)\)')

    lines = content.split('\n')
    after_heading = False
    for line in lines:
        stripped = line.strip()
        if not stripped and after_heading:
            # 見出し直後の空行は入力しない（見出しと本文の間に余白が入るため）
            continue
        after_heading = bool(HEADING_LINE.match(stripped))
        if stripped.startswith('https://') or stripped.startswith('http://'):
            # 裸のURL → OGPカード変換
            page.keyboard.type(stripped, delay=3)
            page.keyboard.press('Enter')
            page.wait_for_timeout(2500)
        elif LINK_PATTERN.search(line):
            # [text](url) → テキストラベル + URL単独行（OGPカード変換）
            parts = LINK_PATTERN.split(line)
            # parts: [before, link_text, url, between, link_text2, url2, after, ...]
            i = 0
            while i < len(parts):
                r = i % 3
                chunk = parts[i]
                if r == 0:
                    if chunk:
                        page.keyboard.type(chunk, delay=8)
                elif r == 1:
                    link_text = chunk
                    link_url = parts[i + 1]
                    # テキストラベルを入力してEnter
                    page.keyboard.type(link_text, delay=8)
                    page.keyboard.press('Enter')
                    # URLを単独行に入力 → note.comがOGPカードに変換
                    page.keyboard.type(link_url, delay=3)
                    page.keyboard.press('Enter')
                    page.wait_for_timeout(2500)  # OGPカード変換を待つ
                    i += 1  # url部分(r==2)をスキップ
                i += 1
            # リンク行は上でEnterを押し済みなので追加Enter不要
            page.wait_for_timeout(30)
        else:
            if line:
                page.keyboard.type(line, delay=8)
            page.keyboard.press('Enter')
            page.wait_for_timeout(30)


def upload_eyecatch(page, image_path):
    """アイキャッチ画像をアップロードする"""
    # デバッグ：エディタ上部のクリッカブル要素を列挙
    info = page.evaluate("""
        () => {
            const results = [];
            // アイキャッチ候補を探す
            const selectors = [
                '[class*="eyecatch"]',
                '[class*="Eyecatch"]',
                '[class*="cover"]',
                '[class*="Cover"]',
                '[class*="thumbnail"]',
                '[class*="Thumbnail"]',
                '[data-testid*="eyecatch"]',
                'label[for*="image"]',
                'label[for*="file"]',
                'input[type="file"]',
            ];
            for (const sel of selectors) {
                const els = document.querySelectorAll(sel);
                if (els.length > 0) {
                    results.push(sel + ' (' + els.length + '個)');
                }
            }
            return results;
        }
    """)
    print(f"  アイキャッチ候補: {info}")

    # ファイル選択チャネルを開く
    try:
        with page.expect_file_chooser(timeout=10000) as fc_info:
            # 候補セレクタを順番に試す
            clicked = False
            for sel in [
                '[class*="eyecatch"]',
                '[class*="Eyecatch"]',
                '[class*="cover"] button',
                '[class*="Cover"] button',
                'input[type="file"]',
            ]:
                try:
                    page.click(sel, timeout=3000)
                    clicked = True
                    print(f"  アイキャッチクリック: {sel}")
                    break
                except Exception:
                    continue

            if not clicked:
                # JavaScript でinput[type=file]をトリガー
                page.evaluate("""
                    () => {
                        const inp = document.querySelector('input[type="file"]');
                        if (inp) inp.click();
                    }
                """)

        fc = fc_info.value
        fc.set_files(image_path)
        page.wait_for_timeout(3000)
        ss(page, '03b_after_eyecatch')
        print("  アイキャッチアップロード完了")
        return True
    except Exception as e:
        print(f"  アイキャッチスキップ: {e}")
        return False


def post_article(article_path):
    from playwright.sync_api import sync_playwright

    with open(article_path, encoding='utf-8-sig') as f:
        article = json.load(f)

    title         = article['title']
    content       = article['content']
    genre         = article.get('genre', 'default')
    scheduled_for = article.get('scheduled_for', '')
    print(f"\n=== 投稿開始: {title[:40]}... ===")
    SCREENSHOT_DIR.mkdir(exist_ok=True)

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
            permissions=['clipboard-read', 'clipboard-write'],
        )
        if storage_state:
            ctx_kwargs['storage_state'] = storage_state
        context = browser.new_context(**ctx_kwargs)
        page = context.new_page()

        # ── ネットワーク傍受（全APIコールとボディ・レスポンスを取得） ──
        api_calls = []
        put_bodies = {}    # {numeric_id: body_str}
        put_responses = {} # {numeric_id: (status_code, body_str)}

        import re as _re

        def on_request(req):
            if '/api/' in req.url and req.method in ('POST', 'PUT', 'PATCH'):
                try:
                    api_calls.append(f"{req.method} {req.url}")
                    m = _re.search(r'/text_notes/(\d+)$', req.url)
                    if m and req.method == 'PUT':
                        put_bodies[m.group(1)] = req.post_data or ''
                except Exception:
                    api_calls.append(f"{req.method} {req.url}")

        def on_response(resp):
            if '/api/' in resp.url and resp.request.method == 'PUT':
                try:
                    m = _re.search(r'/text_notes/(\d+)$', resp.url)
                    if m:
                        put_responses[m.group(1)] = (resp.status, resp.text())
                except Exception:
                    pass

        page.on('request', on_request)
        page.on('response', on_response)

        try:
            # ── ログイン確認 ──────────────────────────
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
                else:
                    raise RuntimeError("認証情報なし")
            print(f"  OK: {page.url[:60]}")

            # ── 新規記事ページ ─────────────────────────
            print("[2] 新規記事ページへ...")
            page.goto('https://note.com/notes/new', wait_until='networkidle', timeout=30000)
            page.wait_for_timeout(5000)
            ss(page, '02_editor')
            if 'login' in page.url:
                raise RuntimeError("記事ページでログイン要求")

            # ── タイトル入力（JavaScript + keyboard） ─
            print("[4] タイトル入力...")
            found_sel = page.evaluate("""
                () => {
                    const sels = [
                        '[data-placeholder="タイトル"]',
                        '[placeholder*="タイトル"]',
                        'h1[contenteditable="true"]',
                        '[class*="title"][contenteditable]',
                        '[contenteditable="true"]',
                    ];
                    for (const s of sels) {
                        const el = document.querySelector(s);
                        if (el) {
                            el.focus();
                            el.click();
                            el.textContent = '';
                            el.dispatchEvent(new Event('input', {bubbles: true}));
                            return s;
                        }
                    }
                    return null;
                }
            """)
            print(f"  セレクタ: {found_sel}")
            page.wait_for_timeout(300)
            page.keyboard.type(title, delay=20)
            page.wait_for_timeout(500)
            ss(page, '04_title')
            print(f"  完了: {title[:30]}...")

            # ── 本文エリアへ移動してOGP付き入力 ────────
            print("[5] 本文入力（OGP対応）...")
            # ProseMirrorをクリック
            try:
                page.click('.ProseMirror', timeout=5000)
            except Exception:
                try:
                    els = page.locator('[contenteditable="true"]').all()
                    if len(els) > 1:
                        els[-1].click()
                except Exception:
                    page.keyboard.press('Tab')
            page.wait_for_timeout(300)

            # URLを単独行でペーストしてOGP変換させる
            insert_content_with_ogp(page, content)
            ss(page, '05_content')
            assert_editor_headings(
                page,
                expected_headings=sum(1 for line in content.splitlines() if HEADING_LINE.match(line.strip())),
            )
            print("  完了")

            print("[5b] フォトギャラリーから見出し画像を選択...")
            article['eyecatch_key'] = select_gallery_image(
                page, article, used_eyecatch_keys(NOTE_POSTED_DIR, NOTE_QUEUE_DIR, exclude=Path(article_path)))
            ss(page, '05b_eyecatch')

            # ── 公開に進む ────────────────────────────
            print("[6] 公開ボタン...")
            publish_settings_selectors = [
                'button:has-text("公開に進む")',
                'button:has-text("公開設定")',
            ]
            # すべての記事で、タグ設定前に最終公開ボタンを押さない。
            settings_opened = False
            for sel in publish_settings_selectors:
                try:
                    page.click(sel, timeout=8000)
                    print(f"  clicked: {sel}")
                    settings_opened = True
                    break
                except Exception:
                    continue
            if not settings_opened:
                raise RuntimeError("タグ設定前に公開設定画面を開けませんでした")
            page.wait_for_timeout(3000)
            ss(page, '06_modal')

            apply_article_hashtags(page, article)

            # ── モーダル内でアイキャッチ設定 ──────────
            # [6b] はエディタ段階（[3]）で完了済みのためスキップ

            # ── 公開確定 ──────────────────────────────────
            print("[7] 公開確定...")

            # ページが安定してからクリック（非同期処理の完了を待つ）
            try:
                page.wait_for_load_state('networkidle', timeout=8000)
            except Exception:
                pass
            page.wait_for_timeout(2000)

            # 「投稿する」ボタンの状態を詳細確認
            btn_info = page.evaluate("""
                () => {
                    const btns = Array.from(document.querySelectorAll('button'));
                    const b = btns.find(b => (b.innerText||'').trim() === '投稿する');
                    if (!b) return {found: false};
                    const rect = b.getBoundingClientRect();
                    return {
                        found: true,
                        disabled: b.disabled,
                        ariaDisabled: b.getAttribute('aria-disabled'),
                        display: getComputedStyle(b).display,
                        visibility: getComputedStyle(b).visibility,
                        pointerEvents: getComputedStyle(b).pointerEvents,
                        x: rect.x, y: rect.y, w: rect.width, h: rect.height
                    };
                }
            """)
            print(f"  ボタン状態: {btn_info}")

            confirmed = False
            if btn_info.get('found') and not btn_info.get('disabled'):
                # 座標クリック（セレクタより確実）
                x = btn_info.get('x', 0) + btn_info.get('w', 0) / 2
                y = btn_info.get('y', 0) + btn_info.get('h', 0) / 2
                if x > 0 and y > 0:
                    page.mouse.click(x, y)
                    print(f"  座標クリック: ({x:.0f}, {y:.0f})")
                    confirmed = True
                    page.wait_for_timeout(8000)

            if not confirmed:
                page.click('button:has-text("投稿する")', timeout=5000)
                print("  フォールバッククリック")
                page.wait_for_timeout(8000)

            # クリック後のナビゲーション or API完了を待つ
            try:
                # URL が editor.note.com 以外に変わるまで最大15秒待つ
                page.wait_for_function(
                    "() => !window.location.href.includes('editor.note.com') || "
                    "window.location.href.includes('/published') || "
                    "window.location.href.includes('note.com/') && !window.location.href.includes('editor.')",
                    timeout=15000
                )
                print(f"  URLが変化: {page.url}")
            except Exception:
                print(f"  URLは変化せず: {page.url}")
                page.wait_for_timeout(5000)

            page.wait_for_timeout(5000)
            ss(page, '08_after_publish')
            print(f"  現在URL: {page.url}")

            # /publish/ ページに留まっている場合、追加操作を確認
            if '/publish/' in page.url or '/edit/' in page.url:
                print("  /publish/ ページを検査中...")
                # ページ上のボタンを列挙
                pub_buttons = page.locator('button').all()
                btn_texts = []
                for b in pub_buttons[:20]:
                    try:
                        t = b.inner_text().strip()
                        if t:
                            btn_texts.append(t)
                    except Exception:
                        pass
                print(f"  ページ上ボタン: {btn_texts}")

                # 公開完了・クリエイターページ表示 ボタンを探してクリック
                for sel in [
                    'button:has-text("クリエイターページに表示")',
                    'button:has-text("公開する")',
                    'button:has-text("記事を見る")',
                    'button:has-text("完了")',
                    'a:has-text("記事を見る")',
                    'a:has-text("公開記事を確認")',
                ]:
                    try:
                        page.click(sel, timeout=3000)
                        page.wait_for_timeout(2000)
                        print(f"  追加クリック: {sel}")
                        break
                    except Exception:
                        continue

                page.wait_for_timeout(2000)
                ss(page, '09_final')
                print(f"  最終URL: {page.url}")

            # 傍受したAPIコールを表示
            print(f"\n[API calls]")
            for c in api_calls:
                print(f"  {c}")

            # ── 公開API直接呼び出し ────────────────────────
            if '/publish/' in page.url or '/edit/' in page.url:
                print("\n[公開API直接呼び出し]")
                try:
                    import requests as req_lib, json as _json

                    # 数値IDを特定
                    numeric_id = None
                    for call in api_calls:
                        m = _re.search(r'/text_notes/(\d+)', call)
                        if m:
                            numeric_id = m.group(1)
                            break

                    # 初回PUT（/publish/ナビ時）のレスポンスを確認
                    if numeric_id and numeric_id in put_responses:
                        init_status, init_body = put_responses[numeric_id]
                        print(f"  初回PUT → HTTP {init_status}")
                        try:
                            init_data = _json.loads(init_body).get('data', {})
                            print(f"  初回PUT status: {init_data.get('status')} key: {init_data.get('key')}")
                            if init_data.get('status') == 'published':
                                _key = init_data.get('key', '')
                                _uname = (init_data.get('user') or {}).get('urlname') or NOTE_USERNAME
                                pub_url = f"https://note.com/{_uname}/n/{_key}"
                                print(f"  ✅ 初回PUTで公開完了: {pub_url}")
                                # 公開済みなので追加処理不要
                                numeric_id = None  # 以降のAPI呼び出しをスキップ
                        except Exception:
                            pass

                    if numeric_id and numeric_id in put_bodies:
                        raw_body = put_bodies[numeric_id]
                        print(f"  PUT body長: {len(raw_body)}文字")
                        try:
                            _bd = _json.loads(raw_body)
                            print(f"  PUT body キー: {list(_bd.keys())}")
                            for k in ['status', 'is_paid']:
                                if k in _bd:
                                    print(f"  既存 {k}: {_bd[k]}")
                        except Exception:
                            pass
                        try:
                            body_dict = _json.loads(raw_body)
                        except Exception:
                            body_dict = {}

                        # published_at は 422 の原因なので削除
                        # status を確実に published にセット
                        body_dict.pop('published_at', None)
                        body_dict['status'] = 'published'

                        raw_cookies = context.cookies()
                        jar = req_lib.cookies.RequestsCookieJar()
                        for c in raw_cookies:
                            if 'note.com' in c.get('domain', ''):
                                jar.set(c['name'], c['value'],
                                        domain=c.get('domain',''), path=c.get('path','/'))
                        session = req_lib.Session()
                        session.cookies = jar
                        session.headers.update({
                            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                            'Referer': page.url,
                            'Content-Type': 'application/json',
                        })

                        resp = session.put(
                            f'https://note.com/api/v1/text_notes/{numeric_id}',
                            json=body_dict,
                            timeout=15
                        )
                        print(f"  PUT（status=published）→ {resp.status_code}")
                        if resp.ok:
                            try:
                                rdata = resp.json().get('data', {})
                                print(f"  status: {rdata.get('status')} key: {rdata.get('key')}")
                                _key = rdata.get('key', '')
                                _uname = (rdata.get('user') or {}).get('urlname') or NOTE_USERNAME
                                pub_url = f"https://note.com/{_uname}/n/{_key}"
                                print(f"  ✅ 公開URL: {pub_url}")
                            except Exception:
                                print(f"  レスポンス: {resp.text[:200]}")
                        else:
                            print(f"  エラーレスポンス: {resp.text[:300]}")
                    elif numeric_id:
                        print(f"  ID={numeric_id} のPUTボディなし (put_bodies keys: {list(put_bodies.keys())})")
                    elif numeric_id is None and put_responses:
                        pass  # 初回PUTで既に公開済み
                    else:
                        print("  数値IDが取得できませんでした")
                except Exception as e_pub:
                    print(f"  公開API直接呼び出し失敗: {e_pub}")

            published_url = page.url

            # ── 公開確認（note.com公開URLにGETして200を確認） ──
            note_key = None
            for call in api_calls:
                m = _re.search(r'/text_notes/([a-z0-9]+)$', call)
                if m and not m.group(1).isdigit():
                    note_key = m.group(1)
            # PUTレスポンスからも取得
            if not note_key:
                for _id, (_st, _body) in put_responses.items():
                    try:
                        _d = json.loads(_body).get('data', {})
                        if _d.get('key'):
                            note_key = _d['key']
                            break
                    except Exception:
                        pass

            verified = False
            final_url = published_url
            if note_key:
                import time as _time
                _time.sleep(5)   # 公開反映を待つ
                try:
                    import requests as _req
                    raw_cookies = context.cookies()
                    jar = _req.cookies.RequestsCookieJar()
                    for c in raw_cookies:
                        if 'note.com' in c.get('domain', ''):
                            jar.set(c['name'], c['value'],
                                    domain=c.get('domain', ''), path=c.get('path', '/'))
                    sess = _req.Session()
                    sess.cookies = jar

                    # まず初回PUTレスポンスの公開URLを信頼
                    for _id, (_st, _body) in put_responses.items():
                        try:
                            _d = json.loads(_body).get('data', {})
                            if _d.get('status') == 'published' and _st == 200:
                                _uname = (_d.get('user') or {}).get('urlname') or NOTE_USERNAME
                                final_url = f"https://note.com/{_uname}/n/{_d['key']}"
                                verified = True
                                break
                        except Exception:
                            pass

                    if not verified:
                        # GET で公開確認（リトライ最大2回）
                        for attempt in range(1, 3):
                            r = sess.get(
                                f'https://note.com/api/v2/notes/{note_key}',
                                timeout=10
                            )
                            print(f"  [確認{attempt}] GET /api/v2/notes/{note_key} → {r.status_code}")
                            if r.ok:
                                _d = r.json().get('data', {})
                                if _d.get('status') == 'published':
                                    _uname = (_d.get('user') or {}).get('urlname') or NOTE_USERNAME
                                    final_url = f"https://note.com/{_uname}/n/{note_key}"
                                    verified = True
                                    break
                            if not verified and attempt == 1:
                                # リトライ: PUT を再送
                                print("  [リトライ] 公開APIを再送...")
                                for _id, _body_str in put_bodies.items():
                                    try:
                                        _bd = json.loads(_body_str)
                                        _bd['status'] = 'published'
                                        _bd.pop('published_at', None)
                                        _r2 = sess.put(
                                            f'https://note.com/api/v1/text_notes/{_id}',
                                            json=_bd,
                                            headers={'Content-Type': 'application/json',
                                                     'Referer': page.url},
                                            timeout=15
                                        )
                                        print(f"  リトライPUT → {_r2.status_code}")
                                        _time.sleep(5)
                                    except Exception as _e:
                                        print(f"  リトライ失敗: {_e}")
                except Exception as e_verify:
                    print(f"  確認エラー: {e_verify}")

            if verified:
                print(f"\n=== [OK] 公開確認済み: {final_url} ===")
            else:
                print(f"\n=== [WARN] 公開未確認 (URL: {published_url}) ===")

            # ── アーカイブ ────────────────────────────
            NOTE_POSTED_DIR.mkdir(exist_ok=True)
            result_data = {**article,
                           'posted_at':  datetime.now().isoformat(),
                           'url':        final_url,
                           'verified':   verified}
            with open(NOTE_POSTED_DIR / article_path.name, 'w', encoding='utf-8') as f:
                json.dump(result_data, f, ensure_ascii=False, indent=2)
            article_path.unlink()
            remaining = len(list(NOTE_QUEUE_DIR.glob('*.json')))
            print(f"残りキュー: {remaining}件")

            status_icon = '✅' if verified else '⚠️'
            write_summary(
                f"## note投稿\n"
                f"{status_icon} {'公開確認済み' if verified else '公開未確認（要確認）'}\n"
                f"- タイトル: {title[:50]}\n"
                f"- URL: {final_url}\n"
                f"- アイキャッチ: フォトギャラリー ({article.get('eyecatch_key')})\n"
                f"- 残りキュー: {remaining}件"
            )

            if not verified:
                print("[WARN] 公開確認できませんでした。note.comを手動確認してください。")
                sys.exit(1)

        except Exception as e:
            ss(page, 'error')
            print(f"\n[ERROR] {type(e).__name__}: {e}")
            write_summary(f"## note投稿\n❌ エラー発生: `{type(e).__name__}: {e}`")
            sys.exit(1)
        finally:
            browser.close()


def _add_note_promo_to_threads_queue(article_path: Path):
    """note投稿成功後、Threads告知投稿をキューに追加（Action4: Threads→note誘導ループ）"""
    try:
        with open(NOTE_POSTED_DIR / article_path.name, encoding='utf-8') as f:
            posted = json.load(f)

        title   = posted.get('title', '')
        url     = posted.get('url', '')
        genre   = posted.get('genre', '')
        short_title = title[:40]

        # Threads告知テンプレート（ジャンル別・強いフックで記事への誘導）
        PROMO_TEMPLATES = {
            'キャリア': [
                f"外資系10年で気づいたこと、noteに書いた。\n\n「{short_title}」\n\n転職を考えているなら読んでほしい内容。\n\n{url}",
                "実体験ベースなので、よくある「〇〇すれば年収アップ」系の話とは違う。気になる方はどうぞ。",
            ],
            '英語学習（大人）': [
                f"英語が伸びなかった理由、やっとわかった話をnoteに書いた。\n\n「{short_title}」\n\n外資系で英語を使い続けて気づいたことをまとめた。\n\n{url}",
                "「英語学習系の記事はもう読み飽きた」という人にこそ読んでほしい内容。試験英語と実務英語は別物だった。",
            ],
            '英語学習（子ども）': [
                f"外資系の親として、子どもの英語サービスを選んだ基準をnoteに書いた。\n\n「{short_title}」\n\n3年間の試行錯誤からたどり着いた結論。\n\n{url}",
                "「続けられるか」だけを基準にしたら、選ぶサービスが変わった。コメントや感想があればぜひ。",
            ],
            '投資': [
                f"外資系でRSU・ESPPをもらうようになってから、資産の考え方が変わった話をnoteに書いた。\n\n「{short_title}」\n\n{url}",
                "円建て資産しか持っていない時期と今では、ニュースの見方が違う。具体的な設計を書いているので参考になれば。",
            ],
            'default': [
                f"外資系IT10年の経験から書いた。\n\n「{short_title}」\n\n実体験ベースでまとめています。\n\n{url}",
                "コメントや質問があればぜひ。同じような状況の人の参考になれば。",
            ],
        }

        key    = genre if genre in PROMO_TEMPLATES else 'default'
        tweets = PROMO_TEMPLATES[key]
        fname  = f"{datetime.now().strftime('%Y-%m-%d_%H-%M')}_promo_note.json"

        THREAD_QUEUE_DIR = BASE_DIR / 'thread_queue'
        THREAD_QUEUE_DIR.mkdir(exist_ok=True)
        promo = {
            'type':           'thread',
            'genre':          genre or 'note誘導',
            'title':          title,
            'affiliate_note': 'note記事告知',
            'tweets':         tweets,
            'created_at':     datetime.now().isoformat(),
        }
        with open(THREAD_QUEUE_DIR / fname, 'w', encoding='utf-8') as f:
            json.dump(promo, f, ensure_ascii=False, indent=2)
        print(f"[Threads告知] キューに追加: {fname}")

    except Exception as e:
        print(f"[Threads告知] 追加スキップ（エラー）: {e}")


def main():
    jst_hour = datetime.now(TOKYO).hour
    if jst_hour < 7:
        print(f"深夜帯（JST {jst_hour}時）のため投稿しません")
        write_summary(f"## note投稿\n⏸️ 深夜帯（JST {jst_hour}時）のためスキップ")
        sys.exit(0)

    has_cookie = bool(NOTE_COOKIES_B64 or LOCAL_COOKIE_PATH.exists())
    has_passwd = bool(NOTE_EMAIL and NOTE_PASSWORD)
    if not has_cookie and not has_passwd:
        msg = "認証情報がありません。save_note_cookies.py を実行してください。"
        print(msg)
        write_summary(f"## note投稿\n❌ {msg}")
        sys.exit(1)

    article = get_next_article()
    if not article:
        print("note_queue が空です")
        write_summary("## note投稿\n⚠️ キューが空のため投稿スキップ")
        sys.exit(0)

    print(f"次の記事: {article.name}")

    # ── リトライループ（最大2回） ──────────────────────
    MAX_NOTE_RETRIES = 2
    last_exc = None

    for attempt in range(1, MAX_NOTE_RETRIES + 1):
        try:
            if attempt > 1:
                import time as _t
                wait = 30 * attempt
                print(f"\n[RETRY {attempt}/{MAX_NOTE_RETRIES}] {wait}秒待機後リトライ...")
                _t.sleep(wait)

            post_article(article)

            # ── Action4: note投稿成功後にThreads告知をキューに追加 ──
            _add_note_promo_to_threads_queue(article)
            return  # 成功したら終了

        except SystemExit as e:
            # post_article内でsys.exit(1)が呼ばれた場合
            if e.code == 0:
                return  # 正常終了
            last_exc = e
            if attempt < MAX_NOTE_RETRIES:
                print(f"[RETRY] 投稿失敗（試行{attempt}）、リトライします...")
            else:
                print(f"[FAIL] {MAX_NOTE_RETRIES}回試行後も失敗")
                write_summary(
                    f"## note投稿\n"
                    f"❌ {MAX_NOTE_RETRIES}回試行後も失敗\n"
                    f"- 記事: `{article.name}`\n"
                    f"- 要手動確認: note.comのCookieが期限切れの可能性"
                )
                sys.exit(1)

        except Exception as e:
            last_exc = e
            print(f"[ERROR] 予期しないエラー: {type(e).__name__}: {e}")
            if attempt < MAX_NOTE_RETRIES:
                print(f"[RETRY] リトライします...")
            else:
                write_summary(
                    f"## note投稿\n"
                    f"❌ 予期しないエラー（{MAX_NOTE_RETRIES}回試行）\n"
                    f"- 記事: `{article.name}`\n"
                    f"- エラー: `{type(e).__name__}: {e}`"
                )
                sys.exit(1)


if __name__ == '__main__':
    main()
