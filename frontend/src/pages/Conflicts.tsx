import { useState } from "react";
import { api } from "../api/client";
import { product } from "../api/product";
import { ExplanationView, useExplanation } from "../ui/Explanation";
import { useApi } from "../api/useApi";
import type { Conflict } from "../api/types";
import { Badge, Drawer, EmptyState, ErrorState, Loading, PageHead, Pager, Section, usePage } from "../ui";

/** The conflict register: every disagreement visible at a knowledge point, each side with its provenance and time.
 * No side is chosen; resolution is a human decision. */
export function ConflictsPage() {
  const [asKnownAt, setAsKnownAt] = useState("");
  const [reg, reload] = useApi((s) => api.conflicts(asKnownAt || undefined, s), [asKnownAt]);
  const [open, setOpen] = useState<Conflict | null>(null);
  const page = usePage(reg.state === "ready" ? reg.data.conflicts : [], 10);
  return (
    <>
      <PageHead title="Conflict register" intro="Where admitted sources disagree. Values are shown side by side with
        where and when each was stated; Veloxa never picks the correct side.">
        <label className="field" style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
          Known as at
          <input className="input" type="date" value={asKnownAt} onChange={(e) => setAsKnownAt(e.target.value)}
                 aria-label="Register as known at date" />
        </label>
      </PageHead>
      <Section title="Disagreements" subtitle={reg.state === "ready" ? reg.data.read_basis.banner : undefined}>
        {reg.state === "loading" && <Loading />}
        {reg.state === "error" && <ErrorState error={reg.error} onRetry={reload} />}
        {reg.state === "ready" && (reg.data.conflicts.length === 0 ? (
          <EmptyState title="No disagreements">{reg.data.unknowns[0] ?? "No source disagreement is visible at this knowledge point."}</EmptyState>
        ) : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Subject</th><th>Values</th><th>Match</th><th>Status</th></tr></thead>
              <tbody>
                {page.items.map((c) => (
                  <tr key={c.conflict_id} className="clickable" tabIndex={0} onClick={() => setOpen(c)}
                      onKeyDown={(e) => e.key === "Enter" && setOpen(c)}>
                    <td>{c.subject}</td>
                    <td>{c.claims.map((s) => <div key={s.record_id}>{s.value} <span className="muted small">· {s.source}</span></div>)}</td>
                    <td><Badge value={c.metric_match} /></td>
                    <td><Badge value={c.status} label="open (unresolved)" /></td>
                  </tr>
                ))}
              </tbody>
            </table>
            <Pager page={page} />
          </div>
        ))}
      </Section>
      <Registry />
      {open && (
        <Drawer title={open.subject} onClose={() => setOpen(null)}>
          <p className="small muted">Definition {open.definition}; period {open.period.replace(/_/g, " ")}. Status: open. No value is chosen.</p>
          {open.claims.map((s) => (
            <div key={s.record_id} className="evidence-item">
              <h3>{s.value}</h3>
              <p className="small muted">{s.source}{s.period ? ` · period ${s.period}` : ""}</p>
              <dl className="small" style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "2px 12px", marginTop: 8 }}>
                <dt className="muted">Source</dt><dd className="mono" style={{ margin: 0, wordBreak: "break-all" }}>{s.provenance?.source_path ?? s.record_id}</dd>
                <dt className="muted">Batch K</dt><dd style={{ margin: 0 }}>{s.provenance?.batch_seq ?? "–"} (version {s.provenance?.version ?? "–"})</dd>
                <dt className="muted">Recorded</dt><dd style={{ margin: 0 }}>{s.time?.recorded_day ?? "not stated"} ({s.time?.recorded_basis ?? "–"})</dd>
                <dt className="muted">Valid</dt><dd style={{ margin: 0 }}>{s.time?.valid_from ? `${s.time.valid_from} → ${s.time.valid_to ?? "open"}` : "not stated"}</dd>
                <dt className="muted">Extracted</dt><dd style={{ margin: 0 }}>{s.time?.extracted_at ?? "–"}</dd>
              </dl>
            </div>
          ))}
        </Drawer>
      )}
    </>
  );
}

/** The Conflict Registry (claim level): each disagreement explained in plain English, with every value kept. */
function Registry() {
  const [reg, reload] = useApi((s) => product.registry(s), []);
  return (
    <Section title="Disagreements between sources, explained" aria-label="Conflict Registry"
             subtitle="What each source says, how Veloxa treats it, and what remains uncertain.">
      {reg.state === "loading" && <Loading lines={2} />}
      {reg.state === "error" && <ErrorState error={reg.error} onRetry={reload} />}
      {reg.state === "ready" && (reg.data.conflicts.length === 0
        ? <EmptyState title="No recorded disagreements">No two admitted sources disagree about the same figure at this
            knowledge point{reg.data.hidden_for_access ? `; ${reg.data.hidden_for_access} you may not read are hidden` : ""}.</EmptyState>
        : <ul className="plain stack">{reg.data.conflicts.map((c) => <RegistryItem key={c.conflict_id} id={c.conflict_id}
            title={c.subject.startsWith("metric:") ? `${c.subject.slice(7).toUpperCase()} reported by different sources`
              : `${c.predicate.replace(/_/g, " ")} for ${c.subject}`}
            values={c.claims.map((x) => x.display ?? (typeof x.object === "string" ? x.object : "value not shown"))} />)}</ul>)}
    </Section>
  );
}

function RegistryItem({ id, title, values }: { id: string; title: string; values: string[] }) {
  const ex = useExplanation(async () => (await product.explainConflict(id)).explanation, [id]);
  return (
    <li className="worry-item">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <strong>{title}</strong><span className="small muted">{values.length} differing values</span></div>
      <ExplanationView ex={ex} />
    </li>
  );
}
