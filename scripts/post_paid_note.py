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
from note_hashtags import apply_article_hashtags

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
    allowed_ids = {
        value.strip()
        for value in os.environ.get('PAID_QUEUE_ALLOWLIST', '').split(',')
        if value.strip()
    }
    for path in sorted(QUEUE_DIR.glob('*.json')):
        try:
            article_id = re.search(r'note_(\d+)', path.stem)
            if allowed_ids and (not article_id or article_id.group(1) not in allowed_ids):
                continue
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


def get_published_note_url(api_responses):
    for status_code, body in api_responses.values():
        if not 200 <= status_code < 300:
            continue
        try:
            data = json.loads(body).get('data', {})
        except (json.JSONDecodeError, AttributeError):
            continue
        if data.get('status') != 'published':
            continue
        note_key = data.get('key')
        user_urlname = (data.get('user') or {}).get('urlname')
        if note_key and user_urlname:
            return f"https://note.com/{user_urlname}/n/{note_key}", data.get('price')
    raise RuntimeError("公開完了API応答を確認できません。記事はキューに残します")


def public_url_from_editor(editor_url, user_urlname):
    if editor_url.startswith('https://note.com/') and '/n/' in editor_url:
        return editor_url
    match = re.search(r'/notes/(n[a-z0-9]+)/publish/?', editor_url)
    if not match or not user_urlname:
        raise RuntimeError(f"公開URLを構成できません: {editor_url}")
    return f"https://note.com/{user_urlname}/n/{match.group(1)}"


def verify_public_note(url, expected_price):
    import requests

    response = requests.get(url, timeout=20, allow_redirects=True)
    if response.status_code != 200 or '/n/' not in response.url:
        raise RuntimeError(f"公開URLを確認できません: HTTP {response.status_code} {response.url}")
    price_match = re.search(r'\\?"notePrice\\?"\s*:\s*(\d+)', response.text)
    status_match = re.search(r'\\?"noteStatus\\?"\s*:\s*\\?"([^"\\]+)', response.text)
    if not price_match or not status_match or status_match.group(1) != 'published':
        raise RuntimeError("公開ページの公開状態または価格を確認できません")
    actual_price = int(price_match.group(1))
    if actual_price != expected_price:
        raise RuntimeError(f"公開記事の価格が一致しません: {actual_price}円 (期待={expected_price}円)")
    return actual_price


def type_content(page, content):
    """本文をキーボード入力（OGPカード変換なし・高速版）"""
    link_pattern = re.compile(r'\[([^\]]+)\]\((https?://[^)]+)\)')
    ordered_pattern = re.compile(r'^\s*(?:\d+[.)]\s*|フェーズ\d+[：:]\s*)(.+)$')
    lines = content.split('\n')
    in_ordered_list = False

    for line_index, line in enumerate(lines):
        heading = line.startswith('## ')
        ordered_match = ordered_pattern.match(line)

        if not line.strip():
            next_is_ordered = any(
                ordered_pattern.match(next_line)
                for next_line in lines[line_index + 1:]
                if next_line.strip()
            )
            if in_ordered_list and next_is_ordered:
                continue
            if in_ordered_list:
                page.keyboard.press('Enter')
                in_ordered_list = False
            else:
                page.keyboard.press('Enter')
            continue

        if in_ordered_list and not ordered_match:
            page.keyboard.press('Enter')
            in_ordered_list = False

        if heading:
            line = line[3:].strip()
        elif ordered_match:
            line = ordered_match.group(1).strip()
            if not in_ordered_list:
                page.keyboard.press('Control+Shift+7')
                in_ordered_list = True

        line = re.sub(r'\*\*([^*]+)\*\*', r'\1', line)
        if link_pattern.search(line):
            parts = link_pattern.split(line)
            i = 0
            while i < len(parts):
                part_type = i % 3
                chunk = parts[i]
                if part_type == 0:
                    if chunk:
                        page.keyboard.type(chunk, delay=5)
                elif part_type == 1:
                    link_text = chunk
                    link_url = parts[i + 1]
                    page.keyboard.type(link_text, delay=5)
                    page.keyboard.press('Enter')
                    page.keyboard.type(link_url, delay=3)
                    page.keyboard.press('Enter')
                    page.wait_for_timeout(1500)
                    i += 1
                i += 1
        elif line:
            page.keyboard.type(line, delay=5)

        if heading:
            page.keyboard.press('Control+Alt+2')
        page.keyboard.press('Enter')
        page.wait_for_timeout(20)

    if in_ordered_list:
        page.keyboard.press('Enter')


def insert_paid_line(page):
    """有料エリア設定ボタンをクリックして有料ラインを挿入"""
    print("  有料ライン挿入中...")

    # ツールバーの有料エリアボタンを探す
    return _click_visible_plus_and_insert_paywall(page)


    def _click_plus_with_locator(page):
        """末尾の空段落を表示して、その横の「+」から有料エリアを挿入する"""
        paragraphs = page.locator('.ProseMirror p')
        target_box = None
        for paragraph_index in range(paragraphs.count() - 1, -1, -1):
            paragraph = paragraphs.nth(paragraph_index)
            try:
                if paragraph.inner_text().strip():
                    continue
                paragraph.scroll_into_view_if_needed(timeout=5000)
                target_box = paragraph.bounding_box()
                if target_box and target_box['height'] > 0:
                    break
            except Exception:
                continue

        if not target_box:
            print("  [WARN] 有料エリア用の空段落が見つかりません")
            return False

        viewport = page.evaluate("() => ({width: window.innerWidth, height: window.innerHeight})")
        button_x = max(target_box['x'] - 55, 5)
        button_y = target_box['y'] + target_box['height'] / 2
        if not (0 <= button_y <= viewport['height'] and 0 <= button_x <= viewport['width']):
            print(f"  [WARN] +ボタン位置が画面外です: x={button_x}, y={button_y}")
            return False

        page.mouse.move(button_x, button_y)
        page.wait_for_timeout(600)
        buttons = page.evaluate("""
            () => Array.from(document.querySelectorAll('button, [role="button"]'))
                .map(element => {
                    const rect = element.getBoundingClientRect();
                    const style = window.getComputedStyle(element);
                    return {
                        x: Math.round(rect.left + rect.width / 2),
                        y: Math.round(rect.top + rect.height / 2),
                        width: rect.width,
                        height: rect.height,
                        visible: style.display !== 'none' && style.visibility !== 'hidden'
                            && parseFloat(style.opacity || '1') > 0.3
                            && rect.right > 0 && rect.left < window.innerWidth
                            && rect.bottom > 0 && rect.top < window.innerHeight,
                        text: (element.innerText || '').slice(0, 20),
                        aria: element.getAttribute('aria-label') || '',
                    };
                })
                .filter(button => button.visible && button.width > 5 && button.height > 5)
        """)
        button = min(
            buttons,
            key=lambda candidate: abs(candidate['x'] - button_x) + abs(candidate['y'] - button_y),
            default=None,
        )
        if not button or abs(button['x'] - button_x) > 90 or abs(button['y'] - button_y) > 90:
            print(f"  [WARN] 空段落横に表示された+ボタンが見つかりません: {buttons}")
            return False

        page.mouse.click(button['x'], button['y'])
        page.wait_for_timeout(600)
        ss(page, 'paid_plus_menu')
        for keyword in ['有料', 'paid']:
            for selector in [
                f'[role="menuitem"]:has-text("{keyword}")',
                f'[role="option"]:has-text("{keyword}")',
                f'button:has-text("{keyword}")',
                f'li:has-text("{keyword}")',
            ]:
                try:
                    item = page.locator(selector).first
                    if item.is_visible(timeout=500):
                        item.click()
                        page.wait_for_timeout(400)
                        print(f"  有料エリア選択: {selector}")
                        return True
                except Exception:
                    pass

        page.keyboard.press('Escape')
        print("  [WARN] +メニューで有料エリアが見つかりません")
        return False

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

    return _click_plus_and_insert_paywall(page)


def _click_plus_and_insert_paywall(page):
    """末尾の空段落にある「+」から有料エリアを挿入する"""
    para_info = page.evaluate("""
        () => {
            const editor = document.querySelector('.ProseMirror');
            if (!editor) return null;
            const paras = Array.from(editor.querySelectorAll('p'));
            for (let i = paras.length - 1; i >= 0; i--) {
                const paragraph = paras[i];
                if ((paragraph.innerText || '').trim() === '') {
                    paragraph.scrollIntoView({block: 'center', behavior: 'instant'});
                    const rect = paragraph.getBoundingClientRect();
                    return {left: rect.left, centerY: rect.top + rect.height / 2};
                }
            }
            return null;
        }
    """)
    if not para_info:
        print("  [WARN] 有料エリア用の空段落が見つかりません")
        return False

    button_x = max(para_info['left'] - 55, 5)
    button_y = para_info['centerY']
    page.mouse.move(button_x, button_y)
    page.wait_for_timeout(600)

    buttons = page.evaluate("""
        () => Array.from(document.querySelectorAll('button, [role="button"]'))
            .filter(element => {
                const rect = element.getBoundingClientRect();
                const style = window.getComputedStyle(element);
                return rect.width > 5 && rect.height > 5 && rect.left < 700
                    && style.display !== 'none' && style.visibility !== 'hidden'
                    && parseFloat(style.opacity || '1') > 0.3;
            })
            .map(element => {
                const rect = element.getBoundingClientRect();
                return {
                    x: Math.round(rect.left + rect.width / 2),
                    y: Math.round(rect.top + rect.height / 2),
                    text: (element.innerText || '').slice(0, 20),
                    aria: element.getAttribute('aria-label') || '',
                };
            }).slice(0, 10)
    """)
    print(f"  +ボタン候補: {buttons}")

    nearest = min(buttons or [], key=lambda button: abs(button['y'] - button_y), default=None)
    if nearest and abs(nearest['y'] - button_y) < 80:
        page.mouse.click(nearest['x'], nearest['y'])
    else:
        page.mouse.click(button_x, button_y)
    page.wait_for_timeout(600)
    ss(page, 'paid_plus_menu')

    for keyword in ['有料', 'paid']:
        for selector in [
            f'[role="menuitem"]:has-text("{keyword}")',
            f'[role="option"]:has-text("{keyword}")',
            f'button:has-text("{keyword}")',
            f'li:has-text("{keyword}")',
        ]:
            try:
                item = page.locator(selector).first
                if item.is_visible(timeout=500):
                    item.click()
                    page.wait_for_timeout(400)
                    print(f"  有料エリア選択: {selector}")
                    return True
            except Exception:
                pass

    page.keyboard.press('Escape')
    print("  [WARN] +メニューに有料エリアが見つかりません")
    return False


def find_price_input(page):
    price_input = None
    price_selector = None
    for selector in [
        'input[name="price"]:visible',
        'input[placeholder*="価格"]:visible',
        'input[placeholder*="円"]:visible',
        'input[type="number"]:visible',
        '[class*="price"] input:visible',
        '[class*="Price"] input:visible',
    ]:
        candidates = page.locator(selector)
        for candidate_index in range(candidates.count()):
            candidate = candidates.nth(candidate_index)
            try:
                if candidate.is_visible():
                    price_input = candidate
                    price_selector = selector
                    break
            except Exception:
                continue
        if price_input:
            break

    if not price_input:
        input_index = page.evaluate("""
            () => {
                const allInputs = Array.from(document.querySelectorAll('input'));
                const isVisible = input => {
                    const rect = input.getBoundingClientRect();
                    const style = window.getComputedStyle(input);
                    return rect.width > 0 && rect.height > 0
                        && rect.right > 0 && rect.left < window.innerWidth
                        && rect.bottom > 0 && rect.top < window.innerHeight
                        && style.display !== 'none' && style.visibility !== 'hidden'
                        && parseFloat(style.opacity || '1') > 0.3;
                };
                const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                let node;
                while ((node = walker.nextNode())) {
                    if ((node.textContent || '').trim() !== '価格') continue;
                    let parent = node.parentElement;
                    for (let depth = 0; parent && depth < 5; depth++, parent = parent.parentElement) {
                        const matches = Array.from(parent.querySelectorAll('input'))
                            .filter(input => isVisible(input)
                                && !['checkbox', 'radio', 'hidden'].includes(input.type));
                        if (matches.length === 1) return allInputs.indexOf(matches[0]);
                    }
                }
                return -1;
            }
        """)
        if input_index >= 0:
            price_input = page.locator('input').nth(input_index)
            price_selector = 'input associated with 価格 label'

    return price_input, price_selector


def wait_for_price_input(page, timeout):
    try:
        page.wait_for_function("""
            () => {
                const visible = input => {
                    const rect = input.getBoundingClientRect();
                    const style = getComputedStyle(input);
                    return rect.width > 0 && rect.height > 0
                        && style.display !== 'none' && style.visibility !== 'hidden'
                        && !['checkbox', 'radio', 'hidden'].includes(input.type);
                };
                const direct = Array.from(document.querySelectorAll(
                    'input[name="price"], input[placeholder*="価格"], input[placeholder*="円"], '
                    + 'input[type="number"], [class*="price"] input, [class*="Price"] input'
                )).some(visible);
                if (direct) return true;

                const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                let node;
                while ((node = walker.nextNode())) {
                    if ((node.textContent || '').trim() !== '価格') continue;
                    let parent = node.parentElement;
                    for (let depth = 0; parent && depth < 5; depth++, parent = parent.parentElement) {
                        if (Array.from(parent.querySelectorAll('input')).some(visible)) return true;
                    }
                }
                return false;
            }
        """, timeout=timeout)
        return True
    except Exception:
        return False


def set_price_in_modal(page, price):
    """公開モーダルで価格を設定する"""
    print(f"  価格設定: {price}円")

    wait_for_price_input(page, timeout=3000)
    price_input, price_selector = find_price_input(page)
    paid_selected = price_input is not None
    if paid_selected:
        print("  有料設定: 有料価格欄が表示されています")
    else:
        paid_option_clicked = False
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
                print(f"  有料設定を選択: {sel}")
                paid_option_clicked = True
                break
            except Exception:
                continue
        if paid_option_clicked:
            wait_for_price_input(page, timeout=10000)
            price_input, price_selector = find_price_input(page)
            paid_selected = price_input is not None

    if not paid_selected:
        modal_state = page.evaluate("""
            () => {
                const visible = el => {
                    const rect = el.getBoundingClientRect();
                    const style = getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0
                        && style.display !== 'none' && style.visibility !== 'hidden';
                };
                return {
                    inputs: Array.from(document.querySelectorAll('input')).map(el => ({
                        type: el.type, name: el.name, placeholder: el.placeholder,
                        checked: el.checked, visible: visible(el),
                    })),
                    controls: Array.from(document.querySelectorAll('button, label, [role="radio"], [role="switch"]'))
                        .filter(el => /有料|価格|無料/.test(el.innerText || el.getAttribute('aria-label') || ''))
                        .map(el => ({
                            tag: el.tagName, text: (el.innerText || '').trim().slice(0, 60),
                            ariaPressed: el.getAttribute('aria-pressed'),
                            ariaChecked: el.getAttribute('aria-checked'),
                            dataState: el.getAttribute('data-state'),
                            className: String(el.className || '').slice(0, 120),
                            visible: visible(el),
                        })).slice(0, 30),
                };
            }
        """)
        print(f"  有料モーダル診断: {modal_state}")

    price_set = False
    if price_input:
        try:
            print(f"  価格欄: {price_selector}; type={price_input.get_attribute('type')}; name={price_input.get_attribute('name')}; placeholder={price_input.get_attribute('placeholder')}")
            price_input.fill(str(price))
            price_input.press('Tab')
            page.wait_for_timeout(500)
            if price_input.input_value().strip() != str(price):
                price_input.evaluate("""
                    (el, value) => {
                        const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
                        setter.call(el, value);
                        el.dispatchEvent(new Event('input', {bubbles: true}));
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                    }
                """, str(price))
                price_input.press('Tab')
                page.wait_for_timeout(500)
            price_set = price_input.input_value().strip() == str(price)
            print(f"  価格入力確認: {price_input.input_value()}円 (期待={price}円)")
        except Exception as error:
            print(f"  価格入力失敗: {error}")

    if not paid_selected or not price_set:
        raise RuntimeError(f"有料設定を確認できませんでした (paid={paid_selected}, price={price_set})")


def move_paywall_before_first_paid_heading(page):
    result = page.evaluate("""
        () => {
            const paidStart = Array.from(document.querySelectorAll('p, div, h1, h2, h3'))
                .find(el => (el.innerText || '').trim().startsWith('■') && el.children.length <= 2);
            if (!paidStart) return {method: 'paid-heading-not-found'};

            const candidates = [];
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
            let node;
            while ((node = walker.nextNode())) {
                if (!(node.textContent || '').includes('ラインをこの場所に変更')) continue;
                let element = node.parentElement;
                while (element && element.tagName === 'SPAN') element = element.parentElement;
                if (element && !candidates.includes(element)) candidates.push(element);
            }

            let target = null;
            for (const candidate of candidates) {
                if (paidStart.compareDocumentPosition(candidate) & 2) target = candidate;
            }
            if (!target) return {method: 'position-before-heading-not-found', count: candidates.length};
            target.scrollIntoView({block: 'center', behavior: 'instant'});
            target.click();
            return {method: 'before-paid-heading', count: candidates.length};
        }
    """)
    print(f"  有料壁位置: {result}")
    if result.get('method') != 'before-paid-heading':
        raise RuntimeError(f"有料壁を有料見出し直前に設定できませんでした: {result}")
    page.wait_for_timeout(800)


def _click_visible_plus_and_insert_paywall(page):
    paragraphs = page.locator('.ProseMirror p')
    target_box = None
    last_content_index = -1
    for paragraph_index in range(paragraphs.count()):
        paragraph = paragraphs.nth(paragraph_index)
        try:
            if paragraph.inner_text().strip():
                last_content_index = paragraph_index
        except Exception:
            continue

    for paragraph_index in range(last_content_index + 1, paragraphs.count()):
        paragraph = paragraphs.nth(paragraph_index)
        try:
            if paragraph.inner_text().strip():
                continue
            paragraph.scroll_into_view_if_needed(timeout=5000)
            target_box = paragraph.bounding_box()
            if target_box and target_box['height'] > 0:
                print('  有料壁位置: 無料部分末尾の空段落')
                break
        except Exception:
            continue

    if not target_box:
        print('  [WARN] 有料エリア用の空段落が見つかりません')
        return False

    viewport = page.evaluate('() => ({width: window.innerWidth, height: window.innerHeight})')
    button_x = max(target_box['x'] - 55, 5)
    button_y = target_box['y'] + target_box['height'] / 2
    if not (0 <= button_x <= viewport['width'] and 0 <= button_y <= viewport['height']):
        print(f'  [WARN] ＋ボタン位置が画面外です: x={button_x}, y={button_y}')
        return False

    page.mouse.move(button_x, button_y)
    page.wait_for_timeout(600)
    buttons = page.evaluate('''
        () => Array.from(document.querySelectorAll('button, [role="button"]'))
            .map(element => {
                const rect = element.getBoundingClientRect();
                const style = window.getComputedStyle(element);
                return {
                    x: Math.round(rect.left + rect.width / 2),
                    y: Math.round(rect.top + rect.height / 2),
                    width: rect.width,
                    height: rect.height,
                    visible: style.display !== 'none' && style.visibility !== 'hidden'
                        && parseFloat(style.opacity || '1') > 0.3
                        && rect.right > 0 && rect.left < window.innerWidth
                        && rect.bottom > 0 && rect.top < window.innerHeight,
                };
            })
            .filter(button => button.visible && button.width > 5 && button.height > 5)
    ''')
    button = min(
        buttons,
        key=lambda item: abs(item['x'] - button_x) + abs(item['y'] - button_y),
        default=None,
    )
    if not button or abs(button['x'] - button_x) > 90 or abs(button['y'] - button_y) > 90:
        print(f'  [WARN] 空段落横の＋ボタンが見つかりません: {buttons}')
        return False

    page.mouse.click(button['x'], button['y'])
    page.wait_for_timeout(600)
    ss(page, 'paid_plus_menu')
    for keyword in ['有料', 'paid']:
        for selector in [
            f'[role="menuitem"]:has-text("{keyword}")',
            f'[role="option"]:has-text("{keyword}")',
            f'button:has-text("{keyword}")',
            f'li:has-text("{keyword}")',
        ]:
            try:
                item = page.locator(selector).first
                if item.is_visible(timeout=500):
                    item.click()
                    page.wait_for_timeout(400)
                    print(f'  有料エリア選択: {selector}')
                    return True
            except Exception:
                pass

    page.keyboard.press('Escape')
    print('  [WARN] ＋メニューに有料エリアが見つかりません')
    return False


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

    api_calls = []
    api_responses = {}
    account_urlname = None

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

        def on_response(resp):
            nonlocal account_urlname
            if '/api/v2/current_user' in resp.url:
                try:
                    account_urlname = resp.json().get('data', {}).get('urlname')
                except Exception:
                    pass
            if '/api/' in resp.url and '/text_notes' in resp.url:
                try:
                    api_responses[resp.url] = (resp.status, resp.text())
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
                apply_article_hashtags(page, article)

            # [11] 投稿する
            print("[11] 投稿...")
            try:
                page.wait_for_load_state('networkidle', timeout=8000)
            except Exception:
                pass
            page.wait_for_timeout(2000)
            if paid_part:
                settings_button = page.locator('button:has-text("有料エリア設定")').last
                if not settings_button.is_visible(timeout=1500):
                    raise RuntimeError("有料エリア設定画面へのボタンが見つかりません")
                settings_button.click()
                page.wait_for_timeout(2000)
                ss(page, 'paid_area_page')

            visible_post_buttons = page.locator('button:visible')
            post_buttons = []
            for button_index in range(visible_post_buttons.count()):
                candidate = visible_post_buttons.nth(button_index)
                if candidate.inner_text().strip() == '投稿する':
                    post_buttons.append(candidate)
            if not post_buttons:
                visible_buttons = page.evaluate("""
                    () => Array.from(document.querySelectorAll('button'))
                        .filter(button => {
                            const rect = button.getBoundingClientRect();
                            const style = window.getComputedStyle(button);
                            return rect.width > 0 && rect.height > 0
                                && style.display !== 'none' && style.visibility !== 'hidden';
                        })
                        .map(button => ({text: (button.innerText || '').trim(), disabled: button.disabled}))
                """)
                raise RuntimeError(f"投稿するボタンが見つかりません: {visible_buttons}")

            post_button = post_buttons[-1]
            post_button.scroll_into_view_if_needed(timeout=5000)
            if not post_button.is_enabled():
                raise RuntimeError("投稿するボタンが無効です")
            ss(page, 'publish_ready')
            api_responses.clear()
            api_calls.clear()
            post_button.click(timeout=10000)
            print("  投稿するボタンをクリックしました")

            try:
                page.wait_for_url(lambda url: 'editor.note.com' not in url, timeout=20000)
            except Exception:
                pass
            page.wait_for_timeout(2000)
            ss(page, '09_after_publish')
            print(f"  現在URL: {page.url}")
            if 'editor.note.com' in page.url:
                visible_state = page.evaluate("""
                    () => ({
                        dialogs: Array.from(document.querySelectorAll('[role=dialog]'))
                            .filter(el => el.getBoundingClientRect().width > 0)
                            .map(el => (el.innerText || '').trim()),
                        buttons: Array.from(document.querySelectorAll('button'))
                            .filter(el => {
                                const rect = el.getBoundingClientRect();
                                const style = getComputedStyle(el);
                                return rect.width > 0 && rect.height > 0
                                    && style.visibility !== 'hidden' && style.display !== 'none';
                            })
                            .map(el => ({text: (el.innerText || '').trim(), disabled: el.disabled}))
                    })
                """)
                print(f"  公開API応答なし。画面状態: {visible_state}")

            # 公開後にAPIで価格を確認・補正
            print("\n[APIコール]")
            for c in api_calls:
                print(f"  {c}")

            try:
                final_url, published_price = get_published_note_url(api_responses)
            except RuntimeError:
                final_url = public_url_from_editor(page.url, account_urlname)
                published_price = None

            verified_price = verify_public_note(final_url, price)
            if published_price is not None and published_price != verified_price:
                raise RuntimeError(
                    f"APIと公開ページの価格が一致しません: API={published_price}円 / 公開={verified_price}円"
                )

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
