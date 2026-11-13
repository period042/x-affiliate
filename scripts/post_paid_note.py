#!/usr/bin/env python3
"""
有料note記事を投稿するPlaywrightスクリプト。
note_queueの中から paid:true のファイルを対象に処理する。

フロー:
1. 無料部分を入力
2. 「有料エリア設定」ボタンで有料ライン挿入
3. 有料部分を入力
4. 公開モーダルで価格設定（article JSONの price フィールド）
5. 投稿する
"""
import base64, json, sys, os, re
from pathlib import Path
from datetime import datetime, timedelta, timezone
from playwright.sync_api import sync_playwright
from generate_note_image import generate as generate_header_image

sys.stdout.reconfigure(encoding='utf-8')

BASE_DIR  = Path(__file__).parent.parent  # x-affiliate/
DATA_DIR  = Path(os.environ.get('DATA_DIR', str(BASE_DIR)))
QUEUE_DIR = DATA_DIR / 'note_queue'
POSTED_DIR= DATA_DIR / 'note_posted'
LOG_DIR   = Path('/tmp') if os.environ.get('GITHUB_ACTIONS') else BASE_DIR / 'logs'
LOCAL_COOKIE_PATH = BASE_DIR / 'note_cookies.json'
TOKYO = timezone(timedelta(hours=9))
LOG_DIR.mkdir(parents=True, exist_ok=True)


def ss(page, name):
    try:
        page.screenshot(path=str(LOG_DIR / f'paid_{name}.png'))
    except Exception:
        pass


def get_next_paid_article():
    now = datetime.now(TOKYO)
    for path in sorted(QUEUE_DIR.glob('*.json')):
        try:
            d = json.loads(path.read_text(encoding='utf-8-sig'))
            if not d.get('paid'):
                continue
            sched = datetime.fromisoformat(d['scheduled_for'])
            if sched.tzinfo is None:
                sched = sched.replace(tzinfo=TOKYO)
            else:
                sched = sched.astimezone(TOKYO)
            if sched <= now:
                return path, d
        except Exception as e:
            print(f'[WARN] {path.name}: {e}')
    return None, None


def type_content(page, content):
    """本文をキーボード入力（OGPカード変換なし・高速版）"""
    LINK_PATTERN = re.compile(r'\[([^\]]+)\]\((https?://[^)]+)\)')
    lines = content.split('\n')
    for line in lines:
        if line.startswith('## '):
            line = '■ ' + line[3:].strip()
        line = re.sub(r'\*\*([^*]+)\*\*', r'\1', line)
        if LINK_PATTERN.search(line):
            parts = LINK_PATTERN.split(line)
            i = 0
            while i < len(parts):
                r = i % 3
                chunk = parts[i]
                if r == 0:
                    if chunk:
                        page.keyboard.type(chunk, delay=5)
                elif r == 1:
                    link_text = chunk
                    link_url = parts[i + 1]
                    page.keyboard.type(link_text, delay=5)
                    page.keyboard.press('Enter')
                    page.keyboard.type(link_url, delay=3)
                    page.keyboard.press('Enter')
                    page.wait_for_timeout(1500)
                    i += 1
                i += 1
            page.wait_for_timeout(20)
        else:
            if line:
                page.keyboard.type(line, delay=5)
            page.keyboard.press('Enter')
            page.wait_for_timeout(20)


def insert_paid_line(page):
    """有料エリア設定ボタンをクリックして有料ラインを挿入"""
    print("  有料ライン挿入中...")

    # ツールバーの有料エリアボタンを探す
    for sel in [
        'button[aria-label*="有料"]',
        'button[title*="有料"]',
        '[class*="paywall"]',
        '[class*="PaidArea"]',
        '[class*="paidArea"]',
        'button[data-id="paid"]',
    ]:
        try:
            page.click(sel, timeout=3000)
            print(f"  クリック: {sel}")
            page.wait_for_timeout(500)
            return True
        except Exception:
            continue

    # JS経由でツールバーボタンを探す
    clicked = page.evaluate("""
        () => {
            const btns = Array.from(document.querySelectorAll('button'));
            const b = btns.find(b => {
                const t = (b.innerText || b.title || b.getAttribute('aria-label') || '').toLowerCase();
                return t.includes('有料') || t.includes('paid') || t.includes('ペイウォール');
            });
            if (b) { b.click(); return true; }
            // SVGアイコンのみのボタンはtitle属性で識別
            const svgBtns = Array.from(document.querySelectorAll('button[title]'));
            const sb = svgBtns.find(b => b.title.includes('有料') || b.title.includes('Paid'));
            if (sb) { sb.click(); return true; }
            return false;
        }
    """)
    if clicked:
        print("  JS経由で有料ライン挿入")
        page.wait_for_timeout(500)
        return True

    # ツールバーのボタン一覧を出力してデバッグ
    btns = page.evaluate("""
        () => {
            return Array.from(document.querySelectorAll('button')).slice(0,30).map(b => ({
                text: b.innerText?.trim().slice(0,20),
                title: b.title?.slice(0,20),
                aria: b.getAttribute('aria-label')?.slice(0,20),
            }));
        }
    """)
    print(f"  [DEBUG] ボタン一覧: {btns}")
    print("  [WARN] 有料エリアボタンが見つかりません")
    return False


def set_price_in_modal(page, price):
    """公開モーダルで価格を設定する"""
    print(f"  価格設定: {price}円")

    paid_selected = False
    for sel in [
        'input[type="checkbox"][name*="paid"]',
        'input[type="checkbox"][name*="price"]',
        'input[type="radio"][value="paid"]',
        'label:has-text("有料")',
        'button:has-text("有料")',
        '[class*="PriceToggle"]',
    ]:
        try:
            el = page.locator(sel).first
            if not el.is_visible():
                continue
            if el.get_attribute('type') == 'checkbox':
                if not el.is_checked():
                    el.check()
            else:
                el.click()
            paid_selected = True
            print(f"  有料設定: {sel}")
            break
        except Exception:
            continue

    price_set = False
    for sel in [
        'input[name="price"]',
        'input[placeholder*="価格"]',
        'input[placeholder*="円"]',
        'input[type="number"]',
        '[class*="price"] input',
        '[class*="Price"] input',
    ]:
        try:
            el = page.locator(sel).first
            if not el.is_visible():
                continue
            el.fill(str(price))
            el.press('Tab')
            page.wait_for_timeout(500)
            price_set = el.input_value().strip() == str(price)
            print(f"  価格入力: {el.input_value()}円")
            break
        except Exception:
            continue

    if not paid_selected or not price_set:
        raise RuntimeError(f"有料設定を確認できませんでした (paid={paid_selected}, price={price_set})")


def main():
    article_path, article = get_next_paid_article()
    if not article_path:
        print("有料記事がnote_queueにありません")
        sys.exit(0)

    print(f"投稿開始: {article['title'][:50]}")
    print(f"価格: {article.get('price', 400)}円")
    print(f"文字数: {len(article['content'])}字")

    paid_marker = article.get('paid_start_marker', '※ここから有料部分')
    price       = article.get('price', 400)
    content     = article['content']
    title       = article['title']
    if content.count(paid_marker) != 1:
        raise ValueError(f"有料開始マーカーは1か所必要です: {paid_marker}")

    # 無料部分と有料部分に分割
    if paid_marker in content:
        idx        = content.index(paid_marker)
        free_part  = content[:idx].rstrip('\n')
        # マーカー行自体を除いた次の行から有料部分
        after      = content[idx + len(paid_marker):].lstrip('\n')
        paid_part  = after
        print(f"分割: 無料={len(free_part)}字 / 有料={len(paid_part)}字")
    if not free_part.strip() or not paid_part.strip():
        raise ValueError("無料部分と有料部分の両方に本文が必要です")

    # ヘッダー画像
    img_path = LOG_DIR / f'header_{article_path.stem}.png'
    generate_header_image(
        title,
        article.get('genre', '不妊治療'),
        str(img_path),
        seed_extra=article.get('scheduled_for', ''),
    )
    if not img_path.is_file():
        raise RuntimeError(f"ヘッダー画像を生成できませんでした: {img_path}")

    storage_state = None
    cookie_data = os.environ.get('NOTE_COOKIES', '')
    if cookie_data:
        storage_state = json.loads(base64.b64decode(cookie_data).decode('utf-8'))
    elif LOCAL_COOKIE_PATH.exists():
        storage_state = json.loads(LOCAL_COOKIE_PATH.read_text(encoding='utf-8'))

    api_calls   = []
    put_bodies  = {}
    put_responses = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=bool(os.environ.get('GITHUB_ACTIONS')),
            args=['--no-sandbox'],
        )
        ctx_kwargs = dict(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            viewport={'width': 1280, 'height': 900},
        )
        if storage_state:
            ctx_kwargs['storage_state'] = storage_state
        ctx  = browser.new_context(**ctx_kwargs)
        page = ctx.new_page()

        def on_request(req):
            if '/api/' in req.url and req.method in ('POST', 'PUT', 'PATCH'):
                api_calls.append(f"{req.method} {req.url}")
                m = re.search(r'/text_notes/(\d+)$', req.url)
                if m and req.method == 'PUT':
                    put_bodies[m.group(1)] = req.post_data or ''

        def on_response(resp):
            if '/api/' in resp.url and resp.request.method == 'PUT':
                m = re.search(r'/text_notes/(\d+)$', resp.url)
                if m:
                    try:
                        put_responses[m.group(1)] = (resp.status, resp.text())
                    except Exception:
                        pass

        page.on('request', on_request)
        page.on('response', on_response)

        try:
            # [1] ログイン確認
            print("\n[1] ログイン確認...")
            page.goto('https://note.com', wait_until='domcontentloaded', timeout=30000)
            page.wait_for_timeout(2000)
            if 'login' in page.url:
                raise RuntimeError("ログインが必要です。save_note_cookies.pyを実行してください。")
            print(f"  OK: {page.url[:60]}")

            # [2] 新規記事ページ
            print("[2] 新規記事ページへ...")
            page.goto('https://note.com/notes/new', wait_until='networkidle', timeout=30000)
            page.wait_for_timeout(5000)
            ss(page, '01_editor')

            # [3] アイキャッチ設定
            if img_path and img_path.exists():
                print("[3] アイキャッチ設定...")
                try:
                    for eyecatch_sel in [
                        '[class*="eyecatch"]', '[class*="Eyecatch"]',
                        '[class*="coverImage"]', '[class*="CoverImage"]',
                        'button[aria-label*="画像"]',
                    ]:
                        try:
                            page.click(eyecatch_sel, timeout=2000)
                            page.wait_for_timeout(1500)
                            print(f"  クリック: {eyecatch_sel}")
                            break
                        except Exception:
                            continue
                    else:
                        page.mouse.click(519, 125)
                        page.wait_for_timeout(1500)

                    for upload_text in ["画像をアップロード","写真をアップロード","アップロード","画像を選ぶ"]:
                        try:
                            page.click(f'button:has-text("{upload_text}")', timeout=3000)
                            break
                        except Exception:
                            continue

                    file_inputs = page.locator('input[type="file"]').all()
                    if file_inputs:
                        file_inputs[0].set_input_files(str(img_path))
                        page.wait_for_timeout(3000)
                        saved = page.evaluate("""
                            () => {
                                const btns = Array.from(document.querySelectorAll('button'));
                                const b = [...btns].reverse().find(b => (b.innerText||'').trim() === '保存');
                                if (b) { b.click(); return true; }
                                return false;
                            }
                        """)
                        print(f"  保存クリック: {saved}")
                        page.wait_for_timeout(4000)
                        print("  アイキャッチ設定完了")
                    ss(page, '02_eyecatch')
                except Exception as e:
                    print(f"  アイキャッチスキップ: {e}")
                    try:
                        page.keyboard.press('Escape')
                    except Exception:
                        pass

            # [4] タイトル入力
            print("[4] タイトル入力...")
            page.evaluate("""
                () => {
                    const sels = ['[data-placeholder="タイトル"]','[placeholder*="タイトル"]',
                                  'h1[contenteditable="true"]','[class*="title"][contenteditable]',
                                  '[contenteditable="true"]'];
                    for (const s of sels) {
                        const el = document.querySelector(s);
                        if (el) { el.focus(); el.click(); el.textContent = '';
                                  el.dispatchEvent(new Event('input',{bubbles:true})); return s; }
                    }
                }
            """)
            page.wait_for_timeout(300)
            page.keyboard.type(title, delay=15)
            ss(page, '03_title')

            # [5] 本文エリアへ移動
            print("[5] 本文エリアへ移動...")
            try:
                page.click('.ProseMirror', timeout=5000)
            except Exception:
                page.keyboard.press('Tab')
            page.wait_for_timeout(300)

            # [6] 無料部分を入力
            print("[6] 無料部分入力...")
            type_content(page, free_part)
            ss(page, '04_free_content')

            # [7] 有料エリア設定ボタンをクリック
            if paid_part:
                print("[7] 有料エリアライン挿入...")
                result = insert_paid_line(page)
                ss(page, '05_paid_line')
                if not result:
                    raise RuntimeError("Noteの有料エリアを挿入できませんでした")

                # [8] 有料部分を入力
                print("[8] 有料部分入力...")
                type_content(page, paid_part)
                ss(page, '06_paid_content')

            # [9] 公開に進む
            print("[9] 公開に進む...")
            for sel in ['button:has-text("公開に進む")','button:has-text("公開設定")',
                        'button:has-text("公開する")']:
                try:
                    page.click(sel, timeout=8000)
                    print(f"  クリック: {sel}")
                    break
                except Exception:
                    continue
            page.wait_for_timeout(3000)
            ss(page, '07_modal')

            # [10] 有料価格設定
            if paid_part:
                print("[10] 価格設定...")
                set_price_in_modal(page, price)
                ss(page, '08_price')
                page.wait_for_timeout(1000)

            # [11] 投稿する
            print("[11] 投稿...")
            try:
                page.wait_for_load_state('networkidle', timeout=8000)
            except Exception:
                pass
            page.wait_for_timeout(2000)

            btn_info = page.evaluate("""
                () => {
                    const btns = Array.from(document.querySelectorAll('button'));
                    const b = btns.find(b => (b.innerText||'').trim() === '投稿する');
                    if (!b) return {found: false};
                    const rect = b.getBoundingClientRect();
                    return {found:true, disabled:b.disabled,
                            x:rect.x, y:rect.y, w:rect.width, h:rect.height};
                }
            """)
            print(f"  ボタン状態: {btn_info}")

            if btn_info.get('found') and not btn_info.get('disabled'):
                x = btn_info.get('x',0) + btn_info.get('w',0)/2
                y = btn_info.get('y',0) + btn_info.get('h',0)/2
                page.mouse.click(x, y)
                print(f"  投稿クリック: ({x:.0f},{y:.0f})")
            else:
                page.click('button:has-text("投稿する")', timeout=5000)

            page.wait_for_timeout(10000)
            ss(page, '09_after_publish')
            print(f"  現在URL: {page.url}")

            # 公開後にAPIで価格を確認・補正
            print("\n[APIコール]")
            for c in api_calls:
                print(f"  {c}")

            # PUT レスポンスからURLを取得
            final_url = page.url
            note_key  = None
            for _id, (_st, _body) in put_responses.items():
                try:
                    d = json.loads(_body).get('data', {})
                    if d.get('key'):
                        note_key = d['key']
                        uname    = (d.get('user') or {}).get('urlname', 'sake_to_meshi_3155')
                        final_url = f"https://note.com/{uname}/n/{note_key}"
                        print(f"  公開URL: {final_url}")
                        # 価格が反映されていなければAPI直接で補正
                        if d.get('price', 0) != price and put_bodies.get(_id):
                            print(f"  [価格補正] 現在={d.get('price')} → {price}円でPUT")
                            import requests
                            raw_cookies = ctx.cookies()
                            jar = requests.cookies.RequestsCookieJar()
                            for c in raw_cookies:
                                if 'note.com' in c.get('domain',''):
                                    jar.set(c['name'], c['value'],
                                            domain=c.get('domain',''), path=c.get('path','/'))
                            sess = requests.Session()
                            sess.cookies = jar
                            bd = json.loads(put_bodies[_id])
                            bd['price'] = price
                            bd['status'] = 'published'
                            bd.pop('published_at', None)
                            r = sess.put(
                                f'https://note.com/api/v1/text_notes/{_id}',
                                json=bd,
                                headers={'Content-Type':'application/json',
                                         'Referer': page.url},
                                timeout=15
                            )
                            print(f"  価格補正PUT → {r.status_code}")
                        break
                except Exception:
                    pass

            # アーカイブ
            POSTED_DIR.mkdir(exist_ok=True)
            result = {**article, 'posted_at': datetime.now().isoformat(),
                      'url': final_url, 'paid': True, 'price': price}
            (POSTED_DIR / article_path.name).write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            article_path.unlink()
            print(f"\n✅ 投稿完了: {final_url}")
            print(f"残りキュー: {len(list(QUEUE_DIR.glob('*.json')))}件")

        except Exception as e:
            import traceback
            ss(page, 'error')
            print(f"\n[ERROR] {type(e).__name__}: {e}")
            traceback.print_exc()
            sys.exit(1)
        finally:
            browser.close()


if __name__ == '__main__':
    main()
