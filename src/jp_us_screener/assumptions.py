"""User-facing assumption text. Traditional Chinese, as used in Hong Kong."""

from __future__ import annotations

from jp_us_screener.sctr import RANK_METHOD
from jp_us_screener.scans import clause_labels, scan_catalog
from jp_us_screener.version import ENGINE_VERSION, SCHEMA_VERSION, SEMANTICS_VERSION

ASSUMPTIONS: list[dict[str, str]] = [
    {
        "id": "scope",
        "title": "草稿範圍",
        "body": "這是私人研究草稿，不是 StockCharts 的產品，也不會登入、擷取或改動任何帳戶。沒有選定行情供應商，引擎只讀本地 CSV。",
    },
    {
        "id": "grouping",
        "title": "條件分組待核對",
        "body": "ADX、Pullback、Young Trend 的原文是沒有括號的 OR 然後 AND。本引擎的暫定語意是（大型或中型）並且其餘條件同時成立。這不等於已核對的掃描器語法。Weak ADX 原文有括號，依括號計算。",
    },
    {
        "id": "rolling-di",
        "title": "前 25 根 DI",
        "body": "yesterday's max(25, Plus DI(14)) 是不含今日的前 25 根 +DI(14) 最高值，不是與數字 25 比較。yesterday's min 同樣是前 25 根最低值。沒有 yesterday's 的 min(63) 或 min(20) 則包含今日。",
    },
    {
        "id": "sorts",
        "title": "排序不是過濾",
        "body": "Pullback 的 Daily ULCER(10) 與 Young Trend 的 ROC(3) 只排序命中，不決定是否命中。預設較大值在前。這個方向未經來源核對。",
    },
    {
        "id": "universe",
        "title": "股票宇宙",
        "body": "只保留美國與日本的股票。ETF 在計算族群排名和六條掃描之前剔除。security_type 缺漏、unknown 或其他類型會剔除並寫入診斷，不會假設成股票。Young Trend 原文沒有類型條款，股票限定是本引擎的宇宙政策。",
    },
    {
        "id": "adr-otc",
        "title": "ADR 與 OTC",
        "body": "預設政策是 unresolved。listing_segment 明確為 adr 或 otc 時，不會靜默納入，也不會靜默排除後當作已有決定，只會列入資格未決議。空白欄位不會靠代號或交易所名稱去猜測。unknown 同樣不放行進掃描。",
    },
    {
        "id": "adjustment",
        "title": "除權息與成交量",
        "body": "OHLC 必須整列標成 split_and_dividend，與檢視當時「adjusted for dividends」的語境一致。引擎不混合調整制度，也不在這裡重算股息。成交量是未調整股數，不是成交金額。",
    },
    {
        "id": "calendar",
        "title": "交易柱",
        "body": "回看是交易柱數，不是日曆日。引擎不會在假期向前填價，也不會補出沒有提供的柱。星期六與日的柱會判為資料錯誤。沒有內建交易所假期表，所以假期缺口要由匯入檔本身跳過。",
    },
    {
        "id": "cap",
        "title": "市值",
        "body": "市值只用檔案裡帶貨幣的數字，不從 OHLCV 反推。Squeeze 大於 1,000 百萬美元（1e9 USD），Weak 大於 10,000 百萬美元（1e10 USD）。這兩個過濾一直生效。缺市值或貨幣不是 USD 時，該列是未滿足依賴，不會變成命中，也不會換算成日圓。",
    },
    {
        "id": "jp-us-only-scans",
        "title": "只限美國的兩條",
        "body": "Squeeze 與 Turtle 原文限美國。日本市場這兩條顯示「需要日股適配參數」，不會套用美國規則。Squeeze 沒有 close > 5；那一條只在 Turtle，也不會改成 5 日圓。",
    },
    {
        "id": "rank",
        "title": "技術排名",
        "body": "排名按 ChartSchool 公開的 SCTR 權重，在每個市場、每個大型或中型族群內部分別計算，標成「技術排名（依SCTR公開方法，非官方）」。不是官方 SCTR。不會用一條全球排名同時填入大型與中型。人數不足時該族群排名不可用。",
    },
    {
        "id": "ppo",
        "title": "PPO 參數",
        "body": "PPO 用 (12, 26, 9)。權重表是這個參數；SCTR 正文曾把訊號線寫成 12 日 EMA。兩者不一致，本引擎跟隨權重表和 PPO 頁的 9 日訊號線，不聲稱與官方分數一致。",
    },
    {
        "id": "cohort-config",
        "title": "族群門檻",
        "body": "cohort_thresholds.enabled 預設 false，只表示宇宙檔沒有 cohort 時不要用市值自動分大型或中型。它不會關掉 Squeeze 或 Weak 的市值過濾。請在宇宙檔填 cohort。設定檔裡的美國數字只是被停用的公開參考。",
    },
    {
        "id": "equality",
        "title": "等式精度",
        "body": "等式只用相對 1e-12、絕對 1e-9 吸收浮點捨入，不是價格容許帶。落在這個範圍內的大於或小於會視作相等，所以不會因為最後一個位元而通過。",
    },
    {
        "id": "failure",
        "title": "失敗不覆寫",
        "body": "檔案結構壞掉、沒有可判定證券，或計算拋出錯誤時，不覆寫上一個成功快照。成功跑完但沒有命中會寫入，並標明已完成計算。",
    },
    {
        "id": "demo",
        "title": "真實與示範",
        "body": "真實模式沒有匯入時保持空白。示範模式只用 SYN- 合成代號，畫面和匯出都帶同一句示範聲明。示範價格不是真實發行人的行情。",
    },
]


def export_rules() -> dict:
    scans = []
    for scan in scan_catalog():
        public = {key: value for key, value in scan.items() if key != "tree"}
        public["clauses"] = clause_labels(scan["tree"])
        scans.append(public)
    return {
        "schema_version": SCHEMA_VERSION,
        "engine_version": ENGINE_VERSION,
        "semantics_version": SEMANTICS_VERSION,
        "assumptions": ASSUMPTIONS,
        "scans": scans,
        "rank_method": RANK_METHOD,
        "ohlcv_columns": [
            "market",
            "symbol",
            "date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "adjustment",
        ],
        "universe_columns": [
            "market",
            "symbol",
            "name",
            "security_type",
            "listing_segment",
            "exchange",
            "currency",
            "cohort",
            "market_cap",
            "market_cap_currency",
            "market_cap_as_of",
            "group",
        ],
    }
