"""Public Yahoo chart and quote reads. No API key and nothing persisted as a secret."""

from __future__ import annotations

import http.cookiejar
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone

USER_AGENT = "jp-us-screener/0.1 (research; +https://github.com/nick-l-wh/jp-us-screener)"
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


def epoch(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp())


class YahooClient:
    def __init__(self, sleep=time.sleep) -> None:
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.crumb: str | None = None
        self._sleep = sleep
        self._lock = threading.Lock()
        self.pause_until = 0.0

    def _wait(self) -> None:
        while True:
            with self._lock:
                delay = self.pause_until - time.time()
            if delay <= 0:
                return
            self._sleep(min(delay, 2))

    def _pause(self, seconds: float) -> None:
        with self._lock:
            self.pause_until = max(self.pause_until, time.time() + seconds)

    def request_public(self, url: str, timeout: int = 30, retries: int = 4) -> bytes:
        """GET without the quote cookie jar, so chart downloads can run in parallel."""

        return self._read(url, urllib.request.urlopen, timeout=timeout, retries=retries)

    def request(self, url: str, timeout: int = 30, retries: int = 4) -> bytes:
        return self._read(url, self.opener.open, timeout=timeout, retries=retries)

    def _read(self, url: str, opener, timeout: int, retries: int) -> bytes:
        last: Exception | None = None
        for attempt in range(retries):
            self._wait()
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            try:
                with opener(request, timeout=timeout) as response:
                    return response.read()
            except urllib.error.HTTPError as exc:
                last = exc
                if exc.code == 429:
                    if attempt == retries - 1:
                        raise
                    self._pause(15 * (attempt + 1))
                    continue
                if exc.code == 404 or exc.code not in RETRY_STATUS or attempt == retries - 1:
                    raise
                self._sleep(min(8, 2**attempt))
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                last = exc
                if attempt == retries - 1:
                    raise
                self._sleep(min(8, 2**attempt))
        raise RuntimeError(f"Yahoo 請求失敗：{last}")

    def ensure_crumb(self) -> str:
        with self._lock:
            if self.crumb:
                return self.crumb
        try:
            self.request("https://fc.yahoo.com", retries=1)
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
        crumb = self.request("https://query1.finance.yahoo.com/v1/test/getcrumb").decode().strip()
        if not crumb or crumb.startswith("<") or any(char.isspace() for char in crumb):
            raise RuntimeError("Yahoo crumb 無效")
        with self._lock:
            self.crumb = crumb
            return self.crumb

    def chart(self, symbol: str, start: date, end_exclusive: date) -> dict:
        if not re.fullmatch(r"[A-Z0-9.\-]+", symbol):
            raise ValueError(f"代號不能下載：{symbol}")
        url = (
            "https://query1.finance.yahoo.com/v8/finance/chart/"
            + urllib.parse.quote(symbol)
            + f"?period1={epoch(start)}&period2={epoch(end_exclusive)}"
            + "&interval=1d&events=div%7Csplit&includeAdjustedClose=true"
        )
        payload = json.loads(self.request_public(url))
        result = (payload.get("chart") or {}).get("result")
        if not result:
            error = (payload.get("chart") or {}).get("error")
            raise RuntimeError(f"{symbol} 沒有 chart result：{error}")
        return result[0]

    def quotes(self, symbols: list[str]) -> list[dict]:
        last: Exception | None = None
        for _ in range(2):
            crumb = urllib.parse.quote(self.ensure_crumb())
            joined = urllib.parse.quote(",".join(symbols))
            url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={joined}&crumb={crumb}"
            try:
                payload = json.loads(self.request(url))
            except urllib.error.HTTPError as exc:
                last = exc
                if exc.code == 401:
                    with self._lock:
                        self.crumb = None
                    continue
                raise
            return (payload.get("quoteResponse") or {}).get("result") or []
        raise RuntimeError(f"Yahoo 報價被拒絕：{last}")
