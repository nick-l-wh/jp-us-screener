import { useState, type ReactNode } from "react";
import { buildCoverageNote, coverageFromSnapshot, type IngestDiagnostics, type PublicUniverse } from "./coverageNote";
import { SCAN_ROUTES, ScanResults, type MarketCode } from "./ScanResults";
import { buildCriteria, CRITERIA_HEADING } from "./scanCriteria";
import type { TradingDay } from "./history";
import { hitLabel } from "./publication";
import type { Snapshot } from "./types";

const SCAN_IDS = SCAN_ROUTES.map(scan => scan.id);

export function MarketResults({ market, scanId, history, choice, onChoose, onImport, error, loading, snapshotId, snapshot, universe, ingest }: {
  market: MarketCode;
  scanId: string | null;
  history: Record<string, TradingDay>;
  choice?: string;
  onChoose: (key: string) => void;
  onImport: (file: File | undefined) => void;
  error: string | null;
  loading?: boolean;
  snapshotId?: string;
  snapshot?: Snapshot | null;
  universe?: PublicUniverse | null;
  ingest?: IngestDiagnostics | null;
}) {
  const marketPath = "#/" + market.toLowerCase();
  const [enabled, setEnabled] = useState<Record<string, boolean>>(() => Object.fromEntries(SCAN_IDS.map(id => [id, true])));
  const [toolsOpen, setToolsOpen] = useState(false);
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
  const used = SCAN_IDS.filter(id => enabled[id]).length;
  const resultsHref = `${marketPath}/${scanId ?? "adx"}`;

  function count(id: string) {
    if (!date) return null;
    const block = day?.scans[id];
    if (!block) return null;
    return hitLabel(block.publication, block.blocked ? null : block.rows.length, block.blocked);
  }

  return (
    <div className="screen-shell">
      <header className="screen-bar">
        <h1>JP and US screen</h1>
        <nav className="screen-toggle" aria-label="股票市場">
          {(["US", "JP"] as const).map(code =>
            <a key={code} href={`#/${code.toLowerCase()}${scanId ? "/" + scanId : ""}`}
               aria-current={code === market ? "page" : undefined} data-testid={`market-${code}`}>
              {code === "US" ? "US 美國" : "JP 日本"}
            </a>
          )}
        </nav>
      </header>
      <div className="screen-tabs">
        <a href={marketPath} aria-current={meta ? undefined : "page"} data-testid="tab-scans">Your Scans</a>
        <a href={resultsHref} aria-current={meta ? "page" : undefined} data-testid="tab-results">Scan Results</a>
        <p className="screen-date" data-testid="data-date">{market} 日期：{date ?? "—"}</p>
      </div>
      <main className="screen-wrap">
        <section className="screen-card">
          <div className="screen-card-head">
            <button type="button" className="screen-menu" aria-expanded={toolsOpen} aria-label="資料工具" onClick={() => setToolsOpen(open => !open)}>≡</button>
            <h2>{meta ? "Scan Results" : "Your Scans"}</h2>
            <span className="screen-pill">{market === "US" ? "美國市場 (USD)" : "日本市場 (JPY)"}</span>
            <label className="screen-import">
              匯入快照 JSON
              <input data-testid="import-snapshot" type="file" accept="application/json,.json" onChange={event => onImport(event.target.files?.[0])} />
            </label>
          </div>
          {toolsOpen && (
            <div className="screen-tools">
              <label>
                交易日期
                <select aria-label="交易日期" disabled={!options.length} value={selected?.key ?? ""} onChange={event => onChoose(event.target.value)}>
                  {!options.length && <option value="">尚無交易日資料</option>}
                  {dateOptions}
                </select>
              </label>
              <a href="#/workspace">進階資料工具</a>
              {day?.coverage && <p>行情檢查通過 {day.coverage.eligible.toLocaleString()} 隻。</p>}
            </div>
          )}
          {loading && !day && <p className="screen-loading" data-testid="loading-published">正在載入已公布結果</p>}
          {error && <p className="screen-error" role="alert">{error}</p>}
          {day?.demo && <p className="screen-demo" role="status">示範／合成資料，並非實際行情或今日命中</p>}
          {!meta ? (
            <>
              <div className="screen-info">
                <p>You have used <strong>{used}</strong> of your <strong>6</strong> scans</p>
                <p className="screen-source">資料日期：{date ?? "—"} ｜ {sourceNote(day, snapshotId)}</p>
                <p className="screen-source" data-testid="universe-rule">宇宙是 Russell 1000 與 TOPIX 500，不再按市值分成大型與中型。Squeeze 與 Weak ADX 不再使用市值條件。已公布快照是美國 2026-11-25、日本 2026-11-26。</p>
              </div>
              <Coverage note={day?.notice} snapshot={snapshot} universe={universe} ingest={ingest} market={market} date={date} demo={day?.demo} />
              <div className="screen-table-wrap">
                <table className="screen-table" aria-label="Your Scans">
                  <thead>
                    <tr>
                      <th scope="col">✓</th>
                      <th scope="col">SCAN NAME</th>
                      <th scope="col" className="num">MATCHES</th>
                      <th scope="col">MARKET RULE / SORT</th>
                    </tr>
                  </thead>
                  <tbody>
                    {SCAN_ROUTES.map(scan => (
                      <tr key={scan.id}>
                        <td>
                          <input type="checkbox" checked={enabled[scan.id]} aria-label={`使用 ${scan.en}`}
                            onChange={event => setEnabled(current => ({ ...current, [scan.id]: event.target.checked }))} />
                        </td>
                        <th scope="row">
                          <a className="scan-name" href={`${marketPath}/${scan.id}`}>{scan.en}</a>
                          <span className="scan-zh">{scan.zh}</span>
                        </th>
                        <td className="num" data-testid={`count-${scan.id}`}>{count(scan.id) ?? "—"}</td>
                        <td className="rule">{marketRule(scan.id, market)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          ) : (
            <div className="screen-results">
              <ScanCriteriaSection scanId={meta.id} />
              <ScanResults key={market + meta.id + selected?.key} scanId={meta.id} market={market} day={day} date={date} />
            </div>
          )}
        </section>
      </main>
    </div>
  );
}

function sourceNote(day: TradingDay | null, snapshotId?: string): string {
  if (!day) return "尚未載入快照";
  if (day.demo) return "示範／合成資料";
  if (day.source === "published") return snapshotId ? `已公布快照 ${snapshotId}` : "已公布快照";
  return "瀏覽器匯入的快照";
}

function marketRule(id: string, market: MarketCode): string {
  if (id === "squeeze") return "條件同時成立；沒有市值條件";
  if (id === "turtle") return market === "JP" ? "條件同時成立；收市 > 500 日圓" : "條件同時成立；收市 > 5 美元";
  if (id === "weak") return "指數名單內的排名條件，再加其餘條件；沒有市值條件";
  return SCAN_ROUTES.find(scan => scan.id === id)?.rule ?? "";
}

function Coverage({ note, snapshot, universe, ingest, market, date, demo }: {
  note?: string;
  snapshot?: Snapshot | null;
  universe?: PublicUniverse | null;
  ingest?: IngestDiagnostics | null;
  market: MarketCode;
  date: string | null;
  demo?: boolean;
}): ReactNode {
  const generated = !demo && snapshot && date && snapshot.markets[market]?.as_of === date
    ? buildCoverageNote(coverageFromSnapshot(snapshot), universe ?? null, ingest ?? null)
    : null;
  const shown = generated ?? note ?? "";
  if (!shown) return null;
  const historical = !generated && shown.includes("按市值劃分");
  const paragraphs = shown.split(/\n\n+/).filter((paragraph) => paragraph.length > 0);
  return (
    <details className="screen-caveat">
      <summary>覆蓋範圍與限制</summary>
      {historical && <p className="screen-source">這段是 2026-11-20 快照當時的說明，不是現在的宇宙規則。現行宇宙是 Russell 1000 與 TOPIX 500，不再按市值分成大型與中型。</p>}
      {paragraphs.length > 0 && (
        <div className="publication-note" data-testid="publication-note">
          {paragraphs.map((paragraph) => <p key={paragraph}>{paragraph}</p>)}
        </div>
      )}
    </details>
  );
}

function ScanCriteriaSection({ scanId }: { scanId: string }): ReactNode {
  const criteria = buildCriteria(scanId);
  return (
    <section className="screen-criteria" data-testid="scan-criteria" data-scan-count={criteria.scans.length} aria-label={CRITERIA_HEADING}>
      <h3>{CRITERIA_HEADING}</h3>
      <p className="criteria-intro">{criteria.intro}</p>
      {criteria.scans.map((scan) => (
        <article key={scan.id} data-testid={`criteria-${scan.id}`}>
          <h4>{scan.title}</h4>
          <p>{scan.text}</p>
        </article>
      ))}
    </section>
  );
}
