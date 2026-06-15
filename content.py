#!/usr/bin/env python3
"""
コンテンツ管理CLI
使い方:
  python content.py add-x        # X投稿を対話形式で追加
  python content.py add-note      # Note記事を対話形式で追加
  python content.py list          # キュー一覧を表示
  python content.py upcoming      # 今後の投稿スケジュール
  python content.py clear-failed  # 失敗キューを確認・再キュー
  python content.py push          # コンテンツをgit pushのみ（ワークフロー不変）
"""
import json, sys, os, subprocess
from pathlib import Path
from datetime import datetime, timedelta
import re

BASE = Path(__file__).parent

# ── CONTENT ZONE（触ってよいファイル）────────────────────────
QUEUE_DIR       = BASE / 'queue'
NOTE_QUEUE_DIR  = BASE / 'note_queue'
THREAD_QUEUE_DIR= BASE / 'thread_queue'
FAILED_DIR      = BASE / 'failed_queue'
POSTED_DIR      = BASE / 'posted'
NOTE_POSTED_DIR = BASE / 'note_posted'
AFF_FILE        = BASE / 'affiliates.json'

# ── SYSTEM ZONE（絶対に触らないファイル）─────────────────────
SYSTEM_FILES = [
    '.github/workflows/',
    'scripts/',
]

AFF = json.loads(AFF_FILE.read_text(encoding='utf-8'))


def load_aff_menu():
    return {str(i+1): (k, v['name']) for i, (k, v) in enumerate(AFF.items())}


def last_seq(directory: Path, kind: str) -> int:
    nums = [int(m.group(1)) for f in directory.glob(f'*_{kind}_*.json')
            if (m := re.search(rf'_{kind}_(\d+)\.json', f.name))]
    return max(nums) if nums else 0


def last_queue_date(directory: Path) -> datetime:
    files = sorted([f for f in directory.glob('20*.json') if f.name != '.gitkeep'])
    return datetime.strptime(files[-1].name[:10], '%Y-%m-%d') if files else datetime.now()


def cmd_list():
    """キュー一覧を表示"""
    for label, d, ext in [
        ('🐦 X queue', QUEUE_DIR, None),
        ('📝 Note queue', NOTE_QUEUE_DIR, None),
        ('🧵 Thread queue', THREAD_QUEUE_DIR, None),
        ('❌ Failed queue', FAILED_DIR, None),
    ]:
        files = sorted([f for f in d.glob('*.json') if f.name != '.gitkeep'])
        print(f'\n{label}: {len(files)}件')
        for f in files[:5]:
            d2 = json.loads(f.read_text(encoding='utf-8'))
            content = d2.get('content') or d2.get('title') or d2.get('tweets', [''])[0]
            print(f'  {f.name[:30]} | {str(content)[:45]}...')
        if len(files) > 5:
            print(f'  ... 他{len(files)-5}件')


def cmd_upcoming():
    """今後の投稿スケジュールを簡易表示"""
    from datetime import timezone
    now_jst = datetime.now()
    print(f'\n現在 JST: {now_jst.strftime("%m/%d %H:%M")}')
    print('\n今後の投稿（直近10件）:')
    x_files = sorted([f for f in QUEUE_DIR.glob('*.json') if f.name != '.gitkeep'])
    n_files  = sorted([f for f in NOTE_QUEUE_DIR.glob('*.json') if f.name != '.gitkeep'])
    # trigger.yml: UTC 0,2,4,6,8,10,12,14,21,23 の :30に発火 → X
    # noteHours: UTC 2,6,10,14,23 → Note
    X_HOURS_UTC   = [0,2,4,6,8,10,12,14,21,23]
    NOTE_HOURS_UTC = [2,6,10,14,23]
    from datetime import timezone, timedelta
    utc_now = datetime.now(timezone.utc)
    xi, ni = 0, 0
    shown = 0
    for h_offset in range(48):
        candidate = utc_now.replace(minute=30, second=0, microsecond=0) + timedelta(hours=h_offset)
        h = candidate.hour
        jst = candidate + timedelta(hours=9)
        if h in X_HOURS_UTC and xi < len(x_files):
            d = json.loads(x_files[xi].read_text(encoding='utf-8'))
            c = d.get('content','').replace('\n',' ')[:40]
            print(f'  {jst.strftime("%m/%d %H:%M")} X     | {c}')
            xi += 1; shown += 1
        if h in NOTE_HOURS_UTC and ni < len(n_files):
            d = json.loads(n_files[ni].read_text(encoding='utf-8'))
            t = d.get('title','')[:40]
            print(f'  {jst.strftime("%m/%d %H:%M")} Note  | {t}')
            ni += 1; shown += 1
        if shown >= 10:
            break


def cmd_add_x():
    """X投稿を対話形式で追加"""
    print('\n=== X投稿 追加 ===')
    print('アフィリエイト:')
    menu = load_aff_menu()
    for k, (aid, name) in menu.items():
        print(f'  {k}. {name}')
    choice = input('番号を選択: ').strip()
    if choice not in menu:
        print('無効な選択')
        return
    aff_id, aff_name = menu[choice]
    aff_url = AFF[aff_id]['url']

    print('\n投稿タイプ:')
    types = {'1': ('empathy','共感系'), '2': ('value','価値提供系'), '3': ('branding','ブランディング系')}
    for k, (t, desc) in types.items():
        print(f'  {k}. {desc}')
    t_choice = input('番号: ').strip()
    if t_choice not in types:
        print('無効な選択')
        return
    post_type, _ = types[t_choice]

    print('\nバズパターン:')
    print('  A. 挫折+時間軸+意外な気づき')
    print('  B. 外資系あるある+日本との対比')
    pattern = input('A or B: ').strip().upper()

    if pattern == 'A':
        template = (
            "○○を[期間]続けたのに、[期待した結果]が出なかった。\n\n"
            "[気づき1行]\n\n"
            "[変えたこと・解決策]\n\n"
            "#ハッシュタグ1 #ハッシュタグ2"
        )
    else:
        template = (
            "外資系に[入って/転職して]最初に[驚いたこと]。\n\n"
            "[日本企業との対比1行]\n\n"
            "[自分がどう変わったか]\n\n"
            "#ハッシュタグ1 #ハッシュタグ2"
        )

    print(f'\nテンプレート:\n{template}')
    print('\n本文を入力（空行2回で確定）:')
    lines = []
    empty_count = 0
    while empty_count < 2:
        line = input()
        if line == '':
            empty_count += 1
            if empty_count == 1:
                lines.append('')
        else:
            empty_count = 0
            lines.append(line)
    content = '\n'.join(lines).strip()

    if not content:
        print('キャンセル')
        return

    # ファイル名決定
    start_date = last_queue_date(QUEUE_DIR) + timedelta(days=1)
    seq = {'empathy': last_seq(QUEUE_DIR, 'empathy')+1,
           'value':   last_seq(QUEUE_DIR, 'value')+1,
           'branding':last_seq(QUEUE_DIR, 'branding')+1}[post_type]
    times = ['06-22','09-17','14-33','16-44','22-58']
    t = times[seq % len(times)]
    fname = f'{start_date.strftime("%Y-%m-%d")}_{t}_{post_type}_{seq:02d}.json'
    data = {
        'type': post_type,
        'genre': '転職' if aff_id in ('techgo','posiwill') else
                 'キャリア' if aff_id == 'posiwill' else
                 '英語学習（子ども）' if aff_id in ('campustop','qqenglish') else
                 '英語学習（大人）',
        'affiliate_note': aff_name,
        'content': content,
        'created_at': datetime.now().isoformat(),
    }
    out = QUEUE_DIR / fname
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'\n✓ 追加しました: {fname}')
    print(f'  アフィリエイト: {aff_name}')
    print(f'  内容: {content[:60]}...')

    push = input('\ngit pushしますか？ [y/N]: ').strip().lower()
    if push == 'y':
        cmd_push()


def cmd_add_note():
    """Note記事を対話形式で追加"""
    print('\n=== Note記事 追加 ===')
    menu = load_aff_menu()
    for k, (aid, name) in menu.items():
        print(f'  {k}. {name}')
    choice = input('番号を選択: ').strip()
    if choice not in menu:
        print('無効な選択')
        return
    aff_id, aff_name = menu[choice]
    aff_url = AFF[aff_id]['url']

    print('\nSEOタイトル（形式: [外資系属性]+[逆接体験]+[結果/選択]）:')
    print('例: 外資系IT10年の親がCampusTopを選んだ理由。正社員教師の実力')
    title = input('タイトル: ').strip()
    if not title:
        print('キャンセル')
        return

    genre_map = {
        'campustop': '英語学習（子ども）', 'qqenglish': '英語学習（子ども）',
        'lancul': '英語学習（大人）', 'aques': '英語学習（大人）',
        'studysapuri': '英語学習（大人）', 'posiwill': 'キャリア', 'techgo': '転職',
    }
    genre = genre_map.get(aff_id, '英語学習（大人）')

    print(f'\nジャンル: {genre}')
    print('\n本文を入力（空行2回で確定、またはEnterでテンプレート使用）:')
    first = input()
    if first == '':
        # テンプレート使用
        seo_open = {
            '英語学習（大人）': '外資系IT企業で10年英語を使ってきた経験から書く。',
            '英語学習（子ども）': '外資系IT企業で働く親として、子どもの英語サービスを選んだ経験から書く。',
            'キャリア': '外資系IT企業で10年、キャリアに迷った経験から書く。',
            '転職': '外資系IT企業で10年、IT転職を経験した立場から書く。',
        }.get(genre, '外資系IT企業で10年の経験から書く。')
        content = (
            f'{seo_open}\n\n'
            f'[ここに本文を記入。体験談 → 問題の根本 → サービスの紹介 → CTA]\n\n'
            f'→ {aff_name}の詳細はこちら（PR）\n\n'
            f'{aff_url}\n\n'
            f'※本記事はPR（アフィリエイト）リンクを含みます。'
        )
        print('\nテンプレートを使用します。後でファイルを直接編集してください。')
    else:
        lines = [first]
        empty_count = 0
        while empty_count < 2:
            line = input()
            if line == '':
                empty_count += 1
                if empty_count == 1:
                    lines.append('')
            else:
                empty_count = 0
                lines.append(line)
        content = '\n'.join(lines).strip()

    # ファイル名決定
    from itertools import chain
    all_note_dirs = [NOTE_QUEUE_DIR, NOTE_POSTED_DIR]
    nums = []
    for d in all_note_dirs:
        for f in d.glob('*_note_*.json'):
            m = re.search(r'_note_(\d+)\.json', f.name)
            if m:
                nums.append(int(m.group(1)))
    next_num = max(nums) + 1 if nums else 41
    start_date = last_queue_date(NOTE_QUEUE_DIR) + timedelta(days=1)
    fname = f'{start_date.strftime("%Y-%m-%d")}_note_{next_num:02d}.json'
    data = {'title': title, 'genre': genre, 'content': content,
            'created_at': datetime.now().isoformat()}
    out = NOTE_QUEUE_DIR / fname
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'\n✓ 追加しました: {fname}')
    print(f'  タイトル: {title}')

    push = input('\ngit pushしますか？ [y/N]: ').strip().lower()
    if push == 'y':
        cmd_push()


def cmd_clear_failed():
    """failed_queue を確認して再キューするか選択"""
    files = [f for f in FAILED_DIR.glob('*.json') if f.name != '.gitkeep']
    if not files:
        print('failed_queueは空です')
        return
    print(f'\n失敗キュー: {len(files)}件')
    for f in files:
        d = json.loads(f.read_text(encoding='utf-8'))
        print(f'\n  {f.name}')
        print(f'  理由: {d.get("fail_reason","不明")[:60]}')
        print(f'  内容: {d.get("content","")[:60]}')
        action = input('  [r]再キュー / [d]削除 / [s]スキップ: ').strip().lower()
        if action == 'r':
            d.pop('failed_at', None); d.pop('fail_reason', None)
            target = QUEUE_DIR / f.name
            target.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding='utf-8')
            f.unlink()
            print(f'  → queue/に移動しました')
        elif action == 'd':
            f.unlink()
            print(f'  → 削除しました')


def cmd_push():
    """コンテンツファイルのみgit push（システムファイルは変更しない）"""
    # 変更されたシステムファイルがないか確認
    result = subprocess.run(['git', 'diff', '--name-only', 'HEAD'],
                            capture_output=True, text=True, cwd=str(BASE))
    changed = result.stdout.strip().split('\n') if result.stdout.strip() else []
    system_changed = [f for f in changed
                      if any(f.startswith(s) for s in SYSTEM_FILES)]
    if system_changed:
        print(f'⚠️  システムファイルが変更されています:')
        for f in system_changed:
            print(f'   {f}')
        confirm = input('続行しますか？ [y/N]: ').strip().lower()
        if confirm != 'y':
            print('キャンセル')
            return

    subprocess.run(['git', 'add',
                    'queue/', 'note_queue/', 'thread_queue/',
                    'failed_queue/', 'affiliates.json', 'reply_suggestions/'],
                   cwd=str(BASE))
    result = subprocess.run(['git', 'diff', '--staged', '--quiet'], cwd=str(BASE))
    if result.returncode == 0:
        print('変更なし')
        return
    subprocess.run(['git', 'commit', '-m', 'content: キュー更新 [skip ci]'], cwd=str(BASE))
    result = subprocess.run(['git', 'pull', '--rebase', 'origin', 'main'],
                            cwd=str(BASE), capture_output=True)
    subprocess.run(['git', 'push', 'origin', 'main'], cwd=str(BASE))
    print('✓ pushしました（システムファイルは変更していません）')


COMMANDS = {
    'add-x':       (cmd_add_x,      'X投稿を対話形式で追加'),
    'add-note':    (cmd_add_note,    'Note記事を対話形式で追加'),
    'list':        (cmd_list,        'キュー一覧を表示'),
    'upcoming':    (cmd_upcoming,    '今後の投稿スケジュールを表示'),
    'clear-failed':(cmd_clear_failed,'失敗キューを確認・再キュー'),
    'push':        (cmd_push,        'コンテンツのみgit push'),
}

if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else ''
    if cmd in COMMANDS:
        COMMANDS[cmd][0]()
    else:
        print('X・Noteコンテンツ管理ツール\n')
        print('コマンド:')
        for name, (_, desc) in COMMANDS.items():
            print(f'  python content.py {name:<15} {desc}')
        print('\n⚠️  SYSTEM ZONE（変更禁止）: .github/workflows/, scripts/')
        print('✅  CONTENT ZONE（変更OK）: queue/, note_queue/, affiliates.json')
