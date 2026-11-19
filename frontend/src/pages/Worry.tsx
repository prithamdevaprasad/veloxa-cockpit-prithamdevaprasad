// "What should I worry about?": the D-15 v1 weighted severity ranking (recorded decision content only), every
// criterion's measure, weight and contribution inspectable; items that cannot be ranked listed with the reason;
// findings with who was notified and their lifecycle. Severity is an ordinal interval, never a probability.
import { useEffect, useState } from "react";
import { product } from "../api/product";
import type { Finding, WorryItem } from "../api/product-types";
import { useApi } from "../api/useApi";
import { useAsk } from "../ui/Ask";
import { useEvidence } from "../ui/EvidenceDrawer";
import { ExplanationView, useExplanation } from "../ui/Explanation";
import { Guidance } from "../ui/kit";
import { Badge, Button, ErrorState, Loading, PageHead, Section } from "../ui";

const CRIT: Record<string, string> = { C1: "Delivery", C2: "Financial", C3: "Continuity", C4: "Reporting integrity" };
const pct = (x: number) => `${Math.round(x * 100)}%`;

const NO_MEASURE = "no evidence-backed measure";       // a criterion this concern never declared (not its unknown)
const WHY: Record<string, string> = {
  C1: "A committed delivery date has moved.", C2: "Revenue or contract value is exposed.",
  C3: "The work depends on very few people.",
  C4: "Reported figures disagree, so decisions may rest on an unconfirmed number.",
};
export const groupLabel = (rank: number) =>
  rank === 1 ? "Highest attention group" : rank === 2 ? "Next attention group" : "Further attention";

/** One concern: the executive view first; the D-15 calculation one click away. */
function Item({ x, groupSize }: { x: WorryItem; groupSize: number }) {
  const [explain, setExplain] = useState(false);
  const evidence = useEvidence();
  const ask = useAsk();
  const ex = useExplanation(async () => (await product.explainWorry(x.item_id)).explanation, [x.item_id], explain);
  const s = x.severity;
  const crit = Object.entries(s.criteria);
  const established = crit.filter(([, v]) => v.status === "measured").map(([, v]) => v.reason);
  const why = crit.filter(([, v]) => v.status === "measured").map(([c]) => WHY[c]).filter(Boolean);
  const unknown = [...x.unknowns, ...crit.filter(([, v]) => ["missing", "unquantified", "unmapped"].includes(v.status)
    && v.reason !== NO_MEASURE).map(([, v]) => v.reason)];
  return (
    <li className="worry-item concern" id={`worry-${x.item_id}`}>
      <strong className="concern-title">{x.title}</strong>
      {(x.department || x.project) && <div className="small muted">{[x.department, x.project].filter(Boolean).join(" · ")}</div>}
      {established.length > 0 && <p className="small">{established.map((t) => t.charAt(0).toUpperCase() + t.slice(1)).join(". ")}.</p>}
      {why.length > 0 && <><h4>Why it matters</h4><p className="small">{why.join(" ")}</p></>}
      {unknown.length > 0 && <><h4>What remains unknown</h4>
        <ul className="bullets small">{[...new Set(unknown)].map((u) => <li key={u}>{u.charAt(0).toUpperCase() + u.slice(1)}</li>)}</ul></>}
      {x.follow_up && <><h4>Suggested next step</h4><p className="small">{x.follow_up}</p></>}
      <div className="row" style={{ gap: 8 }}>
        <Button onClick={() => setExplain(!explain)} aria-expanded={explain}>{explain ? "Hide explanation" : "Explain concern"}</Button>
        <Button variant="ghost" disabled={!x.evidence.length} onClick={() => evidence.open(x.evidence[0], x.title)}>
          View evidence ({x.evidence.length})</Button>
        <Button variant="ghost" onClick={() => ask.ask(`What do the records show about "${x.title}", and what is still unknown?`)}>
          Ask about this</Button>
      </div>
      {explain && <ExplanationView ex={ex} />}
      <details className="small assess">
        <summary>How Veloxa assessed this</summary>
        <p>Assessed with the approved D-15 method: four criteria with explicit weights. D-15 position {x.rank}
          {groupSize > 1 ? `, shared with ${groupSize - 1} other concern${groupSize > 2 ? "s" : ""} whose exposure ranges overlap, so the evidence cannot order them` : ""}.
          Exposure range {s.low.toFixed(2)} to {s.high.toFixed(2)} on a 0 to 1 scale: an ordering of exposure, not a probability.
          {` ${pct(s.measured_weight)} of the method's weight is measured or not applicable.`}
          {x.sensitive ? " Its position changes if the weights move by 5 points." : ""}</p>
        <div className="table-wrap"><table className="crit-table">
          <thead><tr><th>Criterion</th><th>State</th><th>Measure</th><th>Value</th><th>Weight</th>
            <th>Contribution</th><th>Why</th></tr></thead>
          <tbody>{crit.map(([c, v]) => (
            <tr key={c}>
              <td>{c} {CRIT[c]}</td>
              <td>{v.reason === NO_MEASURE ? "not declared" : v.status.replace(/_/g, " ")}</td>
              <td className="mono small">{v.measure ?? "—"}</td>
              <td>{v.status === "measured" ? `${v.low}${v.high !== v.low ? `–${v.high}` : ""}`
                : v.status === "not_applicable" ? "n/a" : "unknown"}</td>
              <td>{pct(v.weight)}</td>
              <td>{v.contribution_low.toFixed(2)}–{v.contribution_high.toFixed(2)}</td>
              <td className="small muted">{v.reason}</td>
            </tr>))}</tbody>
        </table></div>
        <p className="muted">Unknown criteria count as their whole range (0 to their weight), never as zero.</p>
      </details>
    </li>
  );
}

/** Ranked concerns grouped by D-15 band: concerns in one band are presented together, never as "1 tied" each. */
function Groups({ items }: { items: WorryItem[] }) {
  const bands = [...new Set(items.map((x) => x.rank))].sort((a, b) => a - b);
  return (
    <div className="stack">{bands.map((r) => {
      const group = items.filter((x) => x.rank === r);
      return (
        <section key={r} aria-label={groupLabel(r)} className="attention-group">
          <h3>{groupLabel(r)} · {group.length} concern{group.length === 1 ? "" : "s"}</h3>
          {group.length > 1 && <p className="small muted">Current evidence does not allow Veloxa to reliably order these
            concerns individually.</p>}
          <ol className="plain">{group.map((x) => <Item key={x.item_id} x={x} groupSize={group.length} />)}</ol>
        </section>);
    })}</div>
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

function useScrollToItem(ready: boolean) {
  useEffect(() => {
    if (!ready) return;
    const id = new URLSearchParams(window.location.hash.split("?")[1] ?? "").get("item");
    if (id) document.getElementById(`worry-${id}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [ready]);
}

export function WorryPage() {
  const [w, reload] = useApi((s) => product.worry(undefined, s), []);
  useScrollToItem(w.state === "ready");
  return (
    <>
      <PageHead title="What should I worry about?" intro="The concerns the company records support, grouped by how
        much attention they need. Each one shows what is recorded, why it matters and what is still unknown." />
      <div className="ov-grid">
        <Section title="Needs attention" className="span-8" aria-label="Ranked worries">
          {w.state === "loading" && <Loading lines={5} />}
          {w.state === "error" && <ErrorState error={w.error} onRetry={reload} />}
          {w.state === "ready" && (w.data.ranking_mode === "pending"
            ? <Guidance title="Not ranked yet">{w.data.note}</Guidance>
            : w.data.ranked.length === 0 ? <Guidance title="Nothing to rank">No item meets the evidence gate.</Guidance>
              : <Groups items={w.data.ranked} />)}
          {w.state === "ready" && <p className="small muted" style={{ marginTop: 8 }}>{w.data.read_basis.banner}.
            Weights: {Object.entries((w.data.policy as { weights?: Record<string, number> }).weights ?? {})
              .map(([k, v]) => `${k} ${CRIT[k]} ${pct(v)}`).join(", ") || "not recorded"}. Probabilities are disabled.</p>}
        </Section>
        <Section title="Not enough evidence to assess" className="span-4" aria-label="Unrankable"
                 subtitle="Unassessed does not mean unimportant or safe: the records lack what is needed to judge it.">
          {w.state === "ready" && ((w.data.unrankable ?? w.data.unranked ?? []).length === 0
            ? <p className="small muted">None.</p>
            : <ul className="plain">{(w.data.unrankable ?? []).map((u) => <Unranked key={u.item_id} u={u} />)}
                {(w.data.unranked ?? []).map((u) => <li key={u.item_id} className="worry-item">{u.title}</li>)}</ul>)}
        </Section>
        <Section title="Findings and notifications" className="span-12" aria-label="Findings">
          <Findings />
        </Section>
      </div>
    </>
  );
}

function Unranked({ u }: { u: { item_id: string; title: string; reasons: string[]; evidence: string[];
                                  unknowns: string[]; follow_up?: string } }) {
  const [more, setMore] = useState(false);
  const ex = useExplanation(async () => (await product.explainWorry(u.item_id)).explanation, [u.item_id], more);
  return (
    <li className="worry-item concern compact" id={`worry-${u.item_id}`}>
      <strong>{u.title}</strong>
      <p className="small muted">Recorded in {u.evidence.length} record{u.evidence.length === 1 ? "" : "s"}.{" "}
        {u.unknowns[0] ?? "The records do not establish enough information to assess this concern."}</p>
      {u.follow_up && <p className="small"><strong>What would help:</strong> {u.follow_up}</p>}
      <button className="link small" aria-expanded={more} onClick={() => setMore(!more)}>
        {more ? "Hide explanation" : "Why it can't be assessed"}</button>
      {more && <><p className="small muted">{u.reasons.join("; ")}.</p><ExplanationView ex={ex} /></>}
    </li>
  );
}
