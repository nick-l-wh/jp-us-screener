# jp-us-screener

美股和日股的收市後股票篩選器。Python 計算 ADX、Pullback、Squeeze On、Turtle Soup Plus One、Weak ADX 和 Young Trend。繁體中文介面可以切換市場和交易日，查看代號、名稱、成交量及 SCTR。日股 SCTR 按規模分組在本機計算。

[開啟線上版收市掃描](https://nick-l-wh.github.io/jp-us-screener/)。在「資料工具」匯入快照後，即可選擇交易日。快照儲存在使用這個網址的瀏覽器內。

## 本機使用

使用 Python 3.11 以上版本及 Node.js 22。在專案根目錄執行以下命令。

```sh
python -m pip install -e ".[dev]"
cd web
npm ci
npm run dev
```

在「資料工具」匯入 Python 產生的快照 JSON。瀏覽器為美股和日股各自保留最近 15 個已匯入的交易日，按交易日期排序。同一市場同日匯入修訂版會更新該日記錄。首次開啟時，美股與日股均顯示尚無交易日資料。

## 計算快照

以下命令在專案根目錄執行。第一行產生 CSV 範本。將自己的行情、股票名單及市值資料另存到 `data/` 後，執行第二行。

```sh
python -m jp_us_screener templates --dir data/templates
python -m jp_us_screener compute --ohlcv data/ohlcv.csv --universe data/universe.csv --marketcap data/marketcap.csv --out snapshots/latest.json
```

股票名單須標明市場和證券類型，以排除 ETF。日股 Squeeze On 及 Weak ADX 要求當日市值大於 1,000 億日圓，Turtle Soup Plus One 要求收市價大於 500 日圓。大型與中型股的排名各自計算。三個含未分組 OR 的原公式目前按「大型或中型」再加其餘條件執行。

## 測試及離線版

```sh
python -m pytest
cd web
npm run build
python standalone.py ../screener.html
```

用瀏覽器開啟產生的 HTML 即可在離線環境匯入快照。公開專案只附有合成測試資料及 CSV 範本。真實行情、歷史名單與包含私人結果的 HTML 均在本機使用。
