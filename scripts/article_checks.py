"""Structure checks shared by every Note posting path."""

from __future__ import annotations

import re

_HEADING = re.compile(r'^#{1,6}\s+(.+)$')


def heading_only_sections(content: str, paid_marker: str | None = None) -> list[str]:
    """Return headings that are followed directly by another heading or by the end of the article."""
    lines = [line.strip() for line in content.splitlines()]
    blocks = [line for line in lines if line and line != paid_marker]
    empty = []
    for index, line in enumerate(blocks):
        match = _HEADING.match(line)
        if not match:
            continue
        following = blocks[index + 1] if index + 1 < len(blocks) else None
        if following is None or _HEADING.match(following):
            empty.append(match.group(1).strip())
    return empty


def validate_heading_bodies(content: str, paid_marker: str | None = None) -> None:
    empty = heading_only_sections(content, paid_marker)
    if empty:
        raise ValueError(f"本文のない見出しがあります: {', '.join(empty)}")


PAID_MIN_CHARS = 5000
FREE_MIN_CHARS = 1500


def _visible_chars(text: str) -> int:
    return len(re.sub(r'\s', '', text))


def duplicate_blocks(content: str) -> list[str]:
    """Return repeated headings and repeated paragraphs longer than 40 characters."""
    seen, duplicates = set(), []
    for block in re.split(r'\n\s*\n', content):
        block = block.strip()
        if not block or (not _HEADING.match(block) and len(block) <= 40):
            continue
        if block in seen and block not in duplicates:
            duplicates.append(block[:40])
        seen.add(block)
    return duplicates


def validate_article_text(content: str, paid_marker: str | None = None) -> None:
    validate_heading_bodies(content, paid_marker)
    duplicates = duplicate_blocks(content)
    if duplicates:
        raise ValueError(f"同じ見出しまたは段落が重複しています: {duplicates}")


def validate_paid_article(content: str, paid_marker: str) -> None:
    validate_article_text(content, paid_marker)
    if content.count(paid_marker) != 1:
        raise ValueError(f"有料開始マーカーは1か所必要です: {paid_marker}")
    free, paid = content.split(paid_marker)
    if _visible_chars(free) < FREE_MIN_CHARS:
        raise ValueError(f"無料部分が短すぎます: {_visible_chars(free)}字 < {FREE_MIN_CHARS}字")
    if _visible_chars(paid) < PAID_MIN_CHARS:
        raise ValueError(f"有料部分が短すぎます: {_visible_chars(paid)}字 < {PAID_MIN_CHARS}字")
    free_headings = [match.group(1) for match in map(_HEADING.match, free.splitlines()) if match]
    if not free_headings or '有料' not in free_headings[-1]:
        raise ValueError("無料部分の最後に、有料部分の内容を案内する見出しが必要です")


def editor_heading_issues(page) -> dict[str, list[str]]:
    """Inspect the Note editor for headings without body text and blank paragraphs under headings."""
    return page.locator('.ProseMirror').evaluate("""editor => {
        const blocks = Array.from(editor.children).filter(block =>
            !block.classList.contains('ProseMirror-widget') && block.tagName !== 'PAYWALL-LINE');
        const isHeading = block => /^H[1-6]$/.test(block.tagName);
        const isEmpty = block => block.tagName === 'P' && !block.textContent.trim()
            && !block.querySelector('img, figure, iframe');
        const blankAfterHeading = [];
        const headingOnly = [];
        blocks.forEach((block, index) => {
            if (!isHeading(block)) return;
            const next = blocks[index + 1];
            if (next && isEmpty(next)) blankAfterHeading.push(block.textContent.trim());
            const body = blocks.slice(index + 1).find(item => !isEmpty(item));
            if (!body || isHeading(body)) headingOnly.push(block.textContent.trim());
        });
        return {blankAfterHeading, headingOnly};
    }""")


def assert_editor_headings(page, expected_headings: int | None = None) -> None:
    issues = editor_heading_issues(page)
    if issues['blankAfterHeading']:
        raise RuntimeError(f"見出し直後に空行があります: {issues['blankAfterHeading']}")
    if issues['headingOnly']:
        raise RuntimeError(f"本文のない見出しがあります: {issues['headingOnly']}")
    if expected_headings is not None:
        actual = page.locator('.ProseMirror h2, .ProseMirror h3').count()
        if actual != expected_headings:
            raise RuntimeError(f"見出しの数が原稿と一致しません: expected={expected_headings}, actual={actual}")
