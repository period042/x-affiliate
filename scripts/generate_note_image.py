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
    "英語留学":          ["tropical study abroad cafe laptop", "beach study english notebook", "overseas learning workspace"],
    "英語会話":          ["conversation coffee shop friends talking", "cafe discussion notebook table", "speaking practice workspace"],
    "転職":              ["modern office desk city view", "professional workspace laptop window", "career office morning business"],
    "IT転職":            ["coding laptop dark desk", "software developer workspace dual monitor", "tech office modern workspace"],
    "投資":              ["laptop finance desk notebook", "business planning office desk", "professional minimal desk"],
    "資産":              ["financial planning notebook calculator", "investment desk morning light", "money management workspace"],
    "FP相談":            ["financial advisor desk consultation", "planning documents calculator coffee", "insurance finance paperwork desk"],
    "固定費":            ["home utility bills calculator desk", "solar panel roof blue sky", "energy saving house exterior"],
    "太陽光":            ["solar panels rooftop blue sky", "renewable energy house roof", "clean energy solar installation"],
    "住宅":              ["modern house exterior architecture", "japanese home interior living room", "new build house wooden frame"],
    "工務店":            ["construction blueprint desk tools", "architectural model house wooden", "building site workers"],
    "婚活":              ["couple coffee date cafe window", "romantic evening restaurant candles", "flowers ring bouquet white"],
    "キャリア":          ["modern office workspace", "professional desk career", "business office morning light"],
    "default":           ["minimal workspace desk laptop", "clean desk coffee notebook", "modern home office light",
                          "productivity workspace morning", "desk setup plant window light", "cozy office corner"],
}

LOREMFLICKR_KEYWORDS = {
    "英語学習（大人）": "studying,notebook",
    "英語学習（子ども）": "children,learning",
    "英語学習":          "books,notebook",
    "英語留学":          "travel,studying",
    "英語会話":          "conversation,cafe",
    "転職":              "office,laptop",
    "IT転職":            "coding,laptop",
    "投資":              "laptop,business",
    "資産":              "finance,planning",
    "FP相談":            "finance,documents",
    "固定費":            "house,energy",
    "太陽光":            "solar,energy",
    "住宅":              "house,architecture",
    "工務店":            "construction,building",
    "婚活":              "couple,flowers",
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
    "英語留学": (
        "tropical cafe terrace with open notebook and coffee, palm trees in background, "
        "bright sunny day, overseas study abroad atmosphere, no people, travel lifestyle photography"
    ),
    "英語会話": (
        "cozy cafe interior, two empty chairs facing each other across a small wooden table, "
        "notebooks and coffee cups, warm afternoon light through window, no people, soft focus"
    ),
    "IT転職": (
        "sleek dual-monitor developer workstation, dark desk with soft ambient lighting, "
        "mechanical keyboard, small succulent plant, city view at night through window, no people, tech photography"
    ),
    "資産管理・FP相談": (
        "clean financial planning desk, leather notebook with pen, calculator, "
        "small stack of documents, potted plant, morning light, no people, professional photography"
    ),
    "資産": (
        "clean financial planning desk, leather notebook with pen, calculator, "
        "small stack of documents, potted plant, morning light, no people, professional photography"
    ),
    "FP相談": (
        "financial consultation desk, neat documents and calculator, coffee mug, "
        "warm office light, no people, professional interior photography"
    ),
    "固定費削減・太陽光": (
        "suburban house rooftop with modern solar panels, bright blue sky with few clouds, "
        "green garden below, clean renewable energy aesthetic, no people, architectural photography"
    ),
    "固定費": (
        "suburban house rooftop with modern solar panels, bright blue sky with few clouds, "
        "green garden below, clean renewable energy aesthetic, no people, architectural photography"
    ),
    "太陽光": (
        "suburban house rooftop with modern solar panels, bright blue sky with few clouds, "
        "green garden below, clean renewable energy aesthetic, no people, architectural photography"
    ),
    "住宅・工務店選び": (
        "bright modern Japanese living room interior, wooden flooring, large windows with garden view, "
        "minimal furniture, warm morning light, no people, interior design photography"
    ),
    "住宅": (
        "bright modern Japanese living room interior, wooden flooring, large windows with garden view, "
        "minimal furniture, warm morning light, no people, interior design photography"
    ),
    "工務店": (
        "construction blueprint spread on wooden desk, small architectural house model, "
        "measuring tools, warm office light, no people, professional photography"
    ),
    "婚活": (
        "elegant cafe table with two coffee cups and small flower vase, "
        "warm bokeh background lights, romantic soft light, no people, lifestyle photography"
    ),
    "婚活・自分磨き": (
        "modern bathroom vanity with skincare products arranged neatly, "
        "clean minimal aesthetic, soft white light, no people, product lifestyle photography"
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
        "photo-1471970394675-613138e45da3",  # 本の山
        "photo-1455390582262-044cdead277a",  # ノートと鉛筆
    ],
    "英語学習（子ども）": [
        "photo-1503676260728-1c00da094a0b",
        "photo-1596495578065-6e0763fa1178",
        "photo-1568667256549-094345857637",
        "photo-1497633762265-9d179a990aa6",
    ],
    "英語学習": [
        "photo-1456735190827-d1262f71b8a3",
        "photo-1501504905252-473c47e087f8",
        "photo-1488190211105-8b0e65b80b4e",
        "photo-1434030216411-0b793f4b4173",
        "photo-1513258496099-48168024aec0",
        "photo-1471970394675-613138e45da3",
    ],
    "英語留学": [
        "photo-1527631746610-bca00a040d60",  # 海外旅行・カフェ
        "photo-1436491865332-7a61a109cc05",  # 飛行機窓
        "photo-1507608616759-54f48f0af0ee",  # 旅行・ノート
        "photo-1488085061387-422e29b40080",  # 旅行計画
        "photo-1530789253388-582c481c54b0",  # トロピカルカフェ
        "photo-1473496169904-658ba7574b0d",  # 海外デスク
    ],
    "英語会話": [
        "photo-1521737604893-d14cc237f11d",  # ビジネス会話
        "photo-1543269664-56d93c1b41a6",  # カフェ会話
        "photo-1522071820081-009f0129c71c",  # チームディスカッション
        "photo-1529156069898-49953e39b3ac",  # カジュアル会話
        "photo-1600880292203-757bb62b4baf",  # オフィス会話
        "photo-1557804506-669a67965ba0",  # コーヒー会議
    ],
    "転職": [
        "photo-1497366216548-37526070297c",
        "photo-1497366412874-3415097a27e7",
        "photo-1497366811353-6870744d04b2",
        "photo-1486406146926-c627a92ad1ab",
        "photo-1568992687947-868a62a9f521",  # モダンオフィスロビー
        "photo-1541746972996-4e0b0f43e02a",  # ラップトップ・仕事
        "photo-1507679799987-c73779587ccf",  # ビジネスマン
        "photo-1454165804606-c3d57bc86b40",  # デスク・書類
    ],
    "IT転職": [
        "photo-1461749280684-dccba630e2f6",  # コーディング画面
        "photo-1498050108023-c5249f4df085",  # ラップトップ・コード
        "photo-1555066931-4365d14bab8c",  # ダークテーマコード
        "photo-1484417894907-623942c8ee29",  # プログラミング
        "photo-1587620962725-abab7fe55159",  # デュアルモニター
        "photo-1517134191118-9d595e4c8c2b",  # 開発者デスク
        "photo-1526374965328-7f61d4dc18c5",  # コード画面
        "photo-1542831371-29b0f74f9713",  # モダン開発環境
    ],
    "投資": [
        "photo-1611974789855-9c2a0a7236a3",
        "photo-1559526324-4b87b5e36e44",
        "photo-1460925895917-afdab827c52f",  # グラフ・分析
        "photo-1579621970563-ebec7560ff3e",  # 投資・計算
        "photo-1611532736597-de2d4265fba3",  # 株式チャート
        "photo-1567427017947-545c5f8d16ad",  # 財務計画
    ],
    "資産": [
        "photo-1579621970563-ebec7560ff3e",  # 投資・計算
        "photo-1554224155-6726b3ff858f",  # 財務書類
        "photo-1565514020179-026b92b84bb6",  # 資産管理
        "photo-1567427017947-545c5f8d16ad",  # 財務計画
        "photo-1460925895917-afdab827c52f",
        "photo-1611974789855-9c2a0a7236a3",
    ],
    "FP相談": [
        "photo-1554224155-6726b3ff858f",  # 財務書類
        "photo-1579621970563-ebec7560ff3e",
        "photo-1450101499163-c8848c66ca85",  # ビジネス相談
        "photo-1507003211169-0a1dd7228f2d",  # 相談・打ち合わせ
        "photo-1565514020179-026b92b84bb6",
        "photo-1527689638836-411945a2b57c",  # 書類・ペン
    ],
    "固定費": [
        "photo-1564013799919-ab600027ffc6",  # 家の外観
        "photo-1509391366360-2e959784a276",  # ソーラーパネル
        "photo-1558618666-fcd25c85cd64",  # 電球・エネルギー
        "photo-1467533003447-e295ff1b0435",  # 節電・家
        "photo-1580745294621-9df9fe4f26a0",  # エコハウス
        "photo-1513828583688-c52646db42da",  # 電気代・領収書
    ],
    "太陽光": [
        "photo-1509391366360-2e959784a276",  # ソーラーパネル
        "photo-1497435334941-8c899a9bd1b8",  # 太陽光発電
        "photo-1592833159057-4e9dda8b9afc",  # 屋根のパネル
        "photo-1595437193398-f24279553f4f",  # 再生可能エネルギー
        "photo-1548605877-b4e9eddcfef9",  # 緑のエネルギー
        "photo-1466611653911-95081537e5b7",  # 風景・太陽
    ],
    "住宅": [
        "photo-1564013799919-ab600027ffc6",  # モダンな家
        "photo-1493809842364-78817add7ffb",  # リビングルーム
        "photo-1484154218962-a197022b5858",  # キッチン
        "photo-1560185127-6ed189bf02f4",  # 明るいリビング
        "photo-1555041469-a586c61ea9bc",  # インテリア
        "photo-1531971589569-0d9370cbe1e5",  # 日本的な家
        "photo-1583608205776-bfd35f0d9f83",  # 家の外観
        "photo-1570129477492-45c003edd2be",  # 住宅街
    ],
    "工務店": [
        "photo-1504307651254-35680f356dfd",  # 建設現場
        "photo-1581094794329-c8112a89af12",  # 建設中の家
        "photo-1590725140246-20acdee442be",  # 設計図・建築
        "photo-1541888946425-d81bb19240f5",  # 建築模型
        "photo-1565008447742-97f6f38c985c",  # 木造建築
        "photo-1503387762-592deb58ef4e",  # 青写真
    ],
    "婚活": [
        "photo-1529636798458-92182e662485",  # カフェ・テーブル
        "photo-1523438885200-e635ba2c371e",  # 花束
        "photo-1518199266791-5375a83190b7",  # ロマンティックカフェ
        "photo-1444653614773-995cb1ef9efa",  # 結婚指輪
        "photo-1478146059778-26885cf4e15e",  # ウェディング装飾
        "photo-1500673922987-e212871fec22",  # ライト・ロマンティック
        "photo-1516589178581-6cd7833ae3b2",  # カップル向けカフェ
        "photo-1511285560929-80b456fea0bc",  # 夕日・ロマンティック
    ],
    "キャリア": [
        "photo-1497366216548-37526070297c",
        "photo-1486406146926-c627a92ad1ab",
        "photo-1497366412874-3415097a27e7",
        "photo-1521737604893-d14cc237f11d",
        "photo-1568992687947-868a62a9f521",
        "photo-1507679799987-c73779587ccf",
    ],
    "default": [
        "photo-1497366216548-37526070297c",
        "photo-1501504905252-473c47e087f8",
        "photo-1456735190827-d1262f71b8a3",
        "photo-1484417894907-623942c8ee29",
        "photo-1512621776951-a57141f2eefd",
        "photo-1454165804606-c3d57bc86b40",
        "photo-1488190211105-8b0e65b80b4e",
        "photo-1558618666-fcd25c85cd64",
        "photo-1486406146926-c627a92ad1ab",
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
