export interface ParsedCsv {
  headers: string[];
  rows: string[][];
}

export function parseCsv(text: string): ParsedCsv {
  const source = text.replace(/^\uFEFF/, "");
  const rows: string[][] = [];
  let cell = "";
  let row: string[] = [];
  let quoted = false;
  for (let index = 0; index < source.length; index += 1) {
    const char = source[index];
    if (quoted) {
      if (char === '"') {
        if (source[index + 1] === '"') {
          cell += '"';
          index += 1;
        } else {
          quoted = false;
        }
      } else {
        cell += char;
      }
      continue;
    }
    if (char === '"') {
      quoted = true;
      continue;
    }
    if (char === ",") {
      row.push(cell);
      cell = "";
      continue;
    }
    if (char === "\n" || char === "\r") {
      if (char === "\r" && source[index + 1] === "\n") index += 1;
      row.push(cell);
      cell = "";
      if (row.some((value) => value.trim() !== "")) rows.push(row);
      row = [];
      continue;
    }
    cell += char;
  }
  if (cell.length || row.length) {
    row.push(cell);
    if (row.some((value) => value.trim() !== "")) rows.push(row);
  }
  if (!rows.length) return { headers: [], rows: [] };
  const [headers, ...body] = rows;
  return { headers: headers.map((header) => header.trim()), rows: body };
}

export function toCsv(headers: string[], rows: Record<string, string>[]): string {
  const lines = [headers.map(escapeCell).join(",")];
  for (const row of rows) lines.push(headers.map((header) => escapeCell(row[header] ?? "")).join(","));
  return lines.join("\n") + "\n";
}

function escapeCell(value: string): string {
  if (/[",\n\r]/.test(value)) return `"${value.replace(/"/g, '""')}"`;
  return value;
}

export interface PreflightIssue {
  level: "error" | "warn";
  message: string;
}

export interface PreflightReport {
  kind: "ohlcv" | "universe" | "unknown";
  rows: number;
  markets: string[];
  symbols: number;
  firstDate: string | null;
  lastDate: string | null;
  issues: PreflightIssue[];
}

const OHLCV = ["market", "symbol", "date", "open", "high", "low", "close", "volume", "adjustment"];
const UNIVERSE = ["market", "symbol", "name", "security_type", "listing_segment", "exchange", "currency", "cohort", "market_cap", "market_cap_currency", "market_cap_as_of", "group"];

export function preflightCsv(text: string): PreflightReport {
  const parsed = parseCsv(text);
  const issues: PreflightIssue[] = [];
  const kind = OHLCV.every((name) => parsed.headers.includes(name))
    ? "ohlcv"
    : UNIVERSE.every((name) => parsed.headers.includes(name))
      ? "universe"
      : "unknown";
  if (kind === "unknown") {
    issues.push({ level: "error", message: "表頭不是 OHLCV 或宇宙範本。這次沒有上傳，也沒有計算。" });
  }
  const index = new Map(parsed.headers.map((header, position) => [header, position]));
  const cell = (row: string[], name: string) => row[index.get(name) ?? -1] ?? "";
  const markets = new Set<string>();
  const symbols = new Set<string>();
  let firstDate: string | null = null;
  let lastDate: string | null = null;
  const seen = new Set<string>();
  const previousDate = new Map<string, string>();
  if (kind === "ohlcv") {
    parsed.rows.forEach((row, line) => {
      const market = cell(row, "market").trim().toUpperCase();
      const symbol = cell(row, "symbol").trim();
      const date = cell(row, "date").trim();
      markets.add(market);
      symbols.add(`${market}:${symbol}`);
      if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) issues.push({ level: "error", message: `第 ${line + 2} 行日期不是 YYYY-MM-DD` });
      else {
        const day = new Date(`${date}T00:00:00Z`);
        if (day.getUTCDay() === 0 || day.getUTCDay() === 6) issues.push({ level: "error", message: `${date} 是週末，引擎不會當交易柱` });
        firstDate = !firstDate || date < firstDate ? date : firstDate;
        lastDate = !lastDate || date > lastDate ? date : lastDate;
      }
      const key = `${market}:${symbol}:${date}`;
      if (seen.has(key)) issues.push({ level: "error", message: `重複 ${key}` });
      seen.add(key);
      const prev = previousDate.get(`${market}:${symbol}`);
      if (prev && date <= prev) issues.push({ level: "error", message: `${market}:${symbol} 的 ${date} 不是嚴格遞增` });
      if (date) previousDate.set(`${market}:${symbol}`, date);
      const nums = ["open", "high", "low", "close", "volume"].map((name) => Number(cell(row, name)));
      if (nums.some((value) => !Number.isFinite(value))) {
        issues.push({ level: "error", message: `第 ${line + 2} 行有非數字價格或成交量` });
      } else {
        const [open, high, low, close, volume] = nums;
        if (high < low || high < open || high < close || low > open || low > close || close <= 0 || volume < 0) {
          issues.push({ level: "error", message: `第 ${line + 2} 行 OHLC 或成交量不一致` });
        }
      }
      if (cell(row, "adjustment").trim() !== "split_and_dividend") {
        issues.push({ level: "error", message: `第 ${line + 2} 行 adjustment 必須是 split_and_dividend` });
      }
    });
  }
  if (kind === "universe") {
    parsed.rows.forEach((row) => {
      const market = cell(row, "market").trim().toUpperCase();
      const symbol = cell(row, "symbol").trim();
      markets.add(market);
      symbols.add(`${market}:${symbol}`);
      const type = cell(row, "security_type").trim().toLowerCase();
      if (!type || type === "unknown") issues.push({ level: "warn", message: `${market}:${symbol} 的類型不明，引擎不會假設為股票` });
      if (type === "etf") issues.push({ level: "warn", message: `${market}:${symbol} 是 ETF，會在排名前剔除` });
    });
  }
  return {
    kind,
    rows: parsed.rows.length,
    markets: [...markets].filter(Boolean).sort(),
    symbols: symbols.size,
    firstDate,
    lastDate,
    issues: issues.slice(0, 12),
  };
}
