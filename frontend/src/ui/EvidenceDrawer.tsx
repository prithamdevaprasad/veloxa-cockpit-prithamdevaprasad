// The evidence drawer: one place, app-wide, where any citation opens. Every action calls an authorised backend
// endpoint; when the original file is not held by Veloxa the drawer says so instead of showing a dead link.
import { createContext, useContext, useState, type ReactNode } from "react";
import { ApiError } from "../api/client";
import { openOriginal, product } from "../api/product";
import { useApi } from "../api/useApi";
import { Badge, Button, Drawer, ErrorState, Loading } from "./index";

type Ctx = { open: (recordId: string, why?: string) => void };
const EvidenceCtx = createContext<Ctx>({ open: () => undefined });
export const useEvidence = () => useContext(EvidenceCtx);

export function EvidenceProvider({ children }: { children: ReactNode }) {
  const [current, setCurrent] = useState<{ id: string; why?: string } | null>(null);
  return (
    <EvidenceCtx.Provider value={{ open: (id, why) => setCurrent({ id, why }) }}>
      {children}
      {current && <EvidencePanel recordId={current.id} why={current.why} onClose={() => setCurrent(null)} />}
    </EvidenceCtx.Provider>
  );
}

function where(loc: Record<string, unknown>): string {
  const parts: string[] = [];
  if (loc.sheet) parts.push(`sheet ${loc.sheet}`);
  if (loc.row) parts.push(`row ${loc.row}`);
  const chunk = loc.chunk as Record<string, unknown> | undefined;
  if (chunk?.page) parts.push(`page ${chunk.page}`);
  if (chunk?.paragraph) parts.push(`paragraph ${chunk.paragraph}`);
  if (loc.message) parts.push("message");
  return parts.join(", ") || "whole document";
}

function EvidencePanel({ recordId, why, onClose }: { recordId: string; why?: string; onClose: () => void }) {
  const [ev, reload] = useApi((s) => product.evidence(recordId, s), [recordId]);
  const [err, setErr] = useState<ApiError | null>(null);
  const act = async (download: boolean) => {
    if (ev.state !== "ready" || !ev.data.original.href) return;
    setErr(null);
    try { await openOriginal(ev.data.original.href, ev.data.file_name, download); }
    catch (e) { setErr(e instanceof ApiError ? e : new ApiError(0, "unknown", String(e))); }
  };
  return (
    <Drawer title={ev.state === "ready" ? ev.data.file_name : "Evidence"} onClose={onClose}>
      {ev.state === "loading" && <Loading />}
      {ev.state === "error" && <ErrorState error={ev.error} onRetry={reload} />}
      {ev.state === "ready" && (() => {
        const d = ev.data;
        const content = Object.entries(d.content ?? {}).filter(([, v]) => typeof v === "string" || typeof v === "number");
        const paras = Array.isArray((d.content as { paragraphs?: string[] }).paragraphs)
          ? (d.content as { paragraphs: string[] }).paragraphs : null;
        return (
          <div className="stack">
            {why && <p className="callout info small">Why this supports the answer: {why.replace(/[⟦⟧]/g, "")}</p>}
            <dl className="meta">
              <dt>Source</dt><dd>{d.system.replace(/_/g, " ")}</dd>
              <dt>Location</dt><dd>{where(d.locator)}</dd>
              <dt>Version</dt><dd>v{d.version} · batch K={d.batch_seq}{d.versions.length > 1 ? ` (${d.versions.length} versions)` : ""}</dd>
              <dt>Extracted</dt><dd>{new Date(d.extracted_at).toLocaleString()}</dd>
              <dt>Classification</dt><dd><Badge value={d.data_class} /></dd>
              <dt>Admission</dt><dd>{d.admission_state}</dd>
              <dt>Access</dt><dd>{d.access}</dd>
            </dl>
            <div className="row">
              <Button variant="primary" disabled={!d.original.available} onClick={() => act(false)}>Open original</Button>
              <Button disabled={!d.original.available} onClick={() => act(true)}>Download original</Button>
            </div>
            {!d.original.available && <p className="small muted">{d.original.note}</p>}
            {err && <ErrorState error={err} />}
            <section aria-label="Extracted content">
              <h3 className="h3">Extracted content</h3>
              {paras ? <div className="excerpt small">{paras.filter(Boolean).slice(0, 40).map((p, i) => <p key={i}>{p}</p>)}</div>
                : <dl className="meta small">{content.slice(0, 30).map(([k, v]) => (
                    <><dt key={`k${k}`}>{k}</dt><dd key={`v${k}`}>{String(v)}</dd></>))}</dl>}
            </section>
            <details className="small">
              <summary>Technical metadata</summary>
              <dl className="meta mono"><dt>Record</dt><dd>{d.record_id}</dd><dt>SHA-256</dt><dd>{d.content_sha256}</dd>
                <dt>Parser</dt><dd>{d.parser_version}</dd></dl>
            </details>
          </div>
        );
      })()}
    </Drawer>
  );
}
