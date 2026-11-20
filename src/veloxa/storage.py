"""Immutable, content-addressed original-file store (C05; §7.2). Local backend for development and the Demo Lab.

**Layout:** `<root>/<tenant_id>/<sha256[:2]>/<sha256>` (the bytes only; names live in the database).
- **Write-once:** an object is created exclusively. Writing an existing address succeeds only if the bytes hash to
  the same value; nothing is ever overwritten.
- **Verified reads:** every read re-hashes the bytes and refuses a mismatch (tamper or corruption).
- **Tenant isolation:** a tenant prefix plus database RLS on the registry (`intake.stored_object`).
- **Location:** the root comes from `VELOXA_ORIGINALS_DIR`. It must be absolute, outside the Git repository, and
  never served directly. Originals reach users only through authorised, audited API responses.

**Production:** an object-lock (WORM) bucket replaces this backend behind the same interface (`ObjectStore`). It is
not configured.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path
from typing import Protocol

REPO_ROOT = Path(__file__).resolve().parents[2]


class StoreError(Exception):
    pass


class ObjectStore(Protocol):
    def put(self, tenant_id: uuid.UUID, data: bytes) -> str: ...
    def get(self, tenant_id: uuid.UUID, sha256: str) -> bytes: ...
    def exists(self, tenant_id: uuid.UUID, sha256: str) -> bool: ...


class LocalContentStore:
    def __init__(self, root: Path):
        root = Path(root).resolve()
        if not root.is_absolute():
            raise StoreError("the originals store root must be absolute")
        if root == REPO_ROOT or REPO_ROOT in root.parents:
            raise StoreError("the originals store must be outside the Git repository")
        self.root = root

    @staticmethod
    def from_env() -> LocalContentStore | None:
        v = os.environ.get("VELOXA_ORIGINALS_DIR", "").strip()
        return LocalContentStore(Path(v)) if v else None

    def _path(self, tenant_id: uuid.UUID, sha256: str) -> Path:
        if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
            raise StoreError("invalid content address")
        return self.root / str(uuid.UUID(str(tenant_id))) / sha256[:2] / sha256

    def put(self, tenant_id: uuid.UUID, data: bytes) -> str:
        sha = hashlib.sha256(data).hexdigest()
        path = self._path(tenant_id, sha)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o440)
        except FileExistsError:
            if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
                raise StoreError("an existing object does not match its address (tamper or corruption)") from None
            return sha                                   # idempotent: the same bytes are already stored
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        return sha

    def get(self, tenant_id: uuid.UUID, sha256: str) -> bytes:
        path = self._path(tenant_id, sha256)
        if not path.is_file():
            raise StoreError("no such object")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != sha256:
            raise StoreError("the stored object does not match its address (tamper or corruption)")
        return data

    def exists(self, tenant_id: uuid.UUID, sha256: str) -> bool:
        return self._path(tenant_id, sha256).is_file()
