"""Company-intelligence API (migration 0021): the ranked worry list, the 14 agent people, the Conflict Registry.

Every endpoint resolves the principal and the read basis per request (no cached permission decision), honours
`as_known_at` (knowledge as it stood then: only sealed batches released by that time), and rechecks evidence access:
an item or conflict citing a record the principal may not read is hidden and counted, never partially shown.
"""

from __future__ import annotations

from fastapi import Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from . import ledger, tools
from .ask import timeparse
from .db import connect, tenant_tx
from .temporal import resolve_basis


class FindingEvent(BaseModel):
    """A human lifecycle event on a finding (the actor is always the authenticated principal)."""
    model_config = ConfigDict(extra="forbid")
    state: str = Field(pattern="^(acknowledged|investigating|resolved|dismissed)$")
    note: str | None = Field(default=None, max_length=2000)
    ref: str | None = Field(default=None, max_length=200)


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

    @app.get("/v1/people")
    def people_list(as_known_at: str | None = None, authorization: str | None = Header(default=None)):
        """The 14 agent people as profiles (grounded work facts, persona assumptions labelled, unknowns)."""
        from .agents import people

        with connect(state["dsn"]) as conn:
            p, basis, _ = ctx(conn, authorization, as_known_at)
            if basis is None:
                return p
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
            with tenant_tx(conn, p.tenant_id, p.principal_id, basis.batch_seq):
                rows = [dict(r) for r in conn.execute(
                    "SELECT finding_id, title, description, department, project, trigger, evidence, ranking,"
                    " confidence, unknowns, batch_seq, created_at, state, updated_at FROM runtime.finding_current"
                    " ORDER BY created_at DESC").fetchall()]
                events = [dict(r) for r in conn.execute(
                    "SELECT finding_id, state, actor, note, ref, at FROM runtime.finding_event ORDER BY at").fetchall()]
                notes = [dict(r) for r in conn.execute(
                    "SELECT finding_id, recipient, basis, delivered_at FROM runtime.notification").fetchall()]
            ok = readable(conn, p, basis, [r for f in rows for r in f["evidence"]])
            shown = [f for f in rows if set(f["evidence"]) <= ok]
            for f in shown:
                f["history"] = [{**e, "at": e["at"].isoformat()} for e in events if e["finding_id"] == f["finding_id"]]
                f["notified"] = [{**n, "delivered_at": n["delivered_at"].isoformat()} for n in notes
                                 if n["finding_id"] == f["finding_id"]]
                f["created_at"], f["updated_at"] = f["created_at"].isoformat(), f["updated_at"].isoformat()
            return {"findings": shown, "hidden_for_access": len(rows) - len(shown)}

    @app.post("/v1/findings/{finding_id}/events")
    def findings_event(finding_id: str, body: FindingEvent, authorization: str | None = Header(default=None)):
        from . import findings

        with connect(state["dsn"]) as conn:
            p, basis, _ = ctx(conn, authorization, None)
            if basis is None:
                return p
            with tenant_tx(conn, p.tenant_id, p.principal_id, basis.batch_seq):
                if not conn.execute("SELECT 1 FROM runtime.finding WHERE finding_id = %s", (finding_id,)).fetchone():
                    return JSONResponse(status_code=404, content={"error": "not_found"})   # or not visible to you
                findings.record_human_event(conn, p.tenant_id, finding_id, body.state, p.principal_id, body.note,
                                            body.ref)
            return {"finding_id": finding_id, "state": body.state}

    @app.get("/v1/admin/connectors")
    def connectors_status(authorization: str | None = Header(default=None)):
        """Connector and model-experiment diagnostics. Never a credential value; 'configured' is not 'connected'
        (a live connection test must pass), and 'synchronised' is not 'admitted' or 'queryable'."""
        import os as _os

        from . import connectors
        from .llm import config as llm_config

        with connect(state["dsn"]) as conn:
            p, basis, _ = ctx(conn, authorization, None)
            if basis is None:
                return p
            with tenant_tx(conn, p.tenant_id, p.principal_id, basis.batch_seq):
                runs = [dict(r) for r in conn.execute(
                    "SELECT connector, mode, status, fetched, changed, submitted, error, started_at, finished_at"
                    " FROM runtime.connector_run ORDER BY started_at DESC LIMIT 20").fetchall()]
        for r in runs:
            r["started_at"] = r["started_at"].isoformat()
            r["finished_at"] = r["finished_at"].isoformat() if r["finished_at"] else None
        planned = [{"connector": n, "state": "not implemented (interface defined: veloxa.connectors.Connector)"}
                   for n in ("github", "gitlab", "hrms", "finance", "crm", "incidents")]
        return {"connectors": [connectors.jira_status()] + planned, "runs": runs,
                "model": llm_config.status(),
                "gemini_experiment": {"key_configured": bool(_os.environ.get("GEMINI_API_KEY")),
                                      "synthetic_tenants": [x for x in _os.environ.get("VELOXA_GEMINI_TENANTS",
                                                                                       "").split(",") if x],
                                      "scope": "synthetic Lumora data only; the supplied company is refused"}}

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

    def _experimental_wording(conn, p, result: dict) -> dict:
        """Optional model wording (Gemini experiment): only through GovernedLLMClient, only for allowlisted synthetic
        tenants, only after the deterministic answer is complete; any failure returns the facts-only answer."""
        from .ask import wording
        from .llm import config as llm_config
        from .llm.client import GovernedLLMClient

        policy, provider = llm_config.policy_and_provider()
        if provider is None or getattr(provider, "name", "") != "gemini":
            return {"mode": "facts_only", "reason": "no experimental model is configured (DENY_ALL)"}
        with tenant_tx(conn, p.tenant_id):
            t = conn.execute("SELECT slug, synthetic FROM directory.tenant WHERE tenant_id = %s",
                             (p.tenant_id,)).fetchone()
        tenant = {"tenant_id": p.tenant_id, "slug": t["slug"], "synthetic": bool(t["synthetic"])}
        return wording.reword(conn, tenant, result, GovernedLLMClient(conn, policy, provider))["wording"]

    @app.post("/v1/answers/{answer_id}/explain")
    def explain_answer(answer_id: str, authorization: str | None = Header(default=None)):
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
            return {"answer_id": answer_id, "wording": _experimental_wording(conn, p, row["answer"])}

    @app.get("/v1/brief")
    def executive_brief(explain: bool = False, authorization: str | None = Header(default=None)):
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
            brief = {"status": "answered" if claims else "abstained", "claims": claims, "evidence": ev,
                     "interpretation": "Executive brief at the current knowledge point", "unknowns": [],
                     "qualifiers": [], "confidence": {"label": "Deterministic"}, "audit_id": f"brief-{basis.batch_seq}"}
            out = {"read_basis": basis_json(basis), "lines": claims,
                   "method": "deterministic: governed metrics, the D-15 item builder, the Conflict Registry, findings"}
            if explain:
                out["wording"] = _experimental_wording(conn, p, brief)
            return out
