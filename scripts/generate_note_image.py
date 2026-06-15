#!/usr/bin/env python3
"""
note記事のヘッダ画像を生成する。
優先: Pexels API（高品質・要APIキー）
 2nd: LoremFlickr（無料・APIキー不要・キーワード検索）
 3rd: Pollinations.ai（AI生成・無料）
 4th: グラデーション fallback
"""
import os, json, urllib.request, urllib.parse, urllib.error
from pathlib import Path

# ────────────────────────────────────────────────
# ジャンル別 検索キーワード
# ────────────────────────────────────────────────
PEXELS_QUERIES = {
    "英語学習（大人）": ["study desk books globe", "english learning workspace coffee", "open book lamp desk morning"],
    "英語学習（子ども）": ["colorful education toys classroom", "children learning toys desk", "educational materials bright colors"],
    "英語学習":          ["study desk notebook laptop", "learning books globe desk", "education workspace morning light"],
    "転職":              ["modern office desk city view", "professional workspace laptop window", "career office morning business"],
    "投資":              ["laptop finance desk notebook", "business planning office desk", "professional minimal desk"],
    "キャリア":          ["modern office workspace", "professional desk career", "business office morning light"],
    "default":           ["minimal workspace desk laptop", "clean desk coffee notebook", "modern home office light"],
}

LOREMFLICKR_KEYWORDS = {
    "英語学習（大人）": "studying,notebook",
    "英語学習（子ども）": "children,learning",
    "英語学習":          "books,notebook",
    "転職":              "office,laptop",
    "投資":              "laptop,business",
    "キャリア":          "office,professional",
    "default":           "desk,office",
}

GENRE_VISUALS = {
    "英語学習（大人）": (
        "cozy study corner, open English grammar textbook, small globe paperweight, "
        "vintage brass lamp glowing warmly, steaming coffee mug, reading glasses beside book, "
        "soft golden afternoon window light, blurred bookshelf background, "
        "no people, professional still life photography"
    ),
    "英語学習（子ども）": (
        "bright cheerful classroom, colorful wooden toy blocks in primary colors on small desk, "
        "picture books with animal illustrations, crayons arranged neatly, small globe toy, "
        "pastel tones, overhead flat lay composition, no people"
    ),
    "英語学習": (
        "elegant desk, open English dictionary, small globe, "
        "steaming coffee cup on saucer, handwritten notebook, warm diffused window light, "
        "no people, professional lifestyle photography"
    ),
    "転職": (
        "modern minimalist office desk with slim laptop, leather notebook and pen, "
        "city skyline through large window, morning sunlight, potted succulent, "
        "no people, architectural interior photography"
    ),
    "投資": (
        "clean office desk, financial planning notebook, laptop with upward chart, "
        "small potted plant, warm morning light, no people, professional photography"
    ),
    "キャリア": (
        "bright modern co-working space, large panoramic windows with city view, "
        "morning golden light, minimal organized workspace, no people, wide angle photography"
    ),
    "default": (
        "minimal clean workspace, laptop, coffee cup, open notebook, "
        "soft natural window light, calm productive atmosphere, no people, professional lifestyle photography"
    ),
}
STYLE = ", no text no letters no logos, no people no faces, professional editorial photography, 16:9"
NEGATIVE = "text, letters, alphabet, face, person, human, child, distorted, blurry, ugly, cartoon, watermark"


def _genre_key(genre: str) -> str:
    for k in PEXELS_QUERIES:
        if k != "default" and k in genre:
            return k
    return "default"


def _seed_from_title(title: str) -> int:
    h = 0
    for c in title:
        h = (h * 31 + ord(c)) & 0xFFFFFF
    return h % 9999 + 1


def _resize_crop(data: bytes, output_path: str):
    from PIL import Image
    import io
    img = Image.open(io.BytesIO(data)).convert("RGB")
    target_w, target_h = 1280, 670
    scale = max(target_w / img.width, target_h / img.height)
    new_w = int(img.width * scale)
    new_h = int(img.height * scale)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - target_w) // 2
    top  = (new_h - target_h) // 2
    img  = img.crop((left, top, left + target_w, top + target_h))
    img.save(output_path, "PNG")


# ────────────────────────────────────────────────
# 1st: Pexels API（高品質・要APIキー）
# ────────────────────────────────────────────────
def _pexels_generate(genre: str, seed: int, output_path: str) -> bool:
    api_key = os.environ.get("PEXELS_API_KEY", "")
    if not api_key:
        return False
    key     = _genre_key(genre)
    queries = PEXELS_QUERIES[key]
    query   = queries[seed % len(queries)]
    page    = (seed % 8) + 1
    url = (
        f"https://api.pexels.com/v1/search"
        f"?query={urllib.parse.quote(query)}&orientation=landscape&per_page=10&page={page}"
    )
    req = urllib.request.Request(url, headers={"Authorization": api_key})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
        photos = result.get("photos", [])
        if not photos:
            url2 = url.replace(f"page={page}", "page=1")
            with urllib.request.urlopen(
                urllib.request.Request(url2, headers={"Authorization": api_key}), timeout=15
            ) as resp2:
                result = json.loads(resp2.read())
            photos = result.get("photos", [])
        if not photos:
            print(f"  Pexels: 写真なし (query={query})")
            return False
        photo   = photos[seed % len(photos)]
        img_url = photo["src"].get("large2x") or photo["src"]["original"]
        print(f"  Pexels: {photo['photographer']}")
        with urllib.request.urlopen(
            urllib.request.Request(img_url, headers={"User-Agent": "Mozilla/5.0"}), timeout=30
        ) as img_resp:
            data = img_resp.read()
        if len(data) < 10000:
            return False
        _resize_crop(data, output_path)
        return True
    except Exception as e:
        print(f"  Pexels失敗: {type(e).__name__}: {e}")
        return False


# ────────────────────────────────────────────────
# 2nd: Unsplash直接URL（APIキー不要・内容に沿った厳選写真）
# ────────────────────────────────────────────────
# 各ジャンルに合った Unsplash 写真IDを事前選定
UNSPLASH_PHOTO_POOLS = {
    "英語学習（大人）": [
        "photo-1456735190827-d1262f71b8a3",  # 勉強机・本
        "photo-1501504905252-473c47e087f8",  # ノートブック・コーヒー
        "photo-1434030216411-0b793f4b4173",  # 勉強している人の手
        "photo-1488190211105-8b0e65b80b4e",  # 本・デスク
        "photo-1513258496099-48168024aec0",  # ノートPC・コーヒー
        "photo-1512621776951-a57141f2eefd",  # デスク・植物
    ],
    "英語学習（子ども）": [
        "photo-1503676260728-1c00da094a0b",  # 子どもの手・勉強
        "photo-1596495578065-6e0763fa1178",  # 子どもの学習
        "photo-1568667256549-094345857637",  # カラフルな文房具
        "photo-1497633762265-9d179a990aa6",  # 積み木・学習
    ],
    "英語学習": [
        "photo-1456735190827-d1262f71b8a3",
        "photo-1501504905252-473c47e087f8",
        "photo-1488190211105-8b0e65b80b4e",
    ],
    "転職": [
        "photo-1497366216548-37526070297c",  # モダンオフィス
        "photo-1497366412874-3415097a27e7",  # ガラス張りオフィス
        "photo-1497366811353-6870744d04b2",  # 会議室
        "photo-1486406146926-c627a92ad1ab",  # 都市のオフィスビル
    ],
    "キャリア": [
        "photo-1497366216548-37526070297c",
        "photo-1486406146926-c627a92ad1ab",
        "photo-1497366412874-3415097a27e7",
        "photo-1521737604893-d14cc237f11d",  # ビジネスミーティング
    ],
    "投資": [
        "photo-1611974789855-9c2a0a7236a3",  # グラフ・投資
        "photo-1611974789855-9c2a0a7236a3",
        "photo-1559526324-4b87b5e36e44",  # ラップトップ・数字
    ],
    "default": [
        "photo-1497366216548-37526070297c",
        "photo-1501504905252-473c47e087f8",
        "photo-1456735190827-d1262f71b8a3",
    ],
}

def _unsplash_generate(genre: str, seed: int, output_path: str) -> bool:
    """Unsplash直接URL - APIキー不要、内容に沿った事前選定写真"""
    key   = _genre_key(genre)
    pool  = UNSPLASH_PHOTO_POOLS.get(key, UNSPLASH_PHOTO_POOLS["default"])
    photo = pool[seed % len(pool)]
    url   = f"https://images.unsplash.com/{photo}?w=1280&h=720&fit=crop&auto=format&q=80"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read()
        if len(data) < 10000:
            return False
        _resize_crop(data, output_path)
        print(f"  Unsplash成功: {photo[:30]}")
        return True
    except Exception as e:
        print(f"  Unsplash失敗: {type(e).__name__}: {e}")
        return False


# ────────────────────────────────────────────────
# 3rd: LoremFlickr（無料・APIキー不要・キーワード検索）
# ────────────────────────────────────────────────
def _loremflickr_generate(genre: str, seed: int, output_path: str) -> bool:
    key      = _genre_key(genre)
    keywords = LOREMFLICKR_KEYWORDS.get(key, LOREMFLICKR_KEYWORDS["default"])
    # lock パラメータで記事ごとに同じ写真が返る
    url = f"https://loremflickr.com/1280/720/{keywords}?lock={seed}"
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; note-image-bot/1.0)"},
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read()
        if len(data) < 10000:
            print(f"  LoremFlickr: レスポンス小さすぎ")
            return False
        _resize_crop(data, output_path)
        print(f"  LoremFlickr成功: keywords={keywords} lock={seed}")
        return True
    except Exception as e:
        print(f"  LoremFlickr失敗: {type(e).__name__}: {e}")
        return False


# ────────────────────────────────────────────────
# 3rd: Pollinations.ai（AI生成・最大3回リトライ）
# ────────────────────────────────────────────────
SIMPLE_PROMPTS = {
    "英語学習（大人）": "study desk with open book globe coffee lamp, no people, professional photo",
    "英語学習（子ども）": "colorful wooden toy blocks pencils globe on bright desk, no people, flat lay",
    "キャリア":          "modern office desk laptop notebook city window, no people, clean minimal photo",
    "default":           "minimal clean workspace laptop coffee notebook, no people, professional photo",
}

def _pollinations_generate(genre: str, seed: int, output_path: str) -> bool:
    import time as _time
    neg = urllib.parse.quote(NEGATIVE)
    key = _genre_key(genre)
    for attempt in range(1, 4):
        prompt = (GENRE_VISUALS.get(key, GENRE_VISUALS["default"]) + STYLE) if attempt == 1 \
                 else SIMPLE_PROMPTS.get(genre, SIMPLE_PROMPTS["default"])
        cur_seed = seed if attempt == 1 else (seed + attempt * 1000) % 9999
        if attempt > 1:
            _time.sleep(5)
        encoded = urllib.parse.quote(prompt)
        url = (
            f"https://image.pollinations.ai/prompt/{encoded}"
            f"?width=1280&height=720&nologo=true&seed={cur_seed}"
            f"&model=flux-schnell&negative={neg}"
        )
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
            if len(data) < 5000:
                continue
            _resize_crop(data, output_path)
            print(f"  Pollinations成功（試行{attempt}）")
            return True
        except Exception as e:
            print(f"  Pollinations試行{attempt}失敗: {type(e).__name__}: {e}")
    return False


# ────────────────────────────────────────────────
# 4th: グラデーション fallback（改善版）
# ────────────────────────────────────────────────
def _gradient_fallback(genre: str, output_path: str):
    from PIL import Image, ImageDraw
    import math
    THEMES = {
        "英語学習（大人）": {"top": (8, 45, 85),    "bot": (40, 130, 210), "acc": (255, 200, 80)},
        "英語学習（子ども）":{"top": (20, 100, 60),  "bot": (80, 200, 120), "acc": (255, 220, 50)},
        "英語学習":          {"top": (8, 45, 85),    "bot": (40, 130, 210), "acc": (255, 200, 80)},
        "転職":              {"top": (15, 45, 25),   "bot": (35, 140, 80),  "acc": (180, 255, 120)},
        "キャリア":          {"top": (20, 30, 80),   "bot": (60, 80, 200),  "acc": (100, 220, 255)},
        "投資":              {"top": (50, 30, 10),   "bot": (180, 100, 20), "acc": (255, 200, 50)},
        "default":           {"top": (15, 30, 60),   "bot": (40, 100, 180), "acc": (100, 200, 255)},
    }
    key = next((k for k in THEMES if k in genre), "default")
    t = THEMES[key]
    top, bot, acc = t["top"], t["bot"], t["acc"]
    W, H = 1280, 670
    img = Image.new("RGB", (W, H))
    draw = ImageDraw.Draw(img)
    for y in range(H):
        for x in range(0, W, 4):
            r_t = (y / H * 0.6 + x / W * 0.4)
            r = int(top[0] + (bot[0]-top[0]) * r_t)
            g = int(top[1] + (bot[1]-top[1]) * r_t)
            b = int(top[2] + (bot[2]-top[2]) * r_t)
            draw.rectangle([(x, y), (x+3, y)], fill=(r, g, b))
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.ellipse([(W-450, H-300), (W+100, H+100)], fill=(acc[0], acc[1], acc[2], 35))
    od.ellipse([(-100, -100), (300, 300)], fill=(255, 255, 255, 15))
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    ImageDraw.Draw(img).rectangle([(0, 0), (W, 3)], fill=acc)
    img.save(output_path, "PNG")
    print(f"  グラデーションfallback: {output_path}")


# ────────────────────────────────────────────────
# メインエントリ
# ────────────────────────────────────────────────
def generate(title: str, genre: str, output_path: str):
    seed = _seed_from_title(title)
    print(f"  画像生成: genre={genre} seed={seed}")

    # 1st: Pexels
    if os.environ.get("PEXELS_API_KEY"):
        print("  [Pexels] 取得中...")
        if _pexels_generate(genre, seed, output_path):
            print(f"  画像生成完了(Pexels): {output_path}")
            return

    # 2nd: Unsplash直接URL（内容に沿った厳選写真）
    print("  [Unsplash] 取得中...")
    if _unsplash_generate(genre, seed, output_path):
        print(f"  画像生成完了(Unsplash): {output_path}")
        return

    # 3rd: LoremFlickr（キーワード検索）
    print("  [LoremFlickr] 取得中...")
    if _loremflickr_generate(genre, seed, output_path):
        print(f"  画像生成完了(LoremFlickr): {output_path}")
        return

    # 3rd: Pollinations
    print("  [Pollinations] 生成中...")
    if _pollinations_generate(genre, seed, output_path):
        print(f"  画像生成完了(Pollinations): {output_path}")
        return

    # 4th: Gradient
    print("  [Fallback] グラデーション")
    _gradient_fallback(genre, output_path)
    print(f"  画像生成完了(fallback): {output_path}")


if __name__ == "__main__":
    import tempfile
    t = Path(tempfile.gettempdir())
    generate("英会話スクール2年で上達しなかった理由", "英語学習（大人）", str(t / "test_adult.png"))
    generate("子どものオンライン英会話、失敗しない選び方", "英語学習（子ども）", str(t / "test_kids.png"))
    generate("外資系10年でキャリアに迷った話", "キャリア", str(t / "test_career.png"))
