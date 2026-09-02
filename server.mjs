import http from "node:http";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { createHmac } from "node:crypto";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = path.dirname(fileURLToPath(import.meta.url));
const storePath = path.join(root, "data", "store.json");
const keywordPath = path.join(root, "config", "keywords.json");
const envPath = path.join(root, ".env");
try {
  const text = await readFile(envPath, "utf8");
  for (const line of text.split(/\r?\n/)) {
    const match = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$/);
    if (match && !process.env[match[1]]) process.env[match[1]] = match[2].replace(/^['"]|['"]$/g, "");
  }
} catch {}

const required = name => {
  if (!process.env[name]) throw new Error(`${name} 환경변수가 없습니다.`);
  return process.env[name];
};
const requiredAny = names => {
  const name = names.find(candidate => process.env[candidate]?.trim());
  if (!name) throw new Error(`${names.join(" 또는 ")} 환경변수가 없습니다.`);
  return process.env[name];
};
const json = (res, status, body) => {
  res.writeHead(status, { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" });
  res.end(JSON.stringify(body));
};
const chunks = (items, size) => Array.from({ length: Math.ceil(items.length / size) }, (_, i) => items.slice(i * size, i * size + size));
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const cleanKeywords = input => [...new Set((input || []).map(v => String(v).trim()).filter(Boolean))].slice(0, 100);
const parseMonthlyCount = value => String(value).includes("<") ? 0 : Number(String(value).replace(/,/g, "")) || 0;
const configured = names => names.every(name => Boolean(process.env[name]?.trim()));
const readJsonBody = async req => {
  let raw = "";
  for await (const chunk of req) {
    raw += chunk;
    if (raw.length > 100_000) throw new Error("요청이 너무 큽니다.");
  }
  return JSON.parse(raw || "{}");
};
const todayInKst = (date = new Date()) => new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Seoul",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
}).format(date);
const shiftIsoDate = (date, days) => {
  const [year, month, day] = date.split("-").map(Number);
  const value = new Date(Date.UTC(year, month - 1, day + days));
  return value.toISOString().slice(0, 10);
};
const completedWindow = () => {
  const endDate = shiftIsoDate(todayInKst(), -1);
  return { startDate: shiftIsoDate(endDate, -29), endDate };
};
const windowForTarget = targetDate => {
  return { startDate: shiftIsoDate(targetDate, -29), endDate: targetDate };
};
const dateRange = (from, to) => {
  const dates = [];
  for (let date = from; date <= to; date = shiftIsoDate(date, 1)) dates.push(date);
  return dates;
};
const emptyStore = () => ({ version: 1, jobs: [], records: [] });
const readStore = async () => {
  try {
    return JSON.parse(await readFile(storePath, "utf8"));
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
    const store = emptyStore();
    await writeStore(store);
    return store;
  }
};
let storeWriteQueue = Promise.resolve();
const writeStore = store => {
  storeWriteQueue = storeWriteQueue.then(async () => {
    await mkdir(path.dirname(storePath), { recursive: true });
    await writeFile(storePath, JSON.stringify(store, null, 2), "utf8");
  });
  return storeWriteQueue;
};
const keywordConfig = async () => JSON.parse(await readFile(keywordPath, "utf8"));
const allConfiguredKeywords = async () => {
  const config = await keywordConfig();
  return [
    ...config.nonbrand.map(keyword => ({ keyword, type: "nonbrand" })),
    ...config.brand.map(keyword => ({ keyword, type: "brand" }))
  ];
};

async function searchAd(keywords) {
  const uri = "/keywordstool";
  const wanted = new Set(keywords.map(k => k.replace(/\s/g, "").toLowerCase()));
  const keywordList = [];
  for (const group of chunks(keywords, 5)) {
    let completed = false;
    for (let attempt = 0; attempt < 5 && !completed; attempt++) {
      if (attempt) await delay(Math.min(1000 * 2 ** attempt, 8000));
      const timestamp = String(Date.now());
      const signature = createHmac("sha256", required("NAVER_AD_SECRET_KEY"))
        .update(`${timestamp}.GET.${uri}`).digest("base64");
      const url = new URL(`https://api.searchad.naver.com${uri}`);
      url.searchParams.set("hintKeywords", group.join(","));
      url.searchParams.set("showDetail", "1");
      const response = await fetch(url, { headers: {
        "X-Timestamp": timestamp, "X-API-KEY": required("NAVER_AD_API_KEY"),
        "X-Customer": required("NAVER_AD_CUSTOMER_ID"), "X-Signature": signature
      }});
      const body = await response.json().catch(() => ({}));
      if (response.ok) { keywordList.push(...(body.keywordList || [])); completed = true; break; }
      if (response.status !== 429 || attempt === 4) throw new Error(`검색광고 API ${response.status}: ${body.title || body.detail || JSON.stringify(body)}`);
    }
    await delay(500);
  }
  const exact = new Map();
  for (const row of keywordList) {
    const key = String(row.relKeyword).replace(/\s/g, "").toLowerCase();
    if (wanted.has(key) && !exact.has(key)) exact.set(key, row);
  }
  return [...exact.values()].map(row => ({
    keyword: row.relKeyword,
    monthlyPc: parseMonthlyCount(row.monthlyPcQcCnt),
    monthlyMobile: parseMonthlyCount(row.monthlyMobileQcCnt),
    monthlyTotal: parseMonthlyCount(row.monthlyPcQcCnt) + parseMonthlyCount(row.monthlyMobileQcCnt),
    competition: row.compIdx || null,
    averagePcClicks: Number(row.monthlyAvePcClkCnt) || 0,
    averageMobileClicks: Number(row.monthlyAveMobileClkCnt) || 0
  }));
}

async function datalab(keywords, startDate, endDate) {
  const all = [];
  for (const group of chunks(keywords, 5)) {
    const response = await fetch("https://naverapihub.apigw.ntruss.com/search-trend/v1/search", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-NCP-APIGW-API-KEY-ID": requiredAny(["NAVER_API_HUB_CLIENT_ID", "NAVER_DATALAB_CLIENT_ID"]),
        "X-NCP-APIGW-API-KEY": requiredAny(["NAVER_API_HUB_CLIENT_SECRET", "NAVER_DATALAB_CLIENT_SECRET"])
      },
      body: JSON.stringify({
        startDate, endDate, timeUnit: "date",
        keywordGroups: group.map(keyword => ({ groupName: keyword, keywords: [keyword] }))
      })
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(`DataLab API ${response.status}: ${body.errorMessage || JSON.stringify(body)}`);
    all.push(...(body.results || []).map(item => ({ keyword: item.title, values: item.data })));
  }
  return all;
}

async function handleDashboard(req, res) {
  const body = await readJsonBody(req);
  const keywords = cleanKeywords(body.keywords);
  if (!keywords.length) return json(res, 400, { error: "키워드를 한 개 이상 입력하세요." });
  if (!/^\d{4}-\d{2}-\d{2}$/.test(body.startDate) || !/^\d{4}-\d{2}-\d{2}$/.test(body.endDate))
    return json(res, 400, { error: "날짜는 YYYY-MM-DD 형식이어야 합니다." });
  const [adRows, trendRows] = await Promise.all([searchAd(keywords), datalab(keywords, body.startDate, body.endDate)]);
  const adMap = new Map(adRows.map(v => [v.keyword.replace(/\s/g, "").toLowerCase(), v]));
  const trendMap = new Map(trendRows.map(v => [v.keyword, v.values]));
  json(res, 200, { generatedAt: new Date().toISOString(), startDate: body.startDate, endDate: body.endDate,
    rows: keywords.map(keyword => {
      const searchAdRow = adMap.get(keyword.replace(/\s/g, "").toLowerCase()) || null;
      const trend = trendMap.get(keyword) || [];
      const validRatios = trend.map(item => Number(item.ratio)).filter(Number.isFinite);
      const ratioSum = validRatios.reduce((sum, value) => sum + value, 0);
      const monthlyBase = searchAdRow?.monthlyTotal || 0;
      const allocated = trend.map((item, index) => {
        const raw = ratioSum ? monthlyBase * Number(item.ratio) / ratioSum : 0;
        return { ...item, index, allocationRate: ratioSum ? Number(item.ratio) / ratioSum : 0, estimatedQuery: Math.floor(raw), fraction: raw - Math.floor(raw) };
      });
      let remainder = monthlyBase - allocated.reduce((sum, item) => sum + item.estimatedQuery, 0);
      for (const item of [...allocated].sort((a, b) => b.fraction - a.fraction || a.index - b.index)) {
        if (remainder-- <= 0) break;
        item.estimatedQuery += 1;
      }
      return {
        keyword, searchAd: searchAdRow, ratioSum,
        trend: allocated.map(({ index, fraction, ...item }) => item)
      };
    }) });
}

async function runDailyJob(targetDate, retryOnly = false) {
  const today = todayInKst();
  if (!/^\d{4}-\d{2}-\d{2}$/.test(targetDate) || targetDate >= today) throw new Error("완료된 전일 이전 날짜만 확정할 수 있습니다.");
  const configuredKeywords = await allConfiguredKeywords();
  const store = await readStore();
  const finalKeys = new Set(store.records.filter(row => row.status === "FINAL").map(row => `${row.date}|${row.keyword}`));
  const failedKeys = new Set(store.records.filter(row => row.date === targetDate && row.status === "FAILED").map(row => `${row.date}|${row.keyword}`));
  const targets = configuredKeywords.filter(row => !finalKeys.has(`${targetDate}|${row.keyword}`) && (!retryOnly || failedKeys.has(`${targetDate}|${row.keyword}`)));
  const job = { id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`, targetDate, startedAt: new Date().toISOString(), status: "FETCHING", retryOnly, requested: targets.length };
  store.jobs.push(job);
  await writeStore(store);
  if (!targets.length) {
    job.status = "SKIPPED"; job.finishedAt = new Date().toISOString(); job.message = "처리할 미확정 키워드가 없습니다.";
    await writeStore(store); return job;
  }
  const keywords = targets.map(row => row.keyword);
  const window = windowForTarget(targetDate);
  try {
    const [adRows, trendRows] = await Promise.all([searchAd(keywords), datalab(keywords, window.startDate, window.endDate)]);
    const adMap = new Map(adRows.map(row => [row.keyword.replace(/\s/g, "").toLowerCase(), row]));
    const trendMap = new Map(trendRows.map(row => [row.keyword, row.values]));
    const fetchedAt = new Date().toISOString();
    for (const target of targets) {
      const recordKey = `${targetDate}|${target.keyword}`;
      const oldIndex = store.records.findIndex(row => `${row.date}|${row.keyword}` === recordKey);
      const attempts = oldIndex >= 0 ? (store.records[oldIndex].attempts || 0) + 1 : 1;
      const searchAdRow = adMap.get(target.keyword.replace(/\s/g, "").toLowerCase());
      const trend = trendMap.get(target.keyword) || [];
      const ratioSum = trend.reduce((sum, row) => sum + (Number(row.ratio) || 0), 0);
      const targetTrend = trend.find(row => row.period === targetDate);
      const completeWindow = trend.length === 30 && Boolean(targetTrend);
      let record;
      if (!searchAdRow || !ratioSum || !completeWindow) {
        record = { date: targetDate, keyword: target.keyword, type: target.type, status: "FAILED", attempts, updatedAt: fetchedAt,
          error: !searchAdRow ? "검색광고 정확 일치 키워드 없음" : !completeWindow ? "DataLab 최근 30일 또는 전일 지수 미완성" : "DataLab 지수 합계 0",
          snapshot: { fetchedAt, window, searchAd: searchAdRow || null, datalab: trend } };
      } else {
        record = { date: targetDate, keyword: target.keyword, type: target.type, status: "FINAL", attempts, finalizedAt: fetchedAt,
          estimatedQuery: Math.round(searchAdRow.monthlyTotal * Number(targetTrend.ratio) / ratioSum),
          calculation: { monthlyTotal: searchAdRow.monthlyTotal, targetRatio: Number(targetTrend.ratio), ratioSum, formula: "monthlyTotal * targetRatio / ratioSum" },
          snapshot: { fetchedAt, window, searchAd: searchAdRow, datalab: trend } };
      }
      if (oldIndex >= 0) store.records[oldIndex] = record; else store.records.push(record);
    }
    job.status = store.records.some(row => row.date === targetDate && row.status === "FAILED") ? "PARTIAL" : "FINAL";
    job.finalCount = store.records.filter(row => row.date === targetDate && row.status === "FINAL").length;
    job.failedCount = store.records.filter(row => row.date === targetDate && row.status === "FAILED").length;
  } catch (error) {
    const failedAt = new Date().toISOString();
    for (const target of targets) {
      const key = `${targetDate}|${target.keyword}`;
      const oldIndex = store.records.findIndex(row => `${row.date}|${row.keyword}` === key);
      const record = { date: targetDate, keyword: target.keyword, type: target.type, status: "FAILED", attempts: oldIndex >= 0 ? (store.records[oldIndex].attempts || 0) + 1 : 1, updatedAt: failedAt, error: error.message };
      if (oldIndex >= 0) store.records[oldIndex] = record; else store.records.push(record);
    }
    job.status = "FAILED"; job.error = error.message; job.failedCount = targets.length;
  }
  job.finishedAt = new Date().toISOString();
  await writeStore(store);
  return job;
}

async function runBackfill(from, to) {
  const today = todayInKst();
  if (!/^\d{4}-\d{2}-\d{2}$/.test(from) || !/^\d{4}-\d{2}-\d{2}$/.test(to) || from > to || to >= today)
    throw new Error("백필 기간은 완료된 전일까지 올바른 날짜로 지정해야 합니다.");
  const configuredKeywords = await allConfiguredKeywords();
  const store = await readStore();
  const dates = dateRange(from, to);
  const finalKeys = new Set(store.records.filter(row => row.status === "FINAL").map(row => `${row.date}|${row.keyword}`));
  const targets = dates.flatMap(date => configuredKeywords.filter(row => !finalKeys.has(`${date}|${row.keyword}`)).map(row => ({ ...row, date })));
  const job = { id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`, type: "BACKFILL_CURRENT_SNAPSHOT", from, to,
    startedAt: new Date().toISOString(), status: "FETCHING", requested: targets.length };
  store.jobs.push(job); await writeStore(store);
  if (!targets.length) { job.status = "SKIPPED"; job.finishedAt = new Date().toISOString(); job.message = "백필할 미확정 데이터가 없습니다."; await writeStore(store); return job; }
  try {
    const keywords = configuredKeywords.map(row => row.keyword);
    const trendStart = shiftIsoDate(from, -29);
    const [adRows, trendRows] = await Promise.all([searchAd(keywords), datalab(keywords, trendStart, to)]);
    const adMap = new Map(adRows.map(row => [row.keyword.replace(/\s/g, "").toLowerCase(), row]));
    const trendMap = new Map(trendRows.map(row => [row.keyword, row.values]));
    const fetchedAt = new Date().toISOString();
    for (const target of targets) {
      const searchAdRow = adMap.get(target.keyword.replace(/\s/g, "").toLowerCase());
      const allTrend = trendMap.get(target.keyword) || [];
      const windowStart = shiftIsoDate(target.date, -29);
      const windowTrend = allTrend.filter(row => row.period >= windowStart && row.period <= target.date);
      const ratioSum = windowTrend.reduce((sum, row) => sum + (Number(row.ratio) || 0), 0);
      const targetTrend = windowTrend.find(row => row.period === target.date) || { period: target.date, ratio: 0 };
      const key = `${target.date}|${target.keyword}`;
      const oldIndex = store.records.findIndex(row => `${row.date}|${row.keyword}` === key);
      const attempts = oldIndex >= 0 ? (store.records[oldIndex].attempts || 0) + 1 : 1;
      const valid = searchAdRow && ratioSum;
      const record = valid ? {
        date: target.date, keyword: target.keyword, type: target.type, status: "FINAL", attempts, finalizedAt: fetchedAt,
        calculationMode: "BACKFILL_CURRENT_SNAPSHOT", estimatedQuery: Math.round(searchAdRow.monthlyTotal * Number(targetTrend.ratio) / ratioSum),
        calculation: { monthlyTotal: searchAdRow.monthlyTotal, targetRatio: Number(targetTrend.ratio), ratioSum, formula: "monthlyTotal * targetRatio / ratioSum" },
        snapshot: { fetchedAt, window: { startDate: windowStart, endDate: target.date }, searchAd: searchAdRow,
          datalab: { target: targetTrend, points: windowTrend.length }, note: "과거 검색광고 스냅샷 부재로 백필 실행일의 월간검색수 사용" }
      } : { date: target.date, keyword: target.keyword, type: target.type, status: "FAILED", attempts, updatedAt: fetchedAt,
        calculationMode: "BACKFILL_CURRENT_SNAPSHOT", error: !searchAdRow ? "검색광고 정확 일치 키워드 없음" : "DataLab 지수 합계 0" };
      if (oldIndex >= 0) store.records[oldIndex] = record; else store.records.push(record);
    }
    job.finalCount = store.records.filter(row => row.date >= from && row.date <= to && row.status === "FINAL").length;
    job.failedCount = store.records.filter(row => row.date >= from && row.date <= to && row.status === "FAILED").length;
    job.status = job.failedCount ? "PARTIAL" : "FINAL";
  } catch (error) { job.status = "FAILED"; job.error = error.message; }
  job.finishedAt = new Date().toISOString(); await writeStore(store); return job;
}

async function historyResponse(url) {
  const store = await readStore();
  const from = url.searchParams.get("from") || "0000-01-01";
  const to = url.searchParams.get("to") || "9999-12-31";
  const records = store.records.filter(row => row.date >= from && row.date <= to).map(({ snapshot, ...row }) => row);
  return { records, jobs: store.jobs.slice(-30), keywordConfig: await keywordConfig(), updatedAt: store.jobs.at(-1)?.finishedAt || null };
}

const server = http.createServer(async (req, res) => {
  try {
    if (req.method === "POST" && req.url === "/api/dashboard") return await handleDashboard(req, res);
    if (req.method === "GET" && req.url.startsWith("/api/history")) return json(res, 200, await historyResponse(new URL(req.url, "http://localhost")));
    if (req.method === "GET" && req.url === "/api/keywords") return json(res, 200, await keywordConfig());
    if (req.method === "POST" && req.url === "/api/jobs/daily") {
      const body = await readJsonBody(req);
      const targetDate = body.targetDate || completedWindow().endDate;
      return json(res, 200, await runDailyJob(targetDate, false));
    }
    if (req.method === "POST" && req.url === "/api/jobs/retry") {
      const body = await readJsonBody(req);
      if (!body.targetDate) return json(res, 400, { error: "targetDate가 필요합니다." });
      return json(res, 200, await runDailyJob(body.targetDate, true));
    }
    if (req.method === "POST" && req.url === "/api/jobs/backfill") {
      const body = await readJsonBody(req);
      return json(res, 200, await runBackfill(body.from, body.to));
    }
    if (req.method === "GET" && req.url === "/api/status") return json(res, 200, {
      searchAdConfigured: configured(["NAVER_AD_API_KEY", "NAVER_AD_SECRET_KEY", "NAVER_AD_CUSTOMER_ID"]),
      datalabConfigured: configured(["NAVER_API_HUB_CLIENT_ID", "NAVER_API_HUB_CLIENT_SECRET"]) || configured(["NAVER_DATALAB_CLIENT_ID", "NAVER_DATALAB_CLIENT_SECRET"]),
      completedWindow: completedWindow()
    });
    if (req.method === "POST" && req.url === "/api/test/searchad") {
      const body = await readJsonBody(req);
      const keyword = String(body.keyword || "자동차보험").trim();
      const rows = await searchAd([keyword]);
      return json(res, 200, { ok: true, keyword, result: rows[0] || null });
    }
    if (req.method === "POST" && req.url === "/api/test/datalab") {
      const body = await readJsonBody(req);
      const keyword = String(body.keyword || "자동차보험").trim();
      const window = completedWindow();
      const rows = await datalab([keyword], window.startDate, window.endDate);
      return json(res, 200, { ok: true, keyword, ...window, device: "all (omitted)", result: rows[0] || null });
    }
    if (req.method !== "GET") return json(res, 405, { error: "Method not allowed" });
    const pathname = req.url === "/" ? "/advertiser.html" : new URL(req.url, "http://localhost").pathname;
    const file = path.join(root, "public", pathname);
    if (!file.startsWith(path.join(root, "public"))) return json(res, 403, { error: "Forbidden" });
    const data = await readFile(file);
    const ext = path.extname(file);
    res.writeHead(200, { "Content-Type": ext === ".html" ? "text/html; charset=utf-8" : "application/octet-stream" });
    res.end(data);
  } catch (error) {
    if (error.code === "ENOENT") return json(res, 404, { error: "Not found" });
    console.error(error);
    json(res, 500, { error: error.message || "서버 오류" });
  }
});
if (process.argv.includes("--run-daily")) {
  const job = await runDailyJob(completedWindow().endDate, false);
  console.log(JSON.stringify(job));
  process.exit(["FAILED", "PARTIAL"].includes(job.status) ? 1 : 0);
} else {
  server.listen(Number(process.env.PORT || 3000), () => console.log(`http://localhost:${process.env.PORT || 3000}`));
}
