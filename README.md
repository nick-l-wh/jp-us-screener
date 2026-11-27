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

Grok Bot 會在香港時間星期一至六約 08:00 啟動（UTC+8，不隨美國夏令時間改鐘）。接受啟動的窗口是 07:45–08:15。

在專案根目錄執行：

```sh
python -m pip install -e .
python -m jp_us_screener update
```

`scripts/update_public_snapshot.py` 是同一條命令。本機價格路徑是：

```sh
python -m jp_us_screener update --prices-dir /path/to/prices
```
`--prices-dir` 讀公開資料形式的日線 CSV，欄位是 `Date, Open, High, Low, Close, AdjClose, Volume`，也可以多 `AdjOpen, AdjHigh, AdjLow`。目錄可以是一層 CSV，或分成 `us/` 與 `jp/`。缺價、檔案對上多個路徑、或可用行情不足八成，都會失敗並保留上一個公開快照，不會補 0，也不會填示範數據。

標準輸出裡，`unchanged US <日期> JP <日期>` 表示兩個市場的已完成交易日都沒有新過已公布快照，檔案不會改。`published <快照 id> US <日期> JP <日期>` 表示已寫入。`failed:` 表示失敗，公開檔維持原樣。退出碼 0 是 unchanged 或 published，1 是失敗。

同一香港日期即使開始得比較晚，交易日仍切在當天 08:00。因此當天 14:00 香港時間收市的日股不會被算進這一次。美股用紐約 16:00 判斷是否已收市，日股用東京 15:00。兩個日期可以不同。

寫入公開檔之後，要發布網站就提交 `web/public/data` 並推上 `main`。現有的 GitHub Pages 工作流程會部署。這條命令本身不推送。`python -m jp_us_screener update --check --prices-dir /path/to/prices` 只讀指數名單和本機 CSV。

行情快取放在 `data/update-cache/`，不進 git。已保存的流通股數寫在 `shares/outstanding.json`。不要把 cookie、crumb 或金鑰寫進倉庫。`scripts/fetch_jp.py` 和 `scripts/compute_jp.py` 是舊的單次日股腳本，不是這條入口。

美股用美元、日股用日圓，金額以元為單位。每筆填寫實際交易日及掃描名單使用的代號。日常 `update` 會跳過已有目標日市值的股票報價，並按最終選定的交易日取值。
