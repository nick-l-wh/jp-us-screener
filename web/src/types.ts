export const DEMO_DISCLAIMER = "示範／合成資料，並非實際行情或今日命中";
export const EMPTY_REAL_MESSAGE = "尚未匯入真實行情";
export const JP_ADAPT_MESSAGE = "需要日股適配參數";
export const GROUPING_WARNING = "條件分組待核對";

export const SCAN_ORDER = ["adx", "pullback", "squeeze", "turtle", "weak", "young"] as const;

export interface Clause {
  id: string;
  label: string;
  status: string;
  code: string | null;
  detail: string | null;
}

export interface MatchRow {
  qualified_symbol: string;
  market: string;
  symbol: string;
  name: string;
  cohort: string | null;
  technical_rank: number | null;
  technical_rank_large: number | null;
  technical_rank_mid: number | null;
  rank_label: string;
  rank_reason: string | null;
  last_date: string;
  diff_status: string;
  data_notice: string;
  close: number;
  volume: number;
  market_cap: number | null;
  market_cap_currency: string | null;
  indicators: Record<string, number | null>;
  sparkline: number[];
  clauses: Clause[];
  reason?: string;
}

export interface MarketResult {
  evaluation: string;
  message: string | null;
  matches: MatchRow[];
  dropped: MatchRow[];
  counts: Record<string, number>;
  diagnostics: {
    unmet_dependency: MatchRow[];
    insufficient_warmup: MatchRow[];
  };
}

export interface ScanBlock {
  id: string;
  source_id?: string;
  name_zh: string;
  name_en: string;
  raw_source: string;
  grouping: string;
  grouping_warning: string | null;
  jp_policy?: string;
  sort: { field: string; direction: string; direction_verified: boolean; note: string } | null;
  markets: Record<"US" | "JP", MarketResult>;
}

export interface Assumption {
  id: string;
  title: string;
  body: string;
}

export interface Snapshot {
  schema_version: string;
  engine_version: string;
  semantics_version: string;
  snapshot_id: string;
  generated_at: string;
  mode: "real" | "demo";
  demo_disclaimer: string | null;
  status: string;
  rank_label: string;
  markets: Record<string, { as_of: string | null; eligible_stocks: number; stale: number; calendar: string }>;
  universe_diagnostics: Record<string, { qualified_symbol: string; name: string; reason: string }[]>;
  diagnostic_counts: Record<string, number>;
  scans: Record<string, ScanBlock>;
  assumptions: Assumption[];
  diff: { baseline_id: string | null; note: string };
  cohort_ranks?: {
    unranked?: { qualified_symbol: string; reason: string | null; market?: string }[];
    US?: Record<string, { qualified_symbol: string; technical_rank: number }[]>;
    JP?: Record<string, { qualified_symbol: string; technical_rank: number }[]>;
  };
}

export function parseSnapshot(value: unknown): Snapshot {
  if (!value || typeof value !== "object") throw new Error("檔案不是快照 JSON");
  const record = value as Partial<Snapshot>;
  if (!record.schema_version || !record.scans || !record.markets || !record.mode) {
    throw new Error("快照缺少 schema_version、markets、scans 或 mode");
  }
  if (record.mode !== "real" && record.mode !== "demo") throw new Error("mode 必須是 real 或 demo");
  return record as Snapshot;
}

export function forcesDemoBanner(snapshot: Snapshot): boolean {
  return snapshot.mode === "demo" || Boolean(snapshot.demo_disclaimer);
}
