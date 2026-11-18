import { useEffect, useState } from "react";
import { ApiError } from "../api/client";
import type { Basis, Explanation } from "../api/product-types";
import { useEvidence } from "./EvidenceDrawer";
import { ErrorState, Loading } from "./index";

/** Where a statement comes from, in words an executive reads without a glossary. */
const BASIS: Record<Basis, { tag: string; tone: string }> = {
  recorded: { tag: "From the records", tone: "info" },
  calculated: { tag: "Calculated by Veloxa", tone: "" },
  assumption: { tag: "Your assumption, not evidence", tone: "warn" },
  unknown: { tag: "Not established by the records", tone: "bad" },
  caveat: { tag: "How to read this", tone: "" },
};

/** Load the explanation of a verified result as soon as it exists (AI-assisted wording where enabled). */
export function useExplanation(load: () => Promise<Explanation>, deps: unknown[], enabled = true) {
  const [e, setE] = useState<Explanation | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [n, setN] = useState(0);
  useEffect(() => {
    if (!enabled) return;
    let live = true;
    setE(null); setError(null);
    load().then((x) => { if (live) setE(x); })
      .catch((x) => { if (live) setError(x instanceof ApiError ? x : new ApiError(0, "unknown", String(x))); });
    return () => { live = false; };
  }, [...deps, enabled, n]);  // eslint-disable-line react-hooks/exhaustive-deps
  return { e, error, loading: enabled && !e && !error, retry: () => setN((k) => k + 1) };
}

/**
 * One explanation experience. The plain-English text comes first; caveats and unknowns stay visible; the verified
 * wording, calculations and sources are one expandable section away. An HTTP error is shown as an error, never as an
 * explanation. `compact` (inside an answer) shows only the explanation and what the answer does not establish,
 * because the answer's own figures and citations are already on screen.
 */
export function ExplanationView({ ex, compact = false }: { ex: ReturnType<typeof useExplanation>; compact?: boolean }) {
  const evidence = useEvidence();
  if (ex.error) {
    return <div className="explain"><ErrorState error={ex.error} onRetry={ex.retry} /></div>;
  }
  if (!ex.e) return ex.loading ? <Loading lines={2} label="Preparing the explanation" /> : null;
  const e = ex.e;
  const w = e.wording;
  const ai = w.mode === "experimental_model";
  const say = (id: string, text: string) => (ai && w.items?.[id]) || text;
  const shown = e.sections.filter((s) => !compact || s.basis === "unknown" || s.basis === "caveat"
    || s.basis === "assumption");
  const sources = e.sections.flatMap((s) => s.items).filter((it) => it.evidence.length || it.detail);
  return (
    <div className="explain" aria-label="Explanation">
      <p className="explain-head">{(ai && w.summary) || e.summary || e.headline}</p>
      {shown.map((s) => (
        <section key={s.key} className={`explain-sec callout ${BASIS[s.basis].tone}`} aria-label={s.title}>
          <div className="explain-sec-head"><h4>{s.title}</h4>
            <span className="basis-tag">{BASIS[s.basis].tag}</span></div>
          <ul className="plain explain-items">{s.items.map((it) => (
            <li key={it.id}>{say(it.id, it.text)}
              {!compact && it.evidence.length > 0 && <button className="link small" onClick={() =>
                evidence.open(it.evidence[0], it.text)}>Source</button>}</li>))}</ul>
        </section>
      ))}
      {w.notice && <p className="small muted" role="note">{w.notice}</p>}
      {(sources.length > 0 || ai) && (
        <details className="explain-details">
          <summary>Evidence and verified details</summary>
          <ul className="plain explain-items">
            {e.sections.flatMap((s) => s.items.map((it) => (
              <li key={it.id}><span className="basis-tag">{BASIS[s.basis].tag}:</span> {it.text}
                {it.detail && <span className="small muted" style={{ display: "block" }}>{it.detail}</span>}
                {it.evidence.length > 0 && <button className="link small" onClick={() => evidence.open(it.evidence[0],
                  it.text)}>{it.evidence.length === 1 ? "View source" : `View sources (${it.evidence.length})`}</button>}
              </li>)))}
          </ul>
          {!compact && e.confidence && <p className="small muted">Confidence: {e.confidence}.</p>}
        </details>
      )}
    </div>
  );
}
