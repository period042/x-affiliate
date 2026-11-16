import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import post_paid_note
from note_hashtags import apply_article_hashtags


class DailyLimitTests(unittest.TestCase):
    def test_counts_only_paid_posts_from_the_current_jst_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            posted = Path(tmp)
            records = {
                'a.json': {'paid': True, 'posted_at': '2026-11-16T13:33:00'},
                'b.json': {'paid': True, 'posted_at': '2026-11-16T22:40:00+09:00'},
                'c.json': {'paid': True, 'posted_at': '2026-11-15T14:59:00'},
                'd.json': {'paid': False, 'posted_at': '2026-11-16T22:00:00+09:00'},
            }
            for name, record in records.items():
                (posted / name).write_text(json.dumps(record), encoding='utf-8')
            now = post_paid_note.datetime(2026, 11, 16, 23, 0, tzinfo=post_paid_note.TOKYO)
            with patch.object(post_paid_note, 'POSTED_DIR', posted):
                self.assertEqual(post_paid_note.paid_posts_today(now), 2)


class DraftApiTests(unittest.TestCase):
    class Response:
        def __init__(self, data=None, status=200, raw=None):
            self.status = status
            self.ok = 200 <= status < 300
            self.raw = raw if raw is not None else json.dumps({'data': data})

        def text(self):
            return self.raw

    class Request:
        def __init__(self, *responses):
            self.responses = responses
            self.calls = []

        def get(self, url, params, timeout):
            self.calls.append((url, params, timeout))
            return self.responses[len(self.calls) - 1]

    def test_scans_all_pages_and_rejects_any_matching_status(self):
        pages = [
            self.Response({'notes': [{'key': 'n1', 'name': None, 'status': 'draft', 'noteDraft': {'name': 'first'}}], 'isLastPage': False, 'totalCount': 2}),
            self.Response({'notes': [{'key': 'n2', 'name': 'second', 'status': 'published'}], 'isLastPage': True, 'totalCount': 2}),
        ]
        request = self.Request(*pages)
        post_paid_note.ensure_no_existing_draft(request, 'new')
        self.assertEqual([call[1] for call in request.calls], [{'limit': 100, 'page': 1}, {'limit': 100, 'page': 2}])
        self.assertTrue(all(call[0] == 'https://note.com/api/v2/note_list/contents' for call in request.calls))
        with self.assertRaises(RuntimeError):
            post_paid_note.ensure_no_existing_draft(self.Request(*pages), 'second')
        with self.assertRaises(RuntimeError):
            post_paid_note.ensure_no_existing_draft(self.Request(*pages), 'first')

    def test_empty_list_is_allowed(self):
        post_paid_note.ensure_no_existing_draft(self.Request(self.Response(
            {'notes': [], 'isLastPage': True, 'totalCount': 0})), 'new')

    def test_untitled_draft_is_allowed(self):
        post_paid_note.ensure_no_existing_draft(self.Request(self.Response(
            {'notes': [{'key': 'n1', 'name': None, 'status': 'draft', 'noteDraft': {'name': ''}}],
             'isLastPage': True, 'totalCount': 1})), 'new')

    def test_nested_draft_title_is_checked(self):
        response = self.Response({'notes': [{'key': 'n1', 'name': None, 'status': 'draft',
                                            'noteDraft': {'name': 'existing'}}],
                                  'isLastPage': True, 'totalCount': 1})
        post_paid_note.ensure_no_existing_draft(self.Request(response), 'existing', exclude_key='n1')
        with self.assertRaises(RuntimeError):
            post_paid_note.ensure_no_existing_draft(self.Request(response), 'existing')
        with self.assertRaises(RuntimeError):
            post_paid_note.ensure_no_existing_draft(self.Request(self.Response(
                {'notes': [{'key': 'n1', 'name': None, 'status': 'draft'}],
                 'isLastPage': True, 'totalCount': 1})), 'existing')

    def test_invalid_or_incomplete_lists_fail_closed(self):
        first = {'notes': [{'key': 'n1', 'name': 'first'}], 'isLastPage': False, 'totalCount': 2}
        invalid = [
            [self.Response(status=403)],
            [self.Response(raw='{')],
            [self.Response(raw='{"data":{"notes":[],"notes":[],"isLastPage":true,"totalCount":0}}')],
            [self.Response({'contents': [], 'isLastPage': True, 'totalCount': 0})],
            [self.Response({'notes': [], 'isLastPage': 1, 'totalCount': 0})],
            [self.Response({'notes': [], 'isLastPage': True, 'totalCount': True})],
            [self.Response({'notes': [{}], 'isLastPage': True, 'totalCount': 1})],
            [self.Response({'notes': [], 'isLastPage': False, 'totalCount': 1})],
            [self.Response({'notes': [], 'isLastPage': True, 'totalCount': 1})],
            [self.Response(first), self.Response(status=500)],
            [self.Response(first), self.Response({'notes': [{'key': 'n1', 'name': 'other'}], 'isLastPage': True, 'totalCount': 2})],
            [self.Response(first), self.Response({'notes': [{'key': 'n2', 'name': 'other'}], 'isLastPage': True, 'totalCount': 3})],
        ]
        for responses in invalid:
            with self.subTest(responses=responses), self.assertRaises(RuntimeError):
                post_paid_note.ensure_no_existing_draft(self.Request(*responses), 'new')

    def test_patch_fragments_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'article.json'
            with patch.object(post_paid_note, 'QUEUE_DIR', Path(directory)):
                for fragment in ['*** Begin Patch', '*** End Patch', '*** End of File', '*** Update File: a',
                                 '*** Add File: a', '*** Delete File: a', '*** Move to: b', '@@class Example',
                                 '@@ -1 +1 @@', 'diff --git a/a b/a', '--- a/file', '+++ b/file']:
                    with self.subTest(fragment=fragment), self.assertRaises(ValueError):
                        post_paid_note.validate_unique_article_content(path, {'content': '本文\n' + fragment})
                valid = ('導入' + 'あ' * 1600 + '\n\n---\n\n## 有料部分で手に入るもの\n\n案内\n\n'
                         '※ここから有料部分\n\n## 有料\n\n' + 'い' * 5000)
                post_paid_note.validate_unique_article_content(path, {'content': valid})


class PaidNoteTests(unittest.TestCase):
    def test_clear_article_editor_replaces_existing_body(self):
        self.page.set_content('<div class="ProseMirror" contenteditable="true"><p>old body</p></div>')
        post_paid_note.clear_article_editor(self.page)
        self.assertEqual(self.page.locator('.ProseMirror').inner_text().strip(), '')

    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        options = {'headless': True}
        if os.environ.get('PLAYWRIGHT_CHANNEL'):
            options['channel'] = os.environ['PLAYWRIGHT_CHANNEL']
        cls.browser = cls.playwright.chromium.launch(**options)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.page = self.browser.new_page(viewport={'width': 800, 'height': 500})

    def tearDown(self):
        self.page.close()

    def test_structured_content_and_boundary(self):
        free = '導入\n\n## 流れ\n\n**フェーズ1：準備**\n説明1\n\n**フェーズ2：実行**\n説明2\n\n**フェーズ3：確認**\n説明3\n\n## 有料見出し'
        paid = '有料本文'
        free_html, free_count = post_paid_note.render_article_html(free)
        paid_html, _ = post_paid_note.render_article_html(paid)
        self.assertTrue(free_html.endswith('</h2>'))
        html = free_html + paid_html
        self.page.set_content('<div class="ProseMirror">' + html + '</div>')
        post_paid_note.validate_article_structure(self.page, free + '\n[有料壁]\n' + paid, html)
        self.assertEqual(self.page.locator('.ProseMirror ol > li').count(), 3)
        self.assertEqual(self.page.locator('.ProseMirror h2').first.evaluate('el => el.nextElementSibling.tagName'), 'OL')
        self.assertEqual(self.page.locator('.ProseMirror p:empty').count(), 0)
        self.page.evaluate('''() => {
            const editor = document.querySelector('.ProseMirror');
            editor.classList.add('paywall-setting');
            for (const block of [...editor.children]) {
                const widget = document.createElement('div');
                widget.className = 'ProseMirror-widget';
                const button = document.createElement('button');
                button.innerText = 'ラインをこの場所に変更';
                button.setAttribute('aria-pressed', 'false');
                button.onclick = () => button.setAttribute('aria-pressed', 'true');
                widget.append(button);
                block.after(widget);
            }
        }''')
        post_paid_note.set_paywall_after_free_blocks(self.page, free_count)
        self.assertEqual(self.page.locator('button[aria-pressed="true"]').count(), 1)

    def test_truncated_content_is_rejected(self):
        html, _ = post_paid_note.render_article_html('## 見出し\n\n本文')
        self.page.set_content('<div class="ProseMirror">' + html + '</div>')
        self.page.locator('.ProseMirror p').last.evaluate("el => el.textContent = '本'")
        with self.assertRaises(RuntimeError):
            post_paid_note.validate_article_structure(self.page, '## 見出し\n\n本文', html)

    def test_pending_draft_is_reused_and_invalid_url_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'article.json'
            article = {'title': 'test'}
            self.assertIsNone(post_paid_note.ensure_no_pending_draft(article))
            post_paid_note.remember_pending_draft(path, article, 'https://editor.note.com/notes/n123abc/edit/')
            saved = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(post_paid_note.ensure_no_pending_draft(saved), saved['pending_editor_url'])
            with self.assertRaises(RuntimeError):
                post_paid_note.ensure_no_pending_draft({'pending_editor_url': 'https://example.com'})

    def test_identical_content_under_different_titles_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            queue = Path(directory)
            first = queue / 'first.json'
            second = queue / 'second.json'
            article = {'title': 'first', 'content': 'same text'}
            first.write_text(json.dumps(article), encoding='utf-8')
            second.write_text(json.dumps({'title': 'second', 'content': 'same text'}), encoding='utf-8')
            with patch.object(post_paid_note, 'QUEUE_DIR', queue):
                with self.assertRaises(ValueError):
                    post_paid_note.validate_unique_article_content(first, article)

    def test_existing_publication_is_detected_on_later_pages(self):
        class Response:
            ok = True

            def __init__(self, data):
                self.data = data

            def json(self):
                return {'data': self.data}

        class Request:
            def get(self, url, params, timeout):
                pages = [
                    {'contents': [{'name': 'first'}], 'isLastPage': False},
                    {'contents': [{'name': 'second', 'noteUrl': 'https://note.com/u/n/n123'}], 'isLastPage': True},
                ]
                return Response(pages[params['page'] - 1])

        post_paid_note.ensure_no_existing_publication(Request(), 'u', 'new')
        with self.assertRaises(RuntimeError):
            post_paid_note.ensure_no_existing_publication(Request(), 'u', 'second')

    def test_published_note_is_verified_from_creator_list(self):
        class Response:
            ok = True

            def json(self):
                return {'data': {'contents': [{'name': 'title', 'status': 'published',
                                               'price': 500, 'noteUrl': 'https://note.com/u/n/n123'}],
                                 'isLastPage': False}}

        class Request:
            def get(self, url, params, timeout):
                self.args = (url, params, timeout)
                return Response()

        request = Request()
        self.assertEqual(
            post_paid_note.wait_for_published_note(request, 'u', 'title', 500),
            ('https://note.com/u/n/n123', 500),
        )
        self.assertEqual(request.args[1], {'kind': 'note', 'page': 1})

    def test_published_note_price_mismatch_is_rejected(self):
        class Response:
            ok = True

            def json(self):
                return {'data': {'contents': [{'name': 'title', 'status': 'published',
                                               'price': 0, 'noteUrl': 'https://note.com/u/n/n123'}],
                                 'isLastPage': True}}

        class Request:
            def get(self, *args, **kwargs):
                return Response()

        with self.assertRaises(RuntimeError):
            post_paid_note.wait_for_published_note(Request(), 'u', 'title', 500)

    def test_hidden_paid_radio_and_price(self):
        self.page.set_content('<input type="radio" value="paid" style="display:none"><div style="height:1200px"></div><div id="holder"></div>')
        self.page.evaluate('''() => document.querySelector('input').addEventListener('click', () => {
            document.querySelector('#holder').innerHTML = '<h3>価格</h3><input id="price" placeholder="300" value="300">';
        })''')
        post_paid_note.set_price_in_modal(self.page, 500)
        self.assertEqual(self.page.locator('#price').input_value(), '500')

    def test_gallery_image_is_searched_and_saved(self):
        self.page.set_content('''
            <button id="draft" onclick="window.draftClicked=true">下書き保存</button>
            <main>
              <button onclick="document.querySelector('#open').hidden=false">画像を追加</button>
              <button id="open" hidden onclick="document.querySelector('#stockPhotoModal').hidden=false">記事にあう画像を選ぶ</button>
              <img alt="eyecatch" src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==" hidden>
            </main>
            <div id="popover" role="dialog">popover</div>
            <div id="stockPhotoModal" role="dialog" hidden>
              <button onclick="document.querySelector('#search').hidden=false">検索する</button>
              <input id="search" hidden aria-label="みんなのフォトギャラリーから検索する"
                onkeydown="if(event.key==='Enter')document.querySelectorAll('.c').forEach(c=>c.hidden=false)">
              <div class="c" role="button" hidden onclick="show('夏','不妊治療の母','猫','猫')"><figure>a</figure></div>
              <div class="c" role="button" hidden onclick="show('こんにちは、不妊治療','作者','病院','初診の絵')"><figure>b</figure></div>
              <div id="detail"></div>
            </div>
            <div id="crop" role="dialog" hidden>
              <button onclick="document.querySelector('#crop').hidden=true;document.querySelector('img').hidden=false">保存</button>
            </div>
            <script>
              function show(t, c, k, d) {
                document.querySelector('#detail').innerHTML =
                  `<div>タイトル</div><div>${t}</div><div>クリエイター</div><div>${c}</div>` +
                  `<div>キーワード</div><div>${k}</div><div>説明</div><div>${d}</div>` +
                  `<button onclick="document.querySelector('#detail').innerHTML=''">この画像を使用しない</button>` +
                  `<button onclick="window.inserted='${t}';document.querySelector('#stockPhotoModal').hidden=true;document.querySelector('#crop').hidden=false">この画像を挿入</button>`;
              }
            </script>
        ''')
        post_paid_note.select_gallery_image(self.page, {'title': '不妊治療の記録', 'genre': '不妊治療'})
        self.assertTrue(self.page.locator('main img[alt="eyecatch"]').is_visible())
        self.assertEqual(self.page.evaluate('window.inserted'), 'こんにちは、不妊治療')
        self.assertIsNone(self.page.evaluate('window.draftClicked'))

    def gallery_fixture(self):
        return '''
            <main>
              <button onclick="document.querySelector('#stockPhotoModal').hidden=false">画像を追加</button>
              <button>記事にあう画像を選ぶ</button>
              <img alt="eyecatch" src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==" hidden>
            </main>
            <div id="stockPhotoModal" role="dialog" hidden>
              <button>検索する</button>
              <input aria-label="みんなのフォトギャラリーから検索する" onkeydown="window.searched=(window.searched||[]).concat(this.value)">
              <div role="button" onclick="show('英会話のレッスン','A','英会話')"><figure>a</figure></div>
              <div role="button" onclick="show('マンツーマン英会話','B','英会話')"><figure>b</figure></div>
              <div id="detail"></div>
            </div>
            <div id="crop" role="dialog" hidden>
              <button onclick="document.querySelector('#crop').hidden=true;document.querySelector('img').hidden=false">保存</button>
            </div>
            <script>
              function show(t, c, k) {
                document.querySelector('#detail').innerHTML =
                  `<div>タイトル</div><div>${t}</div><div>クリエイター</div><div>${c}</div><div>キーワード</div><div>${k}</div>` +
                  `<button onclick="document.querySelector('#detail').innerHTML=''">この画像を使用しない</button>` +
                  `<button onclick="window.inserted='${t}';document.querySelector('#stockPhotoModal').hidden=true;document.querySelector('#crop').hidden=false">この画像を挿入</button>`;
              }
            </script>
        '''

    def test_gallery_skips_images_used_by_other_articles(self):
        article = {'title': 'マンツーマン英会話', 'genre': '英語学習', 'eyecatch_keywords': ['英会話']}
        self.page.set_content(self.gallery_fixture())
        first_key = post_paid_note.select_gallery_image(self.page, article)
        self.assertEqual(self.page.evaluate('window.inserted'), '英会話のレッスン')
        self.page.set_content(self.gallery_fixture())
        second_key = post_paid_note.select_gallery_image(self.page, article, {first_key})
        self.assertEqual(self.page.evaluate('window.inserted'), 'マンツーマン英会話')
        self.assertNotEqual(first_key, second_key)
        self.assertEqual(self.page.evaluate('window.searched')[0], '英会話')

    def test_gallery_image_waits_until_saved_image_loads(self):
        gif = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw=='
        self.page.set_content(f'''
            <main>
              <button onclick="document.querySelector('#stockPhotoModal').hidden=false">画像を追加</button>
              <button>記事にあう画像を選ぶ</button>
              <img alt="eyecatch" hidden>
            </main>
            <div id="stockPhotoModal" role="dialog" hidden>
              <button>検索する</button>
              <input aria-label="みんなのフォトギャラリーから検索する">
              <div role="button" onclick="document.querySelector('#detail').hidden=false"><figure>a</figure></div>
              <div id="detail" hidden><div>タイトル</div><div>不妊治療</div>
                <button onclick="document.querySelector('#stockPhotoModal').hidden=true;document.querySelector('#crop').hidden=false">この画像を挿入</button></div>
            </div>
            <div id="crop" role="dialog" hidden>
              <button onclick="document.querySelector('#crop').hidden=true;const img=document.querySelector('img');img.hidden=false;setTimeout(()=>img.src='{gif}',1000)">保存</button>
            </div>
        ''')
        post_paid_note.select_gallery_image(self.page, {'title': '不妊治療の記録', 'genre': '不妊治療'})
        self.assertTrue(self.page.locator('img[alt="eyecatch"]').evaluate('element => element.naturalWidth > 0'))

    def test_existing_gallery_image_is_reused(self):
        self.page.set_content('''<main><img alt="eyecatch" src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw=="></main>''')
        post_paid_note.select_gallery_image(self.page, {'title': '不妊治療の記録', 'genre': '不妊治療'})
        self.assertTrue(self.page.locator('img[alt="eyecatch"]').is_visible())

    def test_delayed_hashtag_suggestions(self):
        self.page.set_content('<div id="chips"></div><input role="combobox" aria-label="ハッシュタグを追加する"><div id="results"></div>')
        self.page.evaluate('''() => {
            const input = document.querySelector('input');
            input.addEventListener('input', () => {
                const value = input.value;
                setTimeout(() => {
                    if (input.value !== value) return;
                    const results = document.querySelector('#results');
                    results.replaceChildren();
                    const similar = document.createElement('div');
                    similar.setAttribute('role', 'option');
                    similar.innerText = '#' + value + 'extra 100件';
                    results.append(similar);
                    const option = document.createElement('div');
                    option.setAttribute('role', 'option');
                    option.innerText = '#' + value + (value === '英語学習' ? ' 1,000件' : '\\n100件');
                    option.onclick = () => {
                        const chip = document.createElement('button');
                        chip.innerText = '#' + value;
                        const icon = document.createElement('span');
                        icon.setAttribute('aria-label', '削除');
                        chip.append(icon);
                        document.querySelector('#chips').append(chip);
                    };
                    results.append(option);
                }, 250);
            });
        }''')
        tags = ['英語学習', '英会話', '英検', '英語教育']
        self.assertEqual(apply_article_hashtags(self.page, {'hashtags': tags}), tags)


if __name__ == '__main__':
    unittest.main()