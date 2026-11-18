// "What should I worry about?": the D-15 v1 weighted severity ranking (recorded decision content only), every
// criterion's measure, weight and contribution inspectable; items that cannot be ranked listed with the reason;
// findings with who was notified and their lifecycle. Severity is an ordinal interval, never a probability.
import { useState } from "react";
import { product } from "../api/product";
import type { Finding, WorryItem } from "../api/product-types";
import { useApi } from "../api/useApi";
import { useEvidence } from "../ui/EvidenceDrawer";
import { ExplanationView, useExplanation } from "../ui/Explanation";
import { Guidance } from "../ui/kit";
import { Badge, Button, ErrorState, Loading, PageHead, Section } from "../ui";

const CRIT: Record<string, string> = { C1: "Delivery", C2: "Financial", C3: "Continuity", C4: "Reporting integrity" };
const pct = (x: number) => `${Math.round(x * 100)}%`;

function Item({ x }: { x: WorryItem }) {
  const [open, setOpen] = useState(false);
  const evidence = useEvidence();
  const ex = useExplanation(async () => (await product.explainWorry(x.item_id)).explanation, [x.item_id]);
  const s = x.severity;
  return (
    <li className="worry-item">
      <div className="worry-head">
        <div className="rank-n" aria-label={`Rank ${x.rank}${x.tied ? ", tied" : ""}`}>{x.rank}
          {x.tied && <small>tied</small>}</div>
        <div>
          <strong>{x.title}</strong>
          <div className="small muted">{[x.department, x.project].filter(Boolean).join(", ")}
            {x.sensitive && <> <Badge value="open" label="Sensitive to weights" title="A ±5 point change in the weights
              moves this item between ranks" /></>}</div>
        </div>
        <div className="sev">
          <span className="small muted" title="An ordering of exposure on a 0 to 1 scale, never a probability">Exposure (not a probability)</span>
          <div><strong>{s.low.toFixed(2)}–{s.high.toFixed(2)}</strong></div>
          <div className="sev-bar" aria-hidden="true"><span style={{ left: `${s.low * 100}%`,
            width: `${Math.max(2, (s.high - s.low) * 100)}%` }} /></div>
        </div>
      </div>
      <ExplanationView ex={ex} />
      <div className="row">
        <Button variant="ghost" onClick={() => setOpen(!open)} aria-expanded={open}>
          {open ? "Hide the calculation" : "Show the calculation"}</Button>
        <Button variant="ghost" disabled={!x.evidence.length} onClick={() => evidence.open(x.evidence[0], x.title)}>
          View evidence ({x.evidence.length})</Button>
      </div>
      {open && (
        <div className="table-wrap"><table className="crit-table">
          <thead><tr><th>Criterion</th><th>State</th><th>Measure</th><th>Value</th><th>Weight</th>
            <th>Contribution</th><th>Why</th></tr></thead>
          <tbody>{Object.entries(s.criteria).map(([c, v]) => (
            <tr key={c}>
              <td>{c} {CRIT[c]}</td>
              <td><Badge value={v.status === "measured" ? "active" : v.status === "not_applicable" ? "" : "open"}
                         label={v.status.replace(/_/g, " ")} /></td>
              <td className="mono small">{v.measure ?? "—"}</td>
              <td>{v.status === "measured" ? `${v.low}${v.high !== v.low ? `–${v.high}` : ""}`
                : v.status === "not_applicable" ? "n/a" : "unknown"}</td>
              <td>{pct(v.weight)}</td>
              <td>{v.contribution_low.toFixed(2)}–{v.contribution_high.toFixed(2)}</td>
              <td className="small muted">{v.reason}</td>
            </tr>))}</tbody>
        </table>
        <p className="small muted" style={{ marginTop: 6 }}>Unknown criteria count as the whole range (0 to their
          weight), never as zero. Measured or not-applicable weight: {pct(s.measured_weight)}.</p>
        {x.unknowns.length > 0 && <ul className="bullets small">{x.unknowns.map((u) => <li key={u}>{u}</li>)}</ul>}
        {x.follow_up && <p className="small" style={{ marginTop: 6 }}>Suggested follow-up: {x.follow_up}</p>}
        </div>
      )}
    </li>
  );
}

function Findings() {
  const [f, reload] = useApi((s) => product.findings(s), []);
  if (f.state === "loading") return <Loading lines={3} />;
  if (f.state === "error") return <ErrorState error={f.error} onRetry={reload} />;
  if (!f.data.findings.length) return <Guidance title="No findings yet">Findings appear when new evidence is admitted
    or the daily review runs.</Guidance>;
  return (
    <ul className="plain">{f.data.findings.map((x: Finding) => (
      <li key={x.finding_id} className="worry-item">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <strong>{x.title}</strong><Badge value={x.state === "notified" ? "open" : "active"} label={x.state} />
        </div>
        <span className="small muted">Notified: {x.notified.map((n) => n.recipient.startsWith("queue:")
          ? `${n.recipient.slice(6)} queue` : "CEO / department head").join(", ") || "nobody yet"}.
          Trigger: {x.trigger}. Confidence: {x.confidence}.</span>
        {x.state === "notified" && (
          <div className="row"><Button onClick={async () => { await product.findingEvent(x.finding_id, "acknowledged");
            reload(); }}>Acknowledge</Button>
            <Button variant="ghost" onClick={async () => { await product.findingEvent(x.finding_id, "investigating");
              reload(); }}>Mark as investigating</Button></div>)}
      </li>))}</ul>
  );
}

export function WorryPage() {
  const [w, reload] = useApi((s) => product.worry(undefined, s), []);
  return (
    <>
      <PageHead title="What should I worry about?" intro="Ranked by evidence-backed severity under the completed D-15
        method: four criteria with explicit weights. Unknowns widen an item's range instead of lowering it, and items
        whose ranges overlap share a rank." />
      <div className="ov-grid">
        <Section title="Ranked" className="span-8" aria-label="Ranked worries">
          {w.state === "loading" && <Loading lines={5} />}
          {w.state === "error" && <ErrorState error={w.error} onRetry={reload} />}
          {w.state === "ready" && (w.data.ranking_mode === "pending"
            ? <Guidance title="Not ranked yet">{w.data.note}</Guidance>
            : w.data.ranked.length === 0 ? <Guidance title="Nothing to rank">No item meets the evidence gate.</Guidance>
              : <ol className="plain">{w.data.ranked.map((x) => <Item key={x.item_id} x={x} />)}</ol>)}
          {w.state === "ready" && <p className="small muted" style={{ marginTop: 8 }}>{w.data.read_basis.banner}.
            Weights: {Object.entries((w.data.policy as { weights?: Record<string, number> }).weights ?? {})
              .map(([k, v]) => `${k} ${CRIT[k]} ${pct(v)}`).join(", ") || "not recorded"}. Probabilities are disabled.</p>}
        </Section>
        <Section title="Cannot be ranked yet" className="span-4" aria-label="Unrankable"
                 subtitle="Visible, with the reason, so nothing is hidden by the ranking.">
          {w.state === "ready" && ((w.data.unrankable ?? w.data.unranked ?? []).length === 0
            ? <p className="small muted">None.</p>
            : <ul className="plain">{(w.data.unrankable ?? []).map((u) => (
                <li key={u.item_id} className="worry-item"><strong>{u.title}</strong>
                  <span className="small muted">{u.reasons.join("; ")}</span></li>))}
                {(w.data.unranked ?? []).map((u) => <li key={u.item_id} className="worry-item">{u.title}</li>)}</ul>)}
        </Section>
        <Section title="Findings and notifications" className="span-12" aria-label="Findings">
          <Findings />
        </Section>
      </div>
    </>
  );
}
