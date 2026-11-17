"""C01 in-process gateway controls: security headers, body validation, per-client rate limits, trusted proxy."""

import pytest

from veloxa.gateway import TokenBucket


def test_the_token_bucket_refills_and_reports_the_wait():
    b = TokenBucket(60)                                        # one token per second, burst 60
    assert all(b.take("k", now=0.0) == 0 for _ in range(60))
    assert b.take("k", now=0.0) == pytest.approx(1.0)
    assert b.take("other", now=0.0) == 0                       # keys are independent
    assert b.take("k", now=1.0) == 0


@pytest.mark.db
def test_headers_validation_limits_and_the_proxy_boundary(db, monkeypatch, tmp_path):
    from conftest import DEV_TOKEN, api_client, dev_fixture, ensure_configured_d1

    ensure_configured_d1(db, tmp_path)
    monkeypatch.setenv("VELOXA_RATE_LIMIT_PER_MIN", "5")
    monkeypatch.setenv("VELOXA_MAX_BODY_BYTES", "200")
    with api_client(db, monkeypatch, **dev_fixture()) as c:
        r = c.get("/v1/meta/clock")
        assert r.headers["X-Content-Type-Options"] == "nosniff" and r.headers["X-Frame-Options"] == "DENY"
        assert r.headers["Cache-Control"] == "no-store" and "default-src 'none'" in r.headers["Content-Security-Policy"]
        auth = {"Authorization": f"Bearer {DEV_TOKEN}"}
        body = b'{"question": "' + b"x" * 400 + b'"}'
        big = c.post("/v1/ask", content=body, headers={**auth, "Content-Type": "application/json"})
        assert big.status_code == 413
        form = c.post("/v1/ask", content=b"question=x", headers={**auth, "Content-Type": "text/plain"})
        assert form.status_code == 415
        codes = [c.get("/v1/meta/clock", headers={"Authorization": "Bearer someone-else"}).status_code
                 for _ in range(6)]
        assert codes[:5] == [200] * 5 and codes[5] == 429             # per client key
        limited = c.get("/v1/meta/clock", headers={"Authorization": "Bearer someone-else"})
        assert limited.status_code == 429 and int(limited.headers["Retry-After"]) >= 1
        assert limited.headers["X-Content-Type-Options"] == "nosniff"
        assert all(c.get("/health").status_code != 429 for _ in range(8))     # probes are exempt
        spoof = [c.get("/v1/meta/clock", headers={"X-Forwarded-For": f"10.0.0.{i}"}).status_code for i in range(8)]
        assert 429 in spoof                                             # an untrusted client cannot pick its key
