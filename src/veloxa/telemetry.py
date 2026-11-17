"""C29 observability (ARCHITECTURE.md §21.1): structured logs with correlation IDs, spans and metrics.

What this module does and does not do:
- **Spans.** Emitted through the vendor-neutral OpenTelemetry API, which is a no-op until a deployment installs an
  SDK and an OTLP exporter. The trace store (Langfuse candidate) is deferred (§26), and no exporter is configured.
- **Span attributes.** Pass an allowlist: IDs, names, statuses and counts only. Never messages, evidence, emails,
  tokens or protected values ("spans record IDs only after authorization").
- **Logs.** JSON lines carrying the request's correlation ID. A redaction filter scrubs e-mail addresses, bearer
  tokens, passwords and credentials in connection strings from every message.
- **Metrics.** An in-process registry (counters, histograms) with per-metric label allowlists. Rendered as
  Prometheus text by `GET /metrics` only when `VELOXA_METRICS=1` (an internal scrape endpoint; off by default).
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
import threading
import time
import uuid
from contextlib import contextmanager

from opentelemetry import trace

REQUEST_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar("veloxa_request_id", default=None)
_TRACER = trace.get_tracer("veloxa")
SPAN_ATTRIBUTES = frozenset({"tenant_id", "agent_id", "session_id", "execution_id", "workflow_id", "workflow",
                             "tool", "decision", "reason", "status", "stage", "route", "method", "http_status",
                             "count", "batch_seq", "policy", "provider", "model", "prompt_version"})
_REQ_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
_SCRUB = [
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer [redacted]"),
    (re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+"), "[email]"),
    (re.compile(r"(?i)(password|passwd|pwd|secret|token)\s*[=:]\s*\S+"), r"\1=[redacted]"),
    (re.compile(r"(postgres(?:ql)?://[^:/@\s]+):[^@\s]+@"), r"\1:[redacted]@"),
]


def scrub(text: str) -> str:
    for rx, repl in _SCRUB:
        text = rx.sub(repl, text)
    return text


def request_id_from(header: str | None) -> str:
    """Accept a well-formed inbound correlation ID, else mint one."""
    return header if header and _REQ_ID.match(header) else uuid.uuid4().hex


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        body = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + "Z",
                "level": record.levelname, "logger": record.name, "msg": scrub(record.getMessage()),
                "request_id": REQUEST_ID.get()}
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            body.update({k: (scrub(v) if isinstance(v, str) else v) for k, v in fields.items()
                         if k in SPAN_ATTRIBUTES})
        if record.exc_info:
            body["error"] = record.exc_info[0].__name__ if record.exc_info[0] else "error"   # type only
        return json.dumps(body, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger("veloxa")
    if not any(isinstance(h.formatter, JsonFormatter) for h in root.handlers):
        h = logging.StreamHandler()
        h.setFormatter(JsonFormatter())
        root.addHandler(h)
    root.setLevel(level)


log = logging.getLogger("veloxa")


class Metrics:
    """Thread-safe counters and histograms with per-metric label allowlists (no free-form labels)."""
    BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)
    LABELS = {
        "veloxa_http_requests_total": ("route", "method", "http_status"),
        "veloxa_http_request_seconds": ("route", "method"),
        "veloxa_errors_total": ("route",),
        "veloxa_agent_tool_seconds": ("tool", "status"),
        "veloxa_agent_tool_denied_total": ("reason",),
        "veloxa_agent_evidence_excluded_total": ("reason",),
        "veloxa_model_call_seconds": ("stage", "status"),
        "veloxa_model_refusals_total": ("status",),
        "veloxa_workflow_completed_total": ("workflow",),
        "veloxa_span_seconds": ("name",),
    }

    def __init__(self):
        self._lock = threading.Lock()
        self.counters: dict[tuple, float] = {}
        self.hist: dict[tuple, list] = {}

    def _key(self, metric: str, labels: dict) -> tuple:
        allowed = self.LABELS.get(metric)
        if allowed is None:
            raise ValueError(f"undeclared metric {metric!r}")
        return (metric, tuple((k, str(labels.get(k, ""))) for k in allowed))

    def inc(self, metric: str, value: float = 1, **labels) -> None:
        k = self._key(metric, labels)
        with self._lock:
            self.counters[k] = self.counters.get(k, 0) + value

    def observe(self, metric: str, seconds: float, **labels) -> None:
        k = self._key(metric, labels)
        with self._lock:
            h = self.hist.setdefault(k, [0.0, 0, [0] * len(self.BUCKETS)])
            h[0] += seconds
            h[1] += 1
            for i, b in enumerate(self.BUCKETS):
                if seconds <= b:
                    h[2][i] += 1

    def value(self, metric: str, **labels) -> float:
        return self.counters.get(self._key(metric, labels), 0)

    def count(self, metric: str, **labels) -> int:
        return self.hist.get(self._key(metric, labels), [0, 0, []])[1]

    def render(self) -> str:
        def lbl(pairs, extra=()):
            items = [f'{k}="{v}"' for k, v in (*pairs, *extra)]
            return "{" + ",".join(items) + "}" if items else ""
        out = []
        with self._lock:
            for (name, pairs), v in sorted(self.counters.items()):
                out.append(f"{name}{lbl(pairs)} {v}")
            for (name, pairs), (total, n, buckets) in sorted(self.hist.items()):
                for b, c in zip(self.BUCKETS, buckets, strict=True):
                    out.append(f"{name}_bucket{lbl(pairs, (('le', b),))} {c}")
                out.append(f"{name}_bucket{lbl(pairs, (('le', '+Inf'),))} {n}")
                out += [f"{name}_sum{lbl(pairs)} {total}", f"{name}_count{lbl(pairs)} {n}"]
        return "\n".join(out) + "\n"


METRICS = Metrics()


@contextmanager
def span(name: str, **attributes):
    """An OpenTelemetry span (no-op without an SDK) plus a duration metric. Attributes outside the allowlist are
    dropped, never recorded."""
    safe = {k: (str(v) if not isinstance(v, (int, float, bool)) else v) for k, v in attributes.items()
            if k in SPAN_ATTRIBUTES and v is not None}
    started = time.perf_counter()
    with _TRACER.start_as_current_span(name, attributes=safe) as s:
        try:
            yield s
        finally:
            METRICS.observe("veloxa_span_seconds", time.perf_counter() - started, name=name)
