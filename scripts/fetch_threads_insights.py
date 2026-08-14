#!/usr/bin/env python3
"""Threads投稿インサイトを取得してthreads_insights.jsonに保存する"""
import os, json, time, requests
from pathlib import Path

TOKEN = os.environ["THREADS_ACCESS_TOKEN"]
DATA_DIR = Path(os.environ.get("DATA_DIR", "."))
OUTPUT = DATA_DIR / "threads_insights.json"

BASE = "https://graph.threads.net/v1.0"

me = requests.get(f"{BASE}/me", params={"fields": "id,username", "access_token": TOKEN}, timeout=15).json()
user_id = me["id"]
username = me.get("username", "")
print(f"user: {username} (id={user_id})")

all_posts = []
url = f"{BASE}/{user_id}/threads"
params = {"fields": "id,text,timestamp,media_type", "limit": 50, "access_token": TOKEN}
while url:
    r = requests.get(url, params=params, timeout=30).json()
    all_posts.extend(r.get("data", []))
    nxt = r.get("paging", {}).get("next")
    url = nxt if nxt else None
    params = {}

print(f"投稿数: {len(all_posts)}")

results = []
for post in all_posts:
    pid = post["id"]
    ri = requests.get(
        f"{BASE}/{pid}/insights",
        params={"metric": "views,likes,replies,reposts,quotes,shares", "access_token": TOKEN},
        timeout=15,
    )
    ins = {}
    for m in ri.json().get("data", []):
        val = m.get("total_value", {}).get("value")
        if val is None and m.get("values"):
            val = m["values"][0].get("value", 0)
        ins[m["name"]] = val or 0
    engagement = ins.get("likes", 0) + ins.get("replies", 0) + ins.get("reposts", 0) + ins.get("quotes", 0)
    results.append({
        "id": pid,
        "date": post.get("timestamp", "")[:10],
        "text": post.get("text", ""),
        "media_type": post.get("media_type", ""),
        "views": ins.get("views", 0),
        "likes": ins.get("likes", 0),
        "replies": ins.get("replies", 0),
        "reposts": ins.get("reposts", 0),
        "quotes": ins.get("quotes", 0),
        "shares": ins.get("shares", 0),
        "engagement": engagement,
    })
    time.sleep(0.3)

results.sort(key=lambda x: x["engagement"], reverse=True)
OUTPUT.parent.mkdir(parents=True, exist_ok=True)
OUTPUT.write_text(json.dumps({"username": username, "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "posts": results}, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"保存: {OUTPUT}")

# サマリー出力
summary_path = os.environ.get("GITHUB_STEP_SUMMARY", "")
if summary_path:
    with open(summary_path, "a", encoding="utf-8") as f:
        f.write("## Threads投稿インサイト TOP20\n\n")
        f.write(f"取得投稿数: {len(results)}件\n\n")
        f.write("| 日付 | views | likes | replies | reposts | quotes | 本文(先頭80字) |\n")
        f.write("|------|------:|------:|--------:|--------:|-------:|----------------|\n")
        for r in results[:20]:
            text_esc = r["text"][:80].replace("|", "｜").replace("\n", " ")
            f.write(f"| {r['date']} | {r['views']} | {r['likes']} | {r['replies']} | {r['reposts']} | {r['quotes']} | {text_esc} |\n")
