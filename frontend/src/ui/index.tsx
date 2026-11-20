// Reusable interface components. Status colours are semantic only; nothing here invents data.
import { useEffect, useRef, useState, type ReactNode } from "react";
import type { ApiError } from "../api/client";
import type { Evidence } from "../api/types";

export function Button(props: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "ghost" }) {
  const { variant, className, ...rest } = props;
  return <button {...rest} className={["btn", variant ?? "", className ?? ""].join(" ").trim()} />;
}

const TONES: Record<string, string> = {
  answered: "ok", qualified: "info", conflict: "warn", abstained: "", refused: "bad", unavailable: "",
  failed: "bad", A: "ok", B: "info", C: "warn", D: "", open: "warn", blocked: "", synthetic_test: "info",
  active: "ok", sealed: "ok", not_released: "", awaiting_authorisation: "warn", pending_admission: "warn",
  allowed: "ok", denied: "bad",
};

export function Badge({ value, label, title }: { value: string; label?: string; title?: string }) {
  return <span className={`badge ${TONES[value] ?? ""}`} title={title}>{label ?? value.replace(/_/g, " ")}</span>;
}

export function Section(props: { title?: string; subtitle?: ReactNode; actions?: ReactNode; children: ReactNode;
                                  "aria-label"?: string; className?: string }) {
  return (
    <section className={`section${props.className ? ` ${props.className}` : ""}`} aria-label={props["aria-label"] ?? props.title}>
      {(props.title || props.actions) && (
        <div className="section-head">
          <div>
            {props.title && <h2>{props.title}</h2>}
            {props.subtitle && <p>{props.subtitle}</p>}
          </div>
          {props.actions}
        </div>
      )}
      {props.children}
    </section>
  );
}

export function PageHead({ title, intro, children }: { title: string; intro?: ReactNode; children?: ReactNode }) {
  return (
    <div className="page-head">
      <div>
        <h1>{title}</h1>
        {intro && <p>{intro}</p>}
      </div>
      {children}
    </div>
  );
}

export function Loading({ lines = 3, label = "Loading" }: { lines?: number; label?: string }) {
  return (
    <div role="status" aria-label={label}>
      {Array.from({ length: lines }, (_, i) => <div key={i} className="skeleton" style={{ width: `${90 - i * 15}%` }} />)}
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="state">
      <h3>{title}</h3>
      {children && <p>{children}</p>}
    </div>
  );
}

export function errorTitle(error: ApiError): string {
  if (error.status === 401) return "Sign in required";
  if (error.status === 403) return "Not authorised";
  if (error.code === "network") return "The API is unreachable";
  if (error.code === "schema_out_of_date") return "This feature needs a database update";
  if (error.code === "internal_error") return "Veloxa hit an internal error";
  if (error.status === 429) return "Too many requests";
  if (error.status === 422) return "Veloxa could not use that request";
  return "Something went wrong";
}

export function ErrorState({ error, onRetry }: { error: ApiError; onRetry?: () => void }) {
  return (
    <div className="state error" role="alert">
      <h3>{errorTitle(error)}</h3>
      <p>{error.message}</p>
      {error.requestId && <p className="small muted" style={{ marginTop: 6 }}>
        Reference for the operator: <span className="mono">{error.requestId}</span></p>}
      {onRetry && error.status !== 401 && error.status !== 403 && error.code !== "schema_out_of_date" && (
        <p style={{ marginTop: 12 }}><Button onClick={onRetry}>Try again</Button></p>
      )}
    </div>
  );
}

export function Segmented<T extends string>({ value, options, onChange, label }:
  { value: T; options: { value: T; label: string }[]; onChange: (v: T) => void; label: string }) {
  return (
    <div className="seg" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={o.value} type="button" aria-pressed={o.value === value} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Drawer({ title, onClose, children, modal, header }: { title: string; onClose: () => void;
  children: ReactNode; modal?: boolean; header?: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    ref.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      prev?.focus();
    };
  }, [onClose]);
  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <div className={modal ? "drawer modal" : "drawer"} role="dialog" aria-modal="true" aria-label={title} tabIndex={-1} ref={ref}>
        <header>
          {header ?? <h2>{title}</h2>}
          <Button variant="ghost" onClick={onClose} aria-label="Close">Close</Button>
        </header>
        <div className="body">{children}</div>
      </div>
    </>
  );
}

export function EvidenceList({ items, focus }: { items: Evidence[]; focus?: string | null }) {
  if (!items.length) return <EmptyState title="No evidence">Nothing was cited.</EmptyState>;
  return (
    <ul className="plain">
      {items.map((e) => (
        <li key={e.record_id} className="evidence-item" aria-current={focus === e.record_id ? "true" : undefined}
            style={focus === e.record_id ? { borderColor: "var(--accent)" } : undefined}>
          <div className="row">
            <Badge value={e.kind} />
            <Badge value={e.data_class} />
            {e.knowledge_basis && <Badge value={e.knowledge_basis} title="knowledge basis (§14.2)" />}
            <span className="small muted">release {e.batch_seq}</span>
          </div>
          <div className="mono small" style={{ marginTop: 6, wordBreak: "break-all" }}>{e.record_id}</div>
          {e.excerpt && <div className="excerpt small">{e.excerpt.replace(/[⟦⟧]/g, "")}</div>}
        </li>
      ))}
    </ul>
  );
}

export function BarList({ rows, unit }: { rows: { label: string; value: number }[]; unit?: string }) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  return (
    <ul className="plain stack" aria-label="Values">
      {rows.map((r) => (
        <li key={r.label} className="row" style={{ flexWrap: "nowrap" }}>
          <span style={{ width: 200, flex: "none" }} className="small">{r.label}</span>
          <span className="bar-track"><span className="bar" style={{ display: "block", width: `${(r.value / max) * 100}%` }} /></span>
          <span className="small mono" style={{ width: 110, textAlign: "right", flex: "none" }}>
            {r.value.toLocaleString("en-IN")}{unit ? ` ${unit}` : ""}
          </span>
        </li>
      ))}
    </ul>
  );
}

/** Client-side pagination for lists already fully returned by the API (never invents rows). */
export function usePage<T>(items: T[], size: number) {
  const [index, setIndex] = useState(0);
  const pages = Math.max(1, Math.ceil(items.length / size));
  const current = Math.min(index, pages - 1);
  return { items: items.slice(current * size, current * size + size), index: current, pages, setIndex, total: items.length };
}

export function Pager({ page }: { page: { index: number; pages: number; setIndex: (i: number) => void; total: number } }) {
  if (page.pages <= 1) return null;
  return (
    <nav className="row" aria-label="Pagination" style={{ justifyContent: "flex-end", marginTop: 10 }}>
      <span className="small muted">Page {page.index + 1} of {page.pages} ({page.total} items)</span>
      <Button disabled={page.index === 0} onClick={() => page.setIndex(page.index - 1)}>Previous</Button>
      <Button disabled={page.index >= page.pages - 1} onClick={() => page.setIndex(page.index + 1)}>Next</Button>
    </nav>
  );
}
