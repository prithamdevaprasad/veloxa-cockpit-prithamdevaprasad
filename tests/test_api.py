from datetime import datetime

import pytest

pytestmark = pytest.mark.db


@pytest.fixture(scope="session")
def veloxa_d1(db, tmp_path_factory):
    """Admit one synthetic D1 pack into the configured 'veloxa' tenant, once per test session."""
    from conftest import ensure_configured_d1

    ensure_configured_d1(db, tmp_path_factory.mktemp("veloxa_pack"))
    return db.dsn("app")


def _drop_privileged(monkeypatch):
    from veloxa.api import PRIVILEGED_ENV

    for name in PRIVILEGED_ENV:
        monkeypatch.delenv(name, raising=False)


def test_health_and_time_metadata(veloxa_d1, monkeypatch):
    from fastapi.testclient import TestClient

    from veloxa.api import create_app

    _drop_privileged(monkeypatch)
    monkeypatch.setenv("VELOXA_TEST_MODE", "1")
    monkeypatch.setenv("VELOXA_CLOCK", "2026-11-16T12:00:00+05:30")

    with TestClient(create_app(dsn=veloxa_d1)) as client:
        assert client.get("/health").json() == {"status": "ok", "selfcheck": "pass", "pending_admission": [],
                                                "awaiting_authorisation": [], "auth": "none", "oidc": "off",
            "model": {"model": "disabled", "reason": "no provider configured (DENY_ALL); facts-only composition"}}

        clock = client.get("/v1/meta/clock").json()
        assert clock["sealed_watermark"] == 1 and clock["time_zone"] == "Asia/Kolkata"

        packs = {p["pack"]: p for p in client.get("/v1/meta/releases").json()["packs"]}
        assert packs["D1"]["status"] == "sealed" and packs["D1"]["admitted"]
        assert packs["D2"]["status"] == "not_released" and packs["D2"]["record_count"] is None


def test_api_keeps_serving_the_latest_sealed_batch_when_a_released_pack_is_not_admitted(veloxa_d1, monkeypatch):
    """Owner decision (16 Nov 2026): a later pack that fails admission does not take the API down. The API keeps
    serving the latest sealed batch, reports the pending pack, and never exposes unsealed data."""
    from fastapi.testclient import TestClient

    from veloxa.api import create_app

    _drop_privileged(monkeypatch)
    after_d2_release = datetime.fromisoformat("2026-11-17T12:00:00+05:30")  # D2 released, never admitted here
    with TestClient(create_app(dsn=veloxa_d1, clock=lambda: after_d2_release)) as client:
        health = client.get("/health").json()
        assert health == {"status": "degraded", "selfcheck": "pass", "pending_admission": ["D2"],
                          "awaiting_authorisation": [], "auth": "none", "oidc": "off",   # D2 authorised (K-13, 18 Nov)
                          "model": {"model": "disabled",
                                    "reason": "no provider configured (DENY_ALL); facts-only composition"}}
        assert client.get("/v1/meta/clock").json()["sealed_watermark"] == 1
        packs = {p["pack"]: p for p in client.get("/v1/meta/releases").json()["packs"]}
        assert packs["D1"]["status"] == "sealed"
        assert packs["D2"]["status"] == "pending_admission" and not packs["D2"]["admitted"]
        assert packs["D2"]["record_count"] is None and packs["D2"]["sealed_at"] is None
        assert packs["D3"]["status"] == "not_released"


def test_api_refuses_to_start_as_a_superuser(db, monkeypatch):
    from fastapi.testclient import TestClient

    from veloxa.api import create_app

    admin_dsn = db.admin_dsn
    _drop_privileged(monkeypatch)
    with pytest.raises(RuntimeError, match="self-check failed"):
        with TestClient(create_app(dsn=admin_dsn)):
            pass


@pytest.mark.parametrize("name", ["VELOXA_DB_ADMIN_PASSWORD", "VELOXA_MIGRATOR_PASSWORD", "VELOXA_INGEST_PASSWORD"])
def test_api_refuses_privileged_credentials_in_its_environment(monkeypatch, name):
    """Regression: the long-running API must never hold administrator, migrator or ingest credentials."""
    from veloxa.api import create_app

    _drop_privileged(monkeypatch)
    monkeypatch.setenv(name, "anything")
    with pytest.raises(RuntimeError, match="privileged database credentials"):
        create_app(dsn="postgresql://unused@127.0.0.1:1/unused")
