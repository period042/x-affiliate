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
from xml.etree import ElementTree
import markdown
from playwright.sync_api import sync_playwright
from article_checks import validate_heading_bodies
from note_hashtags import apply_article_hashtags, derive_article_hashtags

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


def validate_unique_article_content(article_path, article):
    content = article.get('content', '').strip()
    if not content:
        raise ValueError(f"記事本文が空です: {article_path.name}")
    if re.search(r'(?m)^[ \t]*(?:\*\*\* (?:Begin Patch|End Patch|End of File|(?:Add|Update|Delete) File:|Move to:)|diff --git\s|(?:---|\+\+\+) [ab]/|@@)', content):
        raise ValueError(f"記事本文に差分パッチ断片があります: {article_path.name}")
    validate_heading_bodies(content, article.get('paid_start_marker', '※ここから有料部分'))
    for other_path in QUEUE_DIR.glob('*.json'):
        if other_path == article_path:
            continue
        other = json.loads(other_path.read_text(encoding='utf-8-sig'))
        if other.get('title') != article.get('title') and other.get('content', '').strip() == content:
            raise ValueError(f"別タイトルの記事と本文が重複しています: {article_path.name}, {other_path.name}")


def ensure_no_pending_draft(article):
    editor_url = article.get('pending_editor_url')
    if editor_url and not re.match(r'^https://editor\.note\.com/notes/n[a-z0-9]+/edit/?$', editor_url):
        raise RuntimeError(f"保存済み下書きURLを確認できません: {editor_url}")
    return editor_url


def remember_pending_draft(article_path, article, editor_url):
    if not re.match(r'^https://editor\.note\.com/notes/n[a-z0-9]+/edit/?$', editor_url):
        raise RuntimeError(f"作成した下書きURLを確認できません: {editor_url}")
    article['pending_editor_url'] = editor_url
    article_path.write_text(json.dumps(article, ensure_ascii=False, indent=2), encoding='utf-8')


def ensure_no_existing_publication(request, account_urlname, title):
    for page_number in range(1, 1001):
        response = request.get(
            f'https://note.com/api/v2/creators/{account_urlname}/contents',
            params={'kind': 'note', 'page': page_number}, timeout=20000,
        )
        if not response.ok:
            raise RuntimeError(f"公開記事一覧を確認できません: HTTP {response.status}")
        data = response.json().get('data', {})
        contents = data.get('contents')
        if not isinstance(contents, list) or not isinstance(data.get('isLastPage'), bool):
            raise RuntimeError('公開記事一覧の形式を確認できません')
        for existing in contents:
            if existing.get('name') == title:
                raise RuntimeError(f"同じタイトルの記事が公開済みです: {existing.get('noteUrl')}")
        if data['isLastPage']:
            return
        if not contents:
            raise RuntimeError('公開記事一覧の途中で記事が取得できませんでした')
    raise RuntimeError('公開記事一覧が上限を超えました')


def ensure_no_existing_draft(request, title, exclude_key=None):
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f'duplicate JSON key: {key}')
            result[key] = value
        return result

    seen = set()
    total = None
    for page_number in range(1, 1001):
        response = request.get(
            'https://note.com/api/v2/note_list/contents',
            params={'limit': 100, 'page': page_number}, timeout=20000,
        )
        if not response.ok:
            raise RuntimeError(f'記事一覧を確認できません: HTTP {response.status}')
        try:
            body = json.loads(response.text(), object_pairs_hook=unique_object)
            data = body['data']
            notes = data['notes']
            is_last_page = data['isLastPage']
            page_total = data['totalCount']
        except (ValueError, KeyError, TypeError) as exc:
            raise RuntimeError('記事一覧の形式を確認できません') from exc
        if (not isinstance(notes, list) or not isinstance(is_last_page, bool)
                or type(page_total) is not int or page_total < 0 or len(notes) > 100):
            raise RuntimeError('記事一覧の形式を確認できません')
        if total is None:
            total = page_total
        elif total != page_total:
            raise RuntimeError('記事一覧の件数が途中で変化しました')
        for note in notes:
            if (not isinstance(note, dict) or not isinstance(note.get('key'), str)
                    or not note['key'] or 'name' not in note
                    or (note['name'] is not None and not isinstance(note['name'], str))):
                raise RuntimeError('記事一覧の形式を確認できません')
            if note['key'] in seen:
                raise RuntimeError(f"記事一覧に重複キーがあります: {note['key']}")
            seen.add(note['key'])
            if note['key'] == exclude_key:
                continue
            draft = note.get('noteDraft')
            if note.get('status') == 'draft' and (not isinstance(draft, dict) or not isinstance(draft.get('name'), str)):
                raise RuntimeError('下書きのタイトルを確認できません')
            if draft is not None and (not isinstance(draft, dict) or not isinstance(draft.get('name'), str)):
                raise RuntimeError('下書きのタイトルを確認できません')
            if note['name'] == title or (draft and draft['name'] == title):
                raise RuntimeError(f'同じタイトルの記事が既にあります: {title}')
        if len(seen) > total:
            raise RuntimeError('記事一覧の件数が一致しません')
        if is_last_page:
            if len(seen) != total:
                raise RuntimeError('記事一覧の件数が一致しません')
            return
        if not notes or len(seen) >= total:
            raise RuntimeError('記事一覧を全件確認できません')
    raise RuntimeError('記事一覧がページ数の上限を超えました')


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


def wait_for_published_note(request, account_urlname, title, expected_price):
    import time

    for attempt in range(6):
        response = request.get(
            f'https://note.com/api/v2/creators/{account_urlname}/contents',
            params={'kind': 'note', 'page': 1}, timeout=20000,
        )
        if not response.ok:
            raise RuntimeError(f'公開記事一覧を確認できません: HTTP {response.status}')
        try:
            data = response.json()['data']
            contents = data['contents']
            is_last_page = data['isLastPage']
        except (ValueError, KeyError, TypeError) as exc:
            raise RuntimeError('公開記事一覧の形式を確認できません') from exc
        if not isinstance(contents, list) or not isinstance(is_last_page, bool):
            raise RuntimeError('公開記事一覧の形式を確認できません')
        for note in contents:
            if not isinstance(note, dict):
                raise RuntimeError('公開記事一覧の形式を確認できません')
            if note.get('name') != title:
                continue
            if note.get('status') != 'published':
                raise RuntimeError('公開一覧に記事はありますが、公開状態ではありません')
            if type(note.get('price')) is not int or note['price'] != expected_price:
                raise RuntimeError(f"公開記事の価格が一致しません: {note.get('price')}円 (期待={expected_price}円)")
            url = note.get('noteUrl')
            if not isinstance(url, str) or not url.startswith(f'https://note.com/{account_urlname}/n/'):
                raise RuntimeError('公開記事のURLを確認できません')
            return url, note['price']
        if attempt < 5:
            time.sleep(3)
    raise RuntimeError(f'公開記事一覧にタイトルが見つかりません: {title}')


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


def validate_article_structure(page, content, expected_html=None):
    expected_headings = [line[3:].strip() for line in content.splitlines() if line.startswith('## ')]
    phase_count = len(re.findall(r'(?m)^\s*(?:\*\*)?フェーズ\d+[：:]', content))
    structure = page.locator('.ProseMirror').evaluate("""editor => ({
        headings: Array.from(editor.querySelectorAll('h2')).map(heading => heading.innerText.trim()),
        orderedItems: editor.querySelectorAll('ol > li').length
    })""")
    if structure['headings'] != expected_headings:
        raise RuntimeError(f"見出し構造が原稿と一致しません: expected={expected_headings}, actual={structure['headings']}")
    if phase_count and structure['orderedItems'] < phase_count:
        raise RuntimeError(f"番号付きリストの項目が不足しています: expected={phase_count}, actual={structure['orderedItems']}")
    if expected_html is not None:
        matches = page.locator('.ProseMirror').evaluate("""(editor, html) => {
            const expected = new DOMParser().parseFromString(html, 'text/html').body;
            const normalize = value => value.replace(/\\s/g, '');
            const blocks = node => Array.from(node.children).map(block => ({
                tag: block.tagName, text: normalize(block.textContent)
            }));
            return JSON.stringify(blocks(editor)) === JSON.stringify(blocks(expected));
        }""", expected_html)
        if not matches:
            raise RuntimeError('貼り付け後の本文または段落構造が原稿と一致しません')


def render_article_html(content):
    html = markdown.markdown(content, extensions=['nl2br'], output_format='xhtml')
    root = ElementTree.fromstring(f'<div>{html}</div>')
    rendered = ElementTree.Element('div')
    ordered_list = None
    for block in list(root):
        text = ''.join(block.itertext()).strip()
        if block.tag == 'p' and re.match(r'^フェーズ\d+[：:]', text):
            if ordered_list is None:
                ordered_list = ElementTree.SubElement(rendered, 'ol')
            item = ElementTree.SubElement(ordered_list, 'li')
            item.append(block)
        else:
            ordered_list = None
            rendered.append(block)
    return ''.join(ElementTree.tostring(block, encoding='unicode', method='html') for block in rendered), len(rendered)


def paste_article_html(page, html):
    result = page.locator('.ProseMirror').evaluate("""(editor, html) => {
        editor.focus();
        const paragraph = editor.querySelector('p');
        if (!paragraph || editor.textContent.trim()) return false;
        const range = document.createRange();
        range.selectNodeContents(paragraph);
        range.collapse(true);
        const selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        const data = new DataTransfer();
        data.setData('text/html', html);
        data.setData('text/plain', '');
        return !editor.dispatchEvent(new ClipboardEvent('paste', {
            clipboardData: data, bubbles: true, cancelable: true
        }));
    }""", html)
    if not result:
        raise RuntimeError('本文を構造化して貼り付けられませんでした')
    page.wait_for_timeout(500)


def clear_article_editor(page):
    editor = page.locator('.ProseMirror')
    editor.click()
    page.keyboard.press('Control+A')
    page.keyboard.press('Backspace')
    if editor.inner_text().strip():
        raise RuntimeError('既存下書き本文を安全に置き換えられませんでした')


def gallery_image_metadata(detail_text):
    # The creator name is excluded because it often contains the search word for unrelated images.
    lines = [line.strip() for line in detail_text.splitlines() if line.strip()]
    labels = {'タイトル', 'クリエイター', 'キーワード', '説明', 'キャンセル', 'この画像を挿入'}
    collected, current = [], None
    for line in lines:
        if line in labels:
            current = line if line in {'タイトル', 'キーワード', '説明'} else None
        elif current:
            collected.append(line)
    return '\n'.join(collected)


def select_gallery_image(page, article):
    existing_image = page.locator('img[alt="eyecatch"]')
    if existing_image.count() and existing_image.is_visible():
        if existing_image.evaluate('element => element.complete && element.naturalWidth > 0'):
            print('  既存の見出し画像を再利用')
            return
    page.get_by_role('button', name='画像を追加').click(timeout=10000)
    page.get_by_role('button', name='記事にあう画像を選ぶ').click(timeout=10000)
    gallery = page.locator('#stockPhotoModal')
    gallery.get_by_role('button', name='検索する').click(timeout=10000)
    search = gallery.get_by_role('textbox', name='みんなのフォトギャラリーから検索する')
    for keyword in derive_article_hashtags(article):
        search.fill(keyword)
        search.press('Enter')
        page.wait_for_timeout(1500)
        candidates = gallery.locator('[role="button"]:has(figure)')
        for index in range(min(candidates.count(), 20)):
            candidates.nth(index).click(timeout=10000)
            page.wait_for_timeout(300)
            if keyword in gallery_image_metadata(gallery.inner_text()):
                gallery.get_by_role('button', name='この画像を挿入').click(timeout=10000)
                save = page.get_by_role('button', name='保存', exact=True)
                page.get_by_role('dialog').filter(has=save).get_by_role(
                    'button', name='保存', exact=True).click(timeout=10000)
                image = page.locator('img[alt="eyecatch"]')
                image.wait_for(state='visible', timeout=15000)
                if not image.evaluate('element => element.complete && element.naturalWidth > 0'):
                    raise RuntimeError('見出し画像を読み込めませんでした')
                print(f"  フォトギャラリー検索: {keyword}")
                return
            gallery.get_by_role('button', name='この画像を使用しない').click(timeout=10000)
            page.wait_for_timeout(300)
    raise RuntimeError('記事に関連するフォトギャラリー画像を確認できませんでした')


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
        'input#price:visible',
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
                    'input#price, input[name="price"], input[placeholder*="価格"], input[placeholder*="円"], '
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
        paid_radio = page.locator('input[type="radio"][value="paid"]')
        if paid_radio.count() == 1:
            paid_radio.evaluate('(radio) => radio.click()')
            paid_option_clicked = True
        for sel in [
            'input[type="checkbox"][name*="paid"]',
            'input[type="checkbox"][name*="price"]',
            'input[type="radio"][value="paid"]',
            'label:has-text("有料")',
            'button:has-text("有料")',
            '[class*="PriceToggle"]',
        ]:
            if paid_option_clicked:
                break
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


def set_paywall_after_free_blocks(page, free_block_count):
    result = page.locator('.ProseMirror.paywall-setting').evaluate("""(editor, freeBlockCount) => {
        const blocks = Array.from(editor.children).filter(element =>
            !element.classList.contains('ProseMirror-widget'));
        const boundary = blocks[freeBlockCount - 1];
        const widget = boundary && boundary.nextElementSibling;
        const button = widget && widget.querySelector('button');
        if (!button || !button.innerText.includes('ライン')) return null;
        button.click();
        return {block: boundary.innerText.trim(), blockCount: blocks.length};
    }""", free_block_count)
    if not result:
        raise RuntimeError(f"無料部分の直後に有料ラインを設定できません: {free_block_count}")
    page.wait_for_timeout(500)
    selected = page.locator('.ProseMirror.paywall-setting').evaluate("""(editor, freeBlockCount) => {
        const blocks = Array.from(editor.children).filter(element =>
            !element.classList.contains('ProseMirror-widget'));
        return blocks[freeBlockCount - 1]?.nextElementSibling?.querySelector('button')?.getAttribute('aria-pressed');
    }""", free_block_count)
    if selected != 'true':
        raise RuntimeError(f"有料ラインの位置を確認できません: {result}")
    print(f"  有料ライン設定: {result}")


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

    pending_editor_url = ensure_no_pending_draft(article)
    validate_unique_article_content(article_path, article)

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
    free_html, free_block_count = render_article_html(free_part)
    paid_html, _ = render_article_html(paid_part)

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
            current_user = ctx.request.get('https://note.com/api/v2/current_user', timeout=20000)
            if not current_user.ok:
                raise RuntimeError(f"Noteのログインを確認できません: HTTP {current_user.status}")
            account_urlname = current_user.json().get('data', {}).get('urlname')
            if not account_urlname:
                raise RuntimeError('Noteアカウントを確認できません')
            pending_key_match = re.search(r'/notes/(n[a-z0-9]+)/edit/?$', pending_editor_url or '')
            ensure_no_existing_draft(
                ctx.request, title,
                exclude_key=pending_key_match.group(1) if pending_key_match else None,
            )
            ensure_no_existing_publication(ctx.request, account_urlname, title)
            print(f"  OK: {page.url[:60]}")

            # [2] 新規記事ページ
            print("[2] 記事編集ページへ...")
            if pending_editor_url:
                page.goto(pending_editor_url, wait_until='networkidle', timeout=30000)
            else:
                page.goto('https://note.com/notes/new', wait_until='networkidle', timeout=30000)
                page.wait_for_url(re.compile(r'^https://editor\.note\.com/notes/n[a-z0-9]+/edit/?$'), timeout=30000)
            remember_pending_draft(article_path, article, page.url)
            page.wait_for_timeout(5000)
            ss(page, '01_editor')

            # [4] タイトル入力
            print("[4] タイトル入力...")
            title_box = page.get_by_role('textbox', name='記事タイトル')
            if title_box.input_value(timeout=10000) != title:
                title_box.fill(title)
            if title_box.input_value() != title:
                raise RuntimeError('タイトルを原稿どおりに入力できませんでした')
            ss(page, '03_title')

            # [5] 本文エリアへ移動
            print("[5] 本文エリアへ移動...")
            try:
                page.click('.ProseMirror', timeout=5000)
            except Exception:
                page.keyboard.press('Tab')
            page.wait_for_timeout(300)

            print("[6] 本文を構造化して入力...")
            try:
                validate_article_structure(page, content, free_html + paid_html)
                print('  保存済み本文が原稿と一致するため再利用')
            except RuntimeError:
                clear_article_editor(page)
                paste_article_html(page, free_html + paid_html)
                validate_article_structure(page, content, free_html + paid_html)
            ss(page, '06_paid_content')
            print("[7] フォトギャラリーから見出し画像を選択...")
            select_gallery_image(page, article)
            ss(page, '02_eyecatch')

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
                set_paywall_after_free_blocks(page, free_block_count)
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

            final_url, published_price = wait_for_published_note(
                ctx.request, account_urlname, title, price,
            )
            print(f'  公開一覧で確認: {final_url} ({published_price}円)')

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
