"""Company-intelligence API (migration 0021): the ranked worry list, the 14 agent people, the Conflict Registry.

Every endpoint resolves the principal and the read basis per request (no cached permission decision), honours
`as_known_at` (knowledge as it stood then: only sealed batches released by that time), and rechecks evidence access:
an item or conflict citing a record the principal may not read is hidden and counted, never partially shown.
"""

from __future__ import annotations

import uuid

from fastapi import Header, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from . import explain, ledger, schema, tools
from .ask import timeparse
from .db import connect, tenant_tx
from .temporal import resolve_basis


class FindingEvent(BaseModel):
    """A human lifecycle event on a finding (the actor is always the authenticated principal)."""
    model_config = ConfigDict(extra="forbid")
    state: str = Field(pattern="^(investigating|resolved|dismissed|reopened)$")
    note: str | None = Field(default=None, max_length=2000)
    ref: str | None = Field(default=None, max_length=200)
    responsible: str | None = Field(default=None, max_length=200)


class FollowUpIn(BaseModel):
    """The next state of a follow-up action (a recommendation turned into work)."""
    model_config = ConfigDict(extra="forbid")
    item_id: str = Field(min_length=1, max_length=200)
    recommendation: str = Field(min_length=1, max_length=2000)
    status: str = Field(pattern="^(created|assigned|in_progress|completed|blocked)$")
    action_id: str | None = Field(default=None, max_length=64)
    finding_id: str | None = Field(default=None, pattern="^FD-[0-9a-f]{12}$")
    jira_key: str | None = Field(default=None, pattern="^[A-Z][A-Z0-9_]{0,19}-[0-9]{1,9}$")
    jira_assignee: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=2000)


def register(app, *, state, principal_of, now_fn, tz_of) -> None:
    def ctx(conn, authorization, as_known_at):
        p = principal_of(conn, authorization, now_fn())
        if isinstance(p, JSONResponse):
            return p, None, None
        tz = tz_of(conn, p.tenant_id)
        try:
            spec = timeparse.parse("", now_fn(), tz, None, None, as_known_at)
        except ValueError as e:
            return JSONResponse(status_code=422, content={"error": "invalid_time", "detail": str(e)}), None, None
        basis = resolve_basis(p.tenant_id, ledger.sealed_releases(conn, p.tenant_id), spec.mode, spec.as_of, tz,
                              now_fn())
        return p, basis, tz

    def readable(conn, principal, basis, ids: list[str]) -> set[str]:
        ev = tools._evidence(conn, basis, sorted(set(ids)))
        acc = principal.access
        return {e.record_id for e in ev if acc is None or acc.mode != "rebac" or acc.allows(e.system, e.data_class)}

    def basis_json(basis):
        return {"batch_seq": basis.batch_seq, "banner": basis.banner(),
                "as_of": str(basis.as_of) if getattr(basis, "as_of", None) else None}

    @app.get("/v1/worry")
    def worry(as_known_at: str | None = None, authorization: str | None = Header(default=None)):
        """'What should I worry about?': the D-15 v1 weighted severity ranking (hackathon scope), with every
        criterion's score, weight and reason; unrankable items listed separately with their reasons."""
        from .risk import governance
        from .risk import worry as w
        from .signals.registry import assessment_date

        with connect(state["dsn"]) as conn:
            p, basis, tz = ctx(conn, authorization, as_known_at)
            if basis is None:
                return p
            schema.require(conn, "company_intelligence")   # authenticated: 503, not 500
            policy = governance.current_policy(conn, p.tenant_id)
            with tenant_tx(conn, p.tenant_id, p.principal_id, basis.batch_seq):
                items = w.build_items(conn, p.tenant_id, basis.batch_seq, assessment_date(basis, tz)) \
                    if basis.batch_seq else []
                content = None
                if policy.status == "approved":
                    content = conn.execute("SELECT content FROM policy.ranking_decision WHERE tenant_id = %s"
                                           " AND version = %s", (p.tenant_id, policy.version)).fetchone()["content"]
            ok = readable(conn, p, basis, [r for i in items for r in i.evidence])
            shown = [i for i in items if set(i.evidence) <= ok]
            hidden = len(items) - len(shown)
            if content is None:
                return {"ranking_mode": "pending", "policy": policy.as_json(), "read_basis": basis_json(basis),
                        "ranked": [], "unranked": [{"item_id": i.item_id, "title": i.title, "evidence": i.evidence}
                                                   for i in shown],
                        "hidden_for_access": hidden,
                        "note": "D-15 is not recorded for this tenant, so nothing is ranked."}
            out = governance.weighted_ranking(policy, shown, content)
            return {"ranking_mode": "weighted", "policy": policy.as_json(), "read_basis": basis_json(basis),
                    **out, "hidden_for_access": hidden, "engine": governance.ENGINE_VERSION}

    @app.get("/v1/directory")
    def directory(as_known_at: str | None = None, authorization: str | None = Header(default=None)):
        """The real HR directory: every person in the HR export at the basis, work facts only (name, title,
        department, recorded status, join date, manager), each row only if the reader may read its record. Whether
        an AI assistant exists is shown, but the person and the AI assistant are never the same thing."""
        from .agents import people

        with connect(state["dsn"]) as conn:
            p, basis, _ = ctx(conn, authorization, as_known_at)
            if basis is None:
                return p
            if not basis.batch_seq:
                return {"read_basis": basis_json(basis), "people": [], "count": 0}
            with tenant_tx(conn, p.tenant_id, p.principal_id, basis.batch_seq):
                staff = [dict(r) for r in people._people_at(conn, p.tenant_id, basis.batch_seq)]
                mapped = {r["person_record"]: r["agent_id"] for r in conn.execute(
                    "SELECT DISTINCT ON (person_record) person_record, agent_id FROM runtime.agent_person"
                    " ORDER BY person_record, version DESC").fetchall()} if schema.available(
                    conn, "company_intelligence") else {}
            ok = readable(conn, p, basis, [s["source_record_id"] for s in staff])
            names = {s["emp_id"]: s["full_name"] for s in staff}
            rows = [{"name": s["full_name"], "title": s["title"], "department": s["department"],
                     "status": s["status"], "join_date": str(s["join_date"]) if s.get("join_date") else None,
                     "manager": names.get(s.get("manager_emp_id")), "record": s["source_record_id"],
                     "has_ai_representation": s["source_record_id"] in mapped,
                     "assistant_id": mapped.get(s["source_record_id"])}
                    for s in staff if s["source_record_id"] in ok]
            return {"read_basis": basis_json(basis), "people": rows, "count": len(rows),
                    "withheld": len(staff) - len(rows)}

    @app.get("/v1/map")
    def business_map(as_known_at: str | None = None, authorization: str | None = Header(default=None)):
        """The business risk and dependency map: every visible source pooled into one graph of stated links."""
        from .agents import people
        from .risk import business_map as bm
        from .risk import worry as w
        from .signals.registry import assessment_date

        with connect(state["dsn"]) as conn:
            p, basis, tz = ctx(conn, authorization, as_known_at)
            if basis is None:
                return p
            if not basis.batch_seq:
                return {"nodes": [], "edges": [], "hidden_for_access": 0, "notes": ["Nothing is admitted yet."],
                        "read_basis": basis_json(basis)}
            with tenant_tx(conn, p.tenant_id, p.principal_id, basis.batch_seq):
                staff = [dict(r) for r in people._people_at(conn, p.tenant_id, basis.batch_seq)]
                items = w.build_items(conn, p.tenant_id, basis.batch_seq, assessment_date(basis, tz)) \
                    if schema.available(conn, "company_intelligence") else []
            ok_people = readable(conn, p, basis, [s["source_record_id"] for s in staff])
            staff = [s for s in staff if s["source_record_id"] in ok_people]
            return bm.build(conn, basis, tz, p, lambda ids: readable(conn, p, basis, ids), staff, items)

    @app.get("/v1/people")
    def people_list(as_known_at: str | None = None, authorization: str | None = Header(default=None)):
        """The 14 agent people as profiles (grounded work facts, persona assumptions labelled, unknowns)."""
        from .agents import people

        with connect(state["dsn"]) as conn:
            p, basis, _ = ctx(conn, authorization, as_known_at)
            if basis is None:
                return p
            schema.require(conn, "company_intelligence")   # authenticated: 503, not 500
            with tenant_tx(conn, p.tenant_id, p.principal_id, basis.batch_seq):
                ids = [r["agent_id"] for r in conn.execute(
                    "SELECT DISTINCT agent_id FROM runtime.agent_person ORDER BY agent_id").fetchall()]
                profiles = [people.profile(conn, p.tenant_id, basis.batch_seq, a) for a in ids]
            return {"read_basis": basis_json(basis), "people": profiles, "count": len(profiles)}

    @app.get("/v1/people/{agent_id}")
    def people_one(agent_id: str, as_known_at: str | None = None, authorization: str | None = Header(default=None)):
        from .agents import people

        with connect(state["dsn"]) as conn:
            p, basis, _ = ctx(conn, authorization, as_known_at)
            if basis is None:
                return p
            schema.require(conn, "company_intelligence")   # authenticated: 503, not 500
            with tenant_tx(conn, p.tenant_id, p.principal_id, basis.batch_seq):
                prof = people.profile(conn, p.tenant_id, basis.batch_seq, agent_id)
            return {"read_basis": basis_json(basis), **prof}

    @app.get("/v1/knowledge/conflicts")
    def conflict_registry(as_known_at: str | None = None, authorization: str | None = Header(default=None)):
        """The Conflict Registry at the basis: competing claims with their records and locators, the precedence
        rule applied (if any), the rationale, the remaining uncertainty and any human decision."""
        from . import knowledge

        with connect(state["dsn"]) as conn:
            p, basis, _ = ctx(conn, authorization, as_known_at)
            if basis is None:
                return p
            schema.require(conn, "company_intelligence")   # authenticated: 503, not 500
            with tenant_tx(conn, p.tenant_id, p.principal_id, basis.batch_seq):
                found = knowledge.conflicts_at(conn, p.tenant_id, basis.batch_seq) if basis.batch_seq else []
            ok = readable(conn, p, basis, [c["record_id"] for f in found for c in f["claims"]])
            shown = [f for f in found if {c["record_id"] for c in f["claims"]} <= ok]
            for f in shown:
                for c in f["claims"]:
                    for k in ("created_at", "available_at"):
                        if c.get(k) is not None:
                            c[k] = c[k].isoformat()
                f["created_at"] = f["created_at"].isoformat()
                if f["decision"]:
                    f["decision"]["at"] = f["decision"]["at"].isoformat()
                    f["decision"].pop("tenant_id", None)
                    f["decision"]["decision_id"] = str(f["decision"]["decision_id"])
                f.pop("tenant_id", None)
                for c in f["claims"]:
                    c.pop("tenant_id", None)
            return {"read_basis": basis_json(basis), "conflicts": shown, "hidden_for_access": len(found) - len(shown)}

    @app.get("/v1/findings")
    def findings_list(authorization: str | None = Header(default=None)):
        """Findings the principal may see (database policy) AND whose every cited record they may read (rechecked
        now, never cached), with lifecycle history and notifications."""
        with connect(state["dsn"]) as conn:
            p, basis, _ = ctx(conn, authorization, None)
            if basis is None:
                return p
            schema.require(conn, "company_intelligence")   # authenticated: 503, not 500
            schema.require(conn, "finding_workflow")
            with tenant_tx(conn, p.tenant_id, p.principal_id, basis.batch_seq):
                rows = [dict(r) for r in conn.execute(
                    "SELECT finding_id, split_part(dedup_key, '|', 1) AS item_id, title, description, department,"
                    " project, trigger, evidence, ranking, confidence, unknowns, batch_seq, created_at, state,"
                    " updated_at FROM runtime.finding_current"
                    " ORDER BY created_at DESC").fetchall()]
                events = [dict(r) for r in conn.execute(
                    "SELECT finding_id, state, actor, note, ref, responsible, at FROM runtime.finding_event"
                    " ORDER BY at").fetchall()]
                notes = [dict(r) for r in conn.execute(
                    "SELECT notification_id, finding_id, recipient, basis, channel, delivered_at"
                    " FROM runtime.notification").fetchall()]
                acks = [dict(r) for r in conn.execute(
                    "SELECT notification_id, actor, at FROM runtime.notification_ack").fetchall()]
            ok = readable(conn, p, basis, [r for f in rows for r in f["evidence"]])
            shown = [f for f in rows if set(f["evidence"]) <= ok]
            for f in shown:
                f["history"] = [{**e, "at": e["at"].isoformat()} for e in events if e["finding_id"] == f["finding_id"]]
                f["notified"] = [{**n, "notification_id": str(n["notification_id"]),
                                  "created_at": n["delivered_at"].isoformat(), "delivered_at": None,
                                  "delivery": "in-app record only: no e-mail or chat channel is configured",
                                  "acknowledged": [{"actor": a["actor"], "at": a["at"].isoformat()} for a in acks
                                                   if a["notification_id"] == n["notification_id"]]}
                                 for n in notes if n["finding_id"] == f["finding_id"]]
                f["created_at"], f["updated_at"] = f["created_at"].isoformat(), f["updated_at"].isoformat()
            return {"findings": shown, "hidden_for_access": len(rows) - len(shown)}

    @app.post("/v1/findings/{finding_id}/events")
    def findings_event(finding_id: str, body: FindingEvent, authorization: str | None = Header(default=None)):
        from . import findings

        with connect(state["dsn"]) as conn:
            p, basis, _ = ctx(conn, authorization, None)
            if basis is None:
                return p
            schema.require(conn, "company_intelligence")   # authenticated: 503, not 500
            with tenant_tx(conn, p.tenant_id, p.principal_id, basis.batch_seq):
                if not conn.execute("SELECT 1 FROM runtime.finding WHERE finding_id = %s", (finding_id,)).fetchone():
                    return JSONResponse(status_code=404, content={"error": "not_found"})   # or not visible to you
                try:
                    findings.record_human_event(conn, p.tenant_id, finding_id, body.state, p.principal_id, body.note,
                                                body.ref, body.responsible)
                except findings.WorkflowError as e:
                    return JSONResponse(status_code=422, content={"error": "invalid_transition", "detail": str(e)})
            return {"finding_id": finding_id, "state": body.state}

    @app.post("/v1/notifications/{notification_id}/ack")
    def notification_ack(notification_id: uuid.UUID, authorization: str | None = Header(default=None)):
        """The signed-in person has seen this alert. It does not change the finding's state."""
        from . import findings

        with connect(state["dsn"]) as conn:
            p, basis, _ = ctx(conn, authorization, None)
            if basis is None:
                return p
            schema.require(conn, "finding_workflow")
            with tenant_tx(conn, p.tenant_id, p.principal_id, basis.batch_seq):
                try:
                    return findings.acknowledge(conn, p.tenant_id, str(notification_id), p.principal_id)
                except LookupError:
                    return JSONResponse(status_code=404, content={"error": "not_found"})

    @app.get("/v1/follow-ups")
    def follow_ups_list(authorization: str | None = Header(default=None)):
        from . import findings

        with connect(state["dsn"]) as conn:
            p, basis, _ = ctx(conn, authorization, None)
            if basis is None:
                return p
            schema.require(conn, "finding_workflow")
            with tenant_tx(conn, p.tenant_id, p.principal_id, basis.batch_seq):
                return {"follow_ups": findings.follow_ups(conn, p.tenant_id)}

    @app.post("/v1/follow-ups")
    def follow_up_record(body: FollowUpIn, authorization: str | None = Header(default=None)):
        from . import findings

        with connect(state["dsn"]) as conn:
            p, basis, _ = ctx(conn, authorization, None)
            if basis is None:
                return p
            schema.require(conn, "finding_workflow")
            with tenant_tx(conn, p.tenant_id, p.principal_id, basis.batch_seq):
                if body.finding_id and not conn.execute("SELECT 1 FROM runtime.finding WHERE finding_id = %s",
                                                        (body.finding_id,)).fetchone():
                    return JSONResponse(status_code=404, content={"error": "not_found"})
                try:
                    aid = findings.record_follow_up(conn, p.tenant_id, p.principal_id, body.item_id,
                                                    body.recommendation, body.status, body.action_id,
                                                    body.finding_id, body.jira_key, body.jira_assignee, body.note)
                except findings.WorkflowError as e:
                    return JSONResponse(status_code=422, content={"error": "invalid_transition", "detail": str(e)})
            return {"action_id": aid, "status": body.status}

    @app.get("/v1/admin/connectors")
    def connectors_status(authorization: str | None = Header(default=None)):
        """Connector and model-experiment diagnostics. Never a credential value; 'configured' is not 'connected'
        (a live connection test must pass), and 'synchronised' is not 'admitted' or 'queryable'."""
        import os as _os

        from .llm import config as llm_config

        with connect(state["dsn"]) as conn:
            p, basis, _ = ctx(conn, authorization, None)
            if basis is None:
                return p
            schema.require(conn, "company_intelligence")   # authenticated: 503, not 500
            with tenant_tx(conn, p.tenant_id, p.principal_id, basis.batch_seq):
                runs = [dict(r) for r in conn.execute(
                    "SELECT connector, mode, status, fetched, changed, submitted, error, started_at, finished_at"
                    " FROM runtime.connector_run ORDER BY started_at DESC LIMIT 20").fetchall()]
                t = conn.execute("SELECT slug, synthetic FROM directory.tenant WHERE tenant_id = %s",
                                 (p.tenant_id,)).fetchone()
        for r in runs:
            r["started_at"] = r["started_at"].isoformat()
            r["finished_at"] = r["finished_at"].isoformat() if r["finished_at"] else None
        planned = [{"connector": "github", "state": "available: read-only GitHub App (see Integrations: GitHub)"},
                   {"connector": "gmail", "state": "available: read-only mailbox (see Integrations: Gmail)"}] + [
            {"connector": n, "state": "planned, not configured"} for n in ("gitlab", "hrms", "finance", "crm",
                                                                          "incidents")]
        from .llm.gemini import TenantNotPermitted, check_tenant

        status = llm_config.status()
        try:
            check_tenant(t["slug"], bool(t["synthetic"]))
            in_scope, scope_reason = True, "this synthetic workspace is on the allowlist"
        except TenantNotPermitted as e:
            in_scope, scope_reason = False, str(e)
        active = status.get("model") == "configured" and status.get("provider") == "gemini" and in_scope
        ai = {"active": active, "provider": status.get("provider"),
              "model": _os.environ.get("VELOXA_MODEL_NAME") if status.get("model") == "configured" else None,
              "workspace": t["slug"], "workspace_synthetic": bool(t["synthetic"]), "workspace_in_scope": in_scope,
              "scope_reason": scope_reason, "configuration": status,
              "what_it_does": "re-words verified explanations only; never answers, ranks or computes"}
        j = state.get("jira_get", lambda: None)()
        if j is None:
            jira_row = {"connector": "jira", "state": "not configured: set ATLASSIAN_CLIENT_ID and "
                        "ATLASSIAN_CLIENT_SECRET "
                        "on the server, then connect in Administration > Integrations"}
        else:
            st = j.status(str(p.tenant_id))
            site = (st.get("site") or {}).get("name")
            jira_row = {"connector": "jira", "state": (
                "access expired: reconnect in Integrations" if st.get("expired") else
                f"connected over Atlassian OAuth{f' to {site}' if site else ''}; sync runs from Integrations"
                if st.get("connected") else "not connected: connect in Administration > Integrations")}
        return {"connectors": [jira_row] + planned, "runs": runs, "ai_wording": ai,
                "model": status,
                "gemini_experiment": {"key_configured": bool(_os.environ.get("GEMINI_API_KEY")),
                                      "synthetic_tenants": [x for x in _os.environ.get("VELOXA_GEMINI_TENANTS",
                                                                                       "").split(",") if x],
                                      "scope": "synthetic Lumora data only; the supplied company is refused"}}

    @app.get("/v1/worry/{item_id:path}/explain")
    def worry_explain(item_id: str, model: bool = True, authorization: str | None = Header(default=None)):
        """One worry (ranked or unrankable) explained in plain English: what is recorded, how it was ranked, what is
        not known. Built from the same response as /v1/worry, so access rules and hidden items are identical."""
        data = worry(None, authorization)
        if isinstance(data, JSONResponse):
            return data
        ranked = [x for x in data.get("ranked", []) if x["item_id"] == item_id]
        other = [x for x in data.get("unrankable", data.get("unranked", [])) if x["item_id"] == item_id]
        if not ranked and not other:
            return JSONResponse(status_code=404, content={
                "error": "not_found",
                "detail": "no such worry at this knowledge point, or you may not read all of its evidence"})
        ex = explain.worry(ranked[0] if ranked else other[0], ranked=bool(ranked),
                           total_ranked=len(data.get("ranked", [])))
        return {"item_id": item_id, "explanation": _maybe_model(authorization, ex, model)}

    @app.get("/v1/knowledge/conflicts/{conflict_id}/explain")
    def conflict_explain(conflict_id: str, model: bool = True, authorization: str | None = Header(default=None)):
        """One source disagreement explained: what each source says, how it is treated, what remains uncertain."""
        data = conflict_registry(None, authorization)
        if isinstance(data, JSONResponse):
            return data
        found = [c for c in data["conflicts"] if c["conflict_id"] == conflict_id]
        if not found:
            return JSONResponse(status_code=404, content={"error": "not_found"})
        return {"conflict_id": conflict_id,
                "explanation": _maybe_model(authorization, explain.conflict(found[0]), model)}

    def _maybe_model(authorization, ex: dict, model: bool) -> dict:
        if not model:
            return ex
        with connect(state["dsn"]) as conn:
            p, basis, _ = ctx(conn, authorization, None)
            if basis is None:
                return ex
            ids = sorted({r for _, it in explain.items_of(ex) for r in it["evidence"]})
            ev = [{"record_id": x.record_id, "data_class": x.data_class} for x in tools._evidence(conn, basis, ids)]
            return explain.with_model(conn, p.tenant_id, ex, ev)

    @app.get("/v1/workforce")
    def workforce(as_known_at: str | None = None, authorization: str | None = Header(default=None)):
        """Workforce distribution from the governed headcount metric (headcount@1, the HR export as at extraction):
        aggregate counts by department and by recorded status only. No individual record, no pay, no leave detail.
        The same evidence access gate as the Overview KPI applies; a principal without access gets `denied`."""
        from . import metrics
        from .authz import evidence as ev_authz

        with connect(state["dsn"]) as conn:
            p, basis, _ = ctx(conn, authorization, as_known_at)
            if basis is None:
                return p
            if not basis.batch_seq:
                return {"read_basis": basis_json(basis), "state": "no_data"}
            hc = tools.headcount(conn, basis, p)
            if p.access is not None:
                hc, _ = ev_authz.gate(hc, p.access)
            if hc.status != "ok" or not hc.facts:
                return {"read_basis": basis_json(basis), "state": "denied" if hc.status == "ok" else hc.status}
            d = metrics.load_definitions()["headcount"]
            by = {}
            for dim in ("department", "status"):
                res = metrics.run(conn, d, basis, [dim])
                by[dim] = sorted(({"label": r.dims[dim], "count": r.value} for r in res.rows),
                                 key=lambda x: (-x["count"], x["label"]))
            return {"read_basis": basis_json(basis), "state": "ok", "total": hc.facts[0].value,
                    "by_department": by["department"], "by_status": by["status"],
                    "basis": "governed metric headcount@1 (HR export, as at extraction; contractors not included)",
                    "evidence_records": len({r for f in hc.facts for r in f.inputs})}

    @app.post("/v1/answers/{answer_id}/explain")
    def explain_answer(answer_id: str, model: bool = True, authorization: str | None = Header(default=None)):
        """Plain-English wording of one of YOUR recorded answers (read from the server, never from the client)."""
        import uuid as _uuid

        with connect(state["dsn"]) as conn:
            p, basis, _ = ctx(conn, authorization, None)
            if basis is None:
                return p
            try:
                aid = _uuid.UUID(answer_id)
            except ValueError:
                return JSONResponse(status_code=404, content={"error": "not_found"})
            with tenant_tx(conn, p.tenant_id, p.principal_id):
                row = conn.execute("SELECT answer FROM runtime.answer WHERE answer_id = %s AND principal_id = %s",
                                   (aid, p.principal_id)).fetchone()
            if row is None:
                return JSONResponse(status_code=404, content={"error": "not_found"})
            a = row["answer"]
            ex = explain.answer(a)
            if model and ex["status"] != "refused":
                ex = explain.with_model(conn, p.tenant_id, ex, [{"record_id": x.get("record_id"),
                                                                 "data_class": x.get("data_class")}
                                                                for x in a.get("evidence", [])])
            return {"answer_id": answer_id, "explanation": ex, "wording": ex["wording"]}   # one model call

    @app.get("/v1/brief")
    def executive_brief(explain_: bool = Query(True, alias="explain"),
                        authorization: str | None = Header(default=None)):
        """A deterministic executive brief: each line is a verified figure or registry fact with its records. The
        optional `explain=true` adds an experimental model wording (synthetic tenants only); the lines never change."""
        from . import knowledge, metrics
        from .authz import evidence as ev_authz
        from .risk import worry as w
        from .signals.registry import assessment_date

        with connect(state["dsn"]) as conn:
            p, basis, tz = ctx(conn, authorization, None)
            if basis is None:
                return p
            schema.require(conn, "company_intelligence")   # authenticated: 503, not 500
            claims, records = [], set()
            if basis.batch_seq:
                hc = tools.headcount(conn, basis, p)
                if p.access is not None:
                    hc, _ = ev_authz.gate(hc, p.access)
                if hc.status == "ok" and hc.facts:
                    res = metrics.run(conn, metrics.load_definitions()["headcount"], basis, ["department"])
                    top = sorted(res.rows, key=lambda r: -r.value)[:2]
                    ids = sorted({i for f in hc.facts for i in f.inputs})
                    largest = ", ".join(f"{r.dims['department']} {r.value}" for r in top)
                    claims.append({"text": f"Headcount in the HR export: {hc.facts[0].value} across {len(res.rows)} "
                                   "departments" + (f"; largest: {largest}" if top else ""), "evidence": ids[:3]})
                    records |= set(ids[:3])
                with tenant_tx(conn, p.tenant_id, p.principal_id, basis.batch_seq):
                    items = w.build_items(conn, p.tenant_id, basis.batch_seq, assessment_date(basis, tz))
                    conflicts = knowledge.conflicts_at(conn, p.tenant_id, basis.batch_seq)
                    open_f = conn.execute("SELECT count(*) AS n FROM runtime.finding_current WHERE state NOT IN"
                                          " ('resolved', 'dismissed')").fetchone()["n"]
                ok = readable(conn, p, basis, [r for i in items for r in i.evidence])
                items = [i for i in items if set(i.evidence) <= ok]
                for i in items[:3]:
                    claims.append({"text": i.title, "evidence": i.evidence[:3]})
                    records |= set(i.evidence[:3])
                if conflicts:
                    claims.append({"text": f"Open source disagreements: {len(conflicts)}", "evidence": []})
                claims.append({"text": f"Findings not yet resolved: {open_f}", "evidence": []})
            ev = [{"record_id": e.record_id, "system": e.system, "data_class": e.data_class}
                  for e in tools._evidence(conn, basis, sorted(records))]
            out = {"read_basis": basis_json(basis), "lines": claims,
                   "method": "deterministic: governed metrics, the D-15 item builder, the Conflict Registry, findings"}
            ex = explain.brief(claims)
            if explain_:
                ex = explain.with_model(conn, p.tenant_id, ex, ev)                 # one model call
                out["wording"] = ex["wording"]
            out["explanation"] = ex
            return out
