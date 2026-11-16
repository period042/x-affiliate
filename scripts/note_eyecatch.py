"""Pick a relevant, not-yet-used Note photo gallery image for an article's eyecatch."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from note_hashtags import derive_article_hashtags

_DETAIL_LABELS = {'タイトル', 'クリエイター', 'キーワード', '説明', 'キャンセル', 'この画像を挿入'}


def _labelled_text(detail_text: str, wanted: set[str]) -> str:
    lines = [line.strip() for line in detail_text.splitlines() if line.strip()]
    collected, current = [], None
    for line in lines:
        if line in _DETAIL_LABELS:
            current = line if line in wanted else None
        elif current:
            collected.append(line)
    return '\n'.join(collected)


def gallery_image_metadata(detail_text: str) -> str:
    # The creator name is excluded because it often contains the search word for unrelated images.
    return _labelled_text(detail_text, {'タイトル', 'キーワード', '説明'})


def gallery_image_key(candidate, detail_text: str) -> str:
    image = candidate.locator('img')
    source = image.first.get_attribute('src') if image.count() else None
    identity = source.split('?')[0] if source else _labelled_text(detail_text, {'タイトル', 'クリエイター', 'キーワード'})
    return hashlib.sha1(identity.encode('utf-8')).hexdigest()[:16]


def eyecatch_keywords(article: dict) -> list[str]:
    keywords = []
    for keyword in [*article.get('eyecatch_keywords', []), *derive_article_hashtags(article)]:
        keyword = keyword.strip()
        if keyword and keyword not in keywords:
            keywords.append(keyword)
    return keywords


def used_eyecatch_keys(*folders: Path, exclude: Path | None = None) -> set[str]:
    used = set()
    for folder in folders:
        for path in folder.glob('*.json'):
            if exclude is not None and path.name == exclude.name and path.parent == exclude.parent:
                continue
            try:
                key = json.loads(path.read_text(encoding='utf-8-sig')).get('eyecatch_key')
            except (OSError, ValueError):
                continue
            if key:
                used.add(key)
    return used


def _wait_for_loaded_eyecatch(page):
    image = page.locator('img[alt="eyecatch"]')
    image.wait_for(state='visible', timeout=15000)
    try:
        loaded = image.first.evaluate(
            """element => element.complete && element.naturalWidth > 0 ? true
                : new Promise((resolve, reject) => {
                    element.addEventListener('load', () => resolve(element.naturalWidth > 0), {once: true});
                    element.addEventListener('error', () => reject(new Error('image error')), {once: true});
                    setTimeout(() => reject(new Error('image timeout')), 30000);
                })"""
        )
    except Exception as exc:
        raise RuntimeError('見出し画像を読み込めませんでした') from exc
    if not loaded:
        raise RuntimeError('見出し画像を読み込めませんでした')


def select_gallery_image(page, article, used_keys=frozenset()):
    """Insert a gallery image matching the article and return its key; reuse an eyecatch already on the draft."""
    existing_image = page.locator('img[alt="eyecatch"]')
    if existing_image.count() and existing_image.is_visible():
        if existing_image.evaluate('element => element.complete && element.naturalWidth > 0'):
            print('  既存の見出し画像を再利用')
            return article.get('eyecatch_key')
    page.get_by_role('button', name='画像を追加').click(timeout=10000)
    page.get_by_role('button', name='記事にあう画像を選ぶ').click(timeout=10000)
    gallery = page.locator('#stockPhotoModal')
    gallery.get_by_role('button', name='検索する').click(timeout=10000)
    search = gallery.get_by_role('textbox', name='みんなのフォトギャラリーから検索する')
    for keyword in eyecatch_keywords(article):
        search.fill(keyword)
        search.press('Enter')
        page.wait_for_timeout(1500)
        candidates = gallery.locator('[role="button"]:has(figure)')
        for index in range(min(candidates.count(), 30)):
            candidate = candidates.nth(index)
            candidate.click(timeout=10000)
            page.wait_for_timeout(300)
            detail = gallery.inner_text()
            key = gallery_image_key(candidate, detail)
            if key not in used_keys and keyword in gallery_image_metadata(detail):
                gallery.get_by_role('button', name='この画像を挿入').click(timeout=10000)
                save = page.get_by_role('button', name='保存', exact=True)
                page.get_by_role('dialog').filter(has=save).get_by_role(
                    'button', name='保存', exact=True).click(timeout=10000)
                _wait_for_loaded_eyecatch(page)
                print(f"  フォトギャラリー検索: {keyword} ({key})")
                return key
            gallery.get_by_role('button', name='この画像を使用しない').click(timeout=10000)
            page.wait_for_timeout(300)
    raise RuntimeError('記事に関連する未使用のフォトギャラリー画像を確認できませんでした')
