"""Startup self-checks (ARCHITECTURE.md §9.3). A service refuses to start unless all of these hold."""

from __future__ import annotations

import psycopg

TENANT_SCHEMAS = ("raw_index", "ledger", "judgement", "knowledge", "projection", "policy", "runtime", "audit")
MIN_SERVER_VERSION = 180000


def run(conn: psycopg.Connection) -> list[str]:
    """Return a list of failures for the connected role; an empty list means all checks passed."""
    failures: list[str] = []
    role = conn.execute(
        "SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
    ).fetchone()
    if role["rolsuper"]:
        failures.append(f"role {role['rolname']} is a superuser")
    if role["rolbypassrls"]:
        failures.append(f"role {role['rolname']} has BYPASSRLS")

    owned = conn.execute(
        "SELECT n.nspname || '.' || c.relname AS rel FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
        " WHERE n.nspname = ANY(%s) AND pg_get_userbyid(c.relowner) = current_user",
        (list(TENANT_SCHEMAS),),
    ).fetchall()
    if owned:
        failures.append(f"role owns relations in tenant schemas: {', '.join(r['rel'] for r in owned)}")

    tables = conn.execute(
        "SELECT n.nspname || '.' || c.relname AS rel, c.relrowsecurity, c.relforcerowsecurity,"
        "       EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid AND NOT p.polpermissive"
        "               AND p.polname = 'tenant_isolation') AS has_restrictive"
        " FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
        " WHERE n.nspname = ANY(%s) AND c.relkind IN ('r', 'p')",
        (list(TENANT_SCHEMAS),),
    ).fetchall()
    if not tables:
        failures.append("no tenant tables found; migrations have not run")
    for t in tables:
        if not (t["relrowsecurity"] and t["relforcerowsecurity"] and t["has_restrictive"]):
            failures.append(f"{t['rel']} lacks RLS, FORCE RLS or the restrictive tenant policy")

    views = conn.execute(
        "SELECT n.nspname || '.' || c.relname AS rel, coalesce(c.reloptions, '{}') AS opts"
        " FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
        " WHERE n.nspname = ANY(%s) AND c.relkind = 'v'",
        (list(TENANT_SCHEMAS),),
    ).fetchall()
    for v in views:
        if "security_invoker=true" not in v["opts"]:
            failures.append(f"view {v['rel']} is not security_invoker")

    # Protected pay (§9.4, §15): the application role must not read payroll lines or payroll aggregates
    # except through the disclosure auditor. The restrictive policy must exist and name both classes.
    # Every table carrying a data_class column must have it.
    classed = conn.execute(
        "SELECT c.oid, n.nspname || '.' || c.relname AS rel FROM pg_class c"
        " JOIN pg_namespace n ON n.oid = c.relnamespace"
        " JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'data_class' AND NOT a.attisdropped"
        " WHERE n.nspname = ANY(%s) AND c.relkind IN ('r', 'p')",
        (list(TENANT_SCHEMAS),),
    ).fetchall()
    for t in classed:
        pay = conn.execute(
            "SELECT pg_get_expr(p.polqual, p.polrelid) AS qual, p.polpermissive,"
            "       (SELECT array_agg(r.rolname) FROM pg_roles r WHERE r.oid = ANY(p.polroles)) AS roles"
            " FROM pg_policy p WHERE p.polrelid = %s AND p.polname = 'protected_pay_for_app'",
            (t["oid"],),
        ).fetchone()
        if (pay is None or pay["polpermissive"] or "veloxa_app" not in (pay["roles"] or [])
                or "sensitive_personal" not in pay["qual"] or "payroll_aggregate" not in pay["qual"]):
            failures.append(f"{t['rel']}: restrictive protected-pay policy for veloxa_app is missing or incomplete")

    version = int(conn.execute("SHOW server_version_num").fetchone()["server_version_num"])
    if version < MIN_SERVER_VERSION:
        failures.append(f"PostgreSQL {version} is older than the required 18")
    return failures
