import { useState } from "react";
import { api, ApiError } from "../api/client";
import type { Scenario, ScenarioStatement as Statement } from "../api/types";
import { useApi } from "../api/useApi";
import { Button, EmptyState, ErrorState, Loading, PageHead, Section } from "../ui";

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
      <PageHead title="Scenarios" intro="What follows if a commitment moves. Facts, arithmetic, your assumption and the
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
