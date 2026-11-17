"""C31 database operations: verified logical backups and restore drills (ARCHITECTURE.md §22.2).

Scope of this module:
- **Production backups (NOT here):** §22.2 specifies PostgreSQL PITR with continuous WAL archiving to versioned,
  object-locked storage. That infrastructure is not deployed.
- **What this module gives:** a logical backup (`pg_dump`, custom format) with a manifest, and a repeatable restore
  drill that proves a backup restores to identical evidence.
- **Manifest contents:** SHA-256, size, server version, applied migrations with their hashes, and a data fingerprint
  (ledger, batches, audit chain, judgements, answers).
- **The drill:** restores ONLY into a new disposable database named `veloxa_restore_<random>`. It never restores
  over any existing database, and refuses any other target. It compares fingerprints, verifies every tenant's audit
  hash chain, and drops the copy.

Tools: `pg_dump` and `pg_restore` at least as new as the server (18). They are found on PATH, or given by
`VELOXA_PG_TOOLS`:
- a directory, or
- `docker:<container>`: run inside the database container.

The password is passed through the environment, never on a command line.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg

RESTORE_PREFIX = "veloxa_restore_"


class BackupError(Exception):
    pass


@dataclass(frozen=True)
class PgTools:
    mode: str           # "path" | "docker"
    where: str          # bin directory, or container name

    @staticmethod
    def from_env() -> PgTools | None:
        v = os.environ.get("VELOXA_PG_TOOLS", "")
        if v.startswith("docker:"):
            return PgTools("docker", v.split(":", 1)[1])
        if v:
            return PgTools("path", v)
        found = shutil.which("pg_dump")
        return PgTools("path", str(Path(found).parent)) if found else None

    def run(self, tool: str, args: list[str], dsn: str, data: bytes | None = None) -> subprocess.CompletedProcess:
        """Run a client tool with output captured through a pipe and input passed as bytes (no handle
        inheritance, which is unreliable through Docker Desktop's CLI shim on Windows)."""
        p = urlsplit(dsn)
        env = {**os.environ, "PGPASSWORD": p.password or ""}
        if self.mode == "docker":            # inside the container the server is on localhost:5432
            conn = ["-h", "localhost", "-p", "5432", "-U", p.username or "postgres"]
            cmd = ["docker", "exec", *(["-i"] if data is not None else []), "-e", "PGPASSWORD", self.where, tool,
                   *conn, *args]
            env["MSYS_NO_PATHCONV"] = "1"
        else:
            conn = ["-h", p.hostname or "localhost", "-p", str(p.port or 5432), "-U", p.username or "postgres"]
            cmd = [str(Path(self.where) / tool), *conn, *args]
        # argv list, no shell; the tools and their arguments come from configuration, never from requests
        return subprocess.run(  # noqa: S603
            cmd, input=data, stdin=None if data is not None else subprocess.DEVNULL, capture_output=True, env=env,
            timeout=600, check=False)


def _with_db(dsn: str, database: str) -> str:
    p = urlsplit(dsn)
    return urlunsplit((p.scheme, p.netloc, f"/{database}", p.query, p.fragment))


def fingerprint(dsn: str) -> dict:
    """A content fingerprint independent of physical layout. Needs an administrator DSN (reads every tenant)."""
    from ..audit import verify_chain

    with psycopg.connect(dsn, autocommit=True) as c:
        one = lambda sql: c.execute(sql).fetchone()[0]  # noqa: E731
        fp = {
            "batches": one("SELECT coalesce(string_agg(tenant_id || ':' || batch_seq || ':' || pack_id || ':' || sealed"
                           " || ':' || coalesce(manifest_sha256, ''), ',' ORDER BY tenant_id, batch_seq), '')"
                           " FROM ledger.admission_batch"),
            "ledger_md5": one("SELECT md5(coalesce(string_agg(tenant_id || '|' || record_id || '|' || version || '|'"
                              " || batch_seq || '|' || md5(payload::text), ',' ORDER BY tenant_id, record_id, version),"
                              " '')) FROM ledger.source_record"),
            "audit": one("SELECT count(*) || ':' || md5(coalesce(string_agg(hash, ',' ORDER BY tenant_id, seq), ''))"
                         " FROM audit.event"),
            "answers_md5": one("SELECT md5(coalesce(string_agg(answer_id::text || md5(answer::text), ','"
                               " ORDER BY answer_id), '')) FROM runtime.answer"),
            "judgements": one("SELECT (SELECT count(*) FROM judgement.resolution_decision) || ':' ||"
                              " (SELECT count(*) FROM judgement.source_identity)"),
            "migrations": one("SELECT string_agg(name || '=' || sha256, ',' ORDER BY name)"
                              " FROM veloxa_meta.schema_migration"),
        }
        tenants = [r[0] for r in c.execute("SELECT DISTINCT tenant_id FROM audit.event").fetchall()]
    chains = {}
    with psycopg.connect(dsn, autocommit=True, row_factory=psycopg.rows.dict_row) as c:
        for t in tenants:
            c.execute("SELECT set_config('app.tenant_id', %s, false)", (str(t),))
            chains[str(t)] = verify_chain(c, t)
    fp["audit_chains_valid"] = all(chains.values())
    return fp


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def backup(tools: PgTools, admin_dsn: str, database: str, out_dir: Path) -> dict:
    """Read-only on the source. Writes <stamp>.dump and <stamp>.manifest.json; returns the manifest."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    dump = out_dir / f"{database}-{stamp}.dump"
    src = _with_db(admin_dsn, database)
    r = tools.run("pg_dump", ["--format=custom", "--dbname", database], src)
    if r.returncode != 0 or not r.stdout.startswith(b"PGDMP"):
        raise BackupError(f"pg_dump failed: {r.stderr.decode(errors='replace')[-400:]}")
    dump.write_bytes(r.stdout)
    with psycopg.connect(src, autocommit=True) as c:
        version = c.execute("SHOW server_version").fetchone()[0]
    manifest = {"database": database, "created_at": datetime.now(UTC).isoformat(), "file": dump.name,
                "sha256": _sha256(dump), "bytes": dump.stat().st_size, "server_version": version,
                "format": "pg_dump custom", "fingerprint": fingerprint(src)}
    (out_dir / f"{database}-{stamp}.manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return manifest


def verify(tools: PgTools, admin_dsn: str, dump: Path, manifest: dict) -> list[str]:
    """Problems with a backup file (empty list = verified): checksum, size and a readable archive."""
    problems = []
    if not dump.is_file():
        return ["the backup file is missing"]
    if _sha256(dump) != manifest["sha256"]:
        problems.append("checksum mismatch: the backup file changed or is corrupt")
    if dump.stat().st_size != manifest["bytes"]:
        problems.append("size mismatch")
    r = tools.run("pg_restore", ["--list"], admin_dsn, data=dump.read_bytes())
    if r.returncode != 0:
        problems.append("the archive is not readable by pg_restore")
    return problems


def restore_drill(tools: PgTools, admin_dsn: str, dump: Path, manifest: dict, keep: bool = False) -> dict:
    """Restore into a NEW disposable database, compare its fingerprint with the manifest, then drop it."""
    problems = verify(tools, admin_dsn, dump, manifest)
    if problems:
        return {"ok": False, "differences": problems, "target": None, "seconds": 0.0}
    target = f"{RESTORE_PREFIX}{uuid.uuid4().hex[:10]}"
    if not target.startswith(RESTORE_PREFIX):                       # defence in depth: never another target
        raise BackupError("refusing a restore target outside the drill namespace")
    started = time.monotonic()
    with psycopg.connect(admin_dsn, autocommit=True) as c:
        if c.execute("SELECT 1 FROM pg_database WHERE datname = %s", (target,)).fetchone():
            raise BackupError("the drill target already exists; refusing to restore over it")
        c.execute(f'CREATE DATABASE "{target}"')
    try:
        r = tools.run("pg_restore", ["--dbname", target, "--exit-on-error"], admin_dsn, data=dump.read_bytes())
        if r.returncode != 0:
            return {"ok": False, "differences": [f"pg_restore failed: {r.stderr.decode(errors='replace')[-400:]}"],
                    "target": target, "seconds": time.monotonic() - started}
        got = fingerprint(_with_db(admin_dsn, target))
        want = manifest["fingerprint"]
        diffs = [f"{k} differs" for k in want if got.get(k) != want[k]]
        if not got["audit_chains_valid"]:
            diffs.append("an audit hash chain does not verify after restore")
        return {"ok": not diffs, "differences": diffs, "target": target, "seconds": time.monotonic() - started,
                "fingerprint": got}
    finally:
        if not keep:
            with psycopg.connect(admin_dsn, autocommit=True) as c:
                c.execute(f'DROP DATABASE IF EXISTS "{target}" WITH (FORCE)')
