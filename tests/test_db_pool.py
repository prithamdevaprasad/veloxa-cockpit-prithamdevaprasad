"""The optional API connection pool: connections are reused, capped, and carry no session state between requests."""

import threading

import pytest

from veloxa import db as vdb

pytestmark = pytest.mark.db


@pytest.fixture
def pooled(monkeypatch):
    monkeypatch.setenv("VELOXA_DB_POOL_MAX", "2")
    monkeypatch.setenv("VELOXA_DB_POOL_TIMEOUT", "5")
    yield
    for p in list(vdb._POOLS.values()):
        p.close()
    vdb._POOLS.clear()


def test_a_session_level_tenant_setting_never_reaches_the_next_request(db, pooled):
    with vdb.connect(db.admin_dsn) as c:
        pid = c.execute("SELECT pg_backend_pid() AS p").fetchone()["p"]
        c.execute("SELECT set_config('app.tenant_id', 'tenant-a', false)")         # a careless session-level SET
    seen = []
    for _ in range(4):                                                              # until the same backend is reused
        with vdb.connect(db.admin_dsn) as c:
            row = c.execute("SELECT pg_backend_pid() AS p, current_setting('app.tenant_id', true) AS t").fetchone()
            seen.append((row["p"], row["t"]))
    assert any(p == pid for p, _ in seen), "the pool did not reuse the connection"
    assert all(t in (None, "") for _, t in seen)


def test_the_pool_caps_connections_and_waits_rather_than_opening_more(db, pooled):
    peak, lock, live = [0], threading.Lock(), [0]

    def work():
        with vdb.connect(db.admin_dsn) as c:
            with lock:
                live[0] += 1
                peak[0] = max(peak[0], live[0])
            c.execute("SELECT pg_sleep(0.2)")
            with lock:
                live[0] -= 1

    ts = [threading.Thread(target=work) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert peak[0] <= 2
    stats = next(iter(vdb.pool_stats().values()))
    assert stats["pool_max"] == 2 and stats["requests_num"] >= 8


def test_without_the_setting_each_connect_is_a_fresh_connection(db, monkeypatch):
    monkeypatch.delenv("VELOXA_DB_POOL_MAX", raising=False)
    with vdb.connect(db.admin_dsn) as a:
        pa = a.execute("SELECT pg_backend_pid() AS p").fetchone()["p"]
    with vdb.connect(db.admin_dsn) as b:
        assert b.execute("SELECT pg_backend_pid() AS p").fetchone()["p"] != pa
