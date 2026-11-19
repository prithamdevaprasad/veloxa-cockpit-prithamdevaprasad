import { useState } from "react";
import { api, ApiError, get, request } from "../api/client";
import { useApi } from "../api/useApi";
import { Icon } from "../ui/kit";
import { Badge, Button, EmptyState, ErrorState, Loading, Section } from "../ui";


interface Subject { name: string; kind: string; date: string | null; original_date: string | null; status: string | null }
interface ChangeOut {
  subject: string; kind: string; baseline: { date: string | null; original_date: string | null; status: string | null };
  assumption: string; new_date: string | null; calculations: string[];
  affected_issues: { total: number; open: number; by_status: Record<string, number> };
  dependencies: { blocked_by: string; issues: number }[]; evidence: string[];
}
interface RunOut { changes: ChangeOut[]; order_after_changes: string | null; unknowns: string[]; isolated: boolean }
const day = (d: string | null) => (d ? new Date(d + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "long", year: "numeric" }) : "no recorded date");

/** What-if analysis over every release and named commitment in the admitted evidence. Several changes at once, by
 * free text or by rows; read-only and isolated: nothing is saved to company records or Jira; no probability. */
export function ScenariosPage() {
  const [subs, reload] = useApi((s) => get<{ subjects: Subject[] }>("/v1/scenarios/subjects", s), []);
  const [text, setText] = useState("");
  const [rows, setRows] = useState<{ subject: string; shift_days: number }[]>([]);
  const [out, setOut] = useState<RunOut | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);

  async function run(body: object) {
    setError(null); setBusy(true); setOut(null);
    try { setOut(await request<RunOut>("POST", "/v1/scenarios/run", body)); }
    catch (err) { setError(err instanceof ApiError ? err : new ApiError(0, "unknown", String(err))); }
    finally { setBusy(false); }
  }
  const list = subs.state === "ready" ? subs.data.subjects : [];
  const examples = list.length ? [`Move ${list[0].name} by 2 weeks`, `Move ${list[0].name} by 1 month`,
    list[1] ? `Move ${list[0].name} by 3 weeks and ${list[1].name} earlier by 5 days` : `Bring ${list[0].name} forward by 10 days`] : [];
  const go = (t: string) => { setText(t); void run({ text: t }); };
  return (
    <>
      <header className="page-hero">
        <span className="hero-icon"><Icon name="whatif" size={26} /></span>
        <div><h1>What-if Analysis</h1>
          <p>Test what happens if a release or commitment moves. Veloxa shows today's date, the new date, the work
            affected and what the records can't tell you. Nothing is saved to company records or Jira.</p></div>
      </header>
      <div className="feature-row">
        <div className="feature"><Icon name="timeline" size={22} /><div><strong>Change a date</strong>
          <span>{list[0] ? `e.g. move ${list[0].name} by 2 weeks` : "Move a release or commitment"}</span></div></div>
        <div className="feature"><Icon name="departments" size={22} /><div><strong>See the impact</strong>
          <span>On work, dependencies and timelines</span></div></div>
        <div className="feature"><Icon name="alert" size={22} /><div><strong>Know the limits</strong>
          <span>What Veloxa can and cannot tell you</span></div></div>
      </div>
      <section className="card scenario-input" aria-label="Describe a scenario">
        <h2>Describe a scenario</h2>
        {subs.state === "loading" && <Loading lines={2} />}
        {subs.state === "error" && <ErrorState error={subs.error} onRetry={reload} />}
        {subs.state === "ready" && (list.length === 0
          ? <EmptyState title="Nothing to analyse">The admitted evidence names no release or commitment.</EmptyState>
          : <div className="stack">
              <form className="scenario-form" onSubmit={(e) => { e.preventDefault(); if (text.trim()) void run({ text }); }}>
                <input className="input scenario-box" value={text} onChange={(e) => setText(e.target.value)}
                       aria-label="Scenario" placeholder={`e.g. move ${list[0].name} by 2 weeks`} />
                <Button variant="primary" type="submit" disabled={!text.trim() || busy}>{busy ? "Analysing…" : "Analyse"}</Button>
              </form>
              <div className="small muted">Releases and commitments in the records (click to adjust by days):</div>
              <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>{list.map((s) => (
                <button key={s.name} className="chip" title={s.date ? `Due ${day(s.date)}` : "No recorded date"}
                        onClick={() => setRows((r) => r.some((x) => x.subject === s.name) || r.length >= 6 ? r
                          : [...r, { subject: s.name, shift_days: 14 }])}>{s.name}{s.date ? ` · ${day(s.date)}` : ""}</button>))}</div>
              {rows.length > 0 && <div className="stack">
                {rows.map((r, i) => (
                  <div key={r.subject} className="row" style={{ alignItems: "center" }}>
                    <span style={{ minWidth: 180 }}><strong>{r.subject}</strong></span>
                    <label className="small">Shift in days (minus = earlier){" "}
                      <input className="input" type="number" min={-730} max={730} value={r.shift_days} style={{ width: 110 }}
                             onChange={(e) => setRows(rows.map((x, j) => j === i ? { ...x, shift_days: Number(e.target.value) } : x))} /></label>
                    <button className="link small" onClick={() => setRows(rows.filter((_, j) => j !== i))}>Remove</button>
                  </div>))}
                <div><Button variant="primary" disabled={busy} onClick={() => run({ changes: rows })}>Analyse these changes</Button></div>
              </div>}
            </div>)}
      </section>
      {!out && !busy && examples.length > 0 && (
        <div className="scenario-help">
          <section className="card" aria-label="Try one">
            <h3>Try one</h3>
            <ul className="plain example-list">{examples.map((x) => (
              <li key={x}><button onClick={() => go(x)}>{x}<span aria-hidden="true">›</span></button></li>))}</ul>
          </section>
          <section className="card soft" aria-label="What you get">
            <h3>You'll get a clear comparison</h3>
            <ul className="plain check-list">
              {["Today's date and the date if it moves", "What you assumed", "Work and dependencies affected",
                "What's blocking it", "What Veloxa can't tell you"].map((x) => (
                <li key={x}><Icon name="check" size={16} /> {x}</li>))}
            </ul>
          </section>
        </div>)}
      {error && <Section><ErrorState error={error} /></Section>}
      {out && (
        <section className="scenario-result" aria-label="Scenario result">
          <div className="scenario-banner">
            <strong>Scenario result</strong>
            <span>A hypothesis only. Nothing was saved to company records or Jira.</span>
          </div>
          {out.order_after_changes && <p className="scenario-order"><strong>New order:</strong> {out.order_after_changes}</p>}
          <div className="scenario-grid">
            {out.changes.map((c) => (
              <article key={c.subject} className="scenario-card" aria-label={`Scenario for ${c.subject}`}>
                <h2>{c.subject}</h2>
                <div className="scenario-dates">
                  <div><span className="lbl">Today's date</span><span className="big">{day(c.baseline.date)}</span></div>
                  <div className="arrow" aria-hidden="true">→</div>
                  <div><span className="lbl">If it moves</span><span className="big accent">{day(c.new_date)}</span></div>
                </div>
                <h4>What you assumed</h4>
                <p>{c.assumption}</p>
                {c.calculations.length > 0 && <><h4>What moves</h4>
                  <ul className="bullets">{c.calculations.map((x) => <li key={x}>{x}</li>)}</ul></>}
                <h4>Work affected</h4>
                <p>{c.affected_issues.total
                  ? <><strong className="big-inline">{c.affected_issues.open}</strong> of {c.affected_issues.total} tracker issues are still open.</>
                  : "No tracker issues are linked to this."}</p>
                {c.dependencies.length > 0 && <><h4>What's blocking it</h4>
                  <ul className="bullets">{c.dependencies.map((d) => <li key={d.blocked_by}><strong>{d.blocked_by}</strong> blocks {d.issues} issue{d.issues === 1 ? "" : "s"}</li>)}</ul></>}
              </article>))}
          </div>
          <div className="scenario-unknowns">
            <h4>What Veloxa can't tell you</h4>
            <ul className="bullets">{out.unknowns.map((u) => <li key={u}>{u}</li>)}</ul>
          </div>
        </section>)}
    </>
  );
}

const WARNING_LABEL: Record<string, string> = {
  no_stated_date: "No stated date", moved_without_replacement_date: "Moved, no new date",
  single_source: "Single source", shared_evidence: "Shared evidence", conflicted_evidence: "Conflicted evidence",
};

/** What each commitment rests on, and the early warnings those facts give. Not ranked and not scored. */
export function Dependencies() {
  const [g, reload] = useApi((s) => api.dependencies(s), []);
  return (
    <Section title="Dependencies and early warnings" aria-label="Dependencies"
             subtitle={g.state === "ready" ? g.data.read_basis.banner : undefined}>
      {g.state === "loading" && <Loading lines={2} />}
      {g.state === "error" && <ErrorState error={g.error} onRetry={reload} />}
      {g.state === "ready" && (() => {
        const commitments = g.data.nodes.filter((n) => n.type === "commitment");
        if (commitments.length === 0) {
          return <EmptyState title="No dependencies">{g.data.notes[0] ?? "The admitted evidence names no commitment."}</EmptyState>;
        }
        const edges = g.data.edges.filter((e) => e.relation === "stated_in");
        return (
          <div className="stack">
            <p className="small muted">Listed alphabetically. Warnings are facts about the evidence, not a priority
              order; nothing here is scored or given a probability.</p>
            <div className="table-wrap"><table>
              <thead><tr><th>Commitment</th><th>Rests on</th><th>Warnings</th></tr></thead>
              <tbody>{commitments.map((c) => {
                const recs = [...new Set(edges.filter((e) => e.from === c.id).map((e) => e.to))];
                const ws = g.data.warnings.filter((w) => `commitment:${w.commitment}` === c.id);
                return (
                  <tr key={c.id}>
                    <td>{c.label}</td>
                    <td className="small">{recs.length} record(s)</td>
                    <td>{ws.length === 0 ? <span className="small muted">None</span> : ws.map((w) => (
                      <div key={w.kind} className="small"><Badge value="open" label={WARNING_LABEL[w.kind] ?? w.kind} />{" "}
                        {w.statement} <span className="muted">· {w.evidence.length} source(s)</span></div>
                    ))}</td>
                  </tr>
                );
              })}</tbody>
            </table></div>
            <ul className="bullets small muted">{g.data.unknowns.map((u) => <li key={u}>{u}</li>)}</ul>
          </div>
        );
      })()}
    </Section>
  );
}
