import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import post_note_article
from article_checks import (
    assert_editor_headings, duplicate_blocks, heading_only_sections, validate_heading_bodies, validate_paid_article,
)


class HeadingSourceTests(unittest.TestCase):
    def test_accepts_headings_with_body(self):
        content = '導入\n\n## 見出し1\n\n本文1\n\n## 見出し2\n本文2'
        self.assertEqual(heading_only_sections(content), [])
        validate_heading_bodies(content)

    def test_detects_heading_followed_by_heading_or_end(self):
        content = '## 空の見出し\n\n## 見出し2\n本文\n\n## 最後の見出し\n'
        self.assertEqual(heading_only_sections(content), ['空の見出し', '最後の見出し'])
        with self.assertRaises(ValueError):
            validate_heading_bodies(content)

    def test_paid_marker_does_not_count_as_body(self):
        marker = '※ここから有料部分'
        content = f'導入\n\n## 無料の最後\n\n{marker}\n\n## 有料1\n本文'
        self.assertEqual(heading_only_sections(content, marker), ['無料の最後'])
        ok = f'導入\n\n## 無料の最後\n本文\n\n{marker}\n\n## 有料1\n本文'
        self.assertEqual(heading_only_sections(ok, marker), [])


class PaidArticleRuleTests(unittest.TestCase):
    marker = '[有料壁]'

    def article(self, paid_chars=5200, hook='## 有料部分で手に入るもの'):
        free = '導入' + 'あ' * 800 + '\n\n## 見出し\n\n' + 'い' * 800 + f'\n\n{hook}\n\n' + 'う' * 200
        paid = '## 有料1\n\n' + 'え' * paid_chars
        return f'{free}\n\n{self.marker}\n\n{paid}'

    def test_accepts_long_paid_part_with_hook(self):
        validate_paid_article(self.article(), self.marker)

    def test_rejects_short_paid_part(self):
        with self.assertRaisesRegex(ValueError, '有料部分が短すぎます'):
            validate_paid_article(self.article(paid_chars=1500), self.marker)

    def test_rejects_missing_hook_heading(self):
        with self.assertRaisesRegex(ValueError, '案内する見出し'):
            validate_paid_article(self.article(hook='## まとめ'), self.marker)

    def test_rejects_duplicated_sections(self):
        content = self.article() + '\n\n## 有料1\n\n' + 'お' * 50
        with self.assertRaisesRegex(ValueError, '重複'):
            validate_paid_article(content, self.marker)
        self.assertEqual(duplicate_blocks('## A\n\n本文\n\n## A\n\n別'), ['## A'])


class FakeKeyboard:
    def __init__(self):
        self.text = ''

    def type(self, value, delay=None):
        self.text += value

    def press(self, key):
        self.text += '\n' if key == 'Enter' else f'<{key}>'


class FakePage:
    def __init__(self):
        self.keyboard = FakeKeyboard()

    def wait_for_timeout(self, ms):
        pass


class FreeArticleInputTests(unittest.TestCase):
    def test_blank_lines_after_headings_are_not_typed(self):
        page = FakePage()
        post_note_article.insert_content_with_ogp(page, '導入\n\n## 見出し1\n\n本文1\n\n## 見出し2\n\n\n本文2')
        self.assertEqual(page.keyboard.text.split('\n')[:-1], ['導入', '', '## 見出し1', '本文1', '', '## 見出し2', '本文2'])

    def test_next_article_uses_jst_date_and_skips_heading_only_article(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = Path(tmp)
            body = 'x' * 3000
            (queue / '2026-11-16_a.json').write_text(json.dumps({
                'scheduled_for': '2026-11-16T08:00:00', 'title': 'A', 'content': f'## 空\n\n## 見出し\n{body}',
            }, ensure_ascii=False), encoding='utf-8')
            (queue / '2026-11-16_b.json').write_text(json.dumps({
                'scheduled_for': '2026-11-16T08:00:00', 'title': 'B', 'content': f'## 見出し\n{body}',
            }, ensure_ascii=False), encoding='utf-8')

            class FixedDatetime(datetime):
                @classmethod
                def now(cls, tz=None):
                    value = datetime(2026, 11, 15, 23, 0, tzinfo=timezone.utc)
                    return value.astimezone(tz) if tz else value.replace(tzinfo=None)

            with patch.object(post_note_article, 'NOTE_QUEUE_DIR', queue), \
                    patch.object(post_note_article, 'datetime', FixedDatetime):
                self.assertEqual(post_note_article.get_next_article().name, '2026-11-16_b.json')


class EditorHeadingTests(unittest.TestCase):
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
        self.page = self.browser.new_page()

    def tearDown(self):
        self.page.close()

    def test_accepts_heading_followed_by_body(self):
        self.page.set_content('<div class="ProseMirror"><p>導入</p><p></p><h2>見出し</h2><p>本文</p></div>')
        assert_editor_headings(self.page, expected_headings=1)

    def test_rejects_blank_paragraph_after_heading(self):
        self.page.set_content('<div class="ProseMirror"><h2>見出し</h2><p></p><p>本文</p></div>')
        with self.assertRaisesRegex(RuntimeError, '空行'):
            assert_editor_headings(self.page)

    def test_rejects_heading_without_body(self):
        self.page.set_content('<div class="ProseMirror"><h2>空</h2><h2>見出し</h2><p>本文</p></div>')
        with self.assertRaisesRegex(RuntimeError, '本文のない見出し'):
            assert_editor_headings(self.page)

    def test_rejects_unconverted_heading(self):
        self.page.set_content('<div class="ProseMirror"><p>## 見出し</p><p>本文</p></div>')
        with self.assertRaisesRegex(RuntimeError, '見出しの数'):
            assert_editor_headings(self.page, expected_headings=1)


if __name__ == '__main__':
    unittest.main()
