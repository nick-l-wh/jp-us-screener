# jp-us-screener

美股和日股的收市後股票篩選器。Python 計算 ADX、Pullback、Squeeze On、Turtle Soup Plus One、Weak ADX 和 Young Trend。繁體中文介面可以切換市場和交易日，查看代號、名稱、成交量及 SCTR。大型與中型按市值門檻在本機分組。

[開啟線上版收市掃描](https://nick-l-wh.github.io/jp-us-screener/)。網站會直接載入已公布的 2026-11-20 掃描。若這個瀏覽器已有更後的交易日，則顯示該日。仍可在「資料工具」匯入快照。

## 本機使用

使用 Python 3.11 以上版本及 Node.js 22。在專案根目錄執行以下命令。

```sh
python -m pip install -e ".[dev]"
cd web
npm ci
npm run dev
```

在「資料工具」匯入 Python 產生的快照 JSON。瀏覽器為美股和日股各自保留最近 15 個交易日，按交易日期排序。同一市場同日匯入修訂版會更新該日記錄。沒有更後的交易日時，頁面顯示已公布的 2026-11-20 結果。美國大型是市值大於 1,000 億美元，中型是大於 250 億至 1,000 億美元。日本大型是市值大於 1 兆日圓，中型是大於 2,500 億至 1 兆日圓。沒有市值的股票不歸入這兩個族群。

## 計算快照

以下命令在專案根目錄執行。第一行產生 CSV 範本。將自己的行情、股票名單及市值資料另存到 `data/` 後，執行第二行。

```sh
python -m jp_us_screener templates --dir data/templates
python -m jp_us_screener compute --ohlcv data/ohlcv.csv --universe data/universe.csv --marketcap data/marketcap.csv --out snapshots/latest.json
```

股票名單須標明市場和證券類型，以排除 ETF。日股 Squeeze On 及 Weak ADX 要求當日市值大於 1,000 億日圓，Turtle Soup Plus One 要求收市價大於 500 日圓。大型與中型按上面的市值門檻各自排名；宇宙檔已填 cohort 時以檔案為準。三個含未分組 OR 的原公式目前按「大型或中型」再加其餘條件執行。

## 測試及離線版

```sh
python -m pytest
cd web
npm run build
python standalone.py ../screener.html
```

用瀏覽器開啟產生的 HTML 即可在離線環境匯入快照。公開網站附有 2026-11-20 的掃描快照 `web/public/data/latest.json`，以及同內容的 `snapshot-2026-11-20.json`。完整行情歷史不在庫內。

## 每日更新

已保存的 Grok Bot routine `automation-1795319781957` 會在香港時間星期一至六約 08:00 啟動（UTC+8，不隨美國夏令時間改鐘）。接受啟動的窗口是 07:45–08:15。這個倉庫沒有第二個排程，也沒有計時器。

在專案根目錄執行：

```sh
python -m pip install -e .
python -m jp_us_screener update
```

`scripts/update_public_snapshot.py` 是同一條命令。它會抓取已收市的美股和日股日線，用 `config/default.json` 的現有規則和市值族群計算，檢查通過後才寫入 `web/public/data/latest.json`、對應的日期快照和 `publication.json`。

標準輸出裡，`unchanged US <日期> JP <日期>` 表示兩個市場的已完成交易日都沒有新過已公布快照，檔案不會改。`published <快照 id> US <日期> JP <日期>` 表示已寫入。`failed:` 表示失敗，公開檔維持原樣。退出碼 0 是 unchanged 或 published，1 是失敗。

同一香港日期即使開始得比較晚，交易日仍切在當天 08:00。因此當天 14:00 香港時間收市的日股不會被算進這一次。美股用紐約 16:00 判斷是否已收市，日股用東京 15:00。兩個日期可以不同。沒有內建假期表：預期交易日沒有足夠成市時，沿用上一個有八成以上代號的交易日；若該日已經公布，就不覆寫。最新一根沒有收市價的柱會整根捨棄，不會補價，也不會因此丟掉之前的完整交易日；中間缺價的序列則整列不用。

寫入公開檔之後，要發布網站就提交 `web/public/data` 並推上 `main`。現有的 GitHub Pages 工作流程會部署。這條命令本身不推送。`python -m jp_us_screener update --check` 只下載名單和 AAPL、7203.T，用來確認網路，不會寫公開快照。

行情快取放在 `data/update-cache/`，不進 git。已保存的流通股數寫在 `shares/outstanding.json`。不要把 cookie、crumb 或金鑰寫進倉庫。`scripts/fetch_jp.py` 和 `scripts/compute_jp.py` 是舊的單次日股腳本，不是這條入口。

每次更新沿用已快取的日線，只補缺少的最近交易日。報價每批 100 檔，批次之間暫停，並輪流刷新每個市場最多 100 檔，外加沒有流通股數、或收市價相對上次觀察價移動超過 25% 的代號。市值用流通股數乘以該交易日收市價，所以流通股數齊全時大多數日子不用再叫報價。HTTP 429 會按 Retry-After 或指數退避；Retry-After 超過兩分鐘就停止，不再繼續其餘報價批次。沒有流通股數的代號維持空白。

限制：美股名單是 Nasdaq Trader 目錄裡名稱列明普通股的子集，不是整個美股市場；日股名單是 JPX 英文檔裡 TOPIX Core30、Large70、Mid400 的國內股，指數名稱不用作族群。ADR 與 OTC 仍未決議。日股和美股的市值都是流通股數乘以該交易日收市價，不是歷史申報股本。報價同時有股價和市值時，兩者相差超過 1% 的報價不會採用。沿用較早流通股數時，公布說明會寫明觀察日。沒有市值就維持空白，不會補 0。缺價、下載不足八成、有市值的代號不足一半、計算失敗，或原本有效的命中變成無效，都保留上一份公開快照。不會公布示範數據。Yahoo、Nasdaq Trader 和 JPX 都要能連上；沒有官方授權檔。
