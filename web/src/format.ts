const numberFormat = new Intl.NumberFormat("zh-HK", { maximumFractionDigits: 2 });
const rankFormat = new Intl.NumberFormat("zh-HK", { minimumFractionDigits: 1, maximumFractionDigits: 1 });

export function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return numberFormat.format(value);
}

export function formatRank(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return rankFormat.format(value);
}

export function formatVolume(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return new Intl.NumberFormat("zh-HK", { maximumFractionDigits: 0 }).format(value);
}

export function diffLabel(status: string): string {
  if (status === "new") return "新增";
  if (status === "retained") return "保留";
  if (status === "dropped") return "剔除";
  if (status === "no_baseline") return "無對照";
  return status || "—";
}

export function cohortLabel(cohort: string | null): string {
  if (cohort === "large") return "大型";
  if (cohort === "mid") return "中型";
  if (cohort === "small") return "小型";
  return "未分層";
}

export function statusLabel(status: string): string {
  if (status === "complete") return "已完成";
  if (status === "complete_with_diagnostics") return "已完成，有診斷";
  if (status === "complete_zero_matches") return "已完成，沒有命中";
  if (status === "blocked_needs_jp_parameters") return "需要日股適配參數";
  return status;
}
