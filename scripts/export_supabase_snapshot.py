from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storage import make_store


settings = dict(os.environ)
store = make_store(settings)
keyword_config = json.loads((ROOT / "config" / "keywords.json").read_text(encoding="utf-8"))

records = []
for record in store.records():
    if record.get("status") != "FINAL":
        continue
    records.append({
        "date": record["date"], "keyword": record["keyword"], "type": record["type"], "status": "FINAL",
        "estimatedQuery": record.get("estimatedQuery"), "calculationMode": record.get("calculationMode"), "finalizedAt": record.get("finalizedAt"),
    })

records.sort(key=lambda row: (row["date"], row["type"], row["keyword"]))
payload = {"keywordConfig": keyword_config, "records": records, "jobs": [], "updatedAt": datetime.now(timezone.utc).isoformat()}
(ROOT / "public" / "history.json").write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
latest = records[-1]["date"] if records else "없음"
print(f"Supabase 정적 스냅샷 생성: records={len(records)}, latest={latest}")
