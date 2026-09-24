"""Tiny JSON-over-HTTP helper with retries (stdlib only)."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

DEBUG = bool(os.environ.get("TRADE_BOT_DEBUG"))
USER_AGENT = "roblox-trade-bot/1.0 (+https://github.com/krat3r/roblox-trade-bot)"


class HttpError(RuntimeError):
    def __init__(self, url: str, status: int | None, message: str):
        super().__init__(f"{url}: {status or ''} {message}".strip())
        self.url = url
        self.status = status


def request_json(url: str, *, body: dict | None = None, retries: int = 3, timeout: float = 20.0):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"

    delay = 2.0
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers=headers)
        started = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
            if DEBUG:
                print(f"[http] {resp.status} {time.monotonic() - started:.1f}s {url}", file=sys.stderr)
            return json.loads(body.decode())
        except urllib.error.HTTPError as e:
            if DEBUG:
                print(f"[http] {e.code} {time.monotonic() - started:.1f}s {url}", file=sys.stderr)
            # 429 = rate limited, 5xx = server trouble: worth retrying.
            if (e.code == 429 or e.code >= 500) and attempt < retries:
                time.sleep(delay)
                delay *= 2
                continue
            raise HttpError(url, e.code, e.reason) from e
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < retries:
                time.sleep(delay)
                delay *= 2
                continue
            raise HttpError(url, None, str(e)) from e
    raise AssertionError("unreachable")
