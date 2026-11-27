import { useEffect, useMemo, useRef, useState } from "react";
import { recentHistory, snapshotDays, updateHistory, type TradingDay } from "./history";
import { asset } from "./asset";
import { applyPublication, choosePublishedDay, hitLabel, isPublicationNotice, publishedSession, unavailableCopy, type PublicationNotice } from "./publication";
import { preflightCsv, toCsv, type PreflightReport } from "./csv";
import { cohortLabel, diffLabel, formatNumber, formatRank, formatVolume, statusLabel } from "./format";
import { isIngestDiagnostics, isPublicUniverse, type IngestDiagnostics, type PublicUniverse } from "./coverageNote";
import { MarketResults } from "./MarketResults";
import { SCAN_ROUTES, type MarketCode } from "./ScanResults";
import { isRulesFile, type RulesFile } from "./scanCriteria";
import {
  DEMO_DISCLAIMER,
  EMPTY_REAL_MESSAGE,
  GROUPING_WARNING,
  SCAN_ORDER,
  forcesDemoBanner,
  parseSnapshot,
  type MatchRow,
  type Snapshot,
} from "./types";

type Market = "US" | "JP";
type Pane = "matches" | "diagnostics" | "rules";

const EXTRA: Record<string, { key: string; label: string }[]> = {
  adx: [
    { key: "roc90", label: "ROC90" },
    { key: "plus_di14", label: "+DI14" },
    { key: "prev25_max_plus_di", label: "前25高" },
  ],
  pullback: [
    { key: "roc20", label: "ROC20" },
    { key: "plus_di14", label: "+DI14" },
    { key: "ulcer10", label: "ULCER10" },
  ],
  squeeze: [
    { key: "bb_width", label: "布林寬度" },
    { key: "plus_di14", label: "+DI" },
    { key: "minus_di14", label: "−DI" },
  ],
  turtle: [
    { key: "yday_min20_low", label: "昨日20低" },
    { key: "sma20_volume", label: "20日均量" },
  ],
  weak: [
    { key: "adx14", label: "ADX14" },
    { key: "plus_di14", label: "+DI14" },
    { key: "prev25_min_plus_di", label: "前25低" },
  ],
  young: [
    { key: "roc3", label: "ROC3" },
    { key: "sma3_volume", label: "3日均量" },
    { key: "sma50_volume", label: "50日均量" },
  ],
};

type PageRoute =
  | { kind: "workspace" }
  | { kind: "scan"; market: MarketCode; scanId: string | null };

export function parseHashRoute(hash: string): PageRoute {
  const id = hash.replace(/^#\/?/, "").split(/[?#]/)[0];
  const parts = id.split("/").filter(Boolean);
  if (parts[0] === "workspace") return { kind: "workspace" };
  let market: MarketCode = "US";
  let scan = parts[0];
  if (parts[0] === "us" || parts[0] === "jp") {
    market = parts[0] === "jp" ? "JP" : "US";
    scan = parts[1];
  }
  return { kind: "scan", market, scanId: SCAN_ROUTES.some(item => item.id === scan) ? scan : null };
}

function usePageRoute(): PageRoute {
  const [hash, setHash] = useState(() => window.location.hash);
  useEffect(() => {
    const onHash = () => setHash(window.location.hash);
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  return parseHashRoute(hash);
}

export function App() {
  const route = usePageRoute();
  const [demoOn, setDemoOn] = useState(false);
  const [demoSnap, setDemoSnap] = useState<Snapshot | null>(null);
  const [history, setHistory] = useState<Record<string, TradingDay>>(() => {
    const data = document.getElementById("bundled-trading-days")?.textContent;
    const days: TradingDay[] = data ? JSON.parse(data) : [];
    return recentHistory(days);
  });
  const [chosen, setChosen] = useState<Partial<Record<Market, string>>>({});
  const [notice, setNotice] = useState<PublicationNotice | null>(null);
  const [loadingPublished, setLoadingPublished] = useState(true);
  const [realSnap, setRealSnap] = useState<Snapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const importedRef = useRef(false);
  const historyRef = useRef(history);
  historyRef.current = history;
  useEffect(() => {
    let active = true;
    (async () => {
      let loadedNotice: PublicationNotice | null = null;
      let publishedDays: TradingDay[] = [];
      let publishedSnap: Snapshot | null = null;
      try {
        const [noticeResponse, snapshotResponse] = await Promise.all([
          fetch(asset("data/publication.json")),
          fetch(asset("data/latest.json")),
        ]);
        if (noticeResponse.ok) {
          const parsed = await noticeResponse.json();
          if (isPublicationNotice(parsed)) loadedNotice = parsed;
        }
        if (snapshotResponse.ok) {
          publishedSnap = parseSnapshot(await snapshotResponse.json());
          if (loadedNotice && publishedSnap.snapshot_id !== loadedNotice.snapshot_id) loadedNotice = null;
          publishedDays = applyPublication(snapshotDays(publishedSnap), loadedNotice);
        }
      } catch {
        loadedNotice = null;
      }
      if (!active) return;
      setNotice(loadedNotice);
      let stored: Record<string, TradingDay> = {};
      try {
        stored = await updateHistory(publishedDays);
      } catch {
        stored = recentHistory(publishedDays);
      }
      if (!active) return;
      const merged = recentHistory([...Object.values(stored), ...publishedDays, ...Object.values(historyRef.current)]);
      if (importedRef.current) {
        setHistory(current => recentHistory([...Object.values(current), ...publishedDays]));
        setLoadingPublished(false);
        return;
      }
      setHistory(merged);
      if (publishedSnap) {
        const snap = publishedSnap;
        setRealSnap(current => current ?? snap);
      }
      if (loadedNotice) {
        const published = loadedNotice;
        setChosen(current => ({
          ...current,
          US: current.US ?? choosePublishedDay(Object.values(merged), "US", publishedSession(published, "US")),
          JP: current.JP ?? choosePublishedDay(Object.values(merged), "JP", publishedSession(published, "JP")),
        }));
      }
      if (!publishedSnap) setError("讀不到已公布快照");
      setLoadingPublished(false);
    })();
    return () => { active = false; };
  }, []);
  const [market, setMarket] = useState<Market>("US");
  const [scanId, setScanId] = useState<string>("adx");
  const [query, setQuery] = useState("");
  const [pane, setPane] = useState<Pane>("matches");
  const [selected, setSelected] = useState<string | null>(null);
  const [sort, setSort] = useState<{ key: string; dir: "asc" | "desc" } | null>(null);
  const [preflight, setPreflight] = useState<PreflightReport | null>(null);
  const [rulesFallback, setRulesFallback] = useState<Snapshot["assumptions"] | null>(null);
  const [currentRules, setCurrentRules] = useState<RulesFile | null>(null);
  const [universe, setUniverse] = useState<PublicUniverse | null>(null);
  const [ingest, setIngest] = useState<IngestDiagnostics | null>(null);

  const snapshot = demoOn ? demoSnap : realSnap;
  const showDemo = snapshot ? forcesDemoBanner(snapshot) : demoOn;

  useEffect(() => {
    if (!demoOn || demoSnap) return;
    fetch(asset("demo/snapshot.json"))
      .then((response) => {
        if (!response.ok) throw new Error("讀不到示範快照");
        return response.json();
      })
      .then((payload) => setDemoSnap(parseSnapshot(payload)))
      .catch((reason: Error) => setError(reason.message));
  }, [demoOn, demoSnap]);

  useEffect(() => {
    fetch(asset("rules.json"))
      .then((response) => (response.ok ? response.json() : null))
      .then((payload) => {
        if (payload?.assumptions) setRulesFallback(payload.assumptions);
        if (isRulesFile(payload)) setCurrentRules(payload);
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    let active = true;
    Promise.all([
      fetch(asset("data/universe.json")).then((response) => (response.ok ? response.json() : null)),
      fetch(asset("data/ingest-diagnostics.json")).then((response) => (response.ok ? response.json() : null)),
    ]).then(([universePayload, ingestPayload]) => {
      if (!active) return;
      if (isPublicUniverse(universePayload)) setUniverse(universePayload);
      if (isIngestDiagnostics(ingestPayload)) setIngest(ingestPayload);
    }).catch(() => undefined);
    return () => { active = false; };
  }, []);

  const scan = snapshot?.scans[scanId];
  const staleRules = Boolean(snapshot && currentRules && !rulesMatch(snapshot, currentRules));
  const shownAssumptions = staleRules && currentRules ? currentRules.assumptions : snapshot?.assumptions ?? rulesFallback ?? [];
  const shownScan = scan && staleRules && currentRules
    ? { ...scan, raw_source: currentRules.scans.find((item) => item.id === scan.id)?.raw_source ?? scan.raw_source }
    : scan;
  const result = scan?.markets[market];
  const publishedNotice = notice && snapshot?.snapshot_id === notice.snapshot_id ? notice : null;

  const rows = useMemo(() => {
    const source = result?.matches ?? [];
    const needle = query.trim().toLowerCase();
    const filtered = needle
      ? source.filter((row) => `${row.qualified_symbol} ${row.name}`.toLowerCase().includes(needle))
      : source;
    const active = sort ?? defaultSort(scan?.sort);
    if (!active) return filtered;
    return [...filtered].sort((left, right) => compareRows(left, right, active.key, active.dir));
  }, [result, query, sort, scan]);

  async function onSnapshotFile(file: File | undefined) {
    if (!file) return;
    importedRef.current = true;
    try {
      const parsed = parseSnapshot(JSON.parse(await file.text()));
      const matchedNotice = notice && parsed.snapshot_id === notice.snapshot_id ? notice : null;
      const days = applyPublication(snapshotDays(parsed), matchedNotice);
      if (!days.length && route.kind === "scan") throw new Error("快照缺少美股或日股的交易日期");
      setHistory(current => recentHistory([...Object.values(current), ...days]));
      setChosen(current => ({ ...current, ...Object.fromEntries(days.map(day => [day.market, day.key])) }));
      setRealSnap(parsed);
      setDemoOn(false);
      setError(null);
      setPane("matches");
      setSelected(null);
      try {
        const stored = await updateHistory(days);
        setHistory(current => recentHistory([...Object.values(stored), ...Object.values(current)]));
      }
      catch { setError("已載入本次結果。瀏覽器無法儲存歷史，重新開啟後請再匯入。"); }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "無法讀取快照");
    }
  }

  async function onCsvFile(file: File | undefined) {
    if (!file) return;
    setPreflight(preflightCsv(await file.text()));
  }

  function exportCsv() {
    if (!snapshot || !result) return;
    const headers = ["data_notice", "scan_id", "market", "qualified_symbol", "name", "cohort", "diff_status", "last_date", "technical_rank", "close", "volume"];
    const notice = showDemo ? DEMO_DISCLAIMER : "";
    const body = [...rows, ...result.dropped].map((row) => ({
      data_notice: notice || row.data_notice || "",
      scan_id: scanId,
      market,
      qualified_symbol: row.qualified_symbol,
      name: row.name,
      cohort: row.cohort ?? "",
      diff_status: diffLabel(row.diff_status),
      last_date: row.last_date,
      technical_rank: row.technical_rank === null || row.technical_rank === undefined ? "" : String(row.technical_rank),
      close: row.close === undefined || row.close === null ? "" : String(row.close),
      volume: row.volume === undefined || row.volume === null ? "" : String(row.volume),
    }));
    if (showDemo && body.length === 0) {
      body.push({ data_notice: DEMO_DISCLAIMER, scan_id: scanId, market, qualified_symbol: "", name: "", cohort: "", diff_status: "", last_date: "", technical_rank: "", close: "", volume: "" });
    }
    const blob = new Blob([toCsv(headers, body)], { type: "text/csv;charset=utf-8" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `${showDemo ? "demo" : "snapshot"}-${scanId}-${market}.csv`;
    link.click();
    URL.revokeObjectURL(link.href);
  }

  const selectedRow = rows.find((row) => row.qualified_symbol === selected) ?? result?.dropped.find((row) => row.qualified_symbol === selected) ?? null;

  if (route.kind === "scan") {
    return <MarketResults scanId={route.scanId} market={route.market} history={history} choice={chosen[route.market]} loading={loadingPublished}
      snapshotId={notice?.snapshot_id ?? realSnap?.snapshot_id}
      snapshot={realSnap}
      universe={universe}
      ingest={ingest}
      error={error}
      onImport={file => void onSnapshotFile(file)}
      onChoose={key => setChosen(current => ({ ...current, [route.market]: key }))} />;
  }

  return (
    <div className="app">
      <header className="top">
        <div>
          <p className="eyebrow">美股與日股 · 只限股票</p>
          <h1 data-testid="page-title">日美收市篩選</h1>
        </div>
        <div className="top-actions">
          <button type="button" className={demoOn ? "toggle on" : "toggle"} aria-pressed={demoOn} data-testid="demo-toggle" onClick={() => setDemoOn((value) => !value)}>
            {demoOn ? "示範開" : "示範關"}
          </button>
          <label className="file">
            匯入快照
            <input data-testid="import-snapshot" type="file" accept="application/json,.json" onChange={(event) => void onSnapshotFile(event.target.files?.[0])} />
          </label>
        </div>
      </header>
      {showDemo && (
        <div className="demo-banner" data-testid="demo-banner" role="status">
          {DEMO_DISCLAIMER}
        </div>
      )}
      {error && <div className="error-banner">{error}</div>}
      <div className="workspace">
        <aside>
          <section>
            <h2>市場</h2>
            <div className="segment" role="tablist" aria-label="市場">
              {(["US", "JP"] as const).map((code) => (
                <button key={code} type="button" role="tab" aria-selected={market === code} data-testid={`market-${code}`} className={market === code ? "active" : ""} onClick={() => { setMarket(code); setSelected(null); }}>
                  {code === "US" ? "美國" : "日本"}
                </button>
              ))}
            </div>
          </section>
          <section data-testid="data-status">
            <h2>數據狀態</h2>
            {snapshot ? (
              <dl>
                <div><dt>美國數據日期</dt><dd>{snapshot.markets.US?.as_of ?? "沒有日期"}</dd></div>
                <div><dt>日本數據日期</dt><dd>{snapshot.markets.JP?.as_of ?? "沒有日期"}</dd></div>
                <div><dt>快照</dt><dd>{statusLabel(snapshot.status)} · {snapshot.snapshot_id}</dd></div>
                <div><dt>引擎</dt><dd>{snapshot.engine_version} · {snapshot.semantics_version}</dd></div>
                <div><dt>對照</dt><dd>{snapshot.diff.note}</dd></div>
                {publishedNotice && (
                  <div>
                    <dt>{staleRules ? "這份快照的公布說明" : "公布說明"}</dt>
                    <dd>
                      {staleRules && <p className="warn">下面是這份快照當時的文字，不是 2026-11-26 的宇宙規則。現行宇宙是 Russell 1000 與 TOPIX 500，不再按市值分成大型與中型。</p>}
                      {publishedNotice.body}
                    </dd>
                  </div>
                )}
              </dl>
            ) : (
              <p className="quiet">真實模式尚未載入快照。</p>
            )}
          </section>
          <section>
            <h2>本地預檢</h2>
            <p className="quiet">只在瀏覽器讀取，不上傳，也不在網頁計算指標。</p>
            <label className="file">OHLCV CSV<input data-testid="import-ohlcv" type="file" accept=".csv,text/csv" onChange={(event) => void onCsvFile(event.target.files?.[0])} /></label>
            <label className="file">宇宙 CSV<input data-testid="import-universe" type="file" accept=".csv,text/csv" onChange={(event) => void onCsvFile(event.target.files?.[0])} /></label>
            <p className="links">
              <a href={asset("templates/ohlcv.csv")}>OHLCV 範本</a>
              <a href={asset("templates/universe.csv")}>宇宙範本</a>
              <a href={asset("templates/marketcap.csv")}>市值範本</a>
            </p>
            {preflight && <PreflightView report={preflight} />}
          </section>
        </aside>
        <main>
          {!snapshot && (
            <section className="empty" data-testid="empty-real">
              <p className="seal">實</p>
              <h2>{loadingPublished ? "正在載入已公布結果" : EMPTY_REAL_MESSAGE}</h2>
              <p>{loadingPublished ? "正在載入已公布的美國與日本收市結果。" : "用命令列由本地 CSV 算出快照，再在這裡匯入。示範開關只會載入合成代號，不會填上看似真實的報價。"}</p>
            </section>
          )}
          {snapshot && scan && result && (
            <>
              <div className="scans" role="tablist" aria-label="掃描">
                {SCAN_ORDER.map((id) => {
                  const block = snapshot.scans[id];
                  const count = block?.markets[market]?.counts.matches ?? 0;
                  const status = publishedNotice?.scans[market]?.[id] ?? "valid";
                  const blocked = block?.markets[market]?.evaluation === "blocked_needs_jp_parameters";
                  return (
                    <button key={id} type="button" role="tab" aria-selected={scanId === id} data-testid={`scan-${id}`} className={scanId === id ? "active" : ""} onClick={() => { setScanId(id); setSort(null); setSelected(null); setPane("matches"); }}>
                      <span>{block?.name_zh ?? id}</span>
                      <strong>{hitLabel(status, count, blocked)}</strong>
                    </button>
                  );
                })}
              </div>
              {scan.grouping_warning && <div className="warn" data-testid="grouping-warning">{GROUPING_WARNING}。暫定語意是（大型或中型欄位）並且其餘條件，不是已核對的官方語法。這不是市值分層；2026-11-26 起整份指數放在同一個排名池。</div>}
              {scan.sort && !scan.sort.direction_verified && <p className="sort-note">{scan.sort.note}</p>}
              <div className="strip" data-testid="counts">
                <span>{(publishedNotice?.scans[market]?.[scanId] ?? "valid") === "valid" ? `命中 ${result.counts.matches ?? 0}` : hitLabel(publishedNotice?.scans[market]?.[scanId], result.counts.matches ?? 0, false)}</span>
                <span>新增 {result.counts.new ?? 0}</span>
                <span>保留 {result.counts.retained ?? 0}</span>
                <span>剔除 {result.counts.dropped ?? result.dropped.length}</span>
                <span>未滿足依賴 {result.counts.unmet_dependency ?? 0}</span>
                <span>歷史不足 {result.counts.insufficient_warmup ?? 0}</span>
                <span className="grow">{statusLabel(result.evaluation)}</span>
              </div>
              <div className="toolbar">
                <div className="panes">
                  {(["matches", "diagnostics", "rules"] as const).map((item) => (
                    <button key={item} type="button" className={pane === item ? "active" : ""} onClick={() => setPane(item)}>
                      {item === "matches" ? "命中" : item === "diagnostics" ? "診斷" : "規則"}
                    </button>
                  ))}
                </div>
                <input data-testid="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜尋代號或名稱" />
                <button type="button" data-testid="export-csv" onClick={exportCsv}>匯出 CSV</button>
              </div>
              {pane === "matches" && (
                unavailableCopy(publishedNotice?.scans[market]?.[scanId] ?? "valid") ? (
                  <section className="blocked" data-testid="scan-unavailable">
                    <h2>{(publishedNotice?.scans[market]?.[scanId]) === "incomplete" ? "結果未完整" : "沒有有效結果"}</h2>
                    <p>{unavailableCopy(publishedNotice?.scans[market]?.[scanId] ?? "valid")}</p>
                  </section>
                ) : result.evaluation === "blocked_needs_jp_parameters" ? (
                  <section className="blocked" data-testid="jp-blocked">
                    <h2>{result.message}</h2>
                    <p>這條掃描的原文限美國。真實模式會跑日股：Squeeze 不再另加市值條件，Turtle 的收市價用 500 日圓。美元價格不會改寫成日圓。</p>
                  </section>
                ) : (
                  <Results rows={rows} dropped={result.dropped} scanId={scanId} sort={sort} onSort={(key) => setSort((current) => ({ key, dir: current?.key === key && current.dir === "desc" ? "asc" : "desc" }))} selected={selected} onSelect={setSelected} />
                )
              )}
              {pane === "diagnostics" && <Diagnostics snapshot={snapshot} market={market} result={result} />}
              {pane === "rules" && shownScan && (
                <>
                  {staleRules && <p className="warn" data-testid="rules-version-note">載入的快照是較早的規則版本。下面是 2026-11-26 的現行規則：宇宙是 Russell 1000 與 TOPIX 500，不再按市值分成大型與中型。快照數字要等下一次用本機價格通過檢查才會換。</p>}
                  <Rules scan={shownScan} assumptions={shownAssumptions} />
                </>
              )}
              {selectedRow && pane === "matches" && <Detail row={selectedRow} rankLabel={snapshot.rank_label} />}
            </>
          )}
          {!snapshot && (
            <Rules scan={null} assumptions={rulesFallback ?? []} />
          )}
        </main>
      </div>
    </div>
  );
}

function rulesMatch(snapshot: Snapshot, rules: RulesFile): boolean {
  return snapshot.semantics_version === rules.semantics_version || snapshot.semantics_version === `${rules.semantics_version}.jp-yen`;
}

function defaultSort(spec: { field: string; direction: string } | null | undefined): { key: string; dir: "asc" | "desc" } | null {
  if (!spec) return { key: "qualified_symbol", dir: "asc" };
  return { key: spec.field, dir: spec.direction === "asc" ? "asc" : "desc" };
}

function compareRows(left: MatchRow, right: MatchRow, key: string, dir: "asc" | "desc"): number {
  const sign = dir === "asc" ? 1 : -1;
  const a = valueOf(left, key);
  const b = valueOf(right, key);
  if (a === null && b === null) return left.qualified_symbol.localeCompare(right.qualified_symbol);
  if (a === null) return 1;
  if (b === null) return -1;
  if (typeof a === "number" && typeof b === "number") return (a - b) * sign || left.qualified_symbol.localeCompare(right.qualified_symbol);
  return String(a).localeCompare(String(b), "zh-HK") * sign;
}

function valueOf(row: MatchRow, key: string): string | number | null {
  if (key in row.indicators) return row.indicators[key];
  const direct = row[key as keyof MatchRow];
  if (typeof direct === "number" || typeof direct === "string") return direct;
  return null;
}

function Results({ rows, dropped, scanId, sort, onSort, selected, onSelect }: { rows: MatchRow[]; dropped: MatchRow[]; scanId: string; sort: { key: string; dir: "asc" | "desc" } | null; onSort: (key: string) => void; selected: string | null; onSelect: (symbol: string) => void }) {
  const columns = [
    { key: "diff_status", label: "對照" },
    { key: "qualified_symbol", label: "代號" },
    { key: "name", label: "名稱" },
    { key: "cohort", label: "族群" },
    { key: "technical_rank", label: "技術排名" },
    { key: "last_date", label: "數據日期" },
    { key: "close", label: "收市" },
    { key: "volume", label: "成交量" },
    ...(EXTRA[scanId] ?? []),
  ];
  if (!rows.length && !dropped.length) {
    return <section className="empty slim" data-testid="zero-matches"><h2>已完成計算，本快照沒有命中。</h2><p>這是算完的結果，不是匯入失敗。</p></section>;
  }
  return (
    <div className="table-wrap">
      <table data-testid="results">
        <caption>掃描結果</caption>
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column.key} aria-sort={sort?.key === column.key ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}>
                <button type="button" onClick={() => onSort(column.key)}>{column.label}</button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.qualified_symbol} data-symbol={row.qualified_symbol} className={selected === row.qualified_symbol ? "selected" : ""} onClick={() => onSelect(row.qualified_symbol)}>
              {columns.map((column) => <td key={column.key}>{renderCell(row, column.key)}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
      {dropped.length > 0 && (
        <div className="dropped" data-testid="dropped">
          <h3>相對上一成功快照剔除</h3>
          <ul>
            {dropped.map((row) => (
              <li key={row.qualified_symbol}>
                <button type="button" onClick={() => onSelect(row.qualified_symbol)}>{row.qualified_symbol} {row.name}</button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function renderCell(row: MatchRow, key: string): string {
  if (key === "diff_status") return diffLabel(row.diff_status);
  if (key === "cohort") return cohortLabel(row.cohort);
  if (key === "technical_rank") return formatRank(row.technical_rank);
  if (key === "volume" || key === "sma20_volume" || key === "sma3_volume" || key === "sma50_volume") {
    const value = key === "volume" ? row.volume : row.indicators[key];
    return formatVolume(value);
  }
  if (key === "close") return formatNumber(row.close);
  if (key === "qualified_symbol" || key === "name" || key === "last_date") return String(row[key as "name"] ?? "");
  return formatNumber(row.indicators[key]);
}

function Detail({ row, rankLabel }: { row: MatchRow; rankLabel: string }) {
  return (
    <section className="detail" data-testid="detail">
      <div>
        <h2>{row.qualified_symbol}</h2>
        <p>{row.name} · {cohortLabel(row.cohort)} · {diffLabel(row.diff_status)}</p>
        <p className="quiet">{rankLabel} {formatRank(row.technical_rank)}</p>
        {row.clauses?.length > 0 && (
          <ul className="clauses">
            {row.clauses.map((clause) => (
              <li key={clause.id} data-status={clause.status}>{clause.label}</li>
            ))}
          </ul>
        )}
      </div>
      <Sparkline values={row.sparkline ?? []} />
    </section>
  );
}

function Sparkline({ values }: { values: number[] }) {
  if (values.length < 2) return <p className="quiet">沒有走勢數據</p>;
  const width = 320;
  const height = 120;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const path = values.map((value, index) => {
    const x = (index / (values.length - 1)) * (width - 8) + 4;
    const y = height - 8 - ((value - min) / span) * (height - 16);
    return `${index === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  return (
    <svg className="spark" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="合成或已匯入收市價走勢">
      <path d={path} />
    </svg>
  );
}

function Diagnostics({ snapshot, market, result }: { snapshot: Snapshot; market: Market; result: Snapshot["scans"][string]["markets"]["US"] }) {
  const blocks: { title: string; rows: { qualified_symbol: string; name?: string; reason?: string }[] }[] = [
    { title: "未滿足依賴", rows: result.diagnostics.unmet_dependency },
    { title: "歷史不足", rows: result.diagnostics.insufficient_warmup },
  ];
  for (const [key, title] of [
    ["excluded_etf", "ETF（排名前已剔除）"],
    ["excluded_unknown_type", "類型不明"],
    ["eligibility_unresolved", "ADR／OTC 資格未決議"],
    ["data_error", "資料錯誤"],
    ["stale", "未跟上該市場最後交易柱"],
  ] as const) {
    const rows = (snapshot.universe_diagnostics[key] ?? []).filter((row) => row.qualified_symbol.startsWith(`${market}:`));
    if (rows.length) blocks.push({ title, rows });
  }
  return (
    <div className="diagnostics" data-testid="diagnostics">
      {blocks.map((block) => (
        <section key={block.title}>
          <h3>{block.title} · {block.rows.length}</h3>
          {block.rows.length === 0 ? <p className="quiet">沒有</p> : (
            <ul>{block.rows.map((row) => <li key={row.qualified_symbol}><strong>{row.qualified_symbol}</strong> {row.name} <span>{row.reason}</span></li>)}</ul>
          )}
        </section>
      ))}
    </div>
  );
}

function Rules({ scan, assumptions }: { scan: Snapshot["scans"][string] | null; assumptions: Snapshot["assumptions"] }) {
  return (
    <div className="rules" data-testid="rules">
      {scan && (
        <section>
          <h2>{scan.name_zh}原文</h2>
          <pre>{scan.raw_source}</pre>
          {scan.grouping_warning && <p className="warn inline">{scan.grouping_warning}</p>}
        </section>
      )}
      {assumptions.map((item) => (
        <section key={item.id}>
          <h3>{item.title}</h3>
          <p>{item.body}</p>
        </section>
      ))}
    </div>
  );
}

function PreflightView({ report }: { report: PreflightReport }) {
  return (
    <div className="preflight" data-testid="preflight">
      <p>{report.kind === "unknown" ? "未識別" : report.kind} · {report.rows} 行 · {report.symbols} 個代號</p>
      <p>市場 {report.markets.join("、") || "—"} · 日期 {report.firstDate ?? "—"} 至 {report.lastDate ?? "—"}</p>
      {report.issues.length === 0 ? <p>預檢沒有看到格式問題。請用命令列計算快照。</p> : (
        <ul>{report.issues.map((issue) => <li key={issue.message} data-level={issue.level}>{issue.message}</li>)}</ul>
      )}
    </div>
  );
}
