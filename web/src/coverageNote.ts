import type { Snapshot } from "./types";

/**
 * Short coverage note for the Scan Results accordion.
 *
 * Dates, list sizes, and included counts come from the loaded snapshot and the
 * universe manifest. Omission names are the index symbols absent from the
 * snapshot's ranked and unranked rows. A reason (no bars, or an adjusted-close
 * jump) is attached only when ingest diagnostics for this snapshot id name
 * exactly those symbols. Short history is the unranked rows whose score is
 * unavailable: the score needs EMA(200), so that reason means fewer than 200
 * daily bars. The Thanksgiving sentence is only for a carried US session whose
 * expected weekday was 2026-11-26.
 */

export interface UniverseSide {
  id?: string;
  name: string;
  as_of: string;
  rows: number;
  source: string;
  note: string;
  symbols: string[];
  series?: Record<string, number>;
}

export interface PublicUniverse {
  decision?: string;
  US: UniverseSide;
  JP: UniverseSide;
}

export interface IngestDiagnostics {
  snapshot_id: string;
  no_bars: { US: string[]; JP: string[] };
  jump: { US: string[]; JP: string[] };
}

export interface CoverageSnapshot {
  snapshot_id: string;
  generated_at: string;
  semantics_version: string;
  markets: {
    US?: { as_of: string | null; eligible_stocks: number };
    JP?: { as_of: string | null; eligible_stocks: number };
  };
  eligibleSymbols: { US: string[]; JP: string[] };
  unranked: { market: string; symbol: string; reason: string | null }[];
}

const CALENDAR = "沒有內建交易所假期表。預期交易日沒有足夠完整行情時沿用上一個密集交易日；與已公布日期相同則不覆寫。下載或計算失敗會保留上一個公開快照。";
const THANKSGIVING_EXPECTED = "2026-11-26";
const SERIES_ORDER = ["TOPIX Core30", "TOPIX Large70", "TOPIX Mid400"];

export function isPublicUniverse(value: unknown): value is PublicUniverse {
  if (!value || typeof value !== "object") return false;
  const record = value as Partial<PublicUniverse>;
  return isUniverseSide(record.US) && isUniverseSide(record.JP);
}

export function isIngestDiagnostics(value: unknown): value is IngestDiagnostics {
  if (!value || typeof value !== "object") return false;
  const record = value as Partial<IngestDiagnostics>;
  return typeof record.snapshot_id === "string" && isSymbolPair(record.no_bars) && isSymbolPair(record.jump);
}

function isUniverseSide(value: unknown): value is UniverseSide {
  if (!value || typeof value !== "object") return false;
  const side = value as Partial<UniverseSide>;
  return typeof side.name === "string"
    && typeof side.as_of === "string"
    && typeof side.rows === "number"
    && typeof side.source === "string"
    && typeof side.note === "string"
    && Array.isArray(side.symbols)
    && side.symbols.every((symbol) => typeof symbol === "string");
}

function isSymbolPair(value: unknown): value is { US: string[]; JP: string[] } {
  if (!value || typeof value !== "object") return false;
  const pair = value as { US?: unknown; JP?: unknown };
  return isStringList(pair.US) && isStringList(pair.JP);
}

function isStringList(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

export function coverageFromSnapshot(snapshot: Snapshot): CoverageSnapshot {
  return {
    snapshot_id: snapshot.snapshot_id,
    generated_at: snapshot.generated_at,
    semantics_version: snapshot.semantics_version,
    markets: {
      US: marketOf(snapshot, "US"),
      JP: marketOf(snapshot, "JP"),
    },
    eligibleSymbols: eligibleSymbols(snapshot),
    unranked: unrankedRows(snapshot),
  };
}

function marketOf(snapshot: Snapshot, market: "US" | "JP") {
  const block = snapshot.markets[market];
  if (!block) return undefined;
  return { as_of: block.as_of, eligible_stocks: block.eligible_stocks };
}

export function eligibleSymbols(snapshot: Snapshot): { US: string[]; JP: string[] } {
  const found = { US: [] as string[], JP: [] as string[] };
  const ranks = snapshot.cohort_ranks;
  if (!ranks) return found;
  for (const market of ["US", "JP"] as const) {
    const cohorts = ranks[market];
    if (!cohorts) continue;
    for (const rows of Object.values(cohorts)) {
      for (const row of rows) found[market].push(symbolOf(row.qualified_symbol));
    }
  }
  for (const row of ranks.unranked ?? []) {
    const market = row.market === "JP" || row.qualified_symbol.startsWith("JP:") ? "JP" : "US";
    found[market].push(symbolOf(row.qualified_symbol));
  }
  return found;
}

export function unrankedRows(snapshot: Snapshot): CoverageSnapshot["unranked"] {
  return (snapshot.cohort_ranks?.unranked ?? []).map((row) => ({
    market: row.market === "JP" || row.qualified_symbol.startsWith("JP:") ? "JP" : "US",
    symbol: symbolOf(row.qualified_symbol),
    reason: row.reason,
  }));
}

function symbolOf(qualified: string): string {
  const split = qualified.indexOf(":");
  return split === -1 ? qualified : qualified.slice(split + 1);
}

export function buildCoverageNote(
  snapshot: CoverageSnapshot,
  universe: PublicUniverse | null,
  ingest: IngestDiagnostics | null,
): string | null {
  if (!isIndexSnapshot(snapshot, universe)) return null;
  const usDate = snapshot.markets.US?.as_of;
  const jpDate = snapshot.markets.JP?.as_of;
  const usEligible = snapshot.markets.US?.eligible_stocks;
  const jpEligible = snapshot.markets.JP?.eligible_stocks;
  if (!usDate || !jpDate || usEligible === undefined || jpEligible === undefined) return null;

  const expectedUs = expectedWeekdaySession(snapshot.generated_at, "US");
  const expectedJp = expectedWeekdaySession(snapshot.generated_at, "JP");
  const sentences = [
    usDateSentence(usDate, expectedUs),
    jpDateSentence(jpDate, expectedJp),
  ];
  if (universe) {
    sentences.push(`指數名單：${describeUs(universe.US)}。${describeJp(universe.JP)}。`);
  }
  sentences.push(`納入計算：美股 ${usEligible} 檔、日股 ${jpEligible} 檔。`);
  if (universe) {
    const usOmitted = omittedSymbols(universe.US.symbols, snapshot.eligibleSymbols.US, usEligible);
    const jpOmitted = omittedSymbols(universe.JP.symbols, snapshot.eligibleSymbols.JP, jpEligible);
    if (usOmitted?.length) sentences.push(omissionSentence("美股", usOmitted, classified(usOmitted, ingest, snapshot.snapshot_id, "US")));
    if (jpOmitted?.length) sentences.push(omissionSentence("日股", jpOmitted, classified(jpOmitted, ingest, snapshot.snapshot_id, "JP")));
  }
  const shortUs = shortHistorySentence(snapshot.unranked, "US", "美股");
  const shortJp = shortHistorySentence(snapshot.unranked, "JP", "日股");
  if (shortUs) sentences.push(shortUs);
  if (shortJp) sentences.push(shortJp);
  return `${sentences.join("")}\n\n${CALENDAR}`;
}

function isIndexSnapshot(snapshot: CoverageSnapshot, universe: PublicUniverse | null): boolean {
  if (!snapshot.semantics_version.includes("2026-11-26.index")) return false;
  const us = snapshot.markets.US?.eligible_stocks ?? 0;
  const jp = snapshot.markets.JP?.eligible_stocks ?? 0;
  if (us < 100 || jp < 100) return false;
  if (!universe) return true;
  if (us > universe.US.rows || jp > universe.JP.rows) return false;
  if (universe.US.rows - us > 40 || universe.JP.rows - jp > 40) return false;
  return true;
}

export function usDateSentence(asOf: string, expected: string | null): string {
  if (expected && asOf < expected) {
    if (expected === THANKSGIVING_EXPECTED) {
      return `美國數據日期 ${asOf}（11 月 26 日美國感恩節休市，沿用上一交易日）。`;
    }
    return `美國數據日期 ${asOf}（預期 ${expected} 沒有足夠完整行情，沿用上一交易日）。`;
  }
  return `美國數據日期 ${asOf}。`;
}

function jpDateSentence(asOf: string, expected: string | null): string {
  if (expected && asOf < expected) {
    return `日本數據日期 ${asOf}（預期 ${expected} 沒有足夠完整行情，沿用上一交易日）。`;
  }
  return `日本數據日期 ${asOf}。`;
}

function describeUs(side: UniverseSide): string {
  const iwb = /IWB/i.test(side.source) || /iShares/i.test(side.source);
  const holdings = iwb ? "iShares IWB 持股裡的權益證券" : side.source;
  const proxy = /ETF holdings proxy/i.test(side.note) || /not the FTSE Russell/i.test(side.note)
    ? "這是 ETF 持股代理，不是富時羅素官方名單"
    : "";
  return `美股是 ${side.name}（${holdings}，持股日 ${side.as_of}，共 ${side.rows} 檔${proxy ? `；${proxy}` : ""}）`;
}

function describeJp(side: UniverseSide): string {
  const series = seriesLabel(side.series);
  const body = series
    ? `${series}，名單生效 ${side.as_of}，共 ${side.rows} 檔`
    : `名單生效 ${side.as_of}，共 ${side.rows} 檔`;
  return `日股是 ${side.name}（${body}）`;
}

function seriesLabel(series: Record<string, number> | undefined): string {
  if (!series) return "";
  const names = [
    ...SERIES_ORDER.filter((name) => name in series),
    ...Object.keys(series).filter((name) => !SERIES_ORDER.includes(name)),
  ];
  return names.map((name) => name.replace(/^TOPIX\s+/, "")).join("、");
}

function omittedSymbols(universeSymbols: string[], eligible: string[], eligibleCount: number): string[] | null {
  if (eligible.length !== eligibleCount) return null;
  const have = new Set(eligible);
  const universe = new Set(universeSymbols);
  if (eligible.some((symbol) => !universe.has(symbol))) return null;
  const missing = universeSymbols.filter((symbol) => !have.has(symbol));
  if (universeSymbols.length - missing.length !== eligibleCount) return null;
  return missing;
}

function classified(
  omitted: string[],
  ingest: IngestDiagnostics | null,
  snapshotId: string,
  market: "US" | "JP",
): { noBars: string[]; jump: string[] } | null {
  if (!ingest || ingest.snapshot_id !== snapshotId) return null;
  const noBars = ingest.no_bars[market];
  const jump = ingest.jump[market];
  const noSet = new Set(noBars);
  const jumpSet = new Set(jump);
  if (noSet.size !== noBars.length || jumpSet.size !== jump.length) return null;
  const omittedSet = new Set(omitted);
  for (const symbol of noBars) {
    if (jumpSet.has(symbol) || !omittedSet.has(symbol)) return null;
  }
  for (const symbol of jump) {
    if (noSet.has(symbol) || !omittedSet.has(symbol)) return null;
  }
  if (noBars.length + jump.length !== omitted.length) return null;
  return {
    noBars: omitted.filter((symbol) => noSet.has(symbol)),
    jump: omitted.filter((symbol) => jumpSet.has(symbol)),
  };
}

function omissionSentence(
  label: "美股" | "日股",
  omitted: string[],
  groups: { noBars: string[]; jump: string[] } | null,
): string {
  if (!groups) return `${label}未納入 ${omitted.length} 檔（${omitted.join("、")}）。`;
  const parts: string[] = [];
  if (groups.noBars.length) parts.push(`${groups.noBars.join("、")} 沒有日線`);
  if (groups.jump.length) parts.push(`${groups.jump.join("、")} 因調整收市價跳升被剔除`);
  return `${label}未納入 ${omitted.length} 檔（${parts.join("，")}）。`;
}

function shortHistorySentence(unranked: CoverageSnapshot["unranked"], market: "US" | "JP", label: "美股" | "日股"): string | null {
  const rows = unranked.filter((row) => row.market === market);
  const short = rows.filter((row) => row.reason === "indicator_score_unavailable");
  const other = rows.filter((row) => row.reason && row.reason !== "indicator_score_unavailable");
  const parts: string[] = [];
  if (short.length) parts.push(`另有 ${short.length} 檔${label}不足 200 根日線，沒有技術排名，但仍會進入掃描。`);
  if (other.length) parts.push(`另有 ${other.length} 檔${label}沒有技術排名，但仍會進入掃描。`);
  return parts.join("") || null;
}

/** Last completed weekday session at the snapshot's publish cut. No holiday table. */
export function expectedWeekdaySession(generatedAt: string, market: "US" | "JP"): string | null {
  const moment = new Date(generatedAt);
  if (Number.isNaN(moment.getTime())) return null;
  const timeZone = market === "US" ? "America/New_York" : "Asia/Tokyo";
  const closeHour = market === "US" ? 16 : 15;
  let cursor = zonedISODate(moment, timeZone);
  for (let step = 0; step < 14; step += 1) {
    if (isoWeekday(cursor) < 5) {
      const closed = zonedDateTimeToUtc(cursor, closeHour, timeZone);
      if (closed.getTime() <= moment.getTime()) return cursor;
    }
    cursor = addDays(cursor, -1);
  }
  return null;
}

function isoWeekday(iso: string): number {
  const [year, month, day] = iso.split("-").map(Number);
  const js = new Date(Date.UTC(year, month - 1, day)).getUTCDay();
  return (js + 6) % 7;
}

function addDays(iso: string, days: number): string {
  const [year, month, day] = iso.split("-").map(Number);
  const date = new Date(Date.UTC(year, month - 1, day));
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

function zonedISODate(instant: Date, timeZone: string): string {
  const parts = zonedParts(instant, timeZone);
  return `${parts.year}-${parts.month}-${parts.day}`;
}

function zonedDateTimeToUtc(isoDate: string, hour: number, timeZone: string): Date {
  const [year, month, day] = isoDate.split("-").map(Number);
  const guess = Date.UTC(year, month - 1, day, hour, 0, 0);
  const offset = zoneOffsetMs(new Date(guess), timeZone);
  const adjusted = new Date(guess - offset);
  const offsetAtAdjusted = zoneOffsetMs(adjusted, timeZone);
  return new Date(guess - offsetAtAdjusted);
}

function zoneOffsetMs(instant: Date, timeZone: string): number {
  const parts = zonedParts(instant, timeZone);
  const asUtc = Date.UTC(Number(parts.year), Number(parts.month) - 1, Number(parts.day), Number(parts.hour), Number(parts.minute), Number(parts.second));
  return asUtc - instant.getTime();
}

function zonedParts(instant: Date, timeZone: string): Record<string, string> {
  const formatted = new Intl.DateTimeFormat("en-US", {
    timeZone,
    hourCycle: "h23",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).formatToParts(instant);
  const parts: Record<string, string> = {};
  for (const part of formatted) parts[part.type] = part.value;
  if (parts.hour === "24") parts.hour = "00";
  return parts;
}
