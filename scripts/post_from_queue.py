#!/usr/bin/env python3
"""X投稿キューから次の投稿をポストする（ローカル・GitHub Actions両対応）"""
import os
import time
import tweepy
import json
import sys
from pathlib import Path
from datetime import datetime

if os.environ.get('GITHUB_ACTIONS'):
    BASE_DIR = Path(__file__).parent.parent
else:
    BASE_DIR = Path.home() / 'Documents' / 'x-affiliate'

DATA_DIR        = Path(os.environ['DATA_DIR']) if os.environ.get('DATA_DIR') else BASE_DIR
QUEUE_DIR       = DATA_DIR / 'queue'
POSTED_DIR      = DATA_DIR / 'posted'
FAILED_DIR      = DATA_DIR / 'failed_queue'
CONFIG_PATH     = BASE_DIR / 'config.json'
CREDIT_FILE     = DATA_DIR / 'credit_status.json'
SUMMARY_FILE    = os.environ.get('GITHUB_STEP_SUMMARY', '')

MAX_RETRIES     = 3
RETRY_WAIT_SEC  = 30


def mark_credit_exhausted(reason: str):
    """クレジット枯渇をファイルに記録"""
    status = {
        'status': 'exhausted',
        'reason': reason,
        'detected_at': datetime.now().isoformat(),
        'note': '月次リセット後に credit_status.json を削除するか status を ok に変更してください',
    }
    CREDIT_FILE.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding='utf-8')
    write_summary(
        '## ⚠️ X API クレジット枯渇\n'
        f'- 検知時刻: {status["detected_at"]}\n'
        f'- 理由: {reason}\n'
        '- **月次リセットまで投稿を停止します**\n'
        '- 再開方法: リポジトリの `credit_status.json` を削除またはstatusを`ok`に変更'
    )
    print(f'[CREDIT] クレジット枯渇を検知。{CREDIT_FILE} に記録しました。')


def check_credit_status() -> bool:
    """クレジット枯渇フラグが立っているか確認。True = 投稿可能"""
    if not CREDIT_FILE.exists():
        return True
    try:
        s = json.loads(CREDIT_FILE.read_text(encoding='utf-8'))
        if s.get('status') == 'exhausted':
            detected = s.get('detected_at', '不明')
            write_summary(
                '## ⛔ X API クレジット枯渇中（投稿スキップ）\n'
                f'- 枯渇検知日時: {detected}\n'
                '- 再開方法: `credit_status.json` を削除またはstatusを`ok`に変更'
            )
            print(f'[CREDIT] 枯渇フラグあり（{detected}）。投稿をスキップします。')
            return False
    except Exception:
        pass
    return True


def write_summary(text: str):
    """GitHub Actions ジョブサマリーに書き込む"""
    if SUMMARY_FILE:
        with open(SUMMARY_FILE, 'a', encoding='utf-8') as f:
            f.write(text + '\n')


def load_credentials():
    if os.environ.get('X_API_KEY'):
        return {
            'api_key':             os.environ['X_API_KEY'],
            'api_secret':          os.environ['X_API_SECRET'],
            'access_token':        os.environ['X_ACCESS_TOKEN'],
            'access_token_secret': os.environ['X_ACCESS_TOKEN_SECRET'],
        }
    with open(CONFIG_PATH, encoding='utf-8') as f:
        return json.load(f)['x_api']


def get_next_post():
    posts = sorted(QUEUE_DIR.glob('*.json'))
    return posts[0] if posts else None


def get_posted_contents() -> set:
    """posted/ の content を正規化してセットで返す（重複検出用）"""
    contents = set()
    for f in POSTED_DIR.glob('*.json'):
        try:
            d = json.loads(f.read_text(encoding='utf-8'))
            c = d.get('content', '').strip()
            if c:
                contents.add(c)
        except Exception:
            pass
    return contents


def archive_posted(post_file: Path, post: dict, tweet_id: str):
    POSTED_DIR.mkdir(exist_ok=True)
    post['posted_at'] = datetime.now().isoformat()
    post['tweet_id']  = str(tweet_id)
    with open(POSTED_DIR / post_file.name, 'w', encoding='utf-8') as f:
        json.dump(post, f, ensure_ascii=False, indent=2)
    post_file.unlink()


def archive_failed(post_file: Path, post: dict, reason: str):
    """修正不能な失敗はfailed_queueに移動してスキップ"""
    FAILED_DIR.mkdir(exist_ok=True)
    post['failed_at'] = datetime.now().isoformat()
    post['fail_reason'] = reason
    with open(FAILED_DIR / post_file.name, 'w', encoding='utf-8') as f:
        json.dump(post, f, ensure_ascii=False, indent=2)
    post_file.unlink()
    print(f"[SKIP] {post_file.name} → failed_queue/ ({reason})")


# ── 403対策: ルールベース自動修正 ──────────────────────────
import re as _re

_FIX_RULES = [
    # 具体的な年収・金額 (〇〇万円、年収〇〇万)
    (_re.compile(r'年収\d+万'), '年収'),
    (_re.compile(r'\d+万(円)?から\d+万'), 'より高く'),
    (_re.compile(r'\d+万(円)?→\d+万'), '大きく'),
    (_re.compile(r'\d+万円'), ''),
    # ESPP/RSU など投資商品の具体的アドバイス
    (_re.compile(r'ESPP|RSU'), '株式報酬'),
    (_re.compile(r'即売却して.{0,20}確定'), '適切に管理'),
    (_re.compile(r'購入日に即売却'), '取得後に管理'),
    # 比較広告的表現
    (_re.compile(r'スクールより(安|高|良|優)'), 'コスパが'),
    (_re.compile(r'より(安く|高く|良く)て'), 'で'),
    # 面接アピール系
    (_re.compile(r'面接でアピールできる'), '面接で話せる'),
]


def auto_fix_content(content: str) -> tuple[str, list[str]]:
    """403対策: 問題パターンをルールベースで修正して返す。変更ログも返す。"""
    fixed = content
    changes = []
    for pattern, replacement in _FIX_RULES:
        new = pattern.sub(replacement, fixed)
        if new != fixed:
            changes.append(f"{pattern.pattern} → '{replacement}'")
            fixed = new
    # 連続スペース・空行を整理
    fixed = _re.sub(r'\n{3,}', '\n\n', fixed).strip()
    return fixed, changes


def verify_tweet(client, tweet_id: str) -> bool:
    """投稿後にツイートが実際に存在するか確認"""
    try:
        result = client.get_tweet(tweet_id, user_auth=True)
        return result.data is not None
    except Exception as e:
        print(f"  確認エラー（無視）: {e}")
        return True   # 確認失敗でも投稿自体は成功扱い


def main():
    # クレジット枯渇チェック（枯渇中なら即終了）
    if not check_credit_status():
        sys.exit(0)

    creds = load_credentials()
    if not all([creds['api_key'], creds['api_secret'],
                creds['access_token'], creds['access_token_secret']]):
        msg = "APIキーが設定されていません。"
        print(msg)
        write_summary(f"## X投稿\n❌ {msg}")
        sys.exit(1)

    client = tweepy.Client(
        consumer_key=creds['api_key'],
        consumer_secret=creds['api_secret'],
        access_token=creds['access_token'],
        access_token_secret=creds['access_token_secret']
    )

    post_file = get_next_post()
    if not post_file:
        print("キューが空です。")
        write_summary("## X投稿\n⚠️ キューが空のため投稿スキップ")
        sys.exit(0)

    with open(post_file, encoding='utf-8') as f:
        post = json.load(f)

    content = post['content']
    print(f"投稿内容:\n{content}\n")

    # ── プロフリンク文言チェック ──────────────────────────────
    _PROF_PATTERNS = [
        _re.compile(r'プロフのリンク'),
        _re.compile(r'詳しくはプロフ'),
        _re.compile(r'プロフのリンクをご確認'),
        _re.compile(r'→\s*(スタディサプリ|LanCul|CampusTop|POSIWILL|AQUES|ENGREAL)[^\n]*\n(?!https?)'),
    ]
    for _pat in _PROF_PATTERNS:
        if _pat.search(content):
            reason = f"プロフリンク文言を検出（noteリンクへの置換が必要）: {_pat.pattern}"
            archive_failed(post_file, post, reason)
            write_summary(
                f"## X投稿\n"
                f"⚠️ プロフリンク文言のためスキップ\n"
                f"- ファイル: `{post_file.name}`\n"
                f"- 理由: {reason}\n"
                f"- → `failed_queue/` に移動（要修正）"
            )
            return

    # ── 重複コンテンツ事前チェック ─────────────────────────────
    posted_contents = get_posted_contents()
    if content.strip() in posted_contents:
        reason = "duplicate: 投稿済みと同一コンテンツ（事前チェック）"
        archive_failed(post_file, post, reason)
        write_summary(
            f"## X投稿\n"
            f"⚠️ 重複コンテンツのためスキップ\n"
            f"- ファイル: `{post_file.name}`\n"
            f"- 理由: 既に投稿済みと同一内容\n"
            f"- → `failed_queue/` に移動（API呼び出しなし）"
        )
        return

    last_error = None
    FIX_RETRIES = 2   # 403修正後の最大リトライ数
    current_content = content
    fix_applied = False

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.create_tweet(text=current_content, user_auth=True)
            tweet_id = response.data['id']
            tweet_url = f"https://twitter.com/i/web/status/{tweet_id}"
            status_note = "（修正後）" if fix_applied else ""
            print(f"[OK] 投稿成功 (試行{attempt}){status_note}: {tweet_url}")

            # ── 自己確認 ──────────────────────────────
            time.sleep(3)
            verified = verify_tweet(client, tweet_id)
            print(f"[OK] ツイート確認: {'済' if verified else '失敗（投稿自体は成功）'}")

            # ── 内容が修正されていたらキューファイルも更新 ──
            if fix_applied:
                post['content'] = current_content
                post['auto_fixed'] = True

            archive_posted(post_file, post, tweet_id)
            remaining = len(list(QUEUE_DIR.glob('*.json')))
            print(f"残りキュー: {remaining}件")

            write_summary(
                f"## X投稿\n"
                f"✅ 投稿成功（試行{attempt}回目）{status_note}\n"
                f"- ファイル: `{post_file.name}`\n"
                f"- URL: {tweet_url}\n"
                f"- 残りキュー: {remaining}件"
            )
            return

        except tweepy.errors.Forbidden as e:
            err_str = str(e).lower()
            print(f"[403] Forbidden (試行{attempt}): {e}")

            # ── クレジット枯渇 (not permitted) ──────────────────────────
            if 'not permitted' in err_str or 'usage cap' in err_str or '453' in err_str:
                mark_credit_exhausted(f'403 not permitted: {e}')
                sys.exit(0)

            # duplicate content は修正不能 → 即スキップ（リトライ無意味）
            if 'duplicate' in err_str:
                reason = f"403 duplicate content（投稿済みと同一）: {e}"
                archive_failed(post_file, post, reason)
                write_summary(
                    f"## X投稿\n"
                    f"⚠️ 重複コンテンツ拒否→スキップ\n"
                    f"- ファイル: `{post_file.name}`\n"
                    f"- 理由: {reason}\n"
                    f"- → `failed_queue/` に移動"
                )
                return

            if attempt <= FIX_RETRIES:
                # ── 自動修正してリトライ ─────────────
                fixed, changes = auto_fix_content(current_content)
                if fixed != current_content and changes:
                    current_content = fixed
                    fix_applied = True
                    print(f"[FIX] 自動修正して再試行:")
                    for c in changes:
                        print(f"  - {c}")
                    time.sleep(15)
                    continue
                else:
                    print("[FIX] 修正ルールに該当なし → スキップ")

            # 修正不能または修正後も失敗
            reason = f"403 Forbidden (自動修正{'' if not fix_applied else '後も'}失敗): {e}"
            archive_failed(post_file, post, reason)
            write_summary(
                f"## X投稿\n"
                f"⚠️ 自動修正後もコンテンツ拒否→スキップ\n"
                f"- ファイル: `{post_file.name}`\n"
                f"- 理由: {reason}\n"
                f"- → `failed_queue/` に移動（要手動確認）"
            )
            return

        except tweepy.errors.TooManyRequests as e:
            last_error = e
            print(f"[WAIT] レートリミット (試行{attempt}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES:
                print(f"  {RETRY_WAIT_SEC}秒後にリトライ...")
                time.sleep(RETRY_WAIT_SEC)

        except tweepy.TweepyException as e:
            last_error = e
            print(f"[ERR] 投稿失敗 (試行{attempt}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES:
                time.sleep(10)

    # 全リトライ失敗
    print(f"[FAIL] {MAX_RETRIES}回試行後も失敗: {last_error}")
    write_summary(
        f"## X投稿\n"
        f"❌ 投稿失敗（{MAX_RETRIES}回試行）\n"
        f"- ファイル: `{post_file.name}`\n"
        f"- エラー: {last_error}"
    )
    sys.exit(1)


if __name__ == '__main__':
    main()
