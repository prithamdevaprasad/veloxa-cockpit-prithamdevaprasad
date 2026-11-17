"""C01 gateway controls, in process (ARCHITECTURE.md §4.1 Edge plane; C01 is "Integrate"; the hackathon profile §25
runs FastAPI directly). A production API gateway or WAF in front of the service replaces or duplicates these
controls; none is deployed.

- **Security headers** on every response: nosniff, frame denial, no referrer, a restrictive CSP for the JSON API,
  HSTS, and `Cache-Control: no-store` on `/v1` responses (they carry business data).
- **Request validation:** bodies above `VELOXA_MAX_BODY_BYTES` (default 65536) are refused with 413, before the
  route runs. A write with a body that is not `application/json` is refused with 415.
- **Rate limiting:** a token bucket per client key (default 120 requests per minute, `VELOXA_RATE_LIMIT_PER_MIN`).
  An operational default, NOT an approved SLA. Exceeding it gives 429 with `Retry-After`.
  - Client key: a hash of the bearer credential when present (never the credential itself), else the peer address.
  - `X-Forwarded-For` is honoured only from the proxy named in `VELOXA_TRUSTED_PROXY` (the WAF/gateway boundary),
    so a client cannot choose its own identity for limiting.
- Uploads (`POST /v1/submissions`) have their own body ceiling, `VELOXA_MAX_UPLOAD_BYTES` (default 70 MiB of
  base64 JSON). C06 then applies its per-file and per-pack limits before anything is stored.
- `/health` is exempt from limiting, so an orchestrator's probes are never throttled.
- **Expensive endpoints** (Ask, agent messages, scenarios: the model-capable paths) have their own, stricter bucket:
  `VELOXA_RATE_LIMIT_EXPENSIVE_PER_MIN`, default 10 per client per minute. This is a PROPOSED pilot value awaiting
  owner approval, not an approved production limit.
"""

from __future__ import annotations

import hashlib
import os
import threading
import time

from fastapi import Request
from fastapi.responses import JSONResponse

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Cross-Origin-Resource-Policy": "same-origin",
}
EXEMPT = {"/health"}
_EXPENSIVE = (("POST", "/v1/ask"), ("POST", "/v1/scenarios/"), ("POST", "/v1/sessions/"))


def expensive(method: str, path: str) -> bool:
    if path.startswith("/v1/sessions/") and not path.endswith("/messages"):
        return False                                     # session open/close are cheap; only messages compose
    return any(method == m and (path == p or (p.endswith("/") and path.startswith(p))) for m, p in _EXPENSIVE)


class TokenBucket:
    def __init__(self, per_minute: int):
        self.rate = per_minute / 60.0
        self.capacity = float(per_minute)
        self._lock = threading.Lock()
        self._buckets: dict[str, tuple[float, float]] = {}

    def take(self, key: str, now: float | None = None) -> float:
        """0 if allowed, else seconds until the next token."""
        now = time.monotonic() if now is None else now
        with self._lock:
            tokens, last = self._buckets.get(key, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - last) * self.rate)
            if tokens >= 1:
                self._buckets[key] = (tokens - 1, now)
                return 0.0
            self._buckets[key] = (tokens, now)
            return (1 - tokens) / self.rate


def client_key(request: Request) -> str:
    auth = request.headers.get("authorization")
    if auth:
        return "cred:" + hashlib.sha256(auth.encode()).hexdigest()[:24]
    peer = request.client.host if request.client else "unknown"
    trusted = os.environ.get("VELOXA_TRUSTED_PROXY")
    if trusted and peer == trusted:
        fwd = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        if fwd:
            return "ip:" + fwd
    return "ip:" + peer


def install(app) -> None:
    max_body = int(os.environ.get("VELOXA_MAX_BODY_BYTES", "65536"))
    # Uploads (POST /v1/submissions: base64 JSON) have their own ceiling, sized for the C06 per-file limit.
    max_upload = int(os.environ.get("VELOXA_MAX_UPLOAD_BYTES", str(70 * 2**20)))
    bucket = TokenBucket(int(os.environ.get("VELOXA_RATE_LIMIT_PER_MIN", "120")))
    costly = TokenBucket(int(os.environ.get("VELOXA_RATE_LIMIT_EXPENSIVE_PER_MIN", "10")))
    app.state.rate_limiter = bucket

    @app.middleware("http")
    async def gateway(request: Request, call_next):
        path = request.url.path
        refusal = None
        if path not in EXEMPT:
            key = client_key(request)
            wait = bucket.take(key)
            if not wait and expensive(request.method, path):
                wait = costly.take(key)
            if wait > 0:
                refusal = JSONResponse(status_code=429, headers={"Retry-After": str(max(1, int(wait + 0.999)))},
                                       content={"error": "rate_limited", "detail": "too many requests"})
        if refusal is None and request.method in ("POST", "PUT", "PATCH"):
            length = request.headers.get("content-length")
            limit = max_upload if (request.method == "POST" and path == "/v1/submissions") else max_body
            if length is not None and (not length.isdigit() or int(length) > limit):
                refusal = JSONResponse(status_code=413, content={"error": "payload_too_large",
                                                                 "detail": f"bodies are limited to {limit} bytes"})
            elif length not in (None, "0") and \
                    request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
                refusal = JSONResponse(status_code=415, content={"error": "unsupported_media_type",
                                                                 "detail": "request bodies must be application/json"})
        response = refusal or await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        if path.startswith("/v1"):
            response.headers["Cache-Control"] = "no-store"
        return response
