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
  coverage?: { eligible: number; excluded: Record<string, number> };
  scans: Record<string, { blocked: boolean; rows: StockRow[] }>;
};

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

export async function readHistory(): Promise<TradingDay[]> {
  const db = await openHistory();
  try {
    return await new Promise<TradingDay[]>((resolve, reject) => {
      const request = db.transaction("days").objectStore("days").getAll();
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  } finally { db.close(); }
}

export async function saveDays(days: TradingDay[]): Promise<void> {
  const db = await openHistory();
  try {
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction("days", "readwrite");
      const store = tx.objectStore("days");
      for (const day of days) store.put(day);
      tx.oncomplete = () => resolve();
      tx.onabort = () => reject(tx.error);
    });
  } finally { db.close(); }
}
