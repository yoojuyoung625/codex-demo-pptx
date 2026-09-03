from __future__ import annotations

import json
import os
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storage import SupabaseStore


settings = dict(os.environ)
env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            name, value = line.split("=", 1)
            settings.setdefault(name.strip(), value.strip().strip("\"'"))
secret_path = ROOT / ".streamlit" / "secrets.toml"
if secret_path.exists():
    settings.update({key: str(value) for key, value in tomllib.loads(secret_path.read_text(encoding="utf-8")).items()})
url = settings.get("SUPABASE_URL")
key = settings.get("SUPABASE_SECRET_KEY") or settings.get("SUPABASE_SERVICE_ROLE_KEY")
if not url or not key:
    raise SystemExit("SUPABASE_URL과 SUPABASE_SECRET_KEY가 필요합니다.")

source = json.loads((ROOT / "data" / "store.json").read_text(encoding="utf-8"))
store = SupabaseStore(url, key)
records = source.get("records", [])
jobs = source.get("jobs", [])
store.upsert_records(records)
for job in jobs:
    if job.get("id") and job.get("startedAt") and job.get("status"):
        store.add_job(job)
print(f"Supabase 이전 완료: records={len(records)}, jobs={len(jobs)}")
