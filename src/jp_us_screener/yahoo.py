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
from email.utils import parsedate_to_datetime

from jp_us_screener.fetchbudget import YAHOO_HTTP_BUDGET, YAHOO_REQUEST_INTERVAL

USER_AGENT = "jp-us-screener/0.1 (research; +https://github.com/nick-l-wh/jp-us-screener)"
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
# A longer Reset is honored by not retrying, rather than by sleeping through it.
MAX_HONORED_RETRY_AFTER = 120.0


class YahooBudgetExceeded(RuntimeError):
    """The per-run Yahoo HTTP budget is spent. No further Yahoo request was sent."""


def is_yahoo_url(url: str) -> bool:
    host = (urllib.parse.urlparse(url).hostname or "").lower().rstrip(".")
    return host == "yahoo.com" or host.endswith(".yahoo.com")


def epoch(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp())


def retry_after_seconds(exc: urllib.error.HTTPError, now: float | None = None) -> float | None:
    """Parse a Retry-After header. None means the header is absent or unreadable."""

    if exc.headers is None:
        return None
    header = exc.headers.get("Retry-After")
    if header is None:
        return None
    text = str(header).strip()
    if not text:
        return None
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        moment = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    clock = time.time() if now is None else now
    return max(0.0, moment.timestamp() - clock)


class YahooClient:
    def __init__(
        self,
        sleep=time.sleep,
        now=time.time,
        *,
        budget: int = YAHOO_HTTP_BUDGET,
        min_interval: float = YAHOO_REQUEST_INTERVAL,
    ) -> None:
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.crumb: str | None = None
        self._sleep = sleep
        self._now = now
        self._lock = threading.Lock()
        self._yahoo_gate = threading.Lock()
        self.pause_until = 0.0
        self._next_yahoo_at = 0.0
        self.budget = budget
        self.min_interval = min_interval
        self.http_requests = 0
        self.yahoo_http = 0
        self.chart_http = 0
        self.quote_http = 0
        self.crumb_http = 0

    @property
    def yahoo_remaining(self) -> int:
        return max(0, self.budget - self.yahoo_http)

    def _wait(self) -> None:
        while True:
            with self._lock:
                delay = self.pause_until - self._now()
            if delay <= 0:
                return
            self._sleep(min(delay, 2))

    def _pause(self, seconds: float) -> None:
        with self._lock:
            self.pause_until = max(self.pause_until, self._now() + seconds)

    def _count_yahoo(self, url: str) -> None:
        if "/v8/finance/chart/" in url:
            self.chart_http += 1
        elif "/v7/finance/quote" in url:
            self.quote_http += 1
        else:
            self.crumb_http += 1

    def _before_attempt(self, yahoo: bool, url: str) -> None:
        """Wait out a 429 pause. Yahoo calls also wait out the inter-request gap.

        The budget is taken before the socket opens. A call that would exceed
        it raises and does not send. Yahoo attempts run one at a time.
        """

        if not yahoo:
            self._wait()
            with self._lock:
                self.http_requests += 1
            return
        self._yahoo_gate.acquire()
        try:
            while True:
                with self._lock:
                    if self.yahoo_http >= self.budget:
                        raise YahooBudgetExceeded(f"Yahoo HTTP 已達本輪上限 {self.budget}")
                    now = self._now()
                    delay = max(self.pause_until, self._next_yahoo_at) - now
                    if delay <= 0:
                        self.yahoo_http += 1
                        self.http_requests += 1
                        self._count_yahoo(url)
                        return
                self._sleep(delay)
        except BaseException:
            self._yahoo_gate.release()
            raise

    def _finish_yahoo_attempt(self) -> None:
        try:
            with self._lock:
                now = self._now()
                self._next_yahoo_at = max(self._next_yahoo_at, now + self.min_interval)
        finally:
            self._yahoo_gate.release()

    def request_public(self, url: str, timeout: int = 30, retries: int = 4) -> bytes:
        """GET without the quote cookie jar. Yahoo calls are still paced one at a time."""

        return self._read(url, urllib.request.urlopen, timeout=timeout, retries=retries)

    def request(self, url: str, timeout: int = 30, retries: int = 4) -> bytes:
        return self._read(url, self.opener.open, timeout=timeout, retries=retries)

    def _read(self, url: str, opener, timeout: int, retries: int) -> bytes:
        yahoo = is_yahoo_url(url)
        last: Exception | None = None
        for attempt in range(retries):
            self._before_attempt(yahoo, url)
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            try:
                try:
                    with opener(request, timeout=timeout) as response:
                        return response.read()
                except urllib.error.HTTPError as exc:
                    last = exc
                    if exc.code == 429:
                        delay = retry_after_seconds(exc, now=self._now())
                        if delay is None:
                            delay = min(60.0, 2 ** (attempt + 1))
                        elif delay > MAX_HONORED_RETRY_AFTER:
                            raise
                        if attempt == retries - 1:
                            raise
                        self._pause(delay)
                        continue
                    if exc.code == 404 or exc.code not in RETRY_STATUS or attempt == retries - 1:
                        raise
                    self._sleep(min(8, 2**attempt))
                except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                    last = exc
                    if attempt == retries - 1:
                        raise
                    self._sleep(min(8, 2**attempt))
            finally:
                if yahoo:
                    self._finish_yahoo_attempt()
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
