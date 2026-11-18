"""`veloxa hackathon-bootstrap` (owner decision 1, 19 Nov 2026) on a disposable database with the synthetic four-day
company: only released AND authorised packs are admitted, in order; an unauthorised pack is refused and audited;
an unreleased pack is refused before any file is opened; re-running is safe; agents are grounded. `init` is not
changed by this command (its own tests still assert it never admits a later pack)."""

import shutil
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from conftest import ADMIN_DSN, TestDatabase

pytestmark = pytest.mark.db
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def env(tmp_path, monkeypatch):
    if not ADMIN_DSN:
        pytest.skip("VELOXA_TEST_ADMIN_DSN not set: database tests skipped")
    import psycopg
    from synthetic_days import SOURCES, make_days

    name = f"veloxa_test_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        c.execute(f'CREATE DATABASE "{name}"')
    u = urlsplit(ADMIN_DSN)
    cfg = tmp_path / "config"
    shutil.copytree(ROOT / "config", cfg)
    sched = (cfg / "release_schedule.toml").read_text(encoding="utf-8")
    for real, shifted in (("2026-11-16", "2026-11-12"), ("2026-11-17", "2026-11-13"), ("2026-11-18", "2026-11-14"),
                          ("2026-11-19", "2099-01-01")):                     # D4 stays unreleased
        sched = sched.replace(f'release_at = "{real}T', f'release_at = "{shifted}T')
    (cfg / "release_schedule.toml").write_text(sched, encoding="utf-8")

    def authorise(packs):
        out = ['[[pack]]\nid = "D1"\nbaseline = true\napproved_by = "Synthetic owner"\napproved_on = 2026-11-16\n'
               'decision_record = "synthetic bootstrap fixture"\n']
        for pid in packs:
            out.append(f'[[pack]]\nid = "{pid}"\napproved_by = "Synthetic owner"\napproved_on = 2026-11-18\n'
                       f'decision_record = "synthetic bootstrap fixture"\nsources = {SOURCES[pid]!r}\n'
                       .replace("'", '"'))
        (cfg / "admission_authorisations.toml").write_text("\n".join(out), encoding="utf-8")

    make_days(tmp_path / "data")
    for k, v in {"VELOXA_DB_HOST": u.hostname, "VELOXA_DB_PORT": str(u.port), "VELOXA_DB_NAME": name,
                 "VELOXA_DB_ADMIN_USER": u.username, "VELOXA_DB_ADMIN_PASSWORD": u.password,
                 "VELOXA_DATA_PATH": str(tmp_path / "data"), "VELOXA_CONFIG_DIR": str(cfg)}.items():
        monkeypatch.setenv(k, v)
    from veloxa import config

    assert config.db_settings().database == name != "veloxa"            # never the real database
    try:
        yield {"db": TestDatabase(ADMIN_DSN, name), "authorise": authorise}
    finally:
        with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
            c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _state(db):
    from veloxa import config, ledger
    from veloxa.db import connect, tenant_tx
    from veloxa.releases import load_schedule

    tid = config.tenant_uuid(load_schedule().tenant_slug)
    with connect(db.admin_dsn) as c:
        packs = sorted(b["pack_id"] for b in ledger.batches(c, tid) if b["sealed"])
        with tenant_tx(c, tid):
            refused = [r["detail"] for r in c.execute("SELECT detail FROM audit.event WHERE kind = 'admission.failed'"
                                                      " ORDER BY seq")]
            agents = c.execute("SELECT count(DISTINCT agent_id) AS n FROM runtime.agent_person").fetchone()["n"]
            ceo_reader = c.execute("SELECT count(*) AS n FROM policy.live_tuple WHERE resource_type = 'tenant'"
                                   " AND relation = 'company_reader'").fetchone()["n"]
    return packs, refused, agents, ceo_reader


def test_bootstrap_admits_only_released_and_authorised_packs_in_order(env, capsys):
    from veloxa.cli import main

    env["authorise"](["D2"])                                         # D3 deliberately NOT authorised
    rc = main(["hackathon-bootstrap"])
    out = capsys.readouterr()
    packs, refused, agents, _ = _state(env["db"])
    assert rc == 1 and packs == ["D1", "D2"]
    assert refused == [{"pack": "D3", "reason": "not_authorised"}]            # refused AND audited
    assert "D4" not in out.out + out.err                                      # nothing after a refused pack
    assert agents == 14                                                       # grounded from the HR export

    env["authorise"](["D2", "D3"])                                   # the owner authorises D3: re-run is safe
    rc = main(["hackathon-bootstrap"])
    out = capsys.readouterr().out
    packs, refused, agents, ceo_reader = _state(env["db"])
    assert rc == 0 and packs == ["D1", "D2", "D3"]
    assert "D1: already admitted" in out and "D2: already admitted" in out
    assert "D4: refused, not released until 2099-01-01" in out and "no file was opened" in out
    assert len(refused) == 1 and agents == 14 and ceo_reader == 0          # no owner grant file: default deny


def test_a_source_outside_the_authorised_list_refuses_the_whole_pack(env, capsys):
    from synthetic_days import SOURCES

    from veloxa.cli import main

    env["authorise"](["D2"])
    cfg = Path(__import__("os").environ["VELOXA_CONFIG_DIR"]) / "admission_authorisations.toml"
    cfg.write_text(cfg.read_text(encoding="utf-8").replace(f'"{SOURCES["D2"][0]}", ', ""), encoding="utf-8")
    assert main(["hackathon-bootstrap"]) == 1
    packs, refused, _, _ = _state(env["db"])
    assert packs == ["D1"] and refused == [{"pack": "D2", "reason": "source_not_authorised"}]   # K-13 per source


def test_company_wide_read_needs_an_explicit_audited_grant_and_revocation_stops_it(env, tmp_path, monkeypatch):
    """Section 6: an HR title never grants access; a grant needs a verified principal, approver, decision and
    purpose; it is audited; ABAC still refuses protected classes; revocation stops it at once."""
    from veloxa import access_grants, config
    from veloxa.agents.authorization import CurrentAuthorization
    from veloxa.cli import main
    from veloxa.db import connect, tenant_tx
    from veloxa.releases import load_schedule

    env["authorise"](["D2", "D3"])
    assert main(["hackathon-bootstrap"]) == 0
    tid = config.tenant_uuid(load_schedule().tenant_slug)
    db = env["db"]
    assert _state(db)[3] == 0                                            # no file: nothing granted (default deny)
    with connect(db.admin_dsn) as a, tenant_tx(a, tid):
        ceo = a.execute("SELECT emp_id FROM projection.person WHERE title = 'CEO' ORDER BY batch_seq DESC LIMIT 1"
                        ).fetchone()["emp_id"]
        other = a.execute("SELECT emp_id FROM projection.person WHERE title <> 'CEO' AND exit_date IS NULL"
                          " ORDER BY emp_id LIMIT 1").fetchone()["emp_id"]

    def allowed(pid, system="keka_hrms", data_class="personal"):
        with connect(db.dsn("app")) as c:
            return CurrentAuthorization(c).allowed(tid, pid, system, data_class)[0]

    assert not allowed(f"person:{ceo}")                                  # the HR title alone grants nothing
    with connect(db.admin_dsn) as a:
        for bad, kw in (("person:NOT-IN-HR", {}), (f"person:{ceo}", {"approved_by": " "}),
                        (f"person:{ceo}", {"purposes": ["marketing"]})):
            args = {"approved_by": "Owner", "decision_ref": "test decision", "purposes": ["interview"]} | kw
            with pytest.raises(access_grants.GrantRefused):
                access_grants.grant_company_reader(a, tid, bad, **args)
        t = access_grants.grant_company_reader(a, tid, f"person:{ceo}", approved_by="Owner",
                                               decision_ref="test decision", purposes=["interview", "cockpit"])
        assert access_grants.grant_company_reader(a, tid, f"person:{ceo}", approved_by="Owner",
                                                  decision_ref="again", purposes=["interview"]) == t   # idempotent
        with tenant_tx(a, tid):
            kinds = [r["kind"] for r in a.execute("SELECT kind FROM audit.event WHERE kind LIKE 'access.%'")]
    assert kinds == ["access.company_reader_granted"]
    assert allowed(f"person:{ceo}")                                      # explicit grant, within scope
    assert not allowed(f"person:{ceo}", "zoho_payroll", "payroll_aggregate")      # protected classes are still
    assert not allowed(f"person:{ceo}", "leave_register", "sensitive_personal")    # refused despite the grant
    assert not allowed(f"person:{other}")                                # nobody else inherits it
    with connect(db.admin_dsn) as a:
        access_grants.revoke(a, tid, t, by="Owner", reason="test revocation")
        with pytest.raises(access_grants.GrantRefused):
            access_grants.revoke(a, tid, t, by="Owner", reason="twice")
        with tenant_tx(a, tid):
            assert a.execute("SELECT count(*) AS n FROM audit.event WHERE kind = 'access.company_reader_revoked'"
                             ).fetchone()["n"] == 1
    assert not allowed(f"person:{ceo}")                                  # revoked: stops at once


def test_bootstrap_applies_only_grants_from_the_owner_file(env, tmp_path, monkeypatch, capsys):
    from veloxa.cli import main

    env["authorise"](["D2", "D3"])
    f = tmp_path / "grants.toml"
    f.write_text('[[company_reader]]\nprincipal = "person:NOT-IN-HR"\napproved_by = "Owner"\n'
                 'decision = "test"\npurposes = ["interview"]\n', encoding="utf-8")
    monkeypatch.setenv("VELOXA_ACCESS_GRANTS", str(f))
    assert main(["hackathon-bootstrap"]) == 0
    assert "refused (the principal does not resolve" in capsys.readouterr().err
    assert _state(env["db"])[3] == 0
