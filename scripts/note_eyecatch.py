"""Pick a relevant, not-yet-used Note photo gallery image for an article's eyecatch."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from note_hashtags import derive_article_hashtags

_DETAIL_LABELS = {'タイトル', 'クリエイター', 'キーワード', '説明', 'キャンセル', 'この画像を挿入', 'この画像を使用しない'}


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


MAX_IMAGE_TAGS = 12


def gallery_image_score(detail_text: str, keyword: str, keywords: list[str]) -> int:
    """Count article keywords found in the image's own tags/description; 0 if the searched keyword is absent.

    The title is ignored because it is the creator's article title, and tag-stuffed images are rejected.
    """
    tags = _labelled_text(detail_text, {'キーワード'}).split()
    description = _labelled_text(detail_text, {'説明'})
    if len(tags) > MAX_IMAGE_TAGS:
        return 0
    if len(description.split()) > MAX_IMAGE_TAGS:
        description = ''
    found = [word for word in keywords if word in tags or word in description]
    return len(found) if keyword in found else 0


def gallery_image_key(source: str | None, detail_text: str) -> str:
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
    """Insert the best-matching unused gallery image and return its key; reuse an eyecatch already on the draft."""
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
    keywords = eyecatch_keywords(article)

    def show_results(keyword):
        search.fill(keyword)
        search.press('Enter')
        page.wait_for_timeout(1500)
        return gallery.locator('[role="button"]:has(figure)')

    def open_candidate(candidate):
        # Read the thumbnail before opening the detail view, which can change the result list.
        image = candidate.locator('img')
        source = image.first.get_attribute('src') if image.count() else None
        candidate.click(timeout=10000)
        page.wait_for_timeout(300)
        detail = gallery.inner_text()
        return gallery_image_key(source, detail), detail

    def close_detail():
        gallery.get_by_role('button', name='この画像を使用しない').click(timeout=10000)
        page.wait_for_timeout(200)

    found = []
    for keyword in keywords:
        candidates = show_results(keyword)
        for index in range(min(candidates.count(), 15)):
            key, detail = open_candidate(candidates.nth(index))
            close_detail()
            score = gallery_image_score(detail, keyword, keywords)
            if key not in used_keys and score and key not in {f[2] for f in found}:
                found.append((score, keyword, key))
        if found and max(f[0] for f in found) >= 3:
            break
    if not found:
        raise RuntimeError('記事に関連する未使用のフォトギャラリー画像を確認できませんでした')

    # Search results can be reordered between searches, so locate the chosen image by key, falling back to the next best.
    for score, keyword, key in sorted(found, key=lambda f: -f[0])[:3]:
        candidates = show_results(keyword)
        for index in range(min(candidates.count(), 30)):
            candidate_key, _ = open_candidate(candidates.nth(index))
            if candidate_key != key:
                close_detail()
                continue
            gallery.get_by_role('button', name='この画像を挿入').click(timeout=10000)
            save = page.get_by_role('button', name='保存', exact=True)
            page.get_by_role('dialog').filter(has=save).get_by_role('button', name='保存', exact=True).click(timeout=10000)
            _wait_for_loaded_eyecatch(page)
            print(f"  フォトギャラリー検索: {keyword} (一致{score}語, {key})")
            return key
        print(f"  選んだ画像が再検索結果に見つからないため次の候補を試します: {keyword} ({key})")
    raise RuntimeError('選んだフォトギャラリー画像を再表示できませんでした')
