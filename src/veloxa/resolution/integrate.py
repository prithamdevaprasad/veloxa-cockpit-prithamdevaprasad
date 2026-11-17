"""Connect ADMITTED evidence to C12 (ARCHITECTURE.md §7.5): HR records anchor person entities; e-mail parties become
source identities with rule candidates. Runs as the projection role for one sealed batch K; idempotent.

Nothing here decides anything:
- every identity cites the ledger record it came from (origin `release_pack`), so the decision guard keeps all of
  them unresolved in this development profile;
- an e-mail address is NOT an HR identity: it gets candidates (classification + evidence), never a link;
- e-mail participation grants nothing: parties are identities, not permissions (C32 ignores them, §10.7);
- observation time is the e-mail's own Date header, or None when it has none (never "now").
Only batch K's projected rows are read, so re-running for an earlier K reproduces that K's identities.
"""

from __future__ import annotations

import uuid

import psycopg

from ..db import tenant_tx
from . import rules
from . import store as c12


def sync_batch(conn: psycopg.Connection, tenant_id: uuid.UUID, batch_seq: int) -> dict:
    with tenant_tx(conn, tenant_id, batch_seq=batch_seq):
        people = conn.execute(
            "SELECT emp_id, full_name, work_email, source_record_id, source_version FROM projection.person"
            " WHERE tenant_id = %s AND batch_seq = %s ORDER BY emp_id", (tenant_id, batch_seq)).fetchall()
        mails = conn.execute(
            "SELECT source_record_id, source_version, event_at, parties FROM projection.document_text"
            " WHERE tenant_id = %s AND batch_seq = %s AND system = 'email' ORDER BY event_at NULLS LAST,"
            " source_record_id", (tenant_id, batch_seq)).fetchall()
    population = []
    for p in people:
        hr = c12.register_identity(conn, tenant_id, "hr", "hr.emp_id", p["emp_id"], "release_pack",
                                   (p["source_record_id"], p["source_version"], batch_seq))
        entity = c12.register_entity(conn, tenant_id, "person", hr)
        population.append(rules.Profile(str(entity), p["full_name"],
                                        frozenset({p["work_email"].lower()}) if p["work_email"] else frozenset(),
                                        frozenset({("hr.emp_id", p["emp_id"])})))
    addresses: dict[str, tuple] = {}
    for m in mails:
        for a in m["parties"]:
            addresses.setdefault(a.lower(), (m["source_record_id"], m["source_version"], m["event_at"]))
    counts = {"hr_identities": len(people), "email_identities": len(addresses), "candidates": 0,
              "unmatched_email_identities": 0}
    for addr, (rec, ver, seen_at) in sorted(addresses.items()):
        ident = c12.register_identity(conn, tenant_id, "email", "email.address", addr, "release_pack",
                                      (rec, ver, batch_seq), observed_at=seen_at)
        found = [rules.Candidate(uuid.UUID(c.entity_id), c.classification, c.evidence)
                 for c in rules.candidates(addr, population)]
        if not found:
            counts["unmatched_email_identities"] += 1
        c12.propose(conn, tenant_id, ident, found)
        counts["candidates"] += len(found)
    return counts
