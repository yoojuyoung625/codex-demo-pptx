from __future__ import annotations

import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from naver_api import NaverClient
from storage import make_store


settings = dict(os.environ)
required = ["NAVER_AD_API_KEY", "NAVER_AD_SECRET_KEY", "NAVER_AD_CUSTOMER_ID", "NAVER_API_HUB_CLIENT_ID", "NAVER_API_HUB_CLIENT_SECRET", "SUPABASE_URL", "SUPABASE_SECRET_KEY"]
missing = [key for key in required if not settings.get(key)]
if missing:
    raise SystemExit(f"Required secrets missing: {', '.join(missing)}")

kst = timezone(timedelta(hours=9))
target_date = datetime.now(kst).date() - timedelta(days=1)
target = target_date.isoformat()
keywords = json.loads((ROOT / "config" / "keywords.json").read_text(encoding="utf-8"))
store = make_store(settings)
existing = store.records()
all_keywords = set(keywords["brand"] + keywords["nonbrand"])
known_dates = sorted(date.fromisoformat(row["date"]) for row in existing if row.get("date") and date.fromisoformat(row["date"]) <= target_date)
start_date = target_date if not known_dates else known_dates[-1]
latest_final = {row["keyword"] for row in existing if row.get("date") == start_date.isoformat() and row.get("status") == "FINAL"}
if latest_final >= all_keywords:
    start_date += timedelta(days=1)

client = NaverClient(settings)
any_failed = False
jobs = []
current = start_date
while current <= target_date:
    day = current.isoformat()
    existing = store.records()
    final = {row["keyword"] for row in existing if row.get("date") == day and row.get("status") == "FINAL"}
    targets = all_keywords - final
    if not targets:
        current += timedelta(days=1)
        continue

    started = datetime.now(timezone.utc).isoformat()
    rows = client.collect_day(day, keywords, targets)
    if current < target_date:
        for row in rows:
            if row.get("status") == "FINAL":
                row["calculationMode"] = "BACKFILL_CURRENT_SNAPSHOT"
    store.upsert_records(rows)
    final_count = sum(row["status"] == "FINAL" for row in rows)
    failed_count = len(rows) - final_count
    job = {"id": f"{int(time.time() * 1000)}-github-actions", "targetDate": day, "startedAt": started, "finishedAt": datetime.now(timezone.utc).isoformat(),
           "status": "FINAL" if not failed_count else "PARTIAL", "requested": len(rows), "finalCount": final_count, "failedCount": failed_count}
    store.add_job(job)
    jobs.append(job)
    print(json.dumps(job, ensure_ascii=False))
    any_failed = any_failed or bool(failed_count)
    current += timedelta(days=1)
    if current <= target_date:
        time.sleep(2)

if not jobs:
    print(json.dumps({"status": "SKIPPED", "targetDate": target, "message": "전일까지 모두 확정됨"}, ensure_ascii=False))
if any_failed:
    raise SystemExit(1)
