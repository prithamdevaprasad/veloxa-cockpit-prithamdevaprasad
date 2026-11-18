"""Company-intelligence API (migration 0021): the ranked worry list, the 14 agent people, the Conflict Registry.

Every endpoint resolves the principal and the read basis per request (no cached permission decision), honours
`as_known_at` (knowledge as it stood then: only sealed batches released by that time), and rechecks evidence access:
an item or conflict citing a record the principal may not read is hidden and counted, never partially shown.
"""

from __future__ import annotations

from fastapi import Header
from fastapi.responses import JSONResponse

from . import ledger, tools
from .ask import timeparse
from .db import connect, tenant_tx
from .temporal import resolve_basis


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
