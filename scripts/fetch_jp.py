import concurrent.futures
import datetime as dt
import json
import sys
import time
import urllib.request
from pathlib import Path
import openpyxl

root = Path(__file__).resolve().parents[1] / "data"
cache = root / 'jp-20261120'
cache.mkdir(exist_ok=True)
rows = list(openpyxl.load_workbook(root / 'data_e.xlsx', read_only=True).active.values)[1:]
large_mid = {'TOPIX Core30', 'TOPIX Large70', 'TOPIX Mid400'}
symbols = [str(r[1]) for r in rows if '(Domestic)' in r[3] and (r[-1] in large_mid or '--all' in sys.argv)]
start = int(dt.datetime(2024, 10, 1, tzinfo=dt.timezone.utc).timestamp())
end = int(dt.datetime(2026, 11, 21, tzinfo=dt.timezone.utc).timestamp())

def fetch(code):
    f = cache / (code + '.json')
    if f.exists():
        return
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{code}.T?period1={start}&period2={end}&interval=1d'
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=20) as response:
                result = json.load(response)['chart']['result'][0]
            if result['meta']['symbol'] != code + '.T' or result['meta']['currency'] != 'JPY':
                raise ValueError('symbol/currency mismatch')
            f.with_suffix('.tmp').write_text(json.dumps(result,separators=(',',':')), encoding='utf8')
            f.with_suffix('.tmp').replace(f)
            return
        except Exception as e:
            if attempt == 2 or getattr(e,'code',0) == 404:
                return code, str(e)
            time.sleep(1 + attempt)

with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
    errors = [r for r in pool.map(fetch,symbols) if r]
print('Requested', len(symbols), 'missing', errors[:30], 'total errors', len(errors))
