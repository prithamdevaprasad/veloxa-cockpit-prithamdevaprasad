"""Runtime configuration from the environment. Secrets are never logged or given defaults here."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

_TENANT_NAMESPACE = uuid.UUID("6f1f3c1e-6c55-4f5b-9a55-1f0d7f3a2b10")


def tenant_uuid(slug: str) -> uuid.UUID:
    """Deterministic, system-generated tenant identifier (never embedded in names, §9.5)."""
    return uuid.uuid5(_TENANT_NAMESPACE, f"tenant:{slug}")


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"environment variable {name} is required")
    return value


def config_dir() -> Path:
    explicit = os.environ.get("VELOXA_CONFIG_DIR")
    if explicit:
        return Path(explicit)
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "config" / "release_schedule.toml"
        if candidate.exists():
            return candidate.parent
    return Path.cwd() / "config"


@dataclass(frozen=True)
class DbSettings:
    host: str
    port: int
    database: str

    def dsn(self, user: str, password: str, database: str | None = None) -> str:
        return (
            f"postgresql://{quote(user)}:{quote(password, safe='')}@{self.host}:{self.port}/"
            f"{database or self.database}"
        )


def db_settings() -> DbSettings:
    return DbSettings(
        # 127.0.0.1, not "localhost": on Windows "localhost" tries IPv6 first and stalls on IPv4-only ports.
        host=os.environ.get("VELOXA_DB_HOST", "127.0.0.1"),
        port=int(os.environ.get("VELOXA_DB_PORT", "5432")),
        database=os.environ.get("VELOXA_DB_NAME", "veloxa"),
    )


def role_password(role: str) -> str:
    """Password for a Veloxa login role, from VELOXA_<ROLE>_PASSWORD (e.g. VELOXA_APP_PASSWORD)."""
    return _required(f"VELOXA_{role.upper()}_PASSWORD")


def admin_credentials() -> tuple[str, str]:
    return _required("VELOXA_DB_ADMIN_USER"), _required("VELOXA_DB_ADMIN_PASSWORD")


def data_path() -> Path | None:
    value = os.environ.get("VELOXA_DATA_PATH")
    return Path(value) if value else None

