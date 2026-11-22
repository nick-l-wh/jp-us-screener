import type { ReactNode } from "react";
import { SCAN_ROUTES, ScanResults, type MarketCode } from "./ScanResults";
import type { TradingDay } from "./history";
import { hitLabel } from "./publication";



export function MarketResults({ market, scanId, history, choice, onChoose, tools, loading }: {
  market: MarketCode;
  scanId: string | null;
  history: Record<string, TradingDay>;
  choice?: string;
  onChoose: (key: string) => void;
  tools: ReactNode;
  loading?: boolean;
}) {
  const marketPath = "#/" + market.toLowerCase();
  const options = Object.values(history).filter(day => day.market === market).map(day => ({
    key: day.key,
    date: day.date,
    label: day.demo ? "示範／合成資料" : day.source === "published" ? "已公布" : "匯入快照",
    demo: day.demo,
  }));
  options.sort((a, b) => Number(a.demo) - Number(b.demo) || b.date.localeCompare(a.date) || a.key.localeCompare(b.key));
  const selected = options.find(option => option.key === choice) ?? options[0];
  const dateOptions = options.map(option => {
    const item = <option key={option.key} value={option.key}>{option.date}</option>;
    return options.some(other => other.key !== option.key && other.date === option.date)
      ? <optgroup key={option.key} label={option.label}>{item}</optgroup>
      : item;
  });
  const day = history[selected?.key] ?? null;
  const date = selected?.date ?? null;
  const meta = SCAN_ROUTES.find(scan => scan.id === scanId);

  function count(id: string) {
    if (!date) return null;
    const block = day?.scans[id];
    if (!block) return null;
    return hitLabel(block.publication, block.blocked ? null : block.rows.length, block.blocked);
  }

  return (
    <main className="market-page">
      <header>
        {meta && <a className="back-link" href={marketPath}>所有掃描</a>}
        <h1>{meta?.en ?? "收市掃描"}</h1>
        <nav className="market-switch" aria-label="股票市場">
          {(["US", "JP"] as const).map(code =>
            <a key={code} href={`#/${code.toLowerCase()}${scanId ? "/" + scanId : ""}`}
               aria-current={code === market ? "page" : undefined} data-testid={`market-${code}`}>
              {code === "US" ? "美股" : "日股"}
            </a>
          )}
        </nav>
        <div className="market-date">
          <select aria-label="交易日期" disabled={!options.length} value={selected?.key ?? ""} onChange={e => onChoose(e.target.value)}>
            {!options.length && <option value="">尚無交易日資料</option>}
            {dateOptions}
          </select>
          {selected?.demo && <span>示範／合成資料</span>}
          {date && <p className="market-asof" data-testid="data-date">{day?.source === "published" ? "已公布數據日期" : "數據日期"} {date}</p>}
        </div>
      </header>
      {loading && !day && <p data-testid="loading-published">正在載入已公布的 2026-11-20 結果</p>}
      {day?.notice && <p className="publication-note" data-testid="publication-note">{day.notice}</p>}
      {!meta ? (
        <div className="market-table"><table aria-label="掃描命中數">
          <thead><tr><th>掃描</th><th>命中數</th></tr></thead>
          <tbody>{SCAN_ROUTES.map(scan => <tr key={scan.id}>
            <th scope="row"><a href={`${marketPath}/${scan.id}`}>{scan.en}</a></th>
            <td data-testid={`count-${scan.id}`}>{count(scan.id) ?? "—"}</td>
          </tr>)}</tbody>
        </table></div>
      ) : (
        <ScanResults key={market + meta.id + selected?.key}
          scanId={meta.id} market={market} day={day} date={date} />
      )}
      <details className="market-tools"><summary>資料工具</summary>{tools}
        {day?.key.endsWith("/bundled") && market === "JP" && <p>日本普通股取自 JPX 上市名單。SCTR 按 TOPIX 規模分組，以 Yahoo Finance 日線在本機計算。Squeeze On 和 Weak ADX 要求當日市值大於 1,000 億日圓，Turtle Soup Plus One 要求收市價大於 500 日圓。</p>}
        {day?.coverage && <p>行情檢查通過 {day.coverage.eligible.toLocaleString()} 隻，另有 {Object.values(day.coverage.excluded).reduce((sum, n) => sum + n, 0)} 隻行情缺漏或不一致。</p>}
      </details>
    </main>
  );
}
