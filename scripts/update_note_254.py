#!/usr/bin/env python3
"""note.com 記事254を内容・リンク修正で更新するスクリプト（一回限り）"""
import os
import json
import base64
import re
from pathlib import Path

NOTE_COOKIES_B64  = os.environ.get('NOTE_COOKIES', '')
NOTE_EMAIL        = os.environ.get('NOTE_EMAIL', '')
NOTE_PASSWORD     = os.environ.get('NOTE_PASSWORD', '')
NOTE_USERNAME     = os.environ.get('NOTE_USERNAME', 'english_gaishi')

BASE_DIR          = Path.home() / 'Documents' / '01_ClaudeCode' / 'x-affiliate'
LOCAL_COOKIE_PATH = BASE_DIR / 'note_cookies.json'
SCREENSHOT_DIR    = BASE_DIR / 'logs'

NOTE_KEY = 'nd2c14f3d5718'
EDIT_URL = f'https://note.com/notes/{NOTE_KEY}/edit'

TITLE = "ESPPを売却したら確定申告が必要だと知らなかった。外資系IT社員が初めてやった計算手順と失敗談"

CONTENT = """\
ESPPを最初に売却した年の2月末、同期から「ESPP売った分、確定申告した？」と聞かれた。

なんとなく「源泉徴収されているはず」と思っていたので、「してないけど大丈夫じゃない？」と軽く答えた。そのあとの同期の顔が今でも忘れられない。「え、まずいよ。うちのESPPは特定口座に入らないから、自分で申告が必要なんだよ」

気づいたのが2月末で、確定申告の期限は3月15日。そこから初めてESPPの確定申告というものに向き合った。

## ESPPは「購入時」と「売却時」の2段階で課税される

外資系IT企業に勤めると、ほぼ標準的に提供されているESPP（Employee Stock Purchase Plan）。一定期間の給与から積み立てをして、市場価格より安く（多くは15%ディスカウント）自社株を購入できる制度だ。

税務上の課税は2段階で発生する。これを最初に知らなかったのが失敗だった。

**購入時：ディスカウント分が給与所得として課税される**
ESPPで株を購入した時点で、「市場価格 − 実際の購入価格」の差額（ディスカウント部分）が「経済的利益」として給与所得に加算される。たとえば市場価格100ドルの株を85ドルで買った場合、15ドル×株数が給与所得扱いになる。外資系企業の場合、この購入時の課税を日本の源泉徴収で処理していないケースが多いため、自分で確定申告する必要がある。

**売却時：値上がり益が譲渡所得として課税される**
購入後に株価が上がった場合、「売却価格 − 購入時の市場価格」の差額が株式等の譲渡所得（申告分離課税・税率20.315%）になる。取得価額は「実際に支払った購入価格（ディスカウント後）」ではなく、「購入時の市場価格」を使う点に注意が必要だ。

つまり、ESPPを購入して売却する場合、購入時（給与所得）と売却時（譲渡所得）の2回、異なる種類の所得として課税イベントが発生する。

[マネーフォワード クラウド確定申告で購入時・売却時の所得を整理する（PR）](https://px.a8.net/svt/ejp?a8mat=4BA6D9+1ZG256+4JGQ+BX3J6)

## 購入時課税を知らなかったことによる失敗

ESPPで株を購入した最初の年、自分は「購入しただけだから申告不要」と思っていた。売却もしていないし、現金は出て行く一方だった。しかしその年の確定申告シーズンに、購入時のディスカウント分が給与所得として申告が必要だと知った。

会社のHR担当に聞いたところ、「購入時の課税については社員自身で確定申告してほしい」と言われた。給与明細にも記載がなく、知らなければそのまま見過ごしていた可能性が高い。

購入時の申告が漏れていると、税務署から後日問い合わせが来るリスクがある。自主的に修正申告すれば加算税が軽減されるが、放置するとペナルティが重くなる。

## 外貨建て所得の円換算

外資系のESPPは株価が米ドル建てのことがほとんどだ。購入時・売却時ともに円換算が必要になる。

国税庁のルールでは：
- **購入時（給与所得の計算）**：購入した日の「TTBレート（対顧客電信買相場）」で市場価格を円換算し、ディスカウント額を算出
- **売却時（譲渡所得の計算）**：売却した日のTTBレートで売却収入を円換算
- **取得価額（譲渡所得の計算に使う）**：購入時の市場価格を購入日のTTBレートで円換算した金額

TTBレートは三菱UFJ銀行や三井住友銀行のウェブサイトで過去のレートを確認できる。複数年にわたってESPPを保有している場合、取引日ごとのレートを遡って調べる作業が必要になる。

## マネーフォワードで購入時・売却時を両方管理した

最初の年はExcelで手計算したが、2年目からマネーフォワード クラウド確定申告を使うようになった。

購入時の給与所得（ディスカウント分）と売却時の譲渡所得を、それぞれ対応する入力画面で管理できる。外貨建ての取引は取引日と為替レートを入力すると自動で円換算される。申告書の「給与所得の内訳」欄と「株式等に係る譲渡所得等の計算明細書」への反映も自動だ。

一番助かったのは、購入年と売却年が異なる場合のデータ管理だ。購入時の市場価格（取得価額として使う）の記録がマネーフォワードに残るので、翌年以降の売却時に「購入時の価格はいくらだったか」を遡る手間が省ける。

[マネーフォワード クラウド確定申告——ESPPの取得価額・譲渡所得を自動計算（PR）](https://px.a8.net/svt/ejp?a8mat=4BA6D9+1ZG256+4JGQ+BX3J6)

## 3月に焦らないために今やっておくこと

外資系IT企業に勤めていてESPPを持っているなら、毎年のチェックポイントはこの2つだ。

まず「購入した年」：ディスカウント分の給与所得が確定申告が必要かを確認する（20万円超なら申告義務あり）。会社が源泉徴収しているかどうかを給与明細とHRに確認する。次に「売却した年」：売却価格と購入時の市場価格の差額が譲渡所得になる。特定口座に入っていない場合は自分で申告する。

どちらの年も、証券会社の取引明細と各取引日のTTBレートの記録を保管しておくことが重要だ。記録が残っていれば、ツールを使えば申告書の作成は対応できる。

損失が出た年も申告しておくことをすすめたい。譲渡損は翌年以降3年間の繰越控除が使えるため、損失年を申告しないと繰越控除の権利が失われる。利益が出た年だけでなく、損失が出た年も申告を習慣にしておくことが長期的に合理的な選択だ。

---

ESPPの確定申告を初めてやる人、購入時課税を知らずに見過ごしてきた人に、一度ツールで整理することをすすめたい。

[マネーフォワード クラウド確定申告——購入時・売却時の外貨建て所得も自動計算（PR）](https://px.a8.net/svt/ejp?a8mat=4BA6D9+1ZG256+4JGQ+BX3J6)\
"""


def load_storage_state():
    if NOTE_COOKIES_B64:
        decoded = base64.b64decode(NOTE_COOKIES_B64.encode('ascii')).decode('utf-8')
        return json.loads(decoded)
    if LOCAL_COOKIE_PATH.exists():
        with open(LOCAL_COOKIE_PATH, encoding='utf-8') as f:
            return json.load(f)
    return None


def ss(page, name):
    SCREENSHOT_DIR.mkdir(exist_ok=True)
    try:
        page.screenshot(path=str(SCREENSHOT_DIR / f'update_{name}.png'))
    except Exception:
        pass


def insert_content_with_ogp(page, content):
    LINK_PATTERN = re.compile(r'\[([^\]]+)\]\((https?://[^)]+)\)')
    lines = content.split('\n')
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('https://') or stripped.startswith('http://'):
            page.keyboard.type(stripped, delay=3)
            page.keyboard.press('Enter')
            page.wait_for_timeout(2500)
        elif LINK_PATTERN.search(line):
            parts = LINK_PATTERN.split(line)
            i = 0
            while i < len(parts):
                r = i % 3
                chunk = parts[i]
                if r == 0:
                    if chunk:
                        page.keyboard.type(chunk, delay=8)
                elif r == 1:
                    link_text = chunk
                    link_url = parts[i + 1]
                    page.keyboard.type(link_text, delay=8)
                    page.keyboard.press('Enter')
                    page.keyboard.type(link_url, delay=3)
                    page.keyboard.press('Enter')
                    page.wait_for_timeout(2500)
                    i += 1
                i += 1
            page.wait_for_timeout(30)
        else:
            if line:
                page.keyboard.type(line, delay=8)
            page.keyboard.press('Enter')
            page.wait_for_timeout(30)


def main():
    from playwright.sync_api import sync_playwright

    storage_state = load_storage_state()
    if not storage_state:
        print("Cookie情報がありません。note_cookies.json を確認してください。")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,  # デバッグ用に有効化
            args=['--no-sandbox']
        )
        ctx_kwargs = dict(
            user_agent=(
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/124.0.0.0 Safari/537.36'
            ),
            viewport={'width': 1280, 'height': 900},
        )
        ctx_kwargs['storage_state'] = storage_state
        context = browser.new_context(**ctx_kwargs)
        page = context.new_page()

        try:
            print(f"[1] 編集ページへ移動: {EDIT_URL}")
            page.goto(EDIT_URL, wait_until='networkidle', timeout=30000)
            page.wait_for_timeout(5000)
            ss(page, '01_edit_page')
            print(f"  現在URL: {page.url}")

            if 'login' in page.url:
                print("[ログイン] メール・パスワードでログイン...")
                if NOTE_EMAIL and NOTE_PASSWORD:
                    page.fill('input[type="email"]', NOTE_EMAIL)
                    page.fill('input[type="password"]', NOTE_PASSWORD)
                    page.click('button[type="submit"]')
                    page.wait_for_load_state('networkidle', timeout=20000)
                    page.goto(EDIT_URL, wait_until='networkidle', timeout=30000)
                    page.wait_for_timeout(5000)
                else:
                    raise RuntimeError("認証情報なし")

            # タイトルエリアをクリア
            print("[2] タイトルをクリア・再入力...")
            title_cleared = page.evaluate("""
                () => {
                    const sels = [
                        '[data-placeholder="タイトル"]',
                        '[placeholder*="タイトル"]',
                        'h1[contenteditable="true"]',
                        '[class*="title"][contenteditable]',
                    ];
                    for (const s of sels) {
                        const el = document.querySelector(s);
                        if (el) {
                            el.focus();
                            el.click();
                            return s;
                        }
                    }
                    return null;
                }
            """)
            print(f"  タイトルセレクタ: {title_cleared}")
            page.wait_for_timeout(300)
            page.keyboard.press('Control+a')
            page.wait_for_timeout(200)
            page.keyboard.press('Delete')
            page.wait_for_timeout(200)
            page.keyboard.type(TITLE, delay=15)
            ss(page, '02_title')
            print(f"  タイトル入力完了")

            # 本文エリアをすべて選択して削除
            print("[3] 本文をすべて選択して削除...")
            try:
                page.click('.ProseMirror', timeout=5000)
            except Exception:
                els = page.locator('[contenteditable="true"]').all()
                if len(els) > 1:
                    els[-1].click()
            page.wait_for_timeout(500)
            page.keyboard.press('Control+a')
            page.wait_for_timeout(300)
            page.keyboard.press('Delete')
            page.wait_for_timeout(500)
            ss(page, '03_cleared')
            print("  本文クリア完了")

            # 新しい内容を入力
            print("[4] 修正済み本文を入力...")
            insert_content_with_ogp(page, CONTENT)
            ss(page, '04_content')
            print("  本文入力完了")

            # 保存・更新ボタンをクリック
            print("[5] 更新ボタンをクリック...")
            page.wait_for_timeout(2000)
            for sel in [
                'button:has-text("公開に進む")',
                'button:has-text("更新する")',
                'button:has-text("保存する")',
                'button:has-text("公開設定")',
            ]:
                try:
                    page.click(sel, timeout=5000)
                    print(f"  クリック: {sel}")
                    break
                except Exception:
                    continue
            page.wait_for_timeout(3000)
            ss(page, '05_modal')

            # モーダルが出た場合「投稿する」または「更新する」を押す
            for sel in [
                'button:has-text("投稿する")',
                'button:has-text("更新する")',
                'button:has-text("変更を保存する")',
            ]:
                try:
                    page.click(sel, timeout=5000)
                    print(f"  確定クリック: {sel}")
                    break
                except Exception:
                    continue
            page.wait_for_timeout(8000)
            ss(page, '06_after_update')
            print(f"  現在URL: {page.url}")

            # /publish/ ページに留まっている場合、追加ボタンを探す
            if '/publish/' in page.url or '/edit/' in page.url:
                print("  /publish/ ページ検査中...")
                pub_buttons = page.locator('button').all()
                btn_texts = []
                for b in pub_buttons[:20]:
                    try:
                        t = b.inner_text().strip()
                        if t:
                            btn_texts.append(t)
                    except Exception:
                        pass
                print(f"  ボタン一覧: {btn_texts}")

                for sel in [
                    'button:has-text("投稿する")',
                    'button:has-text("更新する")',
                    'button:has-text("変更を保存")',
                    'button:has-text("クリエイターページに表示")',
                    'button:has-text("記事を見る")',
                    'button:has-text("完了")',
                ]:
                    try:
                        page.click(sel, timeout=3000)
                        page.wait_for_timeout(4000)
                        print(f"  追加クリック: {sel}")
                        break
                    except Exception:
                        continue

                ss(page, '07_final')
                print(f"  最終URL: {page.url}")

            print("\n=== 更新完了 ===")
            page.wait_for_timeout(3000)

        except Exception as e:
            ss(page, 'error')
            print(f"[ERROR] {type(e).__name__}: {e}")
            import traceback; traceback.print_exc()
        finally:
            browser.close()


if __name__ == '__main__':
    main()
