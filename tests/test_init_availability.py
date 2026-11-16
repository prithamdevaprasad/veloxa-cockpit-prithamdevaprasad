"""`veloxa init` availability behaviour (owner decision, 16 Nov 2026).

A later pack that fails admission must not stop the API: init still succeeds, the failure is audited
(pack and reason code only), nothing from the failed pack is committed, and the earlier sealed batch is
unchanged. Uses a temporary schedule whose second pack is already released, and SYNTHETIC data only.
"""

import shutil
import uuid
from urllib.parse import urlsplit

import psycopg
import pytest
from synthetic_pack import EXPECTED_RECORDS, make_d1, make_poisoned_d2

from veloxa import config, ledger
from veloxa.db import connect, tenant_tx

pytestmark = pytest.mark.db

SCHEDULE = """
[tenant]
slug = "__SLUG__"
time_zone = "Asia/Kolkata"

[[pack]]
id = "D1"
batch_seq = 1
folder = "Day1_Mon_16Nov"
release_at = "2026-11-16T09:00:00+05:30"

[[pack]]
id = "D2"
batch_seq = 2
folder = "Day2_Tue_17Nov"
release_at = "2026-11-16T09:30:00+05:30"
"""


@pytest.fixture
def init_env(db, tmp_path, monkeypatch):
    cfg = tmp_path / "config"
    cfg.mkdir()
    slug = f"init-{uuid.uuid4().hex[:10]}"  # a fresh tenant per test: admitted history is never deleted
    (cfg / "release_schedule.toml").write_text(SCHEDULE.replace("__SLUG__", slug), encoding="utf-8")
    shutil.copy(config.config_dir() / "source_catalogue.toml", cfg / "source_catalogue.toml")
    data = tmp_path / "packs"
    make_d1(data)
    make_poisoned_d2(data)  # matches no catalogue rule: its admission fails
    tenant = config.tenant_uuid(slug)
    with psycopg.connect(db.admin_dsn, autocommit=True) as admin:
        admin.execute("INSERT INTO directory.tenant VALUES (%s, %s, true, 'Asia/Kolkata', now())", (tenant, slug))
    parts = urlsplit(db.admin_dsn)
    monkeypatch.setenv("VELOXA_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("VELOXA_DATA_PATH", str(data))
    monkeypatch.setenv("VELOXA_DB_HOST", parts.hostname)
    monkeypatch.setenv("VELOXA_DB_PORT", str(parts.port))
    monkeypatch.setenv("VELOXA_DB_NAME", db.name)
    monkeypatch.setenv("VELOXA_DB_ADMIN_USER", parts.username)
    monkeypatch.setenv("VELOXA_DB_ADMIN_PASSWORD", parts.password)
    return tenant


def test_init_succeeds_when_a_later_pack_fails_and_the_sealed_batch_is_unchanged(db, init_env, capsys):
    from veloxa.cli import main

    assert main(["init"]) == 0  # the API (which waits for init) will start
    err = capsys.readouterr().err
    assert "D2: admission FAILED (UnclassifiedSource)" in err and "continuing" in err

    with connect(db.dsn("ingest")) as conn:
        batches = ledger.batches(conn, init_env)
        assert [(b["batch_seq"], b["pack_id"], b["sealed"]) for b in batches] == [(1, "D1", True)]
        assert batches[0]["record_count"] == EXPECTED_RECORDS
        with tenant_tx(conn, init_env):
            events = conn.execute("SELECT kind, detail FROM audit.event ORDER BY seq").fetchall()
            assert conn.execute("SELECT count(*) AS n FROM ledger.source_record WHERE batch_seq = 2"
                                ).fetchone()["n"] == 0
    assert [e["kind"] for e in events] == ["admission.sealed", "admission.failed", "projection.built"]
    assert events[1]["detail"] == {"pack": "D2", "reason": "UnclassifiedSource"}  # code only, no content


def test_ingest_command_still_reports_the_failure_with_a_nonzero_exit(db, init_env):
    from veloxa.cli import main

    assert main(["init"]) == 0
    assert main(["ingest"]) == 1  # operators see the failure; D1 is reported as already admitted


def test_init_fails_when_the_data_folder_is_missing(db, init_env, tmp_path, monkeypatch):
    from veloxa.cli import main

    monkeypatch.setenv("VELOXA_DATA_PATH", str(tmp_path / "does-not-exist"))
    assert main(["init"]) == 2


def test_fresh_database_whose_first_pack_fails_does_not_start_the_api(db, init_env, capsys):
    """Owner decision: with no sealed baseline batch there is nothing to serve, so init fails and the API
    (which waits for init to succeed) does not start."""
    import os
    from pathlib import Path

    from veloxa.cli import EXIT_NO_SEALED_BASELINE, main

    d1 = Path(os.environ["VELOXA_DATA_PATH"]) / "Day1_Mon_16Nov"
    (d1 / "zz_unclassified.bin").write_bytes(b"x")  # D1 now fails admission (no catalogue rule)
    assert main(["init"]) == EXIT_NO_SEALED_BASELINE
    assert "no sealed baseline batch" in capsys.readouterr().err
    with connect(db.dsn("ingest")) as conn:
        assert ledger.batches(conn, init_env) == []  # nothing partial, nothing unsealed
        assert ledger.sealed_watermark(conn, init_env) == 0
        with tenant_tx(conn, init_env):
            assert conn.execute("SELECT count(*) AS n FROM ledger.source_record").fetchone()["n"] == 0
            (event,) = conn.execute("SELECT kind, detail FROM audit.event").fetchall()
    assert event["kind"] == "admission.failed" and event["detail"]["pack"] == "D1"


def test_existing_database_with_d1_sealed_serves_d1_degraded_when_d2_fails(db, init_env, monkeypatch):
    """Owner decision: an existing database with D1 sealed keeps serving D1 when a later pack fails."""
    import os
    from pathlib import Path

    from fastapi.testclient import TestClient

    from veloxa.api import PRIVILEGED_ENV, create_app
    from veloxa.cli import main
    from veloxa.ingest.pipeline import admit_pack
    from veloxa.releases import current_time, load_schedule

    # The existing database: D1 already sealed by an earlier run.
    schedule = load_schedule()
    data = Path(os.environ["VELOXA_DATA_PATH"])
    with connect(db.dsn("ingest")) as conn:
        assert admit_pack(conn, init_env, schedule, "D1", data, current_time()).status == "admitted"

    # A later init run: D2 is released but fails admission.
    assert main(["init"]) == 0

    app_dsn = db.dsn("app")
    for name in PRIVILEGED_ENV:
        monkeypatch.delenv(name, raising=False)
    with TestClient(create_app(dsn=app_dsn)) as client:
        assert client.get("/health").json() == {"status": "degraded", "selfcheck": "pass",
                                                "pending_admission": ["D2"]}
        assert client.get("/v1/meta/clock").json()["sealed_watermark"] == 1
        packs = {p["pack"]: p for p in client.get("/v1/meta/releases").json()["packs"]}
        assert packs["D1"]["status"] == "sealed" and packs["D1"]["record_count"] == EXPECTED_RECORDS
        assert packs["D2"]["status"] == "pending_admission" and packs["D2"]["record_count"] is None
    with connect(app_dsn) as conn:
        assert len(ledger.visible_record_ids(conn, init_env, 1)) == EXPECTED_RECORDS - 3  # protected pay hidden
        with pytest.raises(ledger.InvalidTime):
            ledger.visible_record_ids(conn, init_env, 2)  # nothing beyond the sealed baseline is readable
