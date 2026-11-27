/** Render the official scan clauses from the engine trees in rules.json. */

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
  name_en: string;
  name_zh: string;
  warning: string | null;
  text: string;
}

export interface CriteriaView {
  intro: string[];
  scans: FormattedScan[];
}

const SCAN_ORDER = ["adx", "pullback", "squeeze", "turtle", "weak", "young"];
const YOUNG_TYPE = "Young Trend 原文沒有類型條款，股票限定是本引擎的宇宙政策。";

export function isRulesFile(value: unknown): value is RulesFile {
  if (!value || typeof value !== "object") return false;
  const record = value as Partial<RulesFile>;
  return typeof record.semantics_version === "string"
    && Array.isArray(record.assumptions)
    && Array.isArray(record.scans)
    && record.scans.every((scan) => !!scan && typeof scan.id === "string" && typeof scan.name_zh === "string");
}

export function buildCriteria(rules: RulesFile, currentId: string | null): CriteriaView {
  const byId = new Map(rules.scans.map((scan) => [scan.id, scan]));
  const youngNote = rules.assumptions.some((item) => item.id === "universe" && item.body.includes(YOUNG_TYPE))
    ? YOUNG_TYPE
    : null;
  const ordered = SCAN_ORDER.map((id) => byId.get(id)).filter((scan): scan is ScanRule => !!scan);
  for (const scan of rules.scans) {
    if (!SCAN_ORDER.includes(scan.id)) ordered.push(scan);
  }
  const formatted = ordered.map((scan) => formatScan(scan, youngNote));
  return {
    intro: introFrom(rules),
    scans: orderForCurrent(formatted, currentId),
  };
}

export function orderForCurrent(scans: FormattedScan[], currentId: string | null): FormattedScan[] {
  if (!currentId) return scans;
  const current = scans.find((scan) => scan.id === currentId);
  if (!current) return scans;
  return [current, ...scans.filter((scan) => scan.id !== currentId)];
}

function introFrom(rules: RulesFile): string[] {
  return ["grouping", "rank"]
    .map((id) => rules.assumptions.find((item) => item.id === id)?.body)
    .filter((body): body is string => !!body);
}

function formatScan(scan: ScanRule, youngNote: string | null): FormattedScan {
  const warning = scan.grouping_warning ?? null;
  if (!scan.tree) {
    return { id: scan.id, name_en: scan.name_en, name_zh: scan.name_zh, warning, text: scan.raw_source ?? "" };
  }
  const parts = [punctuate(formatNode(scan.tree, scan.jp_tree, scan.grouping))];
  if (scan.sort) parts.push(sortSentence(scan.sort));
  if (scan.id === "young" && youngNote && !hasFlag(scan.tree, "is_stock")) parts.push(youngNote);
  if ((scan.id === "squeeze" || scan.id === "weak") && !hasCap(scan.tree) && !hasCap(scan.jp_tree)) {
    parts.push("沒有市值條件。");
  }
  return { id: scan.id, name_en: scan.name_en, name_zh: scan.name_zh, warning, text: parts.join("") };
}

function formatNode(us: RuleNode, jp: RuleNode | undefined, grouping: string): string {
  if ((us.op === "and" || us.op === "or") && us.children?.length) {
    const jpChildren = jp && jp.op === us.op ? jp.children ?? [] : [];
    const parts = us.children
      .map((child, index) => formatNode(child, jpChildren[index], grouping))
      .filter((part) => part.length > 0);
    if (us.op === "or") {
      const joined = parts.join(" 或 ");
      return grouping === "explicit_brackets" ? `（${joined}）` : joined;
    }
    const separator = grouping === "provisional_cohort_and_rest" ? "；且 " : "；";
    return parts.join(separator);
  }
  const left = us.label ?? "";
  const right = jp?.label;
  if (!right || right === left) return left;
  return `美股：${left}；日股：${right}`;
}

function sortSentence(sort: ScanSort): string {
  const name = sort.field === "ulcer10" ? "Daily ULCER(10)" : sort.field === "roc3" ? "ROC(3)" : sort.field;
  const direction = sort.direction === "desc" ? "由大至小" : sort.direction === "asc" ? "由小至大" : sort.direction;
  const lead = `依 ${name} ${direction}排序。`;
  if (!sort.direction_verified && sort.note) return lead + sort.note;
  return lead;
}

function punctuate(text: string): string {
  return /[。！？.]$/.test(text) ? text : `${text}。`;
}

function hasFlag(node: RuleNode | undefined, flag: string): boolean {
  if (!node) return false;
  if (node.flag === flag) return true;
  return (node.children ?? []).some((child) => hasFlag(child, flag));
}

function hasCap(node: RuleNode | undefined): boolean {
  if (!node) return false;
  if (node.op === "cap_usd" || node.op === "cap_jpy") return true;
  return (node.children ?? []).some((child) => hasCap(child));
}
