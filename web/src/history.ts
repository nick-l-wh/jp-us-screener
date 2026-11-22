import { forcesDemoBanner, type Snapshot } from "./types";

export type Market = "US" | "JP";
export type StockRow = {
  symbol: string;
  name: string;
  reference_sctr: number | null;
  local_volume?: string;
};
export type TradingDay = {
  key: string;
  market: Market;
  date: string;
  demo: boolean;
  source?: "published" | "import";
  notice?: string;
  coverage?: { eligible: number; excluded: Record<string, number> };
  scans: Record<string, { blocked: boolean; rows: StockRow[]; publication?: "valid" | "not_valid" | "incomplete" }>;
};

const TRADING_DAYS_PER_MARKET = 15;

export function recentHistory(days: TradingDay[]): Record<string, TradingDay> {
  const dates = { US: new Set<string>(), JP: new Set<string>() };
  const kept: Record<string, TradingDay> = {};
  for (const day of [...days].sort((a, b) => b.date.localeCompare(a.date))) {
    const marketDates = dates[day.market];
    if (marketDates.size === TRADING_DAYS_PER_MARKET && !marketDates.has(day.date)) continue;
    marketDates.add(day.date);
    kept[day.key] = day;
  }
  return kept;
}

export function snapshotDays(snapshot: Snapshot): TradingDay[] {
  const days: TradingDay[] = [];
  const demo = forcesDemoBanner(snapshot);
  for (const market of ["US", "JP"] as const) {
    const date = snapshot.markets[market]?.as_of;
    if (!date) continue;
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !Number.isFinite(Date.parse(date)) ||
        new Date(date).toISOString().slice(0, 10) !== date) throw new Error("快照的交易日期無效");
    const scans: TradingDay["scans"] = {};
    for (const [id, scan] of Object.entries(snapshot.scans)) {
      const block = scan.markets[market];
      if (!block) continue;
      scans[id] = {
        blocked: block.evaluation === "blocked_needs_jp_parameters",
        rows: block.matches.map(row => ({
          symbol: row.symbol, name: row.name, local_volume: String(row.volume ?? ""), reference_sctr: row.technical_rank,
        })),
      };
    }
    days.push({ key: `${market}/${date}/${demo ? "demo" : "real"}`, market, date, demo, scans });
  }
  return days;
}

function openHistory(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open("jp-us-eod-results", 1);
    request.onupgradeneeded = () => request.result.createObjectStore("days", { keyPath: "key" });
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

export async function updateHistory(days: TradingDay[] = []): Promise<Record<string, TradingDay>> {
  const db = await openHistory();
  try {
    return await new Promise<Record<string, TradingDay>>((resolve, reject) => {
      const tx = db.transaction("days", "readwrite");
      const store = tx.objectStore("days");
      const request = store.getAll();
      let kept: Record<string, TradingDay>;
      request.onsuccess = () => {
        const existing: TradingDay[] = request.result;
        kept = recentHistory([...existing, ...days]);
        for (const day of existing) if (!kept[day.key]) store.delete(day.key);
        for (const day of days) if (kept[day.key]) store.put(day);
      };
      tx.oncomplete = () => resolve(kept);
      tx.onabort = () => reject(tx.error);
    });
  } finally { db.close(); }
}
