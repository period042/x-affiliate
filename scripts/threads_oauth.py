#!/usr/bin/env python3
"""
Threads APIトークン取得スクリプト（threads_manage_replies スコープ付き）

使い方:
  python scripts/get_threads_token.py

事前準備:
  - THREADS_APP_ID      : Meta Developer Console の「アプリID」
  - THREADS_APP_SECRET  : Meta Developer Console の「アプリシークレット」
  - THREADS_REDIRECT_URI: アプリ設定の「コールバックURL」（例: https://localhost/）
"""
import os, sys, webbrowser, urllib.parse, requests

APP_ID       = os.environ.get('THREADS_APP_ID', '')
APP_SECRET   = os.environ.get('THREADS_APP_SECRET', '')
REDIRECT_URI = os.environ.get('THREADS_REDIRECT_URI', 'https://localhost/')
SCOPES       = 'threads_basic,threads_manage_replies,threads_manage_insights'

def main():
    # ① App IDとシークレットの確認
    if not APP_ID or not APP_SECRET:
        print("=" * 60)
        print("Meta Developer Console から以下を取得してください:")
        print("  https://developers.facebook.com/apps/")
        print("  → アプリを選択 → 左メニュー「アプリの設定」→「ベーシック」")
        print("  - アプリID（App ID）")
        print("  - アプリシークレット（App Secret）→「表示」ボタン")
        print("=" * 60)
        app_id     = input("アプリID: ").strip()
        app_secret = input("アプリシークレット: ").strip()
        redirect   = input(f"コールバックURL [{REDIRECT_URI}]: ").strip() or REDIRECT_URI
    else:
        app_id     = APP_ID
        app_secret = APP_SECRET
        redirect   = REDIRECT_URI

    # ② 認証URLを生成してブラウザで開く
    auth_url = (
        "https://threads.net/oauth/authorize"
        f"?client_id={app_id}"
        f"&redirect_uri={urllib.parse.quote(redirect, safe='')}"
        f"&scope={SCOPES}"
        "&response_type=code"
    )
    print(f"\n認証URLをブラウザで開きます...")
    print(f"  {auth_url}")
    webbrowser.open(auth_url)

    print("""
ブラウザで「許可する」をクリックしてください。
リダイレクト後のURLから「code=」以降の値をコピーしてください。
例: https://localhost/?code=AQBxxx...#_
                              ↑ここだけコピー（#_の手前まで）
""")
    code = input("codeを貼り付け: ").strip()
    if code.startswith('http'):
        # URLごと貼られた場合はパース
        parsed = urllib.parse.urlparse(code)
        code = urllib.parse.parse_qs(parsed.query).get('code', [''])[0]
    # #_が含まれる場合は除去
    code = code.split('#')[0].strip()

    # ③ 短期トークン取得
    print("\n[1/2] 短期トークン取得中...")
    r = requests.post(
        "https://graph.threads.net/oauth/access_token",
        data={
            "client_id":     app_id,
            "client_secret": app_secret,
            "grant_type":    "authorization_code",
            "redirect_uri":  redirect,
            "code":          code,
        },
        timeout=30,
    )
    if not r.ok:
        print(f"エラー: {r.status_code} {r.text}")
        sys.exit(1)
    short_token = r.json().get('access_token', '')
    user_id     = r.json().get('user_id', '')
    print(f"  → 短期トークン取得OK (user_id={user_id})")

    # ④ 長期トークン取得（60日有効）
    print("[2/2] 長期トークン取得中...")
    r2 = requests.get(
        "https://graph.threads.net/access_token",
        params={
            "grant_type":    "th_long_lived_token",
            "client_secret": app_secret,
            "access_token":  short_token,
        },
        timeout=30,
    )
    if not r2.ok:
        print(f"エラー: {r2.status_code} {r2.text}")
        sys.exit(1)
    data       = r2.json()
    long_token = data.get('access_token', '')
    expires_in = data.get('expires_in', 0)
    days       = expires_in // 86400

    print(f"\n{'=' * 60}")
    print(f"✅ 長期トークン取得成功（有効期間: {days}日）")
    print(f"\nGitHub Secretsに登録する値:")
    print(f"\n  THREADS_ACCESS_TOKEN = {long_token}")
    if user_id:
        print(f"  THREADS_USER_ID      = {user_id}")
    print(f"\n{'=' * 60}")
    print("登録先: https://github.com/period042/x-affiliate/settings/secrets/actions")
    print(f"{'=' * 60}")

if __name__ == '__main__':
    main()
