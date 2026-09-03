import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const store = JSON.parse(await readFile(path.join(root, "data", "store.json"), "utf8"));
const keywordConfig = JSON.parse(await readFile(path.join(root, "config", "keywords.json"), "utf8"));

const records = (store.records || [])
  .filter(record => record.status === "FINAL")
  .map(record => ({
    date: record.date,
    keyword: record.keyword,
    type: record.type,
    status: "FINAL",
    estimatedQuery: record.estimatedQuery,
    calculationMode: record.calculationMode,
    finalizedAt: record.finalizedAt
  }));

const payload = { keywordConfig, records, jobs: [], updatedAt: new Date().toISOString() };
await writeFile(path.join(root, "public", "history.json"), JSON.stringify(payload), "utf8");
console.log(`광고주용 정적 스냅샷 생성: ${records.length}건`);
