from __future__ import annotations

import base64
import hashlib
import hmac
import time
from datetime import date, timedelta
from typing import Iterable

import requests


def chunks(items: list[str], size: int) -> Iterable[list[str]]:
    for index in range(0, len(items), size):
        yield items[index:index + size]


def parse_count(value: object) -> int:
    text = str(value or "0")
    return 0 if "<" in text else int(float(text.replace(",", "") or 0))


class NaverClient:
    def __init__(self, settings: dict[str, str]):
        self.ad_key = settings["NAVER_AD_API_KEY"]
        self.ad_secret = settings["NAVER_AD_SECRET_KEY"]
        self.customer_id = settings["NAVER_AD_CUSTOMER_ID"]
        self.datalab_id = settings["NAVER_API_HUB_CLIENT_ID"]
        self.datalab_secret = settings["NAVER_API_HUB_CLIENT_SECRET"]
        self.session = requests.Session()

    def search_ad(self, keywords: list[str]) -> dict[str, dict]:
        uri = "/keywordstool"
        wanted = {keyword.replace(" ", "").lower() for keyword in keywords}
        found: dict[str, dict] = {}
        for group in chunks(keywords, 5):
            for attempt in range(5):
                timestamp = str(int(time.time() * 1000))
                message = f"{timestamp}.GET.{uri}".encode()
                signature = base64.b64encode(hmac.new(self.ad_secret.encode(), message, hashlib.sha256).digest()).decode()
                response = self.session.get(
                    f"https://api.searchad.naver.com{uri}",
                    params={"hintKeywords": ",".join(group), "showDetail": "1"},
                    headers={"X-Timestamp": timestamp, "X-API-KEY": self.ad_key, "X-Customer": self.customer_id, "X-Signature": signature},
                    timeout=30,
                )
                if response.status_code == 429 and attempt < 4:
                    time.sleep(min(2 ** attempt, 8))
                    continue
                response.raise_for_status()
                for row in response.json().get("keywordList", []):
                    normalized = str(row.get("relKeyword", "")).replace(" ", "").lower()
                    if normalized in wanted and normalized not in found:
                        pc = parse_count(row.get("monthlyPcQcCnt"))
                        mobile = parse_count(row.get("monthlyMobileQcCnt"))
                        found[normalized] = {"keyword": row.get("relKeyword"), "monthlyPc": pc, "monthlyMobile": mobile, "monthlyTotal": pc + mobile}
                break
            time.sleep(0.5)
        return found

    def datalab(self, keywords: list[str], start: str, end: str) -> dict[str, list[dict]]:
        output: dict[str, list[dict]] = {}
        for group in chunks(keywords, 5):
            response = self.session.post(
                "https://naverapihub.apigw.ntruss.com/search-trend/v1/search",
                headers={"Content-Type": "application/json", "X-NCP-APIGW-API-KEY-ID": self.datalab_id, "X-NCP-APIGW-API-KEY": self.datalab_secret},
                json={"startDate": start, "endDate": end, "timeUnit": "date", "keywordGroups": [{"groupName": keyword, "keywords": [keyword]} for keyword in group]},
                timeout=30,
            )
            response.raise_for_status()
            for row in response.json().get("results", []):
                output[row["title"]] = row.get("data", [])
        return output

    def collect_day(self, target: str, keyword_config: dict[str, list[str]], retry_keywords: set[str] | None = None) -> list[dict]:
        target_date = date.fromisoformat(target)
        start = (target_date - timedelta(days=29)).isoformat()
        typed = [(keyword, kind) for kind in ("nonbrand", "brand") for keyword in keyword_config[kind]]
        if retry_keywords is not None:
            typed = [(keyword, kind) for keyword, kind in typed if keyword in retry_keywords]
        keywords = [keyword for keyword, _ in typed]
        ad_rows = self.search_ad(keywords)
        trend_rows = self.datalab(keywords, start, target)
        now = date.today().isoformat()
        records = []
        for keyword, kind in typed:
            ad = ad_rows.get(keyword.replace(" ", "").lower())
            trend = trend_rows.get(keyword, [])
            target_trend = next((row for row in trend if row.get("period") == target), None)
            ratio_sum = sum(float(row.get("ratio") or 0) for row in trend)
            if not ad or not ratio_sum or not target_trend:
                records.append({"date": target, "keyword": keyword, "type": kind, "status": "FAILED", "attempts": 1, "updatedAt": now,
                    "error": "검색광고 정확 일치 키워드 없음" if not ad else "DataLab 전일 지수 또는 최근 30일 합계 미완성"})
                continue
            ratio = float(target_trend.get("ratio") or 0)
            records.append({
                "date": target, "keyword": keyword, "type": kind, "status": "FINAL", "attempts": 1, "finalizedAt": now,
                "estimatedQuery": round(ad["monthlyTotal"] * ratio / ratio_sum), "calculationMode": "DAILY_SNAPSHOT",
                "calculation": {"monthlyTotal": ad["monthlyTotal"], "targetRatio": ratio, "ratioSum": ratio_sum, "formula": "monthlyTotal * targetRatio / ratioSum"},
                "snapshot": {"fetchedAt": now, "window": {"startDate": start, "endDate": target}, "searchAd": ad, "datalab": trend},
            })
        return records
