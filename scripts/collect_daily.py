from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from naver_api import NaverClient
from storage import make_store


settings = dict(os.environ)
required = ["NAVER_AD_API_KEY", "NAVER_AD_SECRET_KEY", "NAVER_AD_CUSTOMER_ID", "NAVER_API_HUB_CLIENT_ID", "NAVER_API_HUB_CLIENT_SECRET", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"]
missing = [key for key in required if not settings.get(key)]
if missing:
    raise SystemExit(f"Required secrets missing: {', '.join(missing)}")

kst = timezone(timedelta(hours=9))
target = (datetime.now(kst).date() - timedelta(days=1)).isoformat()
keywords = json.loads((ROOT / "config" / "keywords.json").read_text(encoding="utf-8"))
store = make_store(settings)
existing = store.records()
final = {row["keyword"] for row in existing if row.get("date") == target and row.get("status") == "FINAL"}
targets = set(keywords["brand"] + keywords["nonbrand"]) - final

started = datetime.now(timezone.utc).isoformat()
rows = NaverClient(settings).collect_day(target, keywords, targets)
store.upsert_records(rows)
final_count = sum(row["status"] == "FINAL" for row in rows)
failed_count = len(rows) - final_count
job = {"id": f"{int(time.time() * 1000)}-github-actions", "targetDate": target, "startedAt": started, "finishedAt": datetime.now(timezone.utc).isoformat(),
       "status": "FINAL" if not failed_count else "PARTIAL", "requested": len(rows), "finalCount": final_count, "failedCount": failed_count}
store.add_job(job)
print(json.dumps(job, ensure_ascii=False))
if failed_count:
    raise SystemExit(1)

