"""Schema capability check: which features the connected database can serve, read from the catalogue.

A database at an older migration (for example a Demo Lab built before 0021) used to fail with an anonymous
HTTP 500 the first time a page touched a newer table. This module turns that into an explicit, named state:

- `missing(conn)` lists the features whose relations are absent or not readable by the connected role. It reads
  only `pg_catalog` (readable by every role) and never `veloxa_meta`, which the app role cannot read.
- `require(conn, feature)` raises `SchemaOutOfDate`, which the API returns as HTTP 503 `schema_out_of_date`
  naming the migration an operator must apply. Nothing is replaced with empty or invented results.
"""

from __future__ import annotations

FEATURES: dict[str, tuple[str, str, tuple[str, ...]]] = {
    # feature: (migration, what the user loses, relations it needs)
    "company_intelligence": (
        "0021_company_intelligence",
        "people mapping, worries, findings, source conflicts, connectors and the executive brief",
        ("runtime.agent_person", "runtime.assumption_current", "runtime.finding_current", "runtime.notification",
         "runtime.connector_run", "knowledge.claim", "knowledge.conflict"),
    ),
}

_CHECK = (
    "SELECT n.nspname || '.' || c.relname AS rel FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
    " WHERE n.nspname || '.' || c.relname = ANY(%s) AND has_schema_privilege(n.oid, 'USAGE')"
    " AND has_table_privilege(c.oid, 'SELECT')"
)


class SchemaOutOfDate(Exception):
    def __init__(self, feature: str):
        self.feature = feature
        self.migration, self.loses, _ = FEATURES[feature]
        super().__init__(f"{feature} needs migration {self.migration}")

    def body(self) -> dict:
        return {"error": "schema_out_of_date", "feature": self.feature, "migration": self.migration,
                "detail": (f"This workspace's database is on an older schema version, so {self.loses} are not "
                           f"available. An operator must apply migration {self.migration}; no data was lost.")}


def _readable(conn, rels: tuple[str, ...]) -> set[str]:
    return {r["rel"] if isinstance(r, dict) else r[0] for r in conn.execute(_CHECK, (list(rels),)).fetchall()}


def missing(conn) -> list[dict]:
    out = []
    for name, (migration, loses, rels) in FEATURES.items():
        absent = sorted(set(rels) - _readable(conn, rels))
        if absent:
            out.append({"feature": name, "migration": migration, "unavailable": loses, "relations": absent})
    return out


def available(conn, feature: str) -> bool:
    rels = FEATURES[feature][2]
    return _readable(conn, rels) == set(rels)


def require(conn, feature: str) -> None:
    if not available(conn, feature):
        raise SchemaOutOfDate(feature)
