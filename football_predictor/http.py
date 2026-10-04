"""HTTP helper with an on-disk JSON cache, per-host rate limiting and 429 retries."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import deque
from pathlib import Path
from urllib.parse import urlencode, urlparse

import requests

log = logging.getLogger(__name__)


class HttpError(RuntimeError):
    def __init__(self, status: int, url: str, body: str = ""):
        super().__init__(f"HTTP {status} for {url}: {body[:200]}")
        self.status = status


class CachedHttp:
    def __init__(self, cache_dir: Path, rate_limits: dict[str, int] | None = None, timeout: float = 20.0):
        """rate_limits maps host -> max requests per minute."""
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.rate_limits = rate_limits or {}
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "football-predictor/0.1"
        self._calls: dict[str, deque[float]] = {}

    def _cache_file(self, url: str) -> Path:
        return self.cache_dir / (hashlib.sha1(url.encode()).hexdigest() + ".json")

    def _throttle(self, host: str) -> None:
        limit = self.rate_limits.get(host)
        if not limit:
            return
        calls = self._calls.setdefault(host, deque())
        now = time.monotonic()
        while calls and now - calls[0] > 60:
            calls.popleft()
        if len(calls) >= limit:
            wait = 60 - (now - calls[0]) + 0.5
            log.info("rate limit for %s reached, waiting %.0fs", host, wait)
            time.sleep(wait)
        calls.append(time.monotonic())

    def get_json(self, url: str, params: dict | None = None, headers: dict | None = None,
                 ttl: int = 3600, retries: int = 3):
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            url = f"{url}?{urlencode(clean)}" if clean else url
        cache_file = self._cache_file(url)
        if ttl > 0 and cache_file.is_file() and time.time() - cache_file.stat().st_mtime < ttl:
            return json.loads(cache_file.read_text(encoding="utf-8"))

        host = urlparse(url).netloc
        for attempt in range(retries + 1):
            self._throttle(host)
            resp = self.session.get(url, headers=headers, timeout=self.timeout)
            if resp.status_code == 429 and attempt < retries:
                wait = int(resp.headers.get("Retry-After") or resp.headers.get("X-RequestCounter-Reset") or 30)
                log.warning("429 from %s, retrying in %ss", host, wait)
                time.sleep(min(wait, 90) + 1)
                continue
            if resp.status_code >= 500 and attempt < retries:
                time.sleep(2 ** attempt)
                continue
            if resp.status_code != 200:
                raise HttpError(resp.status_code, url, resp.text)
            data = resp.json()
            cache_file.write_text(json.dumps(data), encoding="utf-8")
            return data
        raise HttpError(429, url, "rate limited")
