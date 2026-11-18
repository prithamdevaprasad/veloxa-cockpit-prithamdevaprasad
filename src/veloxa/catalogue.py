"""Source catalogue: classify each file of a pack. Unmatched files are rejected (fail closed)."""

from __future__ import annotations

import fnmatch
import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import config
from .ingest.contracts import Contract

DATA_CLASSES = {
    "internal", "confidential", "personal", "sensitive_personal",
    "payroll_aggregate", "private_comms", "suppressed", "assumption",
}
RECORD_MODES = {"rows", "document", "none"}


@dataclass(frozen=True)
class Rule:
    pattern: str
    kind: str
    system: str
    data_class: str
    records: str
    row_class: dict | None = None
    contract: Contract | None = None
    # Approved business key (tenant catalogue rules, 0018): a row's record identity is these column values, so a
    # corrected export with inserted or deleted rows still supersedes the right record. Empty = the locator.
    key_columns: tuple[str, ...] = ()

    def record_key(self, locator_key: str, payload: dict) -> str:
        if self.key_columns and all(payload.get(c) not in (None, "") for c in self.key_columns):
            return "key:" + "|".join(f"{c}={payload[c]}" for c in self.key_columns)
        return locator_key

    def class_for_row(self, payload: dict) -> str:
        if self.row_class:
            override = dict(self.row_class)
            cls = override.pop("class")
            if all(payload.get(col) == val for col, val in override.items()):
                return cls
        return self.data_class


class UnclassifiedSource(Exception):
    pass


def load_catalogue(path: Path | None = None) -> list[Rule]:
    path = path or (config.config_dir() / "source_catalogue.toml")
    rules = []
    for raw in tomllib.loads(path.read_text(encoding="utf-8"))["rule"]:
        rule = Rule(raw["pattern"], raw["kind"], raw["system"], raw["data_class"], raw["records"],
                    raw.get("row_class"), Contract.from_toml(raw.get("contract")),
                    tuple(raw.get("key_columns", ())))
        if rule.contract and rule.records != "rows":
            raise ValueError(f"a contract is only defined for row exports ({rule.pattern!r})")
        if rule.data_class not in DATA_CLASSES or rule.records not in RECORD_MODES:
            raise ValueError(f"invalid catalogue rule {rule.pattern!r}")
        if rule.row_class and rule.row_class.get("class") not in DATA_CLASSES:
            raise ValueError(f"invalid row_class in rule {rule.pattern!r}")
        rules.append(rule)
    return rules


def classify(rules: list[Rule], relpath: str) -> Rule:
    for rule in rules:
        if fnmatch.fnmatchcase(relpath, rule.pattern):
            return rule
    raise UnclassifiedSource(f"no catalogue rule matches {relpath!r}; refusing to admit an unknown source")
