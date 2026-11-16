"""Requester authentication for requester-facing endpoints (/v1/ask, /v1/briefing). FAIL CLOSED.

The architecture's model is bearer tokens from an identity provider, resolved through the tenant directory
(ARCHITECTURE.md §19.1, §10.3). No identity provider is integrated yet, so:

- DEFAULT (production and every deployment that does not opt in): no authenticator is configured, and every
  requester-facing request is rejected with 401 `not_authenticated`. Nobody, including the CEO, is assumed.
- DEVELOPMENT FIXTURE (local development and tests only): enabled only when ALL of these are set explicitly:
    VELOXA_DEV_AUTH=1
    VELOXA_DEV_TOKEN=<a secret of at least 32 characters>      (no default)
    VELOXA_DEV_PRINCIPAL=<the employee ID the fixture acts as> (no default; never a title or a role)
  Requests must then carry `Authorization: Bearer <VELOXA_DEV_TOKEN>`. The principal is fixed by server
  configuration and must resolve to exactly one person in the latest sealed HR export. If the flag is set but the
  other settings are missing or weak, the API refuses to start. /health reports the fixture as active.

The caller can never choose an identity: request bodies reject unknown fields, and identity headers other than
the bearer token are ignored.
"""

from __future__ import annotations

import hmac
import os
from collections.abc import Mapping
from dataclasses import dataclass

MIN_TOKEN_LENGTH = 32


class AuthConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class AuthSettings:
    mode: str                     # "none" (fail closed) | "development_fixture"
    token: str | None = None
    principal_emp_id: str | None = None

    def authenticate(self, authorization: str | None) -> str | None:
        """Return the configured employee ID if the bearer token matches; None otherwise (fail closed)."""
        if self.mode != "development_fixture" or not authorization:
            return None
        scheme, _, presented = authorization.partition(" ")
        if scheme.lower() != "bearer" or not presented:
            return None
        if not hmac.compare_digest(presented.strip().encode(), (self.token or "").encode()):
            return None
        return self.principal_emp_id


def settings_from_env(env: Mapping[str, str] | None = None) -> AuthSettings:
    env = os.environ if env is None else env
    flag = env.get("VELOXA_DEV_AUTH", "").strip()
    if flag in ("", "0"):
        return AuthSettings("none")
    if flag != "1":
        raise AuthConfigError("VELOXA_DEV_AUTH must be unset, 0 or 1")
    token = env.get("VELOXA_DEV_TOKEN", "")
    emp_id = env.get("VELOXA_DEV_PRINCIPAL", "").strip()
    if len(token) < MIN_TOKEN_LENGTH:
        raise AuthConfigError(f"the development fixture needs VELOXA_DEV_TOKEN of at least {MIN_TOKEN_LENGTH} "
                              "characters")
    if not emp_id:
        raise AuthConfigError("the development fixture needs VELOXA_DEV_PRINCIPAL (an employee ID; no default)")
    return AuthSettings("development_fixture", token, emp_id)
