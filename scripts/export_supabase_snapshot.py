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
if not settings.get("SUPABASE_URL") or not (settings.get("SUPABASE_SECRET_KEY") or settings.get("SUPABASE_SERVICE_ROLE_KEY")):
    raise SystemExit("Supabase Secrets가 없어 기존 공개 데이터를 유지합니다.")
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
if not records:
    raise SystemExit("Supabase 확정 데이터가 0건이므로 빈 파일을 배포하지 않습니다.")
payload = {"keywordConfig": keyword_config, "records": records, "jobs": [], "updatedAt": datetime.now(timezone.utc).isoformat()}
(ROOT / "public" / "history.json").write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
latest = records[-1]["date"] if records else "없음"
print(f"Supabase 정적 스냅샷 생성: records={len(records)}, latest={latest}")
