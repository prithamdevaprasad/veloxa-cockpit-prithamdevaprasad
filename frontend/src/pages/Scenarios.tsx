import { useState } from "react";
import { api, ApiError } from "../api/client";
import type { Scenario, ScenarioStatement as Statement } from "../api/types";
import { useApi } from "../api/useApi";
import { Badge, Button, EmptyState, ErrorState, Loading, PageHead, Section } from "../ui";

function List({ title, items, tone }: { title: string; items: Statement[] | string[]; tone: string }) {
  return (
    <div className={`callout ${tone}`} style={{ marginBottom: 10 }}>
      <strong>{title}</strong>
      {items.length === 0 ? <p className="small muted">None.</p> : (
        <ul className="bullets small">{items.map((s, i) => typeof s === "string" ? <li key={i}>{s}</li> : (
          <li key={i}>{s.statement}{s.calculation && <span className="muted"> · {s.calculation}</span>}
            {s.evidence && s.evidence.length > 0 && <span className="muted"> · {s.evidence.length} source(s)</span>}</li>
        ))}</ul>
      )}
    </div>
  );
}

/** Deterministic "what if" analysis: observed facts, derived implications, the assumption and the unknowns are
 * shown separately. No probability, score or ranking exists to show. */
export function ScenariosPage() {
  const [list, reload] = useApi((s) => api.scenarioSubjects(s), []);
  const [object, setObject] = useState("");
  const [shift, setShift] = useState(30);
  const [result, setResult] = useState<Scenario | null>(null);
  const [error, setError] = useState<ApiError | null>(null);

  async function run(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try { setResult(await api.commitmentShift(object, shift)); }
    catch (err) { setError(err instanceof ApiError ? err : new ApiError(0, "unknown", String(err))); }
  }

  return (
    <>
      <PageHead title="What-if Analysis" intro="What follows if a commitment moves. Facts, arithmetic, your assumption and the
        unknowns are kept apart. Veloxa does not predict and shows no probability." />
      <Section title="Move a commitment" subtitle={list.state === "ready" ? list.data.read_basis.banner : undefined}>
        {list.state === "loading" && <Loading lines={2} />}
        {list.state === "error" && <ErrorState error={list.error} onRetry={reload} />}
        {list.state === "ready" && (list.data.commitments.length === 0 ? (
          <EmptyState title="No named commitments">The admitted evidence names no commitment to analyse.</EmptyState>
        ) : (
          <form className="row" onSubmit={run}>
            <label className="field">Commitment
              <select className="input" value={object} onChange={(e) => setObject(e.target.value)} required>
                <option value="" disabled>Select</option>
                {list.data.commitments.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </label>
            <label className="field">Shift (days)
              <input className="input" type="number" min={-730} max={730} value={shift}
                     onChange={(e) => setShift(Number(e.target.value))} />
            </label>
            <Button variant="primary" type="submit" disabled={!object}>Analyse</Button>
          </form>
        ))}
      </Section>
      {error && <Section><ErrorState error={error} /></Section>}
      {result && (
        <Section title={result.business_object} subtitle={result.read_basis.banner}>
          <List title="Observed (from records)" items={result.observed} tone="info" />
          <List title="Derived (arithmetic)" items={result.derived} tone="" />
          <List title="Assumption (your input, not evidence)" items={result.assumptions} tone="warn" />
          <List title="Unknown" items={result.unknowns} tone="bad" />
        </Section>
      )}

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
