import { useMemo, useState } from "react";

import { formatVolume } from "./format";
import type { StockRow, TradingDay } from "./history";
import { unavailableCopy } from "./publication";

export const SCAN_ROUTES = [
  { id: "adx", en: "ADX", zh: "ADX", rule: "指數名單內技術排名 + 標準篩選" },
  { id: "pullback", en: "Pullback", zh: "回調", rule: "依 ULCER10 由大至小排序" },
  { id: "squeeze", en: "Squeeze On", zh: "擠壓開啟", rule: "條件全部同時成立" },
  { id: "turtle", en: "Turtle Soup Plus One - buy", zh: "海龜湯加一（買入）", rule: "條件全部同時成立" },
  { id: "weak", en: "Weak ADX", zh: "弱 ADX", rule: "指數名單內的排名條件，再加其餘條件" },
  { id: "young", en: "Young Trend", zh: "初生趨勢", rule: "依 ROC3 由大至小排序" },
] as const;

export type MarketCode = "US" | "JP";

const EMPTY_ROWS: StockRow[] = [];

export function ScanResults({ scanId, market, day, date }: { scanId: string; market: MarketCode; day: TradingDay | null; date: string | null }) {
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<{ key: string; dir: "asc" | "desc" }>({ key: "symbol", dir: "asc" });
  const block = day?.scans[scanId];
  const unavailable = unavailableCopy(block?.publication ?? "valid");
  const displayRows = block?.rows ?? EMPTY_ROWS;
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const matched = needle
      ? displayRows.filter((row) => `${row.symbol} ${row.name}`.toLowerCase().includes(needle))
      : displayRows;
    return [...matched].sort((left, right) => compareValues(left, right, sort.key, sort.dir));
  }, [displayRows, query, sort]);

  function toggleSort(key: string) {
    setSort((current) => ({ key, dir: current.key === key && current.dir === "asc" ? "desc" : "asc" }));
  }

  return (
    <div className="app results-app" data-testid="scan-results" data-market={market}>
      {unavailable ? (
        <p className="publication-note" data-testid="scan-unavailable">{unavailable}</p>
      ) : !date || (day && (!block || block.blocked)) ? (
        <p className="quiet" data-testid="jp-empty">{block?.blocked ? "待設定日股篩選門檻" : "尚未有這項掃描的結果"}</p>
      ) : (
        <>
          <div className="toolbar">
            <input
              aria-label="搜尋結果"
              placeholder="搜尋代號或名稱"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />

          </div>
          <div className="table-wrap">
            <table className="dense" data-testid="result-table">
              <thead>
                <tr>
                  <th><button type="button" onClick={() => toggleSort("symbol")}>代號</button></th>
                  <th><button type="button" onClick={() => toggleSort("name")}>名稱</button></th>
                  <th><button type="button" onClick={() => toggleSort("local_volume")}>成交量</button></th>
                  <th><button type="button" onClick={() => toggleSort("reference_sctr")}>SCTR</button></th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((row) => (
                  <tr key={row.symbol}>
                    <td>{row.symbol}</td>
                    <td>{row.name || "—"}</td>
                    <td>{formatVolume(parseVolume(row.local_volume))}</td>
                    <td>{row.reference_sctr ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {filtered.length === 0 ? (
            <p className="quiet" data-testid="reference-empty">{query ? "沒有符合的代號" : "命中數 0"}</p>
          ) : null}
        </>
      )}
    </div>
  );
}

function parseVolume(value?: string) {
  if (!value?.trim()) return null;
  const n = Number(value.replace(/,/g, ""));
  return Number.isFinite(n) ? n : null;
}

type ResultRow = StockRow;

function compareValues(left: ResultRow, right: ResultRow, key: string, dir: "asc" | "desc"): number {
  const rawLeft = (left as Record<string, unknown>)[key];
  const rawRight = (right as Record<string, unknown>)[key];
  const a = key === "local_volume" ? parseVolume(left.local_volume) : rawLeft === null || rawLeft === undefined || rawLeft === "" ? null : Number(rawLeft);
  const b = key === "local_volume" ? parseVolume(right.local_volume) : rawRight === null || rawRight === undefined || rawRight === "" ? null : Number(rawRight);
  let result = 0;
  if (a !== null && b !== null && Number.isFinite(a) && Number.isFinite(b) && key !== "symbol" && key !== "name") {
    result = a - b;
  } else {
    result = String(rawLeft ?? "").localeCompare(String(rawRight ?? ""));
  }
  return dir === "asc" ? result : -result;
}
