"""C29 observability (§21.1): redaction, structured logs, correlation IDs, span-attribute allowlist, metrics and
error tracking. SYNTHETIC values only."""

import json
import logging
import uuid

import pytest

from veloxa import telemetry


def test_scrub_removes_emails_tokens_passwords_and_dsn_credentials():
    s = telemetry.scrub("user syn@example.test sent Bearer abc.def-123 with password=hunter2 to "
                        "postgresql://veloxa_app:local-secret@db:5432/veloxa token: xyz")
    assert "syn@example.test" not in s and "abc.def-123" not in s and "hunter2" not in s
    assert "local-secret" not in s and "xyz" not in s
    assert "[email]" in s and "Bearer [redacted]" in s and "postgresql://veloxa_app:[redacted]@" in s


def test_json_logs_carry_the_correlation_id_allowlisted_fields_and_error_types_only():
    f = telemetry.JsonFormatter()
    token = telemetry.REQUEST_ID.set("syn-req-00000001")
    try:
        rec = logging.LogRecord("veloxa", logging.INFO, __file__, 1, "asked by syn@example.test", None, None)
        rec.fields = {"route": "/v1/ask", "message": "secret question", "status": "ok"}
        body = json.loads(f.format(rec))
        try:
            raise ValueError("contains syn-private-detail")
        except ValueError:
            import sys

            err = logging.LogRecord("veloxa", logging.ERROR, __file__, 1, "boom", None, sys.exc_info())
        ebody = json.loads(f.format(err))
    finally:
        telemetry.REQUEST_ID.reset(token)
    assert body["request_id"] == "syn-req-00000001" and body["msg"] == "asked by [email]"
    assert body["route"] == "/v1/ask" and "message" not in body                # free-form fields are dropped
    assert ebody["error"] == "ValueError" and "syn-private-detail" not in json.dumps(ebody)


def test_span_attributes_outside_the_allowlist_are_never_recorded(monkeypatch):
    seen = {}

    class FakeTracer:
        def start_as_current_span(self, name, attributes=None):
            from contextlib import contextmanager

            @contextmanager
            def cm():
                seen[name] = attributes
                yield None
            return cm()

    monkeypatch.setattr(telemetry, "_TRACER", FakeTracer())
    with telemetry.span("agent.tool", tool="people.headcount", message="What is X's salary?", email="a@b.example.test",
                        count=3):
        pass
    assert seen["agent.tool"] == {"tool": "people.headcount", "count": 3}
    assert telemetry.METRICS.count("veloxa_span_seconds", name="agent.tool") >= 1


def test_metrics_have_declared_names_and_labels_only_and_render_as_prometheus():
    m = telemetry.Metrics()
    m.inc("veloxa_model_refusals_total", status="refused_budget", tenant_email="x@y.example.test")  # dropped
    m.observe("veloxa_model_call_seconds", 0.02, stage="agent", status="ok")
    with pytest.raises(ValueError):
        m.inc("veloxa_anything_i_like")
    text = m.render()
    assert 'veloxa_model_refusals_total{status="refused_budget"} 1' in text and "x@y.example.test" not in text
    assert 'veloxa_model_call_seconds_bucket{stage="agent",status="ok",le="0.025"} 1' in text
    assert 'veloxa_model_call_seconds_count{stage="agent",status="ok"} 1' in text


@pytest.mark.db
def test_the_api_propagates_correlation_ids_records_route_templates_and_hides_errors(db, monkeypatch, tmp_path):
    from conftest import DEV_TOKEN, api_client, dev_fixture, ensure_configured_d1

    ensure_configured_d1(db, tmp_path)
    with api_client(db, monkeypatch, **dev_fixture()) as c:
        r = c.get("/v1/meta/clock", headers={"X-Request-ID": "syn-corr-00000001"})
        assert r.headers["X-Request-ID"] == "syn-corr-00000001"
        bad = c.get("/v1/meta/clock", headers={"X-Request-ID": "x y<script>"})
        assert bad.headers["X-Request-ID"] != "x y<script>" and len(bad.headers["X-Request-ID"]) == 32
        sid = uuid.uuid4()
        c.post(f"/v1/sessions/{sid}/messages", json={"message": "hi", "idempotency_key": "syn-key-telemetry"},
               headers={"Authorization": f"Bearer {DEV_TOKEN}"})
        assert c.get("/metrics").status_code == 404                                 # off by default

        @c.app.get("/syn-boom")
        def boom():
            raise RuntimeError("syn-internal-detail")
        e = c.get("/syn-boom")
        assert e.status_code == 500 and e.json()["error"] == "internal_error" and "syn-internal" not in e.text
    text = telemetry.METRICS.render()
    assert 'route="/v1/sessions/{session_id}/messages"' in text and str(sid) not in text
    assert telemetry.METRICS.value("veloxa_errors_total", route="/syn-boom") >= 1
    monkeypatch.setenv("VELOXA_METRICS", "1")
    with api_client(db, monkeypatch, **dev_fixture()) as c2:
        monkeypatch.setenv("VELOXA_METRICS", "1")
        m = c2.get("/metrics")
        assert m.status_code == 200 and "veloxa_http_requests_total" in m.text and DEV_TOKEN not in m.text


@pytest.mark.db
def test_agent_tool_denials_and_exclusions_are_counted(db, app_conn, tmp_path):
    from agent_fixtures import SyntheticTestKnowledgeBoundary, syn_agent
    from conftest import ensure_configured_d1

    from veloxa import ledger
    from veloxa import tools as vt
    from veloxa.agents import registry
    from veloxa.agents.runtime import AgentRequest, AgentRuntime, ToolCall
    from veloxa.db import connect
    from veloxa.releases import current_time
    from veloxa.temporal import Mode, resolve_basis

    tenant = ensure_configured_d1(db, tmp_path)
    cfg = syn_agent("finance")
    with connect(db.dsn("projection")) as proj:
        registry.publish(proj, tenant, cfg)
    beta = vt.Principal("person:T002", "authenticated", "beta@example.test")
    basis = resolve_basis(tenant, ledger.sealed_releases(app_conn, tenant), Mode.CURRENT, None, "Asia/Kolkata",
                          current_time())
    rt = AgentRuntime(app_conn, "Asia/Kolkata", kb=SyntheticTestKnowledgeBoundary())
    sid = rt.open_session(tenant, beta, cfg.agent_id, basis)["session_id"]
    before = telemetry.METRICS.value("veloxa_agent_tool_denied_total", reason="undeclared_tool")
    rt.execute(AgentRequest(tenant, beta, sid, "Look up", "syn-key-tel-0001", (ToolCall("shell.exec"),)))
    assert telemetry.METRICS.value("veloxa_agent_tool_denied_total", reason="undeclared_tool") == before + 1
