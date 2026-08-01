#!/usr/bin/env python3
"""Threads 投稿インサイトを全件取得して threads_insights.json に保存"""
import os, json, time, requests
from pathlib import Path

THREADS_ACCESS_TOKEN = os.environ["THREADS_ACCESS_TOKEN"]
DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))

posted_dir = DATA_DIR / "threads_promo_posted"
results = []
errors = []

files = sorted(posted_dir.glob("*.json"))
print(f"対象: {len(files)} 件")

for i, f in enumerate(files):
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except Exception as e:
        errors.append({"file": f.name, "error": str(e)})
        continue

    post_ids = data.get("threads_post_ids", [])
    if not post_ids:
        continue

    post_id = post_ids[0]
    # rate limit 対策
    if i > 0 and i % 30 == 0:
        time.sleep(2)

    try:
        r = requests.get(
            f"https://graph.threads.net/v1.0/{post_id}/insights",
            params={
                "metric": "views,likes,replies,reposts,quotes",
                "access_token": THREADS_ACCESS_TOKEN,
            },
            timeout=15,
        )
        if r.status_code == 200:
            raw = r.json().get("data", [])
            metrics = {item["name"]: item.get("values", [{}])[0].get("value", 0) for item in raw}
            results.append({
                "filename": f.name,
                "post_id": post_id,
                "title": data.get("title", "")[:60],
                "note_url": data.get("note_url", ""),
                "posted_at": data.get("posted_at", "")[:16],
                "views": metrics.get("views", 0),
                "likes": metrics.get("likes", 0),
                "replies": metrics.get("replies", 0),
                "reposts": metrics.get("reposts", 0),
                "quotes": metrics.get("quotes", 0),
            })
            print(f"  [{i+1}/{len(files)}] OK  views={metrics.get('views',0)} likes={metrics.get('likes',0)}  {f.name[:40]}")
        else:
            errors.append({"file": f.name, "post_id": post_id, "status": r.status_code, "body": r.text[:200]})
            print(f"  [{i+1}/{len(files)}] ERR {r.status_code}  {f.name[:40]}")
    except Exception as e:
        errors.append({"file": f.name, "error": str(e)})
        print(f"  [{i+1}/{len(files)}] EXC {e}")

# 保存
out_path = DATA_DIR / "threads_insights.json"
out_path.write_text(json.dumps({"results": results, "errors": errors}, ensure_ascii=False, indent=2))
print(f"\n保存: {out_path} ({len(results)} 件成功, {len(errors)} 件エラー)")
