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
