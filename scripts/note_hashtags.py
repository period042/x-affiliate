"""Choose and apply four article-specific Note hashtags before publication."""

from __future__ import annotations

import re
from typing import Any

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError


def _clean_tag(value: Any) -> str:
    return str(value or "").strip().lstrip("#").strip()


def derive_article_hashtags(article: dict[str, Any]) -> list[str]:
    """Return exactly four relevant tags, preferring explicit article metadata."""
    explicit = article.get("hashtags")
    if isinstance(explicit, list):
        tags = list(dict.fromkeys(_clean_tag(tag) for tag in explicit if _clean_tag(tag)))
        if len(tags) >= 4:
            return tags[:4]

    title = str(article.get("title", ""))
    genre = str(article.get("genre", ""))
    text = f"{genre} {title}"

    if any(word in text for word in ("不妊治療", "不妊", "妊活")):
        tags = ["不妊治療", "妊活"]
        if "人工授精" in text:
            tags.append("人工授精")
        if "体外受精" in text:
            tags.append("体外受精")
        if "転院" in text:
            tags.append("転院")
        tags.extend(["不妊治療体験談", "不妊治療記録"])
    elif "外資" in title:
        tags = ["外資系転職", "IT転職", "キャリア", "英語学習"]
    elif "広告業界" in title or "広告業界" in genre:
        tags = ["広告業界", "広告代理店", "転職", "キャリア"]
    elif "外資" in genre:
        tags = ["外資系転職", "IT転職", "転職", "キャリア"]
    elif "転職" in text:
        tags = ["転職", "キャリア"]
        if "IT" in text:
            tags.append("IT転職")
        if "英語" in text:
            tags.append("英語学習")
        tags.extend(["スキルアップ", "転職活動"])
    elif "婚活" in text or "結婚相談所" in text or "結婚" in genre:
        tags = ["婚活"]
        if "結婚相談所" in text:
            tags.append("結婚相談所")
        if "パーティー" in text:
            tags.append("婚活パーティー")
        if "ペット" in text:
            tags.append("ペット婚")
        tags.extend(["出会い", "婚活体験談", "パートナー探し"])
    elif "住宅" in text or "家づくり" in text or "工務店" in text:
        tags = []
        for keyword, tag in (
            ("工務店", "工務店"),
            ("注文住宅", "注文住宅"),
            ("リノベーション", "リノベーション"),
            ("移住", "移住"),
            ("住宅購入", "住宅購入"),
            ("家選び", "家づくり"),
        ):
            if keyword in text:
                tags.append(tag)
        tags.extend(["住宅", "家づくり", "マイホーム", "住まい選び"])
    elif any(word in text for word in ("確定申告", "会計", "経理", "節税")):
        tags = ["確定申告", "経理", "会計", "個人事業主"]
        if "法人" in text:
            tags = ["会計", "経理", "法人経営", "業務効率化"]
        elif "フリーランス" in text:
            tags = ["確定申告", "フリーランス", "経理", "個人事業主"]
    elif any(word in text for word in ("投資", "資産形成", "FUNDROP", "太陽光")):
        if "太陽光" in text or "省エネ" in text:
            tags = ["太陽光発電", "再生可能エネルギー", "投資", "資産形成"]
        else:
            tags = ["投資", "資産形成", "資産運用", "不動産投資"]
            if "クラウドファンディング" in text or "FUNDROP" in text:
                tags = ["不動産投資", "不動産クラウドファンディング", "投資", "資産形成"]
    elif any(word in text for word in ("子ども英語", "子どもの英語", "子ども英語教育")):
        tags = ["子ども英語", "英語教育", "英語学習", "子育て"]
    elif any(word in text for word in ("英語", "英会話", "TOEIC", "英検")):
        tags = ["英語学習"]
        if "TOEIC" in text:
            tags.append("TOEIC")
        if "英検" in text:
            tags.append("英検")
        if "オンライン" in text:
            tags.append("オンライン英会話")
        elif "英会話" in text:
            tags.append("英会話")
        if "コーチング" in text:
            tags.append("英語コーチング")
        tags.extend(["英語学習法", "英語教育"])
    elif any(word in text for word in ("動画", "クリエイター", "副業", "フリーランス")):
        tags = []
        if "動画" in text or "編集" in text:
            tags.extend(["動画編集", "動画クリエイター"])
        if "フリーランス" in text:
            tags.append("フリーランス")
        tags.extend(["副業", "クリエイター", "スキルアップ"])
    elif any(word in text for word in ("学習塾", "塾", "中学受験", "受験")):
        tags = ["学習塾", "中学受験", "塾選び", "受験"]
    elif any(word in text for word in ("フィットネス", "ダイエット", "トレーニング", "FURDI")):
        tags = ["フィットネス", "ダイエット", "運動習慣", "パーソナルトレーニング"]
    elif "ワイン" in text:
        tags = ["ワイン", "ワイン資格", "ワインエキスパート", "学び"]
    else:
        tags = ["体験談", "サービス選び", "活用方法", "比較"]

    unique = list(dict.fromkeys(_clean_tag(tag) for tag in tags if _clean_tag(tag)))
    if len(unique) < 4:
        unique.extend(tag for tag in ("体験談", "選び方", "活用方法", "比較") if tag not in unique)
    return unique[:4]


def _selected_tags(page) -> list[str]:
    return page.locator("button").evaluate_all(
        "buttons => buttons.filter(button => "
        "button.querySelector('[aria-label=\"削除\"], img[alt=\"削除\"]') && "
        "(button.innerText || '').trim().startsWith('#'))"
        ".map(button => (button.innerText || '').replace(/削除/g, '').trim().replace(/^#/, ''))"
    )


def apply_article_hashtags(page, article: dict[str, Any]) -> list[str]:
    """Set exactly four matching Note hashtags; raise before posting if unavailable."""
    tags = derive_article_hashtags(article)
    tag_input = page.get_by_role("combobox", name="ハッシュタグを追加する")
    try:
        tag_input.wait_for(state="visible", timeout=10000)
    except PlaywrightTimeoutError as exc:
        raise RuntimeError("公開設定のハッシュタグ入力欄が見つかりません") from exc

    # Replace preselected tags so every covered article has exactly four.
    for _ in range(5):
        selected = _selected_tags(page)
        if not selected:
            break
        remove_button = page.get_by_role("button", name=re.compile(r"^#.*削除$"))
        if remove_button.count() == 0:
            raise RuntimeError("既存ハッシュタグを置き換えられません")
        remove_button.first.click()
        page.wait_for_timeout(150)
    if _selected_tags(page):
        raise RuntimeError("既存ハッシュタグをクリアできません")

    selected: list[str] = []
    for desired in tags:
        tag_input.fill(desired)
        try:
            page.wait_for_function(
                "tag => Array.from(document.querySelectorAll('[role=option]')).some(option => "
                "(option.innerText || '').trim().split('\\n')[0].trim()"
                ".replace(/\\s+[0-9,]+件.*$/, '').trim() === '#' + tag)",
                arg=desired,
                timeout=10000,
            )
        except PlaywrightTimeoutError as exc:
            raise RuntimeError(f"指定したハッシュタグが見つかりません: #{desired}") from exc
        options = page.get_by_role("option").all()
        exact = None
        for option in options:
            label = option.inner_text().strip().splitlines()[0].strip()
            match = re.match(r"#(.+?)(?:\s+[0-9,]+件.*)?$", label)
            if not match:
                continue
            candidate = match.group(1).strip()
            if candidate == desired:
                exact = (option, candidate)
                break

        if exact is None:
            raise RuntimeError(f"選択可能なハッシュタグがありません: #{desired}")
        option, actual = exact
        if actual in selected:
            raise RuntimeError(f"異なるタグが重複して選択されました: #{actual}")
        option.click()
        page.wait_for_timeout(200)
        selected.append(actual)

    actual_tags = _selected_tags(page)
    if len(actual_tags) != 4 or len(set(actual_tags)) != 4:
        raise RuntimeError(f"ハッシュタグは4つ必要です: {actual_tags}")
    print(f"記事ハッシュタグ設定済み: {['#' + tag for tag in actual_tags]}")
    article["hashtags"] = actual_tags
    return actual_tags


def is_paid_or_affiliate_article(article: dict[str, Any]) -> bool:
    if article.get("paid"):
        return True
    for key in ("affiliate", "affiliate_url", "affiliate_link", "affiliate_links", "affiliate_id"):
        value = article.get(key)
        if value:
            return True
    return bool(article.get("has_affiliate"))
