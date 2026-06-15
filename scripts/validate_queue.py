#!/usr/bin/env python3
"""
キュー検証スクリプト
- queue/ / thread_queue/ 内でのコンテンツ重複
- queue/ と posted/ のコンテンツ重複
- プロフィール誘導文言の検出（noteリンクへの置換を強制）
1件でもエラーがあれば exit(1)（CI連携可）
"""
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

BASE_DIR       = Path(__file__).parent.parent
QUEUE_DIR      = BASE_DIR / 'queue'
THREAD_QUEUE_DIR = BASE_DIR / 'thread_queue'
POSTED_DIR     = BASE_DIR / 'posted'
THREAD_POSTED_DIR = BASE_DIR / 'thread_posted'

# プロフィール誘導文言パターン（これが含まれていたら投稿NG）
PROF_LINK_PATTERNS = [
    re.compile(r'プロフのリンク'),
    re.compile(r'プロフィールのリンク'),
    re.compile(r'プロフのURL'),
    re.compile(r'詳しくはプロフ'),
    re.compile(r'プロフのリンクをご確認'),
    re.compile(r'プロフのリンク先で詳しく'),
    re.compile(r'→\s*(スタディサプリ|LanCul|CampusTop|POSIWILL|AQUES|ENGREAL)[^\n]*\n(?!https?)'),
]

errors = []


def check_prof_links(content: str, filename: str, dir_name: str):
    for pat in PROF_LINK_PATTERNS:
        if pat.search(content):
            errors.append(
                f"[PROF_LINK] [{dir_name}] {filename}\n"
                f"  → プロフィール誘導文言を検出: {pat.pattern}\n"
                f"  → noteのURLに置換してください"
            )
            return  # 1ファイルにつき1エラーで十分


# ── posted/ のコンテンツ一覧（重複検出用）──
posted_map = {}
for f in POSTED_DIR.glob('*.json'):
    try:
        d = json.loads(f.read_text(encoding='utf-8'))
        c = d.get('content', '').strip()
        if c:
            posted_map[c] = f.name
    except Exception:
        pass

thread_posted_contents = set()
for f in THREAD_POSTED_DIR.glob('*.json') if THREAD_POSTED_DIR.exists() else []:
    try:
        d = json.loads(f.read_text(encoding='utf-8'))
        for t in d.get('tweets', []):
            if t.strip():
                thread_posted_contents.add(t.strip())
    except Exception:
        pass

# ── queue/ 検証 ──
queue_map = {}
for f in sorted(QUEUE_DIR.glob('*.json')):
    try:
        d = json.loads(f.read_text(encoding='utf-8'))
        c = d.get('content', '').strip()
        if not c:
            continue

        if c in posted_map:
            errors.append(f"[DUPLICATE:posted] queue/{f.name}\n  ← {posted_map[c]} と同一")
        if c in queue_map:
            errors.append(f"[DUPLICATE:queue] queue/{f.name}\n  ← {queue_map[c]} と同一")
        else:
            queue_map[c] = f.name

        check_prof_links(c, f.name, 'queue')

    except Exception as ex:
        errors.append(f"[ERROR] queue/{f.name}: {ex}")

# ── thread_queue/ 検証 ──
tqueue_map = {}
for f in sorted(THREAD_QUEUE_DIR.glob('*.json')) if THREAD_QUEUE_DIR.exists() else []:
    try:
        d = json.loads(f.read_text(encoding='utf-8'))
        tweets = d.get('tweets', [])
        for i, t in enumerate(tweets):
            ts = t.strip()
            if not ts:
                continue
            if ts in thread_posted_contents:
                errors.append(
                    f"[DUPLICATE:thread_posted] thread_queue/{f.name} tweet[{i}]\n"
                    f"  ← thread_posted/ と同一ツイート"
                )
            check_prof_links(ts, f"{f.name}[tweet{i}]", 'thread_queue')

    except Exception as ex:
        errors.append(f"[ERROR] thread_queue/{f.name}: {ex}")

# ── 結果出力 ──
if errors:
    print(f"検証エラー: {len(errors)} 件\n")
    for e in errors:
        print(e)
    sys.exit(1)
else:
    total = len(queue_map) + sum(
        len(json.loads(f.read_text(encoding='utf-8')).get('tweets', []))
        for f in (THREAD_QUEUE_DIR.glob('*.json') if THREAD_QUEUE_DIR.exists() else [])
    )
    print(f"OK: queue {len(queue_map)}件 + thread_queue ツイートに問題なし")
    sys.exit(0)
