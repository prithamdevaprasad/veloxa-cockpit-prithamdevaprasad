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
import { Guidance, Icon } from "../ui/kit";
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
function Item({ x, groupSize, bare }: { x: WorryItem; groupSize: number; bare?: boolean }) {
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
      {!bare && <strong className="concern-title">{x.title}</strong>}
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
        <Button variant="ghost" onClick={() => ask.ask(`Explain this concern: "${x.title}". Why does it matter, what `
          + "evidence supports it, and what is still unknown?", { kind: "concern", subject: x.title }, true)}>
          Discuss in chat</Button>
        <Button variant="ghost" onClick={() => ask.task({ summary: x.follow_up || `Follow up: ${x.title}`,
          description: `From the Veloxa concern "${x.title}".` })}>Create Jira task</Button>
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

const TONE: Record<number, string> = { 1: "high", 2: "medium" };
const reasonOf = (x: WorryItem) => {
  const m = Object.values(x.severity.criteria).find((c) => c.status === "measured");
  const u = x.unknowns[0];
  return m ? m.reason.charAt(0).toUpperCase() + m.reason.slice(1) + "."
    : u ? u.charAt(0).toUpperCase() + u.slice(1) : "The records show this but do not yet measure its impact.";
};

/** One concern as a card: what it is and its attention group; the full assessment opens in place. */
function ConcernCard({ x, groupSize }: { x: WorryItem; groupSize: number }) {
  const [open, setOpen] = useState(() => new URLSearchParams(window.location.hash.split("?")[1] ?? "").get("item") === x.item_id);
  return (
    <li className={`concern-card tone-${TONE[x.rank] ?? "low"}${open ? " open" : ""}`} id={`worry-${x.item_id}`}>
      <button className="concern-head" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span className="concern-icon"><Icon name={x.department === "Engineering" || /release|deliver/i.test(x.title)
          ? "timeline" : /hir|notice|on-call|person/i.test(x.title) ? "agents" : "alert"} size={22} /></span>
        <span className="concern-text"><strong>{x.title}</strong><span>{reasonOf(x)}</span></span>
        <span className={`pill sev-${TONE[x.rank] ?? "low"}`}>{groupLabel(x.rank).replace(" group", "")}</span>
        <span className="chev" aria-hidden="true">›</span>
      </button>
      {open && <ol className="plain concern-body"><Item x={x} groupSize={groupSize} bare /></ol>}
    </li>);
}

function NextSteps({ steps }: { steps: { text: string; tag: string }[] }) {
  const ask = useAsk();
  const [picked, setPicked] = useState<Set<number>>(new Set());
  if (!steps.length) return <p className="small muted">The records suggest no next step yet.</p>;
  const chosen = [...picked].map((i) => steps[i]);
  return (
    <>
      <ul className="plain next-steps">{steps.map((s, i) => (
        <li key={s.text}><label>
          <input type="checkbox" checked={picked.has(i)} onChange={() => {
            const n = new Set(picked); if (n.has(i)) n.delete(i); else n.add(i); setPicked(n); }} />
          <span>{s.text}</span></label>{s.tag && <span className="pill">{s.tag}</span>}</li>))}</ul>
      <Button variant="primary" className="btn wide-btn" disabled={!chosen.length} onClick={() => ask.task(chosen.length === 1
        ? { summary: chosen[0].text.slice(0, 250), description: "From Veloxa: What should I worry about?" }
        : { summary: `Follow-ups from What to worry about (${chosen.length})`,
            description: "From Veloxa: What should I worry about?\n" + chosen.map((c) => `- ${c.text}`).join("\n") })}>
        + Create {chosen.length > 1 ? "a Jira task for these" : "a Jira task"}</Button>
    </>);
}

export function WorryPage() {
  const [w, reload] = useApi((s) => product.worry(undefined, s), []);
  useScrollToItem(w.state === "ready");
  const ready = w.state === "ready" ? w.data : null;
  const ranked = ready && ready.ranking_mode !== "pending" ? ready.ranked : [];
  const unrankable = ready?.unrankable ?? [];
  const count = (r: (n: number) => boolean) => ranked.filter((x) => r(x.rank)).length;
  const top = ranked[0];
  const steps = [...ranked, ...unrankable].filter((x) => x.follow_up)
    .map((x) => ({ text: x.follow_up!, tag: x.project ?? x.department ?? "" }))
    .filter((s, i, all) => all.findIndex((o) => o.text === s.text) === i);
  const records = new Set([...ranked, ...unrankable].flatMap((x) => x.evidence)).size;
  return (
    <>
      <div className="worry-hero">
        <PageHead title="What should I worry about?" intro="A plain-English view of the main issues in your company
          records: what they mean, the evidence, and what to do next." />
        {ready && <div className="basis-card small"><Icon name="timeline" size={18} />
          <span>{ready.read_basis.banner}</span></div>}
      </div>
      {ready && ranked.length > 0 && (
        <section className="in-short" aria-label="In short">
          <div className="in-short-text">
            <span className="small muted">In short</span>
            <h2>{count((r) => r === 1)} item{count((r) => r === 1) === 1 ? " needs" : "s need"} your attention first
              {top ? `, led by “${top.title}”` : ""}.</h2>
            {top && <p>{reasonOf(top)}</p>}
          </div>
          <div className="in-short-tiles">
            <div className="tile high"><strong>{count((r) => r === 1)}</strong><span>Highest attention</span></div>
            <div className="tile medium"><strong>{count((r) => r === 2)}</strong><span>Next attention</span></div>
            <div className="tile"><strong>{count((r) => r > 2)}</strong><span>Further attention</span></div>
            <div className="tile plain"><strong>{unrankable.length}</strong><span>Not enough evidence</span></div>
          </div>
        </section>)}
      <div className="worry-grid">
        <section className="card" aria-label="Ranked worries">
          <h2 className="with-icon"><Icon name="alert" size={22} /> Items needing your attention{ranked.length ? ` (${ranked.length})` : ""}</h2>
          {w.state === "loading" && <Loading lines={5} />}
          {w.state === "error" && <ErrorState error={w.error} onRetry={reload} />}
          {ready && (ready.ranking_mode === "pending"
            ? <Guidance title="Not ranked yet">{ready.note}</Guidance>
            : ranked.length === 0 ? <Guidance title="Nothing to rank">No item meets the evidence gate.</Guidance>
              : <ul className="plain concern-list">{ranked.map((x) => (
                  <ConcernCard key={x.item_id} x={x} groupSize={ranked.filter((y) => y.rank === x.rank).length} />))}</ul>)}
          {ready && ranked.length > 1 && new Set(ranked.map((x) => x.rank)).size < ranked.length &&
            <p className="small muted">Concerns in the same attention group can't be reliably ordered against each
              other from the current evidence.</p>}
        </section>
        <div className="worry-side">
          <section className="card" aria-label="What's driving this view">
            <h3>What's driving this view?</h3>
            <p className="small muted">Only admitted company records you may read. {ready ? `${records} record${records === 1 ? "" : "s"} support these concerns.` : ""}</p>
            {ready && <ul className="plain check-list small">{[...new Set([...ranked, ...unrankable]
              .map((x) => x.department).filter(Boolean))].map((d) => <li key={d!}><Icon name="check" size={16} /> {d} records</li>)}
              <li><Icon name="check" size={16} /> Jira, documents and approved uploads</li></ul>}
            {ready && <p className="small muted">Weights: {Object.entries((ready.policy as { weights?: Record<string, number> }).weights ?? {})
              .map(([k, v]) => `${CRIT[k]} ${pct(v)}`).join(", ") || "not recorded"}. Severity is an ordering, never a probability.</p>}
          </section>
          <section className="card" aria-label="What to do next">
            <h3>What to do next</h3>
            <NextSteps steps={steps} />
          </section>
        </div>
      </div>
      <div className="worry-grid lower">
        <Section title="Not enough evidence to assess" aria-label="Unrankable"
                 subtitle="Unassessed does not mean unimportant or safe: the records lack what is needed to judge it.">
          {ready && ((unrankable.length + (ready.unranked?.length ?? 0)) === 0
            ? <p className="small muted">None.</p>
            : <ul className="plain">{unrankable.map((u) => <Unranked key={u.item_id} u={u} />)}
                {(ready.unranked ?? []).map((u) => <li key={u.item_id} className="worry-item">{u.title}</li>)}</ul>)}
        </Section>
        <Section title="Findings and notifications" aria-label="Findings">
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
