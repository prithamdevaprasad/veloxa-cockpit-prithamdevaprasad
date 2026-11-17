"""C32 historical ACL evaluator (ARCHITECTURE.md §10.7, historical ACL suite ACL-H1 to ACL-H6, §21.2).

SYNTHETIC ACL, identity and membership history, admitted through the real admission pipeline (a test-only
catalogue rule; the production catalogue has no ACL source) and projected like any other evidence. Every person,
group, document and instant is invented. Timeline (UTC, 2026):

  person:A -> user:a (from 1 Jan)    person:B -> user:b (from 1 Jan)    person:C: no identity link
  person:D and person:E both -> user:shared (ambiguous)
  user:a in group:eng 1 Mar - 1 Jun; group:eng in group:staff (nested); group:x <-> group:y (cycle)
  doc:plan     grant group:staff (from 1 Feb); explicit deny user:a 1 Jul - 1 Aug
  doc:direct   grant user:a (author) 1 Feb - 1 Apr   [D2 CORRECTION: the revocation was 1 May, not 1 Apr]
  doc:late     grant user:a from 1 Sep               doc:future   created 1 Oct
  doc:observed first observed 15 Sep, no recorded creation
  doc:nohist   no ACL state                         doc:snapshot snapshot-only ACL, extracted 1 Oct
  doc:recorded grant user:a recorded 5 Sep          doc:sameinst grant group:eng from 1 Jul (after A left eng)
  doc:public   domain-wide grant only               doc:deep     grant at the end of person:G's 10-group chain
  doc:d2grant  grant user:b admitted only in D2 (K=2)
"""

import json
import re
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import psycopg
import pytest

from veloxa import ledger, projector
from veloxa.acl import historical as h
from veloxa.acl.historical import AclWindow, HistoricalAclEvaluator, effective_decision, knowledge_basis
from veloxa.catalogue import load_catalogue
from veloxa.db import connect, tenant_tx
from veloxa.temporal import Mode, resolve_basis

pytestmark = pytest.mark.db

D1_AT = datetime.fromisoformat("2026-11-16T08:00:00+05:30")
D2_AT = datetime.fromisoformat("2026-11-16T09:00:00+05:30")
REC = "2026-01-15T00:00:00+00:00"   # default stated recording time (before extraction)
SYNC = "2026-11-10T00:00:00+00:00"


def T(m, d, hh=0):
    return datetime(2026, m, d, hh, tzinfo=UTC)


def iso(m, d):
    return T(m, d).isoformat()


def link(person, principal, start="2026-01-01", end=None, **kw):
    return {"type": "identity_link", "person": person, "principal": principal,
            "valid_from": f"{start}T00:00:00+00:00", "valid_to": end and f"{end}T00:00:00+00:00",
            "recorded_at": REC, **kw}


def member(m, g, start="2026-01-01", end=None, **kw):
    return {"type": "membership", "member": m, "group": g, "valid_from": f"{start}T00:00:00+00:00",
            "valid_to": end and f"{end}T00:00:00+00:00", "recorded_at": REC, **kw}


def grant(res, principal, start, end=None, effect="allow", relation="acl", **kw):
    return {"type": "acl", "resource": res, "principal": principal, "permission": "read", "effect": effect,
            "relation": relation, "valid_from": f"{start}T00:00:00+00:00",
            "valid_to": end and f"{end}T00:00:00+00:00", "recorded_at": REC, **kw}


def resource(res, created="2026-02-01", **kw):
    return {"type": "resource", "resource": res, "created_at": created and f"{created}T00:00:00+00:00",
            "recorded_at": REC, **kw}


def state(res, history_from="2026-01-01", **kw):
    return {"type": "acl_state", "resource": res, "history_from": history_from and f"{history_from}T00:00:00+00:00",
            "acl_source_version": "src-v1", "acl_synced_at": SYNC, "recorded_at": REC, **kw}


DOCS = ["doc:plan", "doc:direct", "doc:late", "doc:recorded", "doc:sameinst", "doc:public", "doc:deep",
        "doc:d2grant"]
D1_ROWS = (
    [link("person:A", "user:a"), link("person:B", "user:b"), link("person:D", "user:shared"),
     link("person:E", "user:shared"),
     member("user:a", "group:eng", "2026-03-01", "2026-06-01"), member("group:eng", "group:staff"),
     member("user:b", "group:x"), member("group:x", "group:y"), member("group:y", "group:x")]
    + [link("person:G", "user:g")]
    + [member("user:g" if i == 0 else f"group:c{i - 1}", f"group:c{i}") for i in range(10)]
    + [resource(d) for d in DOCS] + [state(d) for d in DOCS]
    + [resource("doc:future", "2026-10-01"), state("doc:future"),
       resource("doc:observed", None, observed_first=iso(9, 15), observed_last=iso(11, 10)), state("doc:observed"),
       resource("doc:nohist"),
       resource("doc:snapshot"), state("doc:snapshot", None, snapshot_extracted_at=iso(10, 1)),
       grant("doc:plan", "group:staff", "2026-02-01"),
       grant("doc:plan", "user:a", "2026-07-01", "2026-08-01", effect="deny"),
       grant("doc:direct", "user:a", "2026-02-01", "2026-04-01", relation="author"),
       grant("doc:late", "user:a", "2026-09-01"),
       grant("doc:recorded", "user:a", "2026-03-01", recorded_at="2026-09-05T00:00:00+00:00"),
       grant("doc:sameinst", "group:eng", "2026-07-01"),
       grant("doc:public", "domain:corp", "2026-02-01"),
       grant("doc:deep", "group:c9", "2026-02-01"),
       grant("doc:snapshot", "user:a", "2026-02-01")])
CORRECTED = next(i for i, r in enumerate(D1_ROWS) if r["type"] == "acl" and r["resource"] == "doc:direct")
D2_EXTRA = [grant("doc:d2grant", "user:b", "2026-02-01")]


def _write(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def acl_rules():
    base = load_catalogue()
    return base + [replace(base[0], pattern="data_room/acl/*.jsonl", kind="acl_export", system="acl_history",
                           data_class="confidential", records="rows", row_class=None, contract=None)]


@pytest.fixture
def acl(db, ingest_conn, tenant, tmp_path):
    from synthetic_pack import make_d1

    from veloxa.ingest.pipeline import admit_pack
    from veloxa.releases import Pack, Schedule, current_time

    assert D1_ROWS[CORRECTED]["resource"] == "doc:direct"
    sched = Schedule("acl", "UTC", (Pack("D1", 1, "Day1_Mon_16Nov", D1_AT), Pack("D2", 2, "Day2_Tue_17Nov", D2_AT)))
    d1 = make_d1(tmp_path)
    _write(d1 / "data_room" / "acl" / "history.jsonl", D1_ROWS)
    corrected = list(D1_ROWS)
    corrected[CORRECTED] = {**corrected[CORRECTED], "valid_to": "2026-05-01T00:00:00+00:00"}
    d2 = tmp_path / "Day2_Tue_17Nov"
    _write(d2 / "data_room" / "acl" / "history.jsonl", corrected)       # the same file re-released: corrections
    _write(d2 / "data_room" / "acl" / "history_d2.jsonl", D2_EXTRA)      # admitted only at K=2
    for p in ("D1", "D2"):
        admit_pack(ingest_conn, tenant, sched, p, tmp_path, current_time(), rules=acl_rules())
    with connect(db.dsn("projection")) as prj:
        projector.build(prj, tenant)
    return tenant


def basis(conn, tenant, mode=Mode.VALID_AT, at=date(2026, 4, 15)):
    from veloxa.releases import current_time

    return resolve_basis(tenant, ledger.sealed_releases(conn, tenant), mode, at, "UTC", current_time())


def k1(conn, tenant, at):
    """K=1 with history read as valid at `at` (VALID_AT is answered from best knowledge at the current K, so the
    K=1 view is produced by restricting the history to K=1)."""
    b = basis(conn, tenant, Mode.VALID_AT, at)
    return replace(b, batch_seq=1)


def pit(conn, b, person, res):
    return HistoricalAclEvaluator(conn).evaluate(person, "read", res, b, "POINT_IN_TIME")


def win(conn, b, person, res, start, end, cutoff=None):
    return HistoricalAclEvaluator(conn).evaluate(person, "read", res, b, "ELIGIBILITY_DURING_WINDOW",
                                                 AclWindow(start, end), cutoff)


# ---------------------------------------------------------------------------------------------- point in time


def test_direct_and_group_derived_grants_are_allowed_with_provenance(app_conn, acl):
    b = basis(app_conn, acl, at=date(2026, 3, 15))
    d = pit(app_conn, b, "person:A", "doc:direct")
    assert (d.outcome, d.reason, d.grant_relations) == ("allowed", "grant_valid", ["author"])
    assert knowledge_basis(d) == "participant"
    g = pit(app_conn, b, "person:A", "doc:plan")      # via group:eng -> group:staff, both valid on 15 Mar
    assert g.outcome == "allowed" and g.grant_relations == ["acl", "group_member"] and knowledge_basis(g) == "possible"
    assert g.membership_records and g.acl_records and g.acl_source_version == "src-v1"
    assert g.historical_at == datetime(2026, 3, 15, 23, 59, 59, 999999, tzinfo=UTC)


def test_acl_h2_membership_revoked_before_the_instant_is_no_grant(app_conn, acl):
    r = pit(app_conn, basis(app_conn, acl, at=date(2026, 6, 15)), "person:A", "doc:plan")
    assert (r.outcome, r.reason) == ("denied", "no_grant")    # A left group:eng on 1 Jun


def test_access_granted_after_the_instant_is_not_used(app_conn, acl):
    r = pit(app_conn, basis(app_conn, acl, at=date(2026, 8, 15)), "person:A", "doc:late")
    assert (r.outcome, r.reason) == ("denied", "no_grant")


def test_explicit_deny_overrides_a_grant(app_conn, acl):
    from veloxa.acl.historical import History, Item, RecordVersionRef, decide

    ev = HistoricalAclEvaluator(app_conn)
    b = basis(app_conn, acl, at=date(2026, 7, 15))
    history = h.load_history(app_conn, b)
    # Give A a live membership on 15 Jul too, so a grant and a deny both apply at the same instant.
    extra = Item("membership", RecordVersionRef("synthetic:extra", 1), T(7, 1), None, T(1, 15),
                 {"member": "user:a", "group": "group:staff"})
    r = decide(History(history.items + [extra]), "person:A", "read", "doc:plan", b, h.historical_instant(app_conn, b),
               "POINT_IN_TIME", None, ev.clock())
    assert (r.outcome, r.reason) == ("denied", "explicit_deny")


def test_resource_existence_three_states(app_conn, acl):
    aug = basis(app_conn, acl, at=date(2026, 8, 15))
    assert (pit(app_conn, aug, "person:A", "doc:future").reason) == "resource_not_available"
    assert pit(app_conn, aug, "person:A", "doc:future").outcome == "denied"
    first_seen_later = pit(app_conn, aug, "person:A", "doc:observed")      # ACL-H3: never denied
    assert (first_seen_later.outcome, first_seen_later.reason) == ("unknown", "resource_availability_unknown")
    assert pit(app_conn, aug, "person:A", "doc:never-admitted").reason == "resource_availability_unknown"


def test_acl_h3_every_missing_or_unevaluable_input_is_unknown(app_conn, acl):
    aug = basis(app_conn, acl, at=date(2026, 8, 15))
    cases = {("person:A", "doc:nohist"): "no_acl_history",
             ("person:A", "doc:snapshot"): "snapshot_only_before_extraction",
             ("person:C", "doc:plan"): "identity_unresolved",           # no identity link
             ("person:D", "doc:plan"): "identity_unresolved",           # principal shared with person:E
             ("person:A", "doc:public"): "partial_history",             # domain-wide sharing is not resolvable
             ("person:G", "doc:deep"): "depth_limit"}
    for (person, res), reason in cases.items():
        r = pit(app_conn, aug, person, res)
        assert (r.outcome, r.reason) == ("unknown", reason), (person, res, r.reason)
        assert effective_decision(True, r) is False                     # unknown is treated as denied
    assert pit(app_conn, basis(app_conn, acl, at=date(2026, 10, 15)), "person:A", "doc:snapshot").outcome == "allowed"
    now = basis(app_conn, acl, Mode.CURRENT, None)        # D-10a is not set, so no staleness is tolerated
    stale = pit(app_conn, now, "person:A", "doc:plan")     # ACL last synced 10 Nov
    assert (stale.outcome, stale.reason) == ("unknown", "stale_beyond_limit")
    gone = pit(app_conn, now, "person:A", "doc:observed")  # latest observation 10 Nov
    assert (gone.outcome, gone.reason) == ("unknown", "resource_availability_unknown")
    tolerant = HistoricalAclEvaluator(app_conn, h.EvaluationConfig(acl_staleness=timedelta(days=30)))
    assert tolerant.evaluate("person:A", "read", "doc:plan", now, "POINT_IN_TIME").reason != "stale_beyond_limit"


def test_a_group_cycle_terminates(app_conn, acl):
    r = pit(app_conn, basis(app_conn, acl, at=date(2026, 8, 15)), "person:B", "doc:plan")
    assert (r.outcome, r.reason) == ("denied", "no_grant")


def test_ambiguous_membership_and_invalid_history_fail_closed(app_conn, acl):
    from veloxa.acl.historical import History, Item, RecordVersionRef, decide

    b = basis(app_conn, acl, at=date(2026, 3, 15))
    base = h.load_history(app_conn, b).items
    dup = Item("membership", RecordVersionRef("synthetic:dup", 1), T(2, 1), None, T(1, 15),
               {"member": "user:a", "group": "group:eng"})
    t_h = h.historical_instant(app_conn, b)
    r = decide(History(base + [dup]), "person:A", "read", "doc:plan", b, t_h, "POINT_IN_TIME", None, t_h)
    assert (r.outcome, r.reason) == ("unknown", "membership_ambiguous")
    bad = Item("invalid", RecordVersionRef("synthetic:bad", 1), None, None, T(1, 15),
               {"reason": "valid_to must be after valid_from", "row": {"resource": "doc:plan"}})
    r = decide(History(base + [bad]), "person:A", "read", "doc:plan", b, t_h, "POINT_IN_TIME", None, t_h)
    assert (r.outcome, r.reason) == ("unknown", "partial_history")


def test_the_projection_marks_malformed_rows_invalid_instead_of_dropping_them():
    from veloxa.acl.projection import InvalidItem, validate

    for bad in ({"type": "acl", "resource": "r", "principal": "a", "permission": "read", "effect": "allow",
                 "relation": "acl", "valid_from": "2026-01-01T00:00:00+00:00"},          # untyped principal
                {"type": "membership", "member": "user:a", "group": "group:g", "valid_from": "2026-01-01"},  # no tz
                {"type": "acl_state", "resource": "r", "acl_source_version": "v", "acl_synced_at": SYNC},
                {"type": "identity_link", "person": "a@example.test", "principal": "user:a",   # an address is not
                 "valid_from": "2026-01-01T00:00:00+00:00"},                                 # a Veloxa person
                {"type": "what"}):
        with pytest.raises(InvalidItem):
            validate(bad)


# ---------------------------------------------------------------------------------------------- window mode


def test_window_eligibility_returns_only_the_qualifying_sub_intervals(app_conn, acl):
    b = basis(app_conn, acl, at=date(2026, 8, 15))
    r = win(app_conn, b, "person:A", "doc:plan", T(1, 1), T(8, 1))
    assert r.outcome == "allowed" and r.eligible_intervals == [(T(3, 1), T(6, 1))]   # only while A was in eng


def test_acl_h2_window_never_combines_a_membership_and_a_grant_from_different_instants(app_conn, acl):
    b = basis(app_conn, acl, at=date(2026, 8, 15))
    r = win(app_conn, b, "person:A", "doc:sameinst", T(1, 1), T(8, 1))
    assert (r.outcome, r.reason, r.eligible_intervals) == ("denied", "no_grant", [])


def test_partial_window_with_no_qualifying_instant_is_unknown(app_conn, acl):
    b = basis(app_conn, acl, at=date(2026, 10, 15))
    r = win(app_conn, b, "person:A", "doc:observed", T(8, 1), T(10, 1))
    assert (r.outcome, r.reason) == ("unknown", "resource_availability_unknown")


def test_invalid_windows_are_unknown(app_conn, acl):
    b = basis(app_conn, acl, at=date(2026, 8, 15))
    t_h = h.historical_instant(app_conn, b)
    for start, end, cutoff in ((T(3, 1), T(3, 1), None), (T(5, 1), T(3, 1), None),
                               (T(3, 1), t_h + timedelta(days=1), None),     # past t_h
                               (T(3, 1), T(7, 1), T(6, 1)),                  # past the knowledge cutoff
                               (datetime(2025, 12, 1, tzinfo=UTC), T(7, 1), None)):   # before the earliest link
        r = win(app_conn, b, "person:A", "doc:plan", start, end, cutoff)
        assert (r.outcome, r.reason) == ("unknown", "window_invalid")
    ev = HistoricalAclEvaluator(app_conn)
    assert ev.evaluate("person:A", "read", "doc:plan", b, "ELIGIBILITY_DURING_WINDOW").reason == "window_invalid"


def test_acl_h4_no_eligible_interval_lies_where_the_resource_does_not_exist(app_conn, acl):
    b = basis(app_conn, acl, at=date(2026, 10, 15))
    r = win(app_conn, b, "person:A", "doc:direct", T(1, 1), T(4, 1))        # created 1 Feb
    assert r.outcome == "allowed" and r.eligible_intervals == [(T(2, 1), T(4, 1))]


# ---------------------------------------------------------------------------------------------- K, R, corrections


def test_acl_h4_records_admitted_after_k_are_never_used(app_conn, acl):
    assert pit(app_conn, basis(app_conn, acl, at=date(2026, 3, 15)), "person:B", "doc:d2grant").outcome == "allowed"
    early = pit(app_conn, k1(app_conn, acl, date(2026, 3, 15)), "person:B", "doc:d2grant")
    assert (early.outcome, early.reason) == ("denied", "no_grant")


def test_recorded_by_uses_only_history_recorded_by_the_cutoff(app_conn, acl):
    valid = pit(app_conn, basis(app_conn, acl, Mode.VALID_AT, date(2026, 8, 15)), "person:A", "doc:recorded")
    assert valid.outcome == "allowed"                                            # best current knowledge
    recorded = pit(app_conn, basis(app_conn, acl, Mode.RECORDED_BY, date(2026, 8, 15)), "person:A", "doc:recorded")
    assert (recorded.outcome, recorded.reason) == ("denied", "no_grant")       # the grant was recorded on 5 Sep


def test_unknown_recorded_time_is_bounded_by_extraction(app_conn, ingest_conn, acl):
    from veloxa.acl.historical import History, Item, RecordVersionRef, decide

    b = basis(app_conn, acl, Mode.RECORDED_BY, date(2026, 8, 15))
    with tenant_tx(app_conn, acl):
        basis_rows = app_conn.execute("SELECT DISTINCT recorded_basis FROM projection.acl_history").fetchall()
    assert {r["recorded_basis"] for r in basis_rows} == {"source_recorded"}
    # A row with no stated recording time is bounded by its extraction (now), so it is unusable in RECORDED_BY.
    history = h.load_history(app_conn, b)
    assert all(i.recorded_hi <= b.recorded_cutoff for i in history.items)
    unbounded = Item("acl", RecordVersionRef("synthetic:x", 1), T(2, 1), None, datetime.now(UTC),
                     {"resource": "doc:plan", "principal": "user:a", "permission": "read", "effect": "allow",
                      "relation": "acl"})
    assert unbounded.recorded_hi > b.recorded_cutoff
    assert decide(History(history.items), "person:A", "read", "doc:plan", b, b.recorded_cutoff, "POINT_IN_TIME",
                  None, b.recorded_cutoff).outcome == "denied"


def test_a_correction_changes_current_truth_without_changing_earlier_knowledge(app_conn, ingest_conn, acl):
    with tenant_tx(ingest_conn, acl):
        v = ingest_conn.execute("SELECT max(version) AS v FROM ledger.source_record WHERE system = 'acl_history'"
                                " AND source_path = 'data_room/acl/history.jsonl'").fetchone()["v"]
    assert v == 2
    at_k1 = pit(app_conn, k1(app_conn, acl, date(2026, 4, 15)), "person:A", "doc:direct")
    assert (at_k1.outcome, at_k1.reason) == ("denied", "no_grant")             # as first recorded: revoked 1 Apr
    now = pit(app_conn, basis(app_conn, acl, at=date(2026, 4, 15)), "person:A", "doc:direct")
    assert now.outcome == "allowed" and {r.version for r in now.acl_records} == {2}   # corrected: revoked 1 May
    again = pit(app_conn, k1(app_conn, acl, date(2026, 4, 15)), "person:A", "doc:direct")
    assert again.replay_view() == at_k1.replay_view()


# ---------------------------------------------------------------------------------------------- security


def test_acl_h1_a_historical_allowed_never_restores_revoked_current_access(app_conn, acl):
    r = pit(app_conn, basis(app_conn, acl, at=date(2026, 3, 15)), "person:A", "doc:plan")
    assert r.outcome == "allowed"
    assert effective_decision(False, r) is False     # requester currently revoked: excluded
    assert effective_decision(True, r) is True       # current AND historical
    assert effective_decision(True, None) is True    # no historical question: current authorization alone


def test_proxy_attack_cannot_turn_an_agent_into_a_confused_deputy(app_conn, acl):
    """An interviewer with no current access asks an agent representing person:A about doc:plan, and tries to
    smuggle a raw principal or a colliding group name in as the subject."""
    b = basis(app_conn, acl, at=date(2026, 3, 15))
    historical = pit(app_conn, b, "person:A", "doc:plan")
    assert historical.outcome == "allowed" and effective_decision(False, historical) is False
    for spoofed in ("user:a", "group:staff", "A", "person:a"):
        r = pit(app_conn, b, spoofed, "doc:plan")
        assert (r.outcome, r.reason) == ("unknown", "identity_unresolved")
    assert pit(app_conn, b, "person:B", "doc:direct").outcome == "denied"   # another person's grant never applies


def test_acl_h5_the_evaluator_never_calls_a_current_authorization_engine():
    import veloxa.acl.historical as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    imports = re.findall(r"^\s*(?:from|import)\s+([\w.]+)", src, re.M)
    assert not [i for i in imports if re.search(r"spicedb|authzed|rebac|relationship|opa|cedar", i, re.I)]
    assert not re.search(r"RelationshipStore|\.check\(|lookup_resources", src)
    tables = set(re.findall(r"(?:FROM|JOIN)\s+([a-z_]+\.[a-z_]+)", src))
    assert tables == {"projection.acl_history", "ledger.admission_batch"}   # read-only, Veloxa-owned history


def test_acl_h6_results_are_replayable_and_differ_only_in_evaluated_at(app_conn, acl):
    b = basis(app_conn, acl, at=date(2026, 8, 15))
    one = HistoricalAclEvaluator(app_conn, clock=lambda: T(11, 17, 1))
    two = HistoricalAclEvaluator(app_conn, clock=lambda: T(11, 17, 9))
    for res in ("doc:plan", "doc:observed", "doc:future"):
        a = one.evaluate("person:A", "read", res, b, "ELIGIBILITY_DURING_WINDOW", AclWindow(T(1, 1), T(8, 1)))
        c = two.evaluate("person:A", "read", res, b, "ELIGIBILITY_DURING_WINDOW", AclWindow(T(1, 1), T(8, 1)))
        assert a.evaluated_at != c.evaluated_at and a.replay_view() == c.replay_view()
        assert "historical_at" in a.replay_view() and "resource_existence" in a.replay_view()


def test_history_never_crosses_tenants(db, app_conn, acl):
    other = uuid.uuid4()
    with psycopg.connect(db.admin_dsn, autocommit=True) as admin:
        admin.execute("INSERT INTO directory.tenant (tenant_id, slug, synthetic, time_zone) VALUES (%s, %s, true,"
                      " 'UTC')", (other, f"t-{other.hex[:12]}"))
    with tenant_tx(app_conn, other):
        assert app_conn.execute("SELECT count(*) AS n FROM projection.acl_history WHERE tenant_id = %s",
                                (acl,)).fetchone()["n"] == 0
    other_now = basis(app_conn, other, Mode.CURRENT, None)
    empty = HistoricalAclEvaluator(app_conn).evaluate("person:A", "read", "doc:plan", other_now, "POINT_IN_TIME")
    assert empty.outcome == "unknown"
    with pytest.raises(psycopg.errors.InsufficientPrivilege), tenant_tx(app_conn, acl):
        app_conn.execute("DELETE FROM projection.acl_history")   # the application role cannot alter history
