import type { TradingDay } from "./history";

export type ScanPublication = "valid" | "not_valid" | "incomplete";

export interface PublicationNotice {
  snapshot_id: string;
  as_of: string;
  title: string;
  body: string;
  scans: Record<"US" | "JP", Record<string, ScanPublication>>;
}

const STATUSES = new Set<ScanPublication>(["valid", "not_valid", "incomplete"]);

export function isPublicationNotice(value: unknown): value is PublicationNotice {
  if (!value || typeof value !== "object") return false;
  const record = value as Partial<PublicationNotice>;
  if (typeof record.snapshot_id !== "string" || typeof record.as_of !== "string" || typeof record.body !== "string") return false;
  if (!record.scans || typeof record.scans !== "object") return false;
  return (["US", "JP"] as const).every((market) => {
    const scans = record.scans?.[market];
    return !!scans && Object.values(scans).every((status) => STATUSES.has(status));
  });
}

export function applyPublication(days: TradingDay[], notice: PublicationNotice | null): TradingDay[] {
  if (!notice) return days;
  return days.map((day) => {
    if (day.demo || day.date !== notice.as_of) return day;
    const scans = Object.fromEntries(Object.entries(day.scans).map(([id, block]) => [
      id,
      { ...block, publication: notice.scans[day.market]?.[id] ?? "valid" },
    ]));
    return { ...day, source: "published", notice: notice.body, scans };
  });
}

export function hitLabel(publication: ScanPublication | undefined, count: number | null, blocked: boolean): string {
  if (blocked || count === null) return "—";
  if (publication === "not_valid") return "不適用";
  if (publication === "incomplete") return "未完整";
  return String(count);
}

export function choosePublishedDay(days: TradingDay[], market: "US" | "JP", publishedDate: string): string | undefined {
  const mine = days.filter((day) => day.market === market && !day.demo);
  const newer = mine.filter((day) => day.date > publishedDate).sort((a, b) => b.date.localeCompare(a.date) || a.key.localeCompare(b.key));
  if (newer[0]) return newer[0].key;
  const published = mine.find((day) => day.source === "published" && day.date === publishedDate);
  if (published) return published.key;
  const sameDate = mine.filter((day) => day.date === publishedDate).sort((a, b) => a.key.localeCompare(b.key));
  return sameDate[0]?.key ?? mine.sort((a, b) => b.date.localeCompare(a.date))[0]?.key;
}

export function unavailableCopy(publication: ScanPublication): string | null {
  if (publication === "not_valid") return "這條掃描沒有有效結果，不能顯示成 0 筆命中。美國這條需要族群排名，而公布宇宙沒有 cohort。";
  if (publication === "incomplete") return "這條掃描未完整，不能顯示成 0 筆命中。日股 ADX 的中型族群是 TOPIX Mid400 的 400/401，另有兩檔歷史不足。";
  return null;
}
