import { describe, expect, test } from "vitest";
import universeJson from "../public/data/universe.json";
import ingestJson from "../public/data/ingest-diagnostics.json";
import rulesJson from "../public/rules.json";
import {
  buildCoverageNote,
  expectedWeekdaySession,
  isIngestDiagnostics,
  isPublicUniverse,
  type CoverageSnapshot,
  type IngestDiagnostics,
  type PublicUniverse,
} from "./coverageNote";
import { buildCriteria, isRulesFile, orderForCurrent } from "./scanCriteria";

const universe: unknown = universeJson;
const ingest: unknown = ingestJson;
const rules: unknown = rulesJson;

function requireUniverse(): PublicUniverse {
  if (!isPublicUniverse(universe)) throw new Error("universe.json 不是指數名單");
  return universe;
}

function requireIngest(): IngestDiagnostics {
  if (!isIngestDiagnostics(ingest)) throw new Error("ingest-diagnostics.json 不是剔除紀錄");
  return ingest;
}

function liveSnapshot(): CoverageSnapshot {
  const lists = requireUniverse();
  const diagnostics = requireIngest();
  const eligible = {
    US: lists.US.symbols.filter((symbol) => !diagnostics.no_bars.US.includes(symbol) && !diagnostics.jump.US.includes(symbol)),
    JP: lists.JP.symbols.filter((symbol) => !diagnostics.no_bars.JP.includes(symbol) && !diagnostics.jump.JP.includes(symbol)),
  };
  return {
    snapshot_id: diagnostics.snapshot_id,
    generated_at: "2026-11-27T00:00:00+00:00",
    semantics_version: "2026-11-26.index.1.jp-yen",
    markets: {
      US: { as_of: "2026-11-25", eligible_stocks: eligible.US.length },
      JP: { as_of: "2026-11-26", eligible_stocks: eligible.JP.length },
    },
    eligibleSymbols: eligible,
    unranked: Array.from({ length: 16 }, (_, index) => ({
      market: "US",
      symbol: eligible.US[index],
      reason: "indicator_score_unavailable",
    })),
  };
}

describe("coverage note", () => {
  test("uses the loaded dates, list sizes, and this snapshot's omissions", () => {
    const note = buildCoverageNote(liveSnapshot(), requireUniverse(), requireIngest());
    expect(note).toContain("美國數據日期 2026-11-25（11 月 26 日美國感恩節休市，沿用上一交易日）。");
    expect(note).toContain("日本數據日期 2026-11-26。");
    expect(note).toContain("美股是 Russell 1000（iShares IWB 持股裡的權益證券，持股日 2026-11-24，共 1021 檔；這是 ETF 持股代理，不是富時羅素官方名單）。");
    expect(note).toContain("日股是 TOPIX 500（Core30、Large70、Mid400，名單生效 2026-10-30，共 501 檔）。");
    expect(note).toContain("納入計算：美股 1017 檔、日股 496 檔。");
    expect(note).toContain("美股未納入 4 檔（SNDK、CEG、HOLX 沒有日線，BMNR 因調整收市價跳升被剔除）。");
    expect(note).toContain("日股未納入 5 檔（641A、642A、646A 沒有日線，8303、8377 因調整收市價跳升被剔除）。");
    expect(note).toContain("另有 16 檔美股不足 200 根日線，沒有技術排名，但仍會進入掃描。");
    expect(note).toContain("沒有內建交易所假期表。預期交易日沒有足夠完整行情時沿用上一個密集交易日；與已公布日期相同則不覆寫。下載或計算失敗會保留上一個公開快照。");
    expect(note).not.toContain("算完的 0 筆");
    expect(note).not.toContain("市值");
  });

  test("names a carried US session only when that session was carried", () => {
    expect(expectedWeekdaySession("2026-11-27T00:00:00+00:00", "US")).toBe("2026-11-26");
    expect(expectedWeekdaySession("2026-11-27T00:00:00+00:00", "JP")).toBe("2026-11-26");
    expect(expectedWeekdaySession("2026-07-16T00:00:00+00:00", "US")).toBe("2026-07-15");
    expect(expectedWeekdaySession("2026-07-16T00:00:00+00:00", "JP")).toBe("2026-07-15");
    const ready = liveSnapshot();
    ready.markets.US = { as_of: "2026-11-26", eligible_stocks: ready.eligibleSymbols.US.length };
    const note = buildCoverageNote(ready, requireUniverse(), requireIngest());
    expect(note).toContain("美國數據日期 2026-11-26。");
    expect(note).not.toContain("感恩節");
  });

  test("does not keep another snapshot's omission reasons", () => {
    const other = liveSnapshot();
    other.snapshot_id = "other-snapshot";
    const note = buildCoverageNote(other, requireUniverse(), requireIngest());
    expect(note).toContain("美股未納入 4 檔（SNDK、CEG、BMNR、HOLX）。");
    expect(note).not.toContain("沒有日線");
    expect(note).not.toContain("跳升");
  });

  test("does not describe a tiny demo book as the index", () => {
    const demo = liveSnapshot();
    demo.markets.US = { as_of: "2023-12-29", eligible_stocks: 13 };
    demo.markets.JP = { as_of: "2023-12-29", eligible_stocks: 21 };
    expect(buildCoverageNote(demo, requireUniverse(), requireIngest())).toBeNull();
  });
});

describe("scan criteria", () => {
  test("renders the engine clause labels for every scan", () => {
    if (!isRulesFile(rules)) throw new Error("rules.json 不是規則檔");
    const view = buildCriteria(rules, "adx");
    expect(view.scans.map((scan) => scan.id)).toEqual(["adx", "pullback", "squeeze", "turtle", "weak", "young"]);
    expect(view.intro.join("\n")).toContain("SCTR.large");
    expect(view.intro.join("\n")).toContain("同一個排名池");
    const text = Object.fromEntries(view.scans.map((scan) => [scan.id, scan.text]));
    expect(text.adx).toBe("大型族群技術排名 > 0 或 中型族群技術排名 > 0；且 90 日 ROC > 20；且 今日 +DI(14) > 不含今日的前 25 根 +DI(14) 最高值；且 類型為股票；且 成交量（股數）> 100000；且 所屬族群技術排名 > 70。");
    expect(view.scans[0].warning).toBe("條件分組待核對");
    expect(text.pullback).toContain("大型族群技術排名 > 70 或 中型族群技術排名 > 70；且 20 日 ROC > 20；且 +DI(14) > 30；且 類型為股票。");
    expect(text.pullback).toContain("依 Daily ULCER(10) 由大至小排序。");
    expect(text.pullback).toContain("方向未經來源核對。");
    expect(text.squeeze).toContain("類型為股票；美股：國家為美國；日股：日本股票；20 日平均成交量（股數）> 300000；");
    expect(text.squeeze).toContain("布林寬度等於含今日的 63 根最小值；+DI(14) < 20；-DI(14) < 20。沒有市值條件。");
    expect(text.turtle).toContain("美股：收市 > 5（美元；不換算成日圓）；日股：收市 > 500 日圓");
    expect(text.turtle).toContain("收市 <= 昨日 20 根最低值 × 1.005。");
    expect(text.turtle).toContain("20 日平均成交量（股數）> 500000");
    expect(text.weak).toContain("（大型族群技術排名 > 0 或 中型族群技術排名 > 0）");
    expect(text.weak).toContain("所屬族群技術排名 < 20；ADX(14) < 20；今日 +DI(14) < 不含今日的前 25 根 +DI(14) 最低值。沒有市值條件。");
    expect(text.young).toContain("大型族群技術排名 > 50 或 中型族群技術排名 > 50；且 3 日 ROC > 9；且 SMA(3,成交量) > SMA(50,成交量) × 1.5。");
    expect(text.young).toContain("依 ROC(3) 由大至小排序。");
    expect(text.young).toContain("Young Trend 原文沒有類型條款，股票限定是本引擎的宇宙政策。");
    expect(text.young).not.toContain("類型為股票");
    for (const scan of view.scans) {
      expect(scan.text).not.toContain("市值 >");
      expect(scan.text).not.toContain("1,000");
      expect(scan.text).not.toContain("10,000");
    }
    const pulled = orderForCurrent(view.scans, "pullback");
    expect(pulled[0].id).toBe("pullback");
    expect(pulled).toHaveLength(6);
  });
});
