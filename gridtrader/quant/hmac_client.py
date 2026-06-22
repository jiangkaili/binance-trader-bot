"""HMAC-signed signed_request helper for Binance REST.

This is intentionally small and dependency-free so you can audit the
signature path end-to-end. It does the standard pattern:

  1. Build query string from params
  2. Append timestamp
  3. HMAC-SHA256(query, secret)
  4. Append &signature=<hex>
  5. Send GET/POST with X-MBX-APIKEY header

Read your key from env vars. Never hardcode.
"""
from __future__ import annotations

import hashlib
import hmac
import time
import urllib.parse
from typing import Any, Mapping

import requests


def sign_params(params: Mapping[str, Any], secret: str, *, timestamp_ms: int | None = None) -> dict:
    """Add `timestamp` and `signature` to a copy of params. Returns the signed dict.

    IMPORTANT: do NOT sort the params here — `requests` sends params in
    insertion order, so the signature must be computed over the same order
    Binance will reconstruct server-side.
    """
    p = dict(params)
    p["timestamp"] = timestamp_ms if timestamp_ms is not None else int(time.time() * 1000)
    qs = urllib.parse.urlencode(list(p.items()), doseq=True)
    sig = hmac.new(secret.encode("utf-8"), qs.encode("utf-8"), hashlib.sha256).hexdigest()
    p["signature"] = sig
    return p


class BinanceTimestampError(RuntimeError):
    """Raised when the server rejects our request for clock drift."""

    def __init__(self, msg: str, time_offset_ms: int):
        super().__init__(msg)
        self.time_offset_ms = time_offset_ms


def signed_request(
    method: str,
    url: str,
    params: Mapping[str, Any],
    api_key: str,
    api_secret: str,
    *,
    proxies: dict | None = None,
    timeout: float = 10.0,
    time_offset_ms: int = 0,
    recv_window_ms: int = 5000,
) -> requests.Response:
    """Issue a HMAC-signed request to a Binance endpoint.

    - `params` should be the request parameters WITHOUT timestamp/signature
    - `time_offset_ms` = (server_time - local_time). Pass the result of one
      /fapi/v1/time call. This avoids the "Timestamp 1000ms ahead" error.
    - `recv_window_ms` = how far off our timestamp may be from the server's
      current time. 5000ms is the Binance default.
    - If the response indicates a -1021 timestamp error, raises
      BinanceTimestampError so the caller can retry with a fresh offset.
    """
    if not api_key or not api_secret:
        raise ValueError("api_key and api_secret must be set (read from env, do not hardcode)")
    # recvWindow must be PART of the signed payload (Binance rejects otherwise)
    to_sign = dict(params)
    to_sign["recvWindow"] = recv_window_ms
    signed = sign_params(to_sign, api_secret, timestamp_ms=int(time.time() * 1000) + time_offset_ms)
    headers = {"X-MBX-APIKEY": api_key}
    method = method.upper()
    if method == "GET":
        r = requests.get(url, params=signed, headers=headers, proxies=proxies, timeout=timeout)
    elif method == "POST":
        r = requests.post(url, params=signed, headers=headers, proxies=proxies, timeout=timeout)
    elif method == "DELETE":
        r = requests.delete(url, params=signed, headers=headers, proxies=proxies, timeout=timeout)
    elif method == "PUT":
        r = requests.put(url, params=signed, headers=headers, proxies=proxies, timeout=timeout)
    else:
        raise ValueError(f"unsupported method: {method}")
    # Detect the timestamp-drift error and surface a clear exception
    if r.status_code == 400:
        try:
            j = r.json()
            if isinstance(j, dict) and j.get("code") == -1021:
                raise BinanceTimestampError(j.get("msg", "timestamp drift"), time_offset_ms)
        except ValueError:
            # not JSON, leave the response to the caller
            pass
    return r
