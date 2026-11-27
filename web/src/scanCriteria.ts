/** Official display copy for 「掃描條件」. This is not derived from the engine trees. */

export interface RuleNode {
  op: string;
  id?: string;
  label?: string;
  flag?: string;
  children?: RuleNode[];
}

export interface ScanSort {
  field: string;
  direction: string;
  direction_verified: boolean;
  note: string;
}

export interface ScanRule {
  id: string;
  name_en: string;
  name_zh: string;
  grouping: string;
  grouping_warning?: string | null;
  sort: ScanSort | null;
  raw_source?: string;
  tree?: RuleNode;
  jp_tree?: RuleNode;
}

export interface RulesFile {
  semantics_version: string;
  assumptions: { id: string; title: string; body: string }[];
  scans: ScanRule[];
}

export interface FormattedScan {
  id: string;
  title: string;
  text: string;
}

export interface CriteriaView {
  intro: string;
  scans: FormattedScan[];
}

export const CRITERIA_HEADING = "掃描條件";

export const CRITERIA_INTRO =
  "排名按 ChartSchool 公開的 SCTR 權重，在每個市場的整份指數名單內計算一次，標成「技術排名（依SCTR公開方法，非官方）」。";

const OFFICIAL_SCANS: FormattedScan[] = [
  {
    id: "adx",
    title: "ADX",
    text: [
      "大型族群技術排名 > 0 或 中型族群技術排名 > 0；且 90 日 ROC > 20；",
      "且 今日 +DI(14) > 不含今日的前 25 根 +DI(14) 最高值；",
      "且 類型為股票；且 成交量（股數）> 100000；",
      "且 所屬族群技術排名 > 70。",
    ].join("\n"),
  },
  {
    id: "pullback",
    title: "Pullback回調",
    text: [
      "大型族群技術排名 > 70 或 中型族群技術排名 > 70；且 20 日 ROC > 20；",
      "且 +DI(14) > 30；且 類型為股票。",
      "依 Daily ULCER(10) 由大至小排序。Daily ULCER(10) 是排序不是過濾。",
    ].join("\n"),
  },
  {
    id: "squeeze",
    title: "Squeeze On擠壓開啟",
    text: [
      "類型為股票；",
      "美股：國家為美國；日股：日本股票；",
      "20 日平均成交量（股數）> 300000；",
      "收市 > SMA(200)；SMA(200) > 20 根之前的 SMA(200)；",
      "Keltner 上軌 > 布林上軌；Keltner 下軌 < 布林下軌；",
      "布林寬度等於含今日的 63 根最小值；",
      "+DI(14) < 20；-DI(14) < 20。",
    ].join("\n"),
  },
  {
    id: "turtle",
    title: "Turtle Soup Plus One - buy海龜湯加一（買入）",
    text: [
      "類型為股票；",
      "美股：國家為美國；日股：日本股票；",
      "20 日平均成交量（股數）> 500000；",
      "group 不是 ETF；",
      "美股：收市 > 5（美元；不換算成日圓）；日股：收市 > 500 日圓；",
      "今日低位等於含今日的 20 根最低值；",
      "3 至 10 根前任一低位等於昨日的 20 根最低值；",
      "收市 <= 昨日 20 根最低值 × 1.005。",
    ].join("\n"),
  },
  {
    id: "weak",
    title: "Weak ADX弱 ADX",
    text: [
      "類型為股票；成交量（股數）> 100000；",
      "（大型族群技術排名 > 0 或 中型族群技術排名 > 0）；",
      "所屬族群技術排名 < 20；",
      "ADX(14) < 20；今日 +DI(14) < 不含今日的前 25 根 +DI(14) 最低值。",
    ].join("\n"),
  },
  {
    id: "young",
    title: "Young Trend初生趨勢",
    text: [
      "大型族群技術排名 > 50 或 中型族群技術排名 > 50；",
      "且 3 日 ROC > 9；且 SMA(3,成交量) > SMA(50,成交量) × 1.5。依 ROC(3) 由大至小排序。",
      "ROC(3) 是排序不是過濾。預設較大值在前。",
    ].join("\n"),
  },
];

export function isRulesFile(value: unknown): value is RulesFile {
  if (!value || typeof value !== "object") return false;
  const record = value as Partial<RulesFile>;
  return typeof record.semantics_version === "string"
    && Array.isArray(record.assumptions)
    && Array.isArray(record.scans)
    && record.scans.every((scan) => !!scan && typeof scan.id === "string" && typeof scan.name_zh === "string");
}

/** A known scan id shows only that scan. Any other view shows all six, in official order. */
export function buildCriteria(currentId: string | null): CriteriaView {
  const current = OFFICIAL_SCANS.find((scan) => scan.id === currentId);
  return {
    intro: CRITERIA_INTRO,
    scans: current ? [current] : OFFICIAL_SCANS,
  };
}
