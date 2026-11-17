"""C26 durable workflows on REAL DBOS and PostgreSQL (disposable databases). SYNTHETIC data only.

Covers: success, step failure with retry, permanent failure, recovery after an abrupt process death without
repeating checkpointed steps, idempotency and duplicate starts, invalid input, cancellation, tenant isolation,
authorization denial, audit events, versioning and pinned names, sensitive-payload exclusion, concurrency and
shutdown/restart."""

import os
import re
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

import psycopg
import pytest

pytestmark = pytest.mark.db
HERE = Path(__file__).resolve().parent


def _url(admin: str, dbname: str) -> str:
    p = urlsplit(admin)
    return f"{p.scheme}://{p.netloc}/{dbname}"


@pytest.fixture(scope="module")
def wf(db, tmp_path_factory):
    """One DBOS runtime for the module: its system tables in their OWN disposable database."""
    from conftest import ADMIN_DSN
    from synthetic_pack import make_d1

    from veloxa.workflows.catalogue import CATALOGUE
    from veloxa.workflows.runner import DBOSActivityRunner

    sysdb = f"veloxa_dbos_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(ADMIN_DSN, autocommit=True) as a:
        a.execute(f'CREATE DATABASE "{sysdb}"')
    p = urlsplit(ADMIN_DSN)
    root = tmp_path_factory.mktemp("packs")
    make_d1(root)
    env = {"VELOXA_WORKFLOW_DATABASE_URL": _url(ADMIN_DSN, sysdb), "VELOXA_DB_HOST": p.hostname,
           "VELOXA_DB_PORT": str(p.port), "VELOXA_DB_NAME": db.name, "VELOXA_DATA_PATH": str(root)}
    saved = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    runner = DBOSActivityRunner(CATALOGUE)
    runner.launch()
    try:
        yield {"runner": runner, "sysdb": sysdb, "sysurl": env["VELOXA_WORKFLOW_DATABASE_URL"], "root": root}
    finally:
        runner.shutdown()
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        with psycopg.connect(ADMIN_DSN, autocommit=True) as a:
            a.execute(f'DROP DATABASE IF EXISTS "{sysdb}" WITH (FORCE)')


def new_tenant(db) -> uuid.UUID:
    tid = uuid.uuid4()
    with psycopg.connect(db.admin_dsn, autocommit=True) as a:
        a.execute("INSERT INTO directory.tenant (tenant_id, slug, synthetic, time_zone) VALUES (%s,%s,true,'UTC')",
                  (tid, f"t-{tid.hex[:12]}"))
    return tid


def key() -> str:
    return f"syn-wf-{uuid.uuid4().hex[:12]}"


def admin_rows(db, sql, params=()):
    with psycopg.connect(db.admin_dsn, autocommit=True) as a:
        return a.execute(sql, params).fetchall()


def test_a_pack_pipeline_runs_every_step_once_and_audits(db, wf):
    r, t = wf["runner"], new_tenant(db)
    h = r.start("veloxa.pack_pipeline.v1", {"tenant_id": str(t), "pack_id": "D1"}, key())
    out = r.result(h.workflow_id)
    assert out["batch_seq"] == 1 and out["projected_rows"] > 0 and out["identity"]["hr_identities"] == 2
    s = r.state(h.workflow_id)
    assert (s.status, s.name) == ("SUCCESS", "veloxa.pack_pipeline.v1")
    from veloxa.workflows.runner import APPLICATION_VERSION
    assert s.application_version == APPLICATION_VERSION                         # recorded on every run
    (detail,) = admin_rows(db, "SELECT detail FROM audit.event WHERE tenant_id = %s AND kind = 'workflow.completed'",
                           (t,))
    assert detail[0]["workflow_id"] == h.workflow_id and detail[0]["batch_seq"] == 1


def test_starting_again_returns_the_same_run_and_a_reused_key_is_refused(db, wf):
    from veloxa.workflows.runner import WorkflowError

    r, t, k = wf["runner"], new_tenant(db), key()
    a = r.start("veloxa.pack_pipeline.v1", {"tenant_id": str(t), "pack_id": "D1"}, k)
    r.result(a.workflow_id)
    b = r.start("veloxa.pack_pipeline.v1", {"tenant_id": str(t), "pack_id": "D1"}, k)
    assert b.workflow_id == a.workflow_id
    assert admin_rows(db, "SELECT count(*) FROM ledger.admission_batch WHERE tenant_id = %s", (t,))[0][0] == 1
    with pytest.raises(WorkflowError) as e:
        r.start("veloxa.pack_pipeline.v1", {"tenant_id": str(new_tenant(db)), "pack_id": "D1"}, k)
    assert e.value.code == "idempotency_conflict"


@pytest.mark.parametrize("name, args, k, code", [
    ("veloxa.pack_pipeline.v1", {"tenant_id": "x", "pack_id": "D1"}, "syn-wf-00000001", "invalid_arguments"),
    ("veloxa.pack_pipeline.v1", {"tenant_id": str(uuid.uuid4()), "pack_id": "D0"}, "syn-wf-00000002",
     "invalid_arguments"),
    ("veloxa.pack_pipeline.v1", {"tenant_id": str(uuid.uuid4()), "pack_id": "D1", "sql": "drop"}, "syn-wf-00000003",
     "invalid_arguments"),
    ("veloxa.shell.v1", {}, "syn-wf-00000004", "unknown_workflow"),
    ("veloxa.pack_pipeline.v1", {"tenant_id": str(uuid.uuid4()), "pack_id": "D1"}, "short", "invalid_request"),
])
def test_invalid_starts_are_refused_before_anything_is_persisted(wf, name, args, k, code):
    from dbos import DBOS

    from veloxa.workflows.runner import WorkflowError

    with pytest.raises(WorkflowError) as e:
        wf["runner"].start(name, args, k)
    assert e.value.code == code and DBOS.list_workflows(workflow_ids=[f"{name}:{k}"]) == []


def test_a_refused_admission_is_a_permanent_failure_without_retries(db, wf):
    r, t = wf["runner"], new_tenant(db)
    h = r.start("veloxa.pack_pipeline.v1", {"tenant_id": str(t), "pack_id": "D2"}, key())   # D2: not authorised
    with pytest.raises(Exception, match="admission refused"):
        r.result(h.workflow_id)
    assert r.state(h.workflow_id).status == "ERROR"
    assert admin_rows(db, "SELECT count(*) FROM ledger.admission_batch WHERE tenant_id = %s", (t,))[0][0] == 0


def test_a_transient_step_failure_is_retried(db, wf, monkeypatch):
    from veloxa import projector

    real, calls = projector.build, []

    def flaky(conn, tenant_id):
        calls.append(1)
        if len(calls) == 1:
            raise psycopg.OperationalError("synthetic transient failure")
        return real(conn, tenant_id)

    monkeypatch.setattr(projector, "build", flaky)
    r, t = wf["runner"], new_tenant(db)
    h = r.start("veloxa.pack_pipeline.v1", {"tenant_id": str(t), "pack_id": "D1"}, key())
    assert r.result(h.workflow_id)["projected_rows"] > 0 and len(calls) == 2


def test_recovery_after_a_crash_resumes_without_repeating_checkpointed_steps(db, wf):
    """With no executor running, a child process dies inside the projection step (DBOS 3.5 leaves the run
    ENQUEUED). Restarting the application recovers it; the admit step is NOT executed again (its checkpoint, with
    its original completion time, is reused) and the pipeline completes once. A run cancelled while interrupted is
    never recovered."""
    from dbos import DBOS, DBOSClient

    from veloxa.workflows.catalogue import CATALOGUE
    from veloxa.workflows.runner import DBOSActivityRunner

    wf["runner"].shutdown()                                              # application shutdown: no executor
    wf["runner"] = None
    try:
        _crash_and_recover(db, wf, DBOS, DBOSClient, DBOSActivityRunner, CATALOGUE)
    finally:
        if wf["runner"] is None:                                         # never leave the module without DBOS
            _relaunch(wf, DBOSActivityRunner, CATALOGUE)


def _relaunch(wf, runner_cls, catalogue):
    runner = runner_cls(catalogue)
    runner.launch()
    wf["runner"] = runner


def _crash_and_recover(db, wf, DBOS, DBOSClient, DBOSActivityRunner, CATALOGUE):  # noqa: N803
    t, k = new_tenant(db), key()
    t2, k2 = new_tenant(db), key()
    child = [sys.executable, str(HERE / "workflow_crash_child.py")]
    first = subprocess.run(child + [str(t), k], capture_output=True, text=True, timeout=120, env=os.environ.copy())
    assert first.returncode == 3, first.stderr[-2000:]
    wid = first.stdout.strip().splitlines()[-1]
    second = subprocess.run(child + [str(t2), k2], capture_output=True, text=True, timeout=120, env=os.environ.copy())
    assert second.returncode == 3, second.stderr[-2000:]
    cancelled = second.stdout.strip().splitlines()[-1]
    client = DBOSClient(system_database_url=wf["sysurl"])
    try:
        (st,) = client.list_workflows(workflow_ids=[wid], load_input=False, load_output=False)
        assert st.status in ("PENDING", "ENQUEUED")                     # interrupted, not finished
        steps_before = {s["function_name"]: s["completed_at_epoch_ms"]
                        for s in client.list_workflow_steps(wid)}
        assert steps_before.get("veloxa.pack_pipeline.admit") and "veloxa.pack_pipeline.project" not in steps_before
        client.cancel_workflow(cancelled)                                # cancellation of an interrupted run
    finally:
        client.destroy()
    relaunched_at = int(time.time() * 1000)
    _relaunch(wf, DBOSActivityRunner, CATALOGUE)                         # restart: recovery on launch
    runner = wf["runner"]
    deadline = time.time() + 60
    while runner.state(wid).status != "SUCCESS" and time.time() < deadline:
        time.sleep(0.5)
    assert runner.state(wid).status == "SUCCESS"
    steps = {s["function_name"]: s["completed_at_epoch_ms"] for s in DBOS.list_workflow_steps(wid)}
    assert steps["veloxa.pack_pipeline.admit"] == steps_before["veloxa.pack_pipeline.admit"]
    assert steps["veloxa.pack_pipeline.admit"] < relaunched_at <= steps["veloxa.pack_pipeline.project"]
    assert admin_rows(db, "SELECT count(*) FROM audit.event WHERE tenant_id = %s AND kind = 'admission.sealed'",
                      (t,))[0][0] == 1
    time.sleep(2)
    assert runner.state(cancelled).status == "CANCELLED"                 # a cancelled run is not recovered
    assert admin_rows(db, "SELECT count(*) FROM audit.event WHERE tenant_id = %s AND kind = 'workflow.completed'",
                      (t2,))[0][0] == 0


def test_concurrent_runs_for_different_tenants_both_complete(db, wf):
    r = wf["runner"]
    tenants = [new_tenant(db), new_tenant(db)]
    handles = [r.start("veloxa.pack_pipeline.v1", {"tenant_id": str(t), "pack_id": "D1"}, key()) for t in tenants]
    with ThreadPoolExecutor(2) as ex:
        outs = list(ex.map(lambda h: r.result(h.workflow_id), handles))
    assert [o["batch_seq"] for o in outs] == [1, 1]


@pytest.fixture
def agent_session(db, wf, tmp_path):
    from agent_fixtures import SyntheticTestKnowledgeBoundary, grant_source, syn_agent
    from conftest import ensure_configured_d1

    from veloxa import ledger
    from veloxa import tools as vt
    from veloxa.agents import registry
    from veloxa.agents.runtime import AgentRuntime
    from veloxa.db import connect
    from veloxa.releases import current_time
    from veloxa.temporal import Mode, resolve_basis
    from veloxa.workflows import catalogue

    tenant = ensure_configured_d1(db, tmp_path)
    cfg = syn_agent("people_ops")
    with connect(db.dsn("projection")) as proj:
        registry.publish(proj, tenant, cfg)
    grant_source(db, tenant, "keka_hrms")
    catalogue.AGENT_RUNTIME_FACTORY = lambda conn: AgentRuntime(conn, "Asia/Kolkata",
                                                                kb=SyntheticTestKnowledgeBoundary())
    alpha = vt.Principal("person:T001", "authenticated", "alpha@example.test")
    with connect(db.dsn("app")) as app:
        basis = resolve_basis(tenant, ledger.sealed_releases(app, tenant), Mode.CURRENT, None, "Asia/Kolkata",
                              current_time())
        sid = AgentRuntime(app, "Asia/Kolkata", kb=SyntheticTestKnowledgeBoundary()).open_session(
            tenant, alpha, cfg.agent_id, basis)["session_id"]
    yield tenant, uuid.UUID(sid)
    catalogue.AGENT_RUNTIME_FACTORY = None


SECRET_MESSAGE = "What is our headcount? syn-confidential-marker-7f3a"


def test_a_durable_agent_turn_keeps_the_message_out_of_workflow_state(db, wf, agent_session):
    from veloxa.db import connect
    from veloxa.workflows.catalogue import submit_agent_request

    tenant, sid = agent_session
    r = wf["runner"]
    with connect(db.dsn("app")) as app:
        rid = submit_agent_request(app, tenant, "person:T001", sid, SECRET_MESSAGE, "syn-turn-0001")
        assert submit_agent_request(app, tenant, "person:T001", sid, SECRET_MESSAGE, "syn-turn-0001") == rid
    args = {"tenant_id": str(tenant), "request_id": str(rid), "interviewer_id": "person:T001"}
    h = r.start("veloxa.agent_turn.v1", args, "syn-turn-0001")
    out = r.result(h.workflow_id)
    assert out["status"] in ("answered", "qualified") and out["replayed"] is False
    again = r.start("veloxa.agent_turn.v1", args, "syn-turn-0001")
    assert again.workflow_id == h.workflow_id
    assert admin_rows(db, "SELECT count(*) FROM runtime.agent_execution WHERE session_id = %s", (sid,))[0][0] == 1
    with psycopg.connect(_url(wf["sysurl"].rsplit("/", 1)[0] + "/postgres", wf["sysdb"]), autocommit=True) as s:
        dump = " ".join(str(x) for row in s.execute(
            "SELECT inputs, output, error FROM dbos.workflow_status UNION ALL "
            "SELECT NULL, output, error FROM dbos.operation_outputs").fetchall() for x in row)
    assert "syn-confidential-marker" not in dump and "headcount" not in dump.lower()
    assert "local-dev-" not in dump and "password" not in dump.lower() and "Bearer" not in dump
    assert not re.search(r"(?<![\d,.])(1,?000|2,?000|3,?000)(?![\d,])", dump)          # no synthetic pay values


def test_another_interviewer_or_tenant_cannot_run_someone_elses_request(db, wf, agent_session):
    from veloxa.db import connect
    from veloxa.workflows.catalogue import submit_agent_request

    tenant, sid = agent_session
    r = wf["runner"]
    with connect(db.dsn("app")) as app:
        rid = submit_agent_request(app, tenant, "person:T001", sid, "What is our headcount?", "syn-turn-0002")
    for args in ({"tenant_id": str(tenant), "request_id": str(rid), "interviewer_id": "person:T002"},
                 {"tenant_id": str(new_tenant(db)), "request_id": str(rid), "interviewer_id": "person:T001"}):
        h = r.start("veloxa.agent_turn.v1", args, key())
        with pytest.raises(Exception, match="no such request"):
            r.result(h.workflow_id)
        assert r.state(h.workflow_id).status == "ERROR"
    assert admin_rows(db, "SELECT count(*) FROM runtime.agent_execution WHERE session_id = %s", (sid,))[0][0] == 0


def test_workflow_and_step_names_are_pinned():
    """§17.4: renaming a workflow or step breaks replay of in-flight runs, so the names are fixed here."""
    from veloxa.workflows import catalogue

    assert sorted(catalogue.CATALOGUE) == ["veloxa.agent_turn.v1", "veloxa.pack_pipeline.v1"]
    src = (HERE.parent / "src" / "veloxa" / "workflows" / "catalogue.py").read_text(encoding="utf-8")
    assert sorted(re.findall(r'name="(veloxa\.[a-z0-9_.]+)"', src)) == sorted([
        "veloxa.pack_pipeline.admit", "veloxa.pack_pipeline.project", "veloxa.pack_pipeline.identity",
        "veloxa.workflow.audit", "veloxa.pack_pipeline.v1", "veloxa.agent_turn.turn", "veloxa.agent_turn.v1"])
