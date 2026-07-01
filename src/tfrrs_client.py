"""Polite TFRRS HTTP client.

Design goals (see README section 3):
  * honor robots.txt
  * hard rate-limit with jitter
  * permanent on-disk cache for historical pages (a re-run costs 0 requests)
  * retry with backoff on 429/5xx
"""
from __future__ import annotations
import hashlib
import os
import random
import time
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests

BASE = "https://www.tfrrs.org"


class TFRRSClient:
    def __init__(self, cfg: dict):
        h = cfg["http"]
        self.ua = h["user_agent"]
        self.min_interval = float(h["min_interval_seconds"])
        self.jitter = float(h["jitter_seconds"])
        self.cache_dir = h["cache_dir"]
        self.max_retries = int(h["max_retries"])
        self.timeout = int(h["timeout_seconds"])
        os.makedirs(self.cache_dir, exist_ok=True)
        self._last_request = 0.0
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": self.ua})
        self._robots = self._load_robots()

    # --- robots -----------------------------------------------------------
    def _load_robots(self) -> RobotFileParser:
        rp = RobotFileParser()
        try:
            resp = self._session.get(f"{BASE}/robots.txt", timeout=self.timeout)
            rp.parse(resp.text.splitlines())
        except Exception:
            # If robots is unreachable, be conservative: allow but keep slow.
            rp.parse(["User-agent: *", "Allow: /"])
        return rp

    def allowed(self, url: str) -> bool:
        return self._robots.can_fetch(self.ua, url)

    # --- cache ------------------------------------------------------------
    def _cache_path(self, url: str) -> str:
        key = hashlib.sha256(url.encode()).hexdigest()[:24]
        host = urlparse(url).netloc.replace(".", "_")
        return os.path.join(self.cache_dir, f"{host}_{key}.html")

    # --- fetch ------------------------------------------------------------
    def get(self, url: str, *, cacheable: bool = True) -> str:
        cp = self._cache_path(url)
        if cacheable and os.path.exists(cp):
            with open(cp, "r", encoding="utf-8") as f:
                return f.read()

        if not self.allowed(url):
            raise PermissionError(f"robots.txt disallows fetching: {url}")

        self._throttle()
        html = self._get_with_retry(url)

        if cacheable:
            with open(cp, "w", encoding="utf-8") as f:
                f.write(html)
        return html

    def _throttle(self):
        elapsed = time.time() - self._last_request
        wait = self.min_interval - elapsed
        if wait > 0:
            time.sleep(wait)
        time.sleep(random.uniform(0, self.jitter))
        self._last_request = time.time()

    def _get_with_retry(self, url: str) -> str:
        backoff = 2.0
        for attempt in range(1, self.max_retries + 1):
            try:
                r = self._session.get(url, timeout=self.timeout)
                if r.status_code == 200:
                    return r.text
                if r.status_code in (429, 500, 502, 503, 504):
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                r.raise_for_status()
            except requests.RequestException:
                if attempt == self.max_retries:
                    raise
                time.sleep(backoff)
                backoff *= 2
        raise RuntimeError(f"exhausted retries for {url}")
