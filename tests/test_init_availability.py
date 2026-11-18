"""Start-up, migration and admission are separate (owner decision, 16 Nov 2026, availability; migration/admission
separation, 17 Nov 2026).

- `veloxa init` migrates and self-checks. It bootstraps a FRESH database with the authorised baseline pack only, and
  never admits a later pack, even when that pack is released.
- Admission is an explicit operation (`veloxa ingest --pack <id>`), allowed only for packs authorised in
  `config/admission_authorisations.toml`; anything else is refused before a file is opened, and audited.
- A failed or refused admission commits nothing and never touches the migrated schema.
- With a sealed baseline the API serves it; a released, unadmitted pack is reported, never exposed.
Uses a temporary schedule whose second pack is already released, and SYNTHETIC data only.
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

AUTH_D1 = """
[[pack]]
id = "D1"
baseline = true
approved_by = "Synthetic owner"
approved_on = 2026-11-16
decision_record = "synthetic test authorisation"
"""
AUTH_D2 = """
[[pack]]
id = "D2"
approved_by = "Synthetic owner"
approved_on = 2026-11-17
decision_record = "synthetic test authorisation"
sources = ["data_room/hr/Hiring_Tracker.xlsx"]   # K-13: source-specific (never pack-wide)
"""


@pytest.fixture
def init_env(db, tmp_path, monkeypatch):
    cfg = tmp_path / "config"
    cfg.mkdir()
    slug = f"init-{uuid.uuid4().hex[:10]}"  # a fresh tenant per test: admitted history is never deleted
    (cfg / "release_schedule.toml").write_text(SCHEDULE.replace("__SLUG__", slug), encoding="utf-8")
    (cfg / "admission_authorisations.toml").write_text(AUTH_D1, encoding="utf-8")
    shutil.copy(config.config_dir() / "source_catalogue.toml", cfg / "source_catalogue.toml")
    data = tmp_path / "packs"
    make_d1(data)
    make_poisoned_d2(data)  # matches no catalogue rule: reading it would fail admission
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


def _state(db, tenant):
    with connect(db.dsn("ingest")) as conn:
        batches = [(b["batch_seq"], b["pack_id"], b["sealed"], b["record_count"]) for b in ledger.batches(conn, tenant)]
        with tenant_tx(conn, tenant):
            events = [(e["kind"], e["detail"]) for e in
                      conn.execute("SELECT kind, detail FROM audit.event ORDER BY seq").fetchall()]
    with psycopg.connect(db.admin_dsn) as admin:
        migrations = [r[0] for r in admin.execute("SELECT name FROM veloxa_meta.schema_migration ORDER BY 1")]
    return batches, events, migrations


def test_init_bootstraps_a_fresh_database_with_the_authorised_baseline_and_never_reads_a_later_pack(
        db, init_env, capsys):
    from veloxa.cli import main

    assert main(["init"]) == 0
    err = capsys.readouterr().err
    assert "D2 is released but not admitted; init never admits a later pack" in err
    batches, events, _ = _state(db, init_env)
    assert batches == [(1, "D1", True, EXPECTED_RECORDS)]
    assert [k for k, _ in events] == ["admission.sealed", "projection.built"]  # no D2 attempt at all


def test_restarting_init_admits_nothing_and_migration_is_idempotent(db, init_env, capsys):
    from veloxa.cli import main

    assert main(["init"]) == 0
    before = _state(db, init_env)
    assert main(["init"]) == 0 and main(["migrate"]) == 0
    assert "none (up to date)" in capsys.readouterr().out
    assert _state(db, init_env) == before          # no new batch, no new audit event, same migrations


def test_migration_and_restart_do_not_need_the_data_folder_once_a_baseline_exists(db, init_env, tmp_path,
                                                                                   monkeypatch):
    from veloxa.cli import main

    assert main(["init"]) == 0
    before = _state(db, init_env)
    monkeypatch.setenv("VELOXA_DATA_PATH", str(tmp_path / "does-not-exist"))
    assert main(["init"]) == 0                     # migrate and serve the sealed baseline; nothing is admitted
    assert _state(db, init_env) == before


def test_a_fresh_database_without_its_data_folder_fails(db, init_env, tmp_path, monkeypatch):
    from veloxa.cli import main

    monkeypatch.setenv("VELOXA_DATA_PATH", str(tmp_path / "does-not-exist"))
    assert main(["init"]) == 2


def test_explicit_admission_of_an_unauthorised_pack_is_refused_and_audited(db, init_env, capsys):
    from veloxa.cli import main

    assert main(["init"]) == 0
    batches, _, migrations = _state(db, init_env)
    assert main(["ingest", "--pack", "D2"]) == 1
    assert "D2: admission FAILED (not_authorised)" in capsys.readouterr().err
    assert main(["ingest"]) == 1                    # "released": D1 already admitted, D2 refused
    after_batches, events, after_migrations = _state(db, init_env)
    assert after_batches == batches and after_migrations == migrations
    assert events[-1] == ("admission.failed", {"pack": "D2", "reason": "not_authorised"})  # code only, no content


def test_an_authorised_pack_that_fails_admission_commits_nothing_and_keeps_the_migrated_schema(
        db, init_env, tmp_path, capsys):
    from veloxa.cli import main

    assert main(["init"]) == 0
    batches, _, migrations = _state(db, init_env)
    (tmp_path / "config" / "admission_authorisations.toml").write_text(AUTH_D1 + AUTH_D2, encoding="utf-8")
    assert main(["ingest", "--pack", "D2"]) == 1   # authorised, but its catalogue rules are incomplete
    assert "D2: admission FAILED (UnclassifiedSource)" in capsys.readouterr().err
    after_batches, events, after_migrations = _state(db, init_env)
    assert after_batches == batches and after_migrations == migrations
    assert events[-1] == ("admission.failed", {"pack": "D2", "reason": "UnclassifiedSource"})
    with connect(db.dsn("ingest")) as conn, tenant_tx(conn, init_env):
        assert conn.execute("SELECT count(*) AS n FROM ledger.source_record WHERE batch_seq = 2").fetchone()["n"] == 0


def test_an_explicit_authorised_d1_admission_works_and_is_idempotent(db, init_env, capsys):
    from veloxa.cli import main

    assert main(["migrate"]) == 0                   # migration alone admits nothing
    assert _state(db, init_env)[0] == []
    assert main(["ingest", "--pack", "D1"]) == 0
    assert main(["ingest", "--pack", "D1"]) == 0    # already admitted: recognised, never admitted twice
    assert _state(db, init_env)[0] == [(1, "D1", True, EXPECTED_RECORDS)]


def test_without_an_authorisation_register_a_fresh_database_is_not_bootstrapped(db, init_env, tmp_path, capsys):
    from veloxa.cli import EXIT_NO_SEALED_BASELINE, main

    (tmp_path / "config" / "admission_authorisations.toml").unlink()
    assert main(["init"]) == EXIT_NO_SEALED_BASELINE
    assert "no authorised baseline pack" in capsys.readouterr().err
    assert _state(db, init_env)[0] == []


def test_fresh_database_whose_baseline_fails_does_not_start_the_api(db, init_env, capsys):
    """Owner decision: with no sealed baseline batch there is nothing to serve, so init fails and the API
    (which waits for init to succeed) does not start."""
    import os
    from pathlib import Path

    from veloxa.cli import EXIT_NO_SEALED_BASELINE, main

    d1 = Path(os.environ["VELOXA_DATA_PATH"]) / "Day1_Mon_16Nov"
    (d1 / "zz_unclassified.bin").write_bytes(b"x")  # D1 now fails admission (no catalogue rule)
    assert main(["init"]) == EXIT_NO_SEALED_BASELINE
    assert "no sealed baseline batch" in capsys.readouterr().err
    batches, events, _ = _state(db, init_env)
    assert batches == [] and [k for k, _ in events] == ["admission.failed"] and events[0][1]["pack"] == "D1"


def test_existing_database_serves_d1_and_reports_d2_as_awaiting_authorisation(db, init_env, monkeypatch):
    """Owner decision: an existing database with D1 sealed keeps serving D1; a released, unauthorised pack is
    reported (health degraded) and nothing from it is exposed."""
    from fastapi.testclient import TestClient

    from veloxa.api import PRIVILEGED_ENV, create_app
    from veloxa.cli import main

    assert main(["init"]) == 0
    app_dsn = db.dsn("app")
    for name in PRIVILEGED_ENV:
        monkeypatch.delenv(name, raising=False)
    from veloxa.releases import current_time, load_authorisations, load_schedule

    now, auth = current_time(), load_authorisations()      # real clock: derive what is released by now
    later = [p.id for p in load_schedule().packs if p.id != "D1" and p.release_at <= now]
    pending = [p for p in later if p in auth]
    awaiting = [p for p in later if p not in auth]
    with TestClient(create_app(dsn=app_dsn)) as client:
        assert client.get("/health").json() == {"status": "degraded" if later else "ok", "selfcheck": "pass",
                                                "pending_admission": pending, "awaiting_authorisation": awaiting,
                                                "auth": "none", "oidc": "off",
            "model": {"model": "disabled", "reason": "no provider configured (DENY_ALL); facts-only composition"}}
        assert client.get("/v1/meta/clock").json()["sealed_watermark"] == 1
        packs = {p["pack"]: p for p in client.get("/v1/meta/releases").json()["packs"]}
        assert packs["D1"]["status"] == "sealed" and packs["D1"]["record_count"] == EXPECTED_RECORDS
        assert packs["D2"]["status"] == ("pending_admission" if "D2" in auth else "awaiting_authorisation")
        assert packs["D2"]["record_count"] is None                    # `init` never admits a later pack
    with connect(app_dsn) as conn:
        assert len(ledger.visible_record_ids(conn, init_env, 1)) == EXPECTED_RECORDS - 3  # protected pay hidden
        with pytest.raises(ledger.InvalidTime):
            ledger.visible_record_ids(conn, init_env, 2)  # nothing beyond the sealed baseline is readable
