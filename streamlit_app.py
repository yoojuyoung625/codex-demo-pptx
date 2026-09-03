from __future__ import annotations

import json
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from naver_api import NaverClient
from storage import make_store


ROOT = Path(__file__).parent
KST = timezone(timedelta(hours=9))
st.set_page_config(page_title="네이버 키워드 트렌드 대시보드", page_icon="📈", layout="wide")


def load_settings() -> dict[str, str]:
    settings = dict(os.environ)
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                settings.setdefault(key.strip(), value.strip().strip("\"'"))
    try:
        settings.update({key: str(value) for key, value in st.secrets.items()})
    except FileNotFoundError:
        pass
    return settings


SETTINGS = load_settings()
KEYWORDS = json.loads((ROOT / "config" / "keywords.json").read_text(encoding="utf-8"))
STORE = make_store(SETTINGS)


def normalized_records() -> pd.DataFrame:
    rows = [row for row in STORE.records() if row.get("status") == "FINAL"]
    if not rows:
        return pd.DataFrame(columns=["date", "keyword", "type", "value"])
    frame = pd.DataFrame([{"date": row["date"], "keyword": row["keyword"], "type": row["type"], "value": int(row.get("estimatedQuery") or 0)} for row in rows])
    frame["date"] = pd.to_datetime(frame["date"])
    return frame


def week_sum(frame: pd.DataFrame, end: pd.Timestamp) -> pd.DataFrame:
    start = end - pd.Timedelta(days=6)
    return frame[(frame.date >= start) & (frame.date <= end)].groupby(["type", "keyword"], as_index=False).value.sum()


def complete_week_ends(frame: pd.DataFrame) -> list[pd.Timestamp]:
    if frame.empty:
        return []
    dates = [pd.Timestamp(day) for day in sorted(frame.date.drop_duplicates())]
    return [day for day in dates if day.dayofweek == 6 and day <= pd.Timestamp(date.today() - timedelta(days=1))]


def pct(now: float, before: float):
    return None if before == 0 else (now - before) / before


def run_collection(target: str, retry_only: bool = False) -> dict:
    required = ["NAVER_AD_API_KEY", "NAVER_AD_SECRET_KEY", "NAVER_AD_CUSTOMER_ID", "NAVER_API_HUB_CLIENT_ID", "NAVER_API_HUB_CLIENT_SECRET"]
    missing = [key for key in required if not SETTINGS.get(key)]
    if missing:
        raise RuntimeError(f"Streamlit Secrets 누락: {', '.join(missing)}")
    current = STORE.records()
    final = {row["keyword"] for row in current if row.get("date") == target and row.get("status") == "FINAL"}
    failed = {row["keyword"] for row in current if row.get("date") == target and row.get("status") == "FAILED"}
    configured = set(KEYWORDS["brand"] + KEYWORDS["nonbrand"])
    targets = (failed if retry_only else configured - final)
    started = datetime.now(timezone.utc).isoformat()
    rows = NaverClient(SETTINGS).collect_day(target, KEYWORDS, targets)
    STORE.upsert_records(rows)
    final_count = sum(row["status"] == "FINAL" for row in rows)
    failed_count = len(rows) - final_count
    job = {"id": f"{int(time.time() * 1000)}-streamlit", "targetDate": target, "startedAt": started, "finishedAt": datetime.now(timezone.utc).isoformat(),
        "status": "FINAL" if not failed_count else "PARTIAL", "requested": len(rows), "finalCount": final_count, "failedCount": failed_count}
    STORE.add_job(job)
    return job


st.markdown("""
<style>
.stApp {background:linear-gradient(135deg,#f4f8fc 0%,#eefbfb 100%)}
.hero {padding:1.5rem 1.8rem;border-radius:18px;background:#123451;color:white;margin-bottom:1rem}
.hero small{color:#67dce7;font-weight:800}.hero h1{margin:.3rem 0;font-size:2rem}.hero p{margin:0;color:#dceaf5}
[data-testid="stMetric"]{background:white;border:1px solid #dfe8f1;padding:1rem;border-radius:14px}
[data-testid="stDataFrame"]{border:1px solid #dfe8f1;border-radius:14px;overflow:hidden}
</style>
<div class="hero"><small>NAVER SEARCH AD × NAVER API HUB</small><h1>네이버 키워드 트렌드 대시보드</h1><p>최근 30일 월간검색수와 DataLab 지수로 전일 예상 쿼리를 확정하고 누적합니다.</p></div>
""", unsafe_allow_html=True)

frame = normalized_records()
report_tab, daily_tab, internal_tab = st.tabs(["광고주 대시보드", "일자별 쿼리", "내부 수집 · 재시도"])

with report_tab:
    week_ends = complete_week_ends(frame)
    if not week_ends:
        st.info("확정 데이터가 없습니다. 내부 수집 화면에서 먼저 데이터를 수집해 주세요.")
    else:
        selected_end = st.selectbox("기준 주차", week_ends[::-1], format_func=lambda value: f"{(value-pd.Timedelta(days=6)):%Y.%m.%d}–{value:%m.%d}")
        current = week_sum(frame, selected_end).rename(columns={"value": "금주"})
        previous = week_sum(frame, selected_end - pd.Timedelta(days=7)).rename(columns={"value": "전주"})
        month_before = week_sum(frame, selected_end - pd.Timedelta(days=28)).rename(columns={"value": "전월"})
        comparison = current.merge(previous, on=["type", "keyword"], how="left").merge(month_before, on=["type", "keyword"], how="left").fillna(0)
        comparison["전주 증감"] = comparison["금주"] - comparison["전주"]
        comparison["전주 증감률"] = comparison.apply(lambda row: pct(row["금주"], row["전주"]), axis=1)
        comparison["전월 증감"] = comparison["금주"] - comparison["전월"]
        comparison["전월 증감률"] = comparison.apply(lambda row: pct(row["금주"], row["전월"]), axis=1)
        base_2025 = frame[(frame.date.dt.year == 2025) & (frame.type == "nonbrand")].groupby("keyword").value.sum() / 365 * 7
        comparison["금주 지수"] = comparison.apply(lambda row: row["금주"] / base_2025.get(row["keyword"], float("nan")) * 100 if row["type"] == "nonbrand" else float("nan"), axis=1)

        totals = comparison.groupby("type").금주.sum()
        c1, c2, c3 = st.columns(3)
        c1.metric("전체 금주 쿼리", f"{int(comparison['금주'].sum()):,}")
        c2.metric("브랜드", f"{int(totals.get('brand', 0)):,}")
        c3.metric("비브랜드", f"{int(totals.get('nonbrand', 0)):,}")

        st.subheader("최근 8주 쿼리 추이")
        chart_ends = week_ends[-8:]
        figure = go.Figure()
        for kind, label, color in [("brand", "브랜드", "#F28B45"), ("nonbrand", "비브랜드", "#20B9C8")]:
            values = [int(week_sum(frame[frame.type == kind], end).value.sum()) for end in chart_ends]
            figure.add_trace(go.Scatter(x=[end.strftime("%m.%d") for end in chart_ends], y=values, name=label, mode="lines", line={"color": color, "width": 3, "shape": "spline", "smoothing": 0.8}))
        figure.update_layout(height=340, margin=dict(l=10,r=10,t=10,b=10), paper_bgcolor="white", plot_bgcolor="white", hovermode="x unified", legend_orientation="h")
        st.plotly_chart(figure, width="stretch")

        st.subheader("월별 키워드 쿼리")
        year = st.selectbox("연도", sorted(frame.date.dt.year.unique(), reverse=True))
        monthly_type = st.segmented_control("월별 유형", ["전체", "브랜드", "비브랜드"], default="전체")
        monthly = frame[frame.date.dt.year == year].copy()
        if monthly_type != "전체": monthly = monthly[monthly.type == ("brand" if monthly_type == "브랜드" else "nonbrand")]
        monthly["월"] = monthly.date.dt.month
        monthly_table = monthly.pivot_table(index=["type", "keyword"], columns="월", values="value", aggfunc="sum", fill_value=0)
        monthly_table["누적 합계"] = monthly_table.sum(axis=1)
        monthly_table.columns = [str(column) for column in monthly_table.columns]
        st.dataframe(monthly_table.style.format("{:,.0f}"), width="stretch", height=430)
        st.download_button("월별 CSV 다운로드", monthly_table.reset_index().to_csv(index=False, encoding="utf-8-sig"), f"네이버_키워드_월별쿼리_{year}.csv", "text/csv")

        st.subheader("키워드별 주차 성과 비교")
        weekly_type = st.segmented_control("주차 유형", ["전체", "브랜드", "비브랜드"], default="전체")
        visible = comparison if weekly_type == "전체" else comparison[comparison.type == ("brand" if weekly_type == "브랜드" else "nonbrand")]
        display = visible.rename(columns={"keyword":"키워드", "type":"구분"}).sort_values(["구분", "금주"], ascending=[True, False])
        st.dataframe(display[["구분","키워드","금주","금주 지수","전주","전주 증감","전주 증감률","전월","전월 증감","전월 증감률"]].style.format({
            "금주":"{:,.0f}","금주 지수":"{:.1f}","전주":"{:,.0f}","전주 증감":"{:+,.0f}","전주 증감률":"{:+.1%}","전월":"{:,.0f}","전월 증감":"{:+,.0f}","전월 증감률":"{:+.1%}"}), width="stretch", height=650)

with daily_tab:
    st.subheader("일자별 키워드 쿼리")
    if not frame.empty:
        daily_type = st.segmented_control("키워드 유형", ["비브랜드", "브랜드"], default="비브랜드")
        kind = "brand" if daily_type == "브랜드" else "nonbrand"
        end = st.date_input("종료일", value=frame.date.max().date(), max_value=date.today() - timedelta(days=1))
        start = end - timedelta(days=6)
        daily = frame[(frame.type == kind) & (frame.date.dt.date >= start) & (frame.date.dt.date <= end)].copy()
        daily["date"] = daily.date.dt.strftime("%Y-%m-%d")
        table = daily.pivot_table(index="keyword", columns="date", values="value", aggfunc="sum", fill_value=0)
        table["합계"] = table.sum(axis=1)
        st.dataframe(table.style.format("{:,.0f}"), width="stretch", height=650)

with internal_tab:
    st.subheader("일일 수집 작업")
    backend = "Supabase 영구 저장소" if SETTINGS.get("SUPABASE_URL") and (SETTINGS.get("SUPABASE_SECRET_KEY") or SETTINGS.get("SUPABASE_SERVICE_ROLE_KEY")) else "로컬 JSON 저장소"
    st.caption(f"현재 저장소: {backend} · 완료된 과거 값은 다시 계산하지 않습니다.")
    yesterday = date.today() - timedelta(days=1)
    target = st.date_input("대상일", value=yesterday, max_value=yesterday, key="collect_target")
    c1, c2, c3 = st.columns(3)
    if c1.button("선택일 미확정 데이터 수집", type="primary", width="stretch"):
        try:
            with st.spinner("네이버 API 수집 중…"):
                job = run_collection(target.isoformat(), False)
            st.success(f"완료: 확정 {job['finalCount']}건 · 실패 {job['failedCount']}건")
            st.rerun()
        except Exception as error:
            st.error(f"수집 실패: {error}")
    if c2.button("선택일 실패 건 재시도", width="stretch"):
        try:
            with st.spinner("실패 건 재시도 중…"):
                job = run_collection(target.isoformat(), True)
            st.success(f"완료: 확정 {job['finalCount']}건 · 실패 {job['failedCount']}건")
            st.rerun()
        except Exception as error:
            st.error(f"재시도 실패: {error}")
    if c3.button("누락일 ~ 전일 일괄 수집", width="stretch"):
        try:
            configured = set(KEYWORDS["brand"] + KEYWORDS["nonbrand"])
            final_keys = {(row["date"], row["keyword"]) for row in STORE.records() if row.get("status") == "FINAL"}
            start = date.fromisoformat(SETTINGS.get("COLLECTION_START_DATE", "2026-08-01"))
            missing_dates = []
            cursor = start
            while cursor <= yesterday:
                iso = cursor.isoformat()
                if any((iso, keyword) not in final_keys for keyword in configured):
                    missing_dates.append(iso)
                cursor += timedelta(days=1)
            if not missing_dates:
                st.success("누락 날짜 없이 전일까지 모두 확정되어 있습니다.")
            else:
                progress = st.progress(0, text="누락일 수집 준비 중")
                failed = 0
                for index, missing_date in enumerate(missing_dates, start=1):
                    job = run_collection(missing_date, False)
                    failed += job["failedCount"]
                    progress.progress(index / len(missing_dates), text=f"{missing_date} 처리 완료")
                if failed:
                    st.warning(f"누락일 {len(missing_dates)}일 처리 완료 · 실패 {failed}건은 재시도 목록에 유지했습니다.")
                else:
                    st.success(f"누락일 {len(missing_dates)}일을 모두 확정했습니다.")
                st.rerun()
        except Exception as error:
            st.error(f"누락일 수집 실패: {error}")
    jobs = STORE.jobs()
    if jobs:
        st.dataframe(pd.DataFrame(jobs)[[column for column in ["targetDate","status","requested","finalCount","failedCount","finishedAt"] if column in pd.DataFrame(jobs).columns]], width="stretch", height=420)

st.caption("NAVER Search Ad × NAVER API HUB · Secret 값은 화면과 저장소에 노출하지 않습니다.")
