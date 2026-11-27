# jp-us-screener

美股和日股的收市後股票篩選器。Python 計算 ADX、Pullback、Squeeze On、Turtle Soup Plus One、Weak ADX 和 Young Trend。繁體中文介面可以切換市場和交易日，查看代號、名稱、成交量及 SCTR。宇宙是指數名單：美股 Russell 1000、日股 TOPIX 500。不再按市值分成大型與中型。

[開啟線上版收市掃描](https://nick-l-wh.github.io/jp-us-screener/)。這個分支的已公布掃描是美國 2026-11-25、日本 2026-11-26。線上版要等這個分支合併進 `main` 才會換成這一份；合併前網站仍是 2026-11-20。若這個瀏覽器已有更後的交易日，則顯示該日。仍可在「資料工具」匯入快照。

## 本機使用

使用 Python 3.11 以上版本及 Node.js 22。在專案根目錄執行以下命令。

```sh
python -m pip install -e ".[dev]"
cd web
npm ci
npm run dev
```

在「資料工具」匯入 Python 產生的快照 JSON。瀏覽器為美股和日股各自保留最近 15 個交易日，按交易日期排序。同一市場同日匯入修訂版會更新該日記錄。沒有更後的交易日時，頁面顯示已公布的美國 2026-11-25、日本 2026-11-26 結果。2026-11-20 的舊快照仍留在 `snapshot-2026-11-20.json`，那一份是改規則之前的計算。2026-11-26 起，美股宇宙是 Russell 1000，日股宇宙是 TOPIX 500（Core30、Large70、Mid400）。

## 計算快照

以下命令在專案根目錄執行。第一行產生 CSV 範本。將自己的行情、股票名單及市值資料另存到 `data/` 後，執行第二行。

```sh
python -m jp_us_screener templates --dir data/templates
python -m jp_us_screener compute --ohlcv data/ohlcv.csv --universe data/universe.csv --marketcap data/marketcap.csv --out snapshots/latest.json
```

股票名單須標明市場和證券類型，以排除 ETF。公布用的名單在 `universes/`：美股讀 iShares IWB 持股裡的權益證券，日股讀 TOPIX 500 的 `code`、`name` 和 manifest 指定的系列欄。換名單時替換 CSV，並改 `universes/manifest.json` 的 `file`、`as_of`，日股再改 `size_column`。解析器不按市值剔除。日股 Turtle Soup Plus One 要求收市價大於 500 日圓。Squeeze 與 Weak ADX 沒有市值條件。三個含未分組 OR 的原公式仍按「大型或中型欄位」再加其餘條件執行；公布時整份指數寫進同一個排名池，讓 `SCTR.large` 覆蓋整份名單，沒有另設中型市值族群。

## 測試及離線版

```sh
python -m pytest
cd web
npm run build
python standalone.py ../screener.html
```

用瀏覽器開啟產生的 HTML 即可在離線環境匯入快照。公開檔是美國 2026-11-25、日本 2026-11-26 的 `web/public/data/latest.json`，同內容的 `snapshot-us-2026-11-25-jp-2026-11-26.json`，以及先前的 `snapshot-2026-11-20.json`。完整行情歷史不在庫內。

## 每日更新

已保存的 Grok Bot routine `automation-1795319781957` 會在香港時間星期一至六約 08:00 啟動（UTC+8，不隨美國夏令時間改鐘）。接受啟動的窗口是 07:45–08:15。這個倉庫沒有第二個排程，也沒有計時器。

在專案根目錄執行：

```sh
python -m pip install -e .
python -m jp_us_screener update
```

`scripts/update_public_snapshot.py` 是同一條命令。本機價格路徑是：

```sh
python -m jp_us_screener update --prices-dir /path/to/prices
```

`--prices-dir` 讀 Asia Breadth 形式的日線 CSV，欄位是 `Date, Open, High, Low, Close, AdjClose, Volume`，也可以多 `AdjOpen, AdjHigh, AdjLow`。目錄可以是一層 CSV，或分成 `us/` 與 `jp/`。`7203.T.csv` 對上 `7203`，`BRK.B.csv`、`BRK-B.csv` 與 `BRK B.csv` 對上 `BRK-B`。調整價用 `AdjClose/Close` 乘 OHLC，成交量用檔內數字。這條路徑不呼叫 Yahoo crumb，也不呼叫 v7 報價。缺價、檔案對上多個路徑、或可用行情不足八成，都會失敗並保留上一個公開快照，不會補 0，也不會填示範數據。

不帶 `--prices-dir` 的 `update` 仍是舊的 Yahoo 日線路徑，宇宙同樣改為上面的指數名單。Yahoo 路徑不是本機價格更新的必要條件。檢查通過後才寫入 `web/public/data/latest.json`、對應的日期快照和 `publication.json`。2026-11-27 香港 08:00 的本機價格更新已寫入美國 2026-11-25、日本 2026-11-26 的快照 `b10aaf00a207ce76`。

標準輸出裡，`unchanged US <日期> JP <日期>` 表示兩個市場的已完成交易日都沒有新過已公布快照，檔案不會改。`published <快照 id> US <日期> JP <日期>` 表示已寫入。`failed:` 表示失敗，公開檔維持原樣。退出碼 0 是 unchanged 或 published，1 是失敗。

同一香港日期即使開始得比較晚，交易日仍切在當天 08:00。因此當天 14:00 香港時間收市的日股不會被算進這一次。美股用紐約 16:00 判斷是否已收市，日股用東京 15:00。兩個日期可以不同。沒有內建假期表：預期交易日沒有足夠成市時，沿用上一個有八成以上代號的交易日；若該日已經公布，就不覆寫。最新一根沒有收市價的柱會整根捨棄，不會補價，也不會因此丟掉之前的完整交易日；中間缺價的序列則整列不用。

寫入公開檔之後，要發布網站就提交 `web/public/data` 並推上 `main`。現有的 GitHub Pages 工作流程會部署。這條命令本身不推送。`python -m jp_us_screener update --check --prices-dir /path/to/prices` 只讀指數名單和本機 CSV，不用 Yahoo，也不寫公開快照。不帶 `--prices-dir` 的 `--check` 仍會用 Yahoo 看 AAPL 與 7203.T。

行情快取放在 `data/update-cache/`，不進 git。已保存的流通股數寫在 `shares/outstanding.json`。不要把 cookie、crumb 或金鑰寫進倉庫。`scripts/fetch_jp.py` 和 `scripts/compute_jp.py` 是舊的單次日股腳本，不是這條入口。

若有券商或行情供應商的收市市值匯出檔，可存為 `data/update-cache/marketcap.csv`（自訂 `--cache-dir` 時放在該目錄）。欄位見 `web/public/templates/marketcap.csv`。美股用美元、日股用日圓，金額以元為單位。每筆填寫實際交易日及掃描名單使用的代號。日常 `update` 會跳過已有目標日市值的股票報價，並按最終選定的交易日取值。兩個市場均有完整市值時，整輪省去報價認證和報價請求；日線仍使用行情快取或 Yahoo。

Yahoo 認證或報價回覆 429 而沒有提供等待時間時，當輪停止認證和報價，以本機市值及已保存股數繼續計算。報價限流期間仍可更新日線；若日線也持續限流，就停止全部 Yahoo 請求。下輪會讀取同一快取目錄的 `yahoo-cooldown.json`，缺少等待指示時預設等候 15 分鐘。若 Yahoo 指定較長的 Retry-After，則依該時間恢復。資料覆蓋不足時會保留上次通過檢查的快照。

舊的 Yahoo 路徑沿用已快取的日線，只補缺少的最近交易日。每一輪 Yahoo HTTP（日線、報價、crumb 合計）最多 100 次，重試也計算在內。指數名單來自倉庫裡的 `universes/`，不再下載 Nasdaq Trader 或 JPX 名單。每次 Yahoo 請求之後暫停 1.5 秒再發下一次。需要下載的日線如果超過剩餘次數，只輪流抓一部分過期或缺少的代號，其餘留在快取，不會整份宇宙一起下載。報價每批 100 檔；沒有流通股數的代號優先，其次是收市價相對上次觀察價移動超過 25% 的代號，再輪流刷新。市值用流通股數乘以該交易日收市價。HTTP 429 會按 Retry-After 或指數退避；Retry-After 超過兩分鐘就停止，不再繼續其餘日線和報價。沒有流通股數的代號維持空白。

限制：美股名單是 iShares IWB 2026-11-24 持股裡的權益證券（這次檔案是 1021 檔；早前一次不公布的測試用了 1017 檔，這裡不為了對齊那個數字而再刪名稱）。日股名單是 TOPIX 500，官方 501 檔（Core30 30、Large70 70、Mid400 401），含 641A、642A、646A。系列名稱不是市值族群。ADR 與 OTC 仍未決議。市值不再決定宇宙、公布門檻或 Squeeze／Weak。沒有市值就留空，不會補 0。本機 CSV 沒有拆股事件，成交量用檔內數字，不能還原成拆股前股數。缺價、可用行情不足八成、計算失敗，或原本有效的命中變成無效，都保留上一份公開快照。不會公布示範數據。2026-11-20 的快照 `333c3e1d581cb716` 留在 `snapshot-2026-11-20.json`。目前公開檔是 `b10aaf00a207ce76`。這次沒有價格檔的是美股 SNDK、CEG、HOLX，以及日股 641A、642A、646A。調整後收市跳動過大而整列沒有入計的是美股 BMNR，日股 8303、8377。
