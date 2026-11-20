"""Source-object identity across ingestion routes (migration 0025). One registry, every route.

Order of matching (deterministic first, never a destructive merge):
1. Canonical key + version: an object already registered with the same key and version is a `duplicate` and a
   connector does not submit it again. A different version of a known key is a `new_version`.
   Canonical keys make the same item recognisable whichever route delivered it:
     email:<Message-ID>            Gmail record or an uploaded .eml (e-mail is immutable: version is empty)
     jira:<KEY>                    polling and webhook-triggered fetches (version = `updated`)
     github:<repo>:<kind>:<id>     repeated syncs (version = SHA or `updated_at`)
     file:<sha256>                 any other uploaded file (identical bytes = identical object)
2. Content fingerprint (SHA-256 of the normalised text): a different key with the same fingerprint is
   `same_content_other_identity`. Both identities are kept and linked; neither is dropped.
3. Near-duplicate text (bottom-k shingle Jaccard >= NEAR): `review_candidate` for a person to decide.
Each registration keeps its own route, URL, timestamps and access scope. A link to a copy elsewhere grants NO
access: readers still need permission on each record (evidence permissions are unchanged)."""

from __future__ import annotations

import email
import email.policy
import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field

from .db import tenant_tx

NEAR = 0.8
K = 128                                  # bottom-k shingle sketch size
MIN_SHINGLES = 8                         # too little text to call anything a near-duplicate


@dataclass
class Obj:
    route: str
    object_key: str
    text: str
    version: str = ""
    external_id: str | None = None
    url: str | None = None
    access_scope: dict = field(default_factory=dict)
    observed_at: str | None = None


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (text or "").lower())).strip()


def fingerprint(text: str) -> str:
    return hashlib.sha256(normalise(text).encode("utf-8")).hexdigest()


def shingles(text: str, n: int = 3) -> list[int]:
    w = normalise(text).split()
    grams = {" ".join(w[i:i + n]) for i in range(max(0, len(w) - n + 1))}
    hs = sorted({int.from_bytes(hashlib.blake2b(g.encode(), digest_size=8).digest(), "big", signed=True)
                 for g in grams})
    return hs[:K]


def jaccard(a: list[int], b: list[int]) -> float:
    if len(a) < MIN_SHINGLES or len(b) < MIN_SHINGLES:
        return 0.0
    sa, sb = set(a), set(b)
    union = sorted(sa | sb)[:K]                       # bottom-k estimator
    return sum(1 for x in union if x in sa and x in sb) / len(union) if union else 0.0


def message_id_key(mid: str | None) -> str | None:
    m = (mid or "").strip().strip("<>").strip().lower()
    return f"email:{m}" if m else None


# ------------------------------------------------------------------ per-route canonical identity
def from_gmail(r: dict) -> Obj:
    return Obj("gmail", message_id_key(r.get("rfc822_message_id")) or f"gmail:{r['message_id']}",
               " ".join(str(r.get(k) or "") for k in ("subject", "from", "date", "snippet")), "",
               r["message_id"], r.get("locator"), r.get("access_scope") or {}, r.get("date"))


def from_github(r: dict) -> Obj:
    return Obj("github", r["source_id"], json.dumps(r.get("data"), sort_keys=True, default=str), r.get("version", ""),
               r["source_id"], r.get("url"), r.get("permission_scope") or {}, r.get("updated_at"))


def from_jira(r: dict) -> Obj:
    return Obj("jira", f"jira:{r['key']}", json.dumps(r.get("fields"), sort_keys=True, default=str),
               str(r.get("updated") or ""), r.get("id"), None, {"project": r["key"].split("-")[0]}, r.get("updated"))


def from_upload(file_name: str, data: bytes, sha256: str, route: str = "upload") -> Obj:
    if file_name.lower().endswith(".eml"):
        msg = email.message_from_bytes(data, policy=email.policy.default)
        body = msg.get_body(preferencelist=("plain", "html"))
        text = " ".join(str(msg.get(h) or "") for h in ("Subject", "From", "Date")) + " " + (
            body.get_content() if body else "")
        key = message_id_key(msg.get("Message-ID"))
        if key:
            return Obj(route, key, text, "", msg.get("Message-ID"), None, {"file": file_name}, msg.get("Date"))
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = ""
    return Obj(route, f"file:{sha256}", text or sha256, "", file_name, None, {"file": file_name})


# ------------------------------------------------------------------ classification + registration
def available(conn) -> bool:
    """False on a database without migration 0025: ingestion keeps working, unregistered (named by /schema)."""
    return conn.execute("SELECT to_regclass('runtime.source_object') IS NOT NULL AS ok").fetchone()["ok"]


def classify(conn, tenant_id, o: Obj) -> tuple[str, list[str], float | None, str, list[int]]:
    fp, sh = fingerprint(o.text), shingles(o.text)
    same = conn.execute("SELECT version FROM runtime.source_object WHERE tenant_id = %s AND object_key = %s",
                        (tenant_id, o.object_key)).fetchall()
    if same:
        return ("duplicate" if any(r["version"] == o.version for r in same) else "new_version"), [], None, fp, sh
    other = conn.execute("SELECT DISTINCT object_key FROM runtime.source_object WHERE tenant_id = %s AND"
                         " fingerprint = %s AND object_key <> %s LIMIT 20", (tenant_id, fp, o.object_key)).fetchall()
    if other:
        return "same_content_other_identity", [r["object_key"] for r in other], 1.0, fp, sh
    if len(sh) >= MIN_SHINGLES:
        cands = conn.execute("SELECT DISTINCT ON (object_key) object_key, shingles FROM runtime.source_object"
                             " WHERE tenant_id = %s AND shingles && %s::bigint[] AND object_key <> %s"
                             " ORDER BY object_key, at DESC LIMIT 500", (tenant_id, sh, o.object_key)).fetchall()
        best = sorted(((jaccard(sh, list(c["shingles"])), c["object_key"]) for c in cands), reverse=True)
        near = [(s, k) for s, k in best if s >= NEAR]
        if near:
            return "review_candidate", [k for _, k in near[:20]], round(near[0][0], 3), fp, sh
    return "new", [], None, fp, sh


def register(conn, tenant_id: uuid.UUID, actor: str, objs: list[Obj], submission_id=None,
             classified: list[tuple] | None = None) -> list[dict]:
    """Record every object with its classification (inside the caller's tenant transaction)."""
    out = []
    for i, o in enumerate(objs):
        cls, rel, sim, fp, sh = classified[i] if classified else classify(conn, tenant_id, o)
        conn.execute(
            "INSERT INTO runtime.source_object (tenant_id, route, object_key, external_id, version, fingerprint,"
            " shingles, url, access_scope, observed_at, classification, related_keys, similarity, submission_id,"
            " actor) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::timestamptz,%s,%s,%s,%s,%s)",
            (tenant_id, o.route, o.object_key[:600], (o.external_id or None) and str(o.external_id)[:600],
             o.version[:200], fp, sh, o.url, json.dumps(o.access_scope, default=str), _ts(o.observed_at), cls, rel,
             sim, submission_id, actor))
        out.append({"object_key": o.object_key, "classification": cls, "related_keys": rel, "similarity": sim})
    return out


def _ts(v):
    if not v:
        return None
    from email.utils import parsedate_to_datetime

    s = str(v)
    if re.match(r"\d{4}-\d{2}-\d{2}", s):
        return s
    try:
        return parsedate_to_datetime(s).isoformat()
    except (TypeError, ValueError):
        return None


def review_queue(conn, tenant_id, limit: int = 100) -> list[dict]:
    with tenant_tx(conn, tenant_id):
        rows = conn.execute(
            "SELECT route, object_key, related_keys, similarity, classification, url, at FROM runtime.source_object"
            " WHERE tenant_id = %s AND classification IN ('review_candidate', 'same_content_other_identity')"
            " ORDER BY at DESC LIMIT %s", (tenant_id, limit)).fetchall()
    return [dict(r) | {"at": r["at"].isoformat()} for r in rows]
