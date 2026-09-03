from __future__ import annotations

import json
from pathlib import Path

import requests


class JsonStore:
    def __init__(self, path: str = "data/store.json"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "jobs": [], "records": []}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self, data: dict) -> None:
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def records(self) -> list[dict]:
        return self._read()["records"]

    def jobs(self) -> list[dict]:
        return self._read()["jobs"]

    def upsert_records(self, rows: list[dict]) -> None:
        data = self._read()
        indexed = {(row["date"], row["keyword"]): row for row in data["records"]}
        for row in rows:
            old = indexed.get((row["date"], row["keyword"]), {})
            row["attempts"] = int(old.get("attempts", 0)) + 1
            indexed[(row["date"], row["keyword"])] = row
        data["records"] = sorted(indexed.values(), key=lambda row: (row["date"], row["type"], row["keyword"]))
        self._write(data)

    def add_job(self, job: dict) -> None:
        data = self._read()
        data["jobs"].append(job)
        self._write(data)


class SupabaseStore:
    def __init__(self, url: str, service_key: str):
        clean_url = url.rstrip("/")
        if clean_url.endswith("/rest/v1"):
            clean_url = clean_url[: -len("/rest/v1")]
        self.base = f"{clean_url}/rest/v1"
        self.headers = {"apikey": service_key, "Content-Type": "application/json"}
        # Legacy service-role keys are JWTs; current sb_secret_ keys use apikey.
        if service_key.startswith("eyJ"):
            self.headers["Authorization"] = f"Bearer {service_key}"

    def _paged(self, table: str, order: str) -> list[dict]:
        rows = []
        offset = 0
        while True:
            response = requests.get(f"{self.base}/{table}", headers={**self.headers, "Range": f"{offset}-{offset + 999}"}, params={"select": "*", "order": order}, timeout=30)
            response.raise_for_status()
            page = response.json()
            rows.extend(page)
            if len(page) < 1000:
                return rows
            offset += 1000

    def records(self) -> list[dict]:
        output = []
        for row in self._paged("keyword_records", "date.asc,keyword.asc"):
            output.append({
                "date": row["date"], "keyword": row["keyword"], "type": row["type"], "status": row["status"],
                "estimatedQuery": row.get("estimated_query"), "attempts": row.get("attempts", 1), "calculationMode": row.get("calculation_mode"),
                "finalizedAt": row.get("finalized_at"), "updatedAt": row.get("updated_at"), "error": row.get("error"),
                "calculation": row.get("calculation"), "snapshot": row.get("snapshot"),
            })
        return output

    def jobs(self) -> list[dict]:
        return [row.get("payload") or row for row in self._paged("collection_jobs", "started_at.desc")]

    def upsert_records(self, rows: list[dict]) -> None:
        payload = [{
            "date": row["date"], "keyword": row["keyword"], "type": row["type"], "status": row["status"],
            "estimated_query": row.get("estimatedQuery"), "attempts": row.get("attempts", 1), "calculation_mode": row.get("calculationMode"),
            "finalized_at": row.get("finalizedAt"), "updated_at": row.get("updatedAt"), "error": row.get("error"),
            "calculation": row.get("calculation"), "snapshot": row.get("snapshot"),
        } for row in rows]
        for index in range(0, len(payload), 250):
            response = requests.post(f"{self.base}/keyword_records", headers={**self.headers, "Prefer": "resolution=merge-duplicates,return=minimal"},
                params={"on_conflict": "date,keyword"}, json=payload[index:index + 250], timeout=60)
            response.raise_for_status()

    def add_job(self, job: dict) -> None:
        payload = {"id": job["id"], "target_date": job.get("targetDate"), "started_at": job["startedAt"], "finished_at": job.get("finishedAt"),
            "status": job["status"], "requested": job.get("requested", 0), "final_count": job.get("finalCount", 0), "failed_count": job.get("failedCount", 0), "payload": job}
        response = requests.post(f"{self.base}/collection_jobs", headers={**self.headers, "Prefer": "resolution=merge-duplicates,return=minimal"},
            params={"on_conflict": "id"}, json=payload, timeout=30)
        response.raise_for_status()


def make_store(settings: dict[str, str]):
    secret_key = settings.get("SUPABASE_SECRET_KEY") or settings.get("SUPABASE_SERVICE_ROLE_KEY")
    if settings.get("SUPABASE_URL") and secret_key:
        return SupabaseStore(settings["SUPABASE_URL"], secret_key)
    return JsonStore()
