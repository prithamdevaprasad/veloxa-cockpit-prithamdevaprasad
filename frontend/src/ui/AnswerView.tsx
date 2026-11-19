// Presents a verified Answer as a brief: direct answer, key figures, statements, caveats, then evidence.
// This is a PRESENTATION layer only. Every figure and sentence comes from the API's verified claims; the layout
// regroups them (e.g. a department breakdown) and rewrites a few fixed system notes into plain English. Nothing is
// added, inferred or dropped: each claim keeps its citations, and the full claim text stays in the accessible text.
import { useState } from "react";
import { product } from "../api/product";
import type { Answer, Claim, Evidence } from "../api/types";
import { useAsk } from "./Ask";
import { useEvidence } from "./EvidenceDrawer";
import { ExplanationView, useExplanation } from "./Explanation";
import { BarChart } from "./kit";
import { Badge, Drawer } from "./index";

const STATUS: Record<string, { label: string; tone: string }> = {
  answered: { label: "Answer", tone: "ok" }, qualified: { label: "Qualified answer", tone: "info" },
  conflict: { label: "The sources disagree", tone: "warn" }, abstained: { label: "Not enough evidence", tone: "" },
  refused: { label: "Refused", tone: "bad" },
};
const FACTS_ONLY = /^facts only/i;

/** Splits the backend interpretation ("I read this as: X. Time: Y.") into its parts. */
function readInterpretation(s: string): { subject: string | null; time: string | null } {
  const m = /^I read this as:\s*(.*?)\.\s*(?:Time:\s*(.*?)\.?)?$/s.exec(s.trim());
  return m ? { subject: m[1] || null, time: m[2] ?? null } : { subject: null, time: null };
}
const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
const clean = (s: string) => s.replace(/[⟦⟧]/g, "");

/** Fixed system notes rewritten in plain English; anything unrecognised is shown as returned. */
function plain(note: string): string {
  const more = /^(\d+) further matching fact\(s\) are not shown \(at most (\d+) claims per answer\)/.exec(note);
  if (more) return `${more[1]} more ${more[1] === "1" ? "result is" : "results are"} not shown: an answer lists at most ` +
    `${more[2]} facts. Ask a narrower question to see the rest.`;
  return cap(note);
}

type Fig = { claim: Claim; label: string; value: string; num: number; note: string | null };
const NUM = /^(.*?):\s*((?:[A-Z]{3}\s)?-?[\d,]+(?:\.\d+)?(?:\s?(?:%|months?|days?|people))?)\s*(?:\(([^)]*)\))?\.?$/s;

function figureOf(c: Claim): Fig | null {
  const m = NUM.exec(clean(c.text).trim());
  if (!m) return null;
  const num = Number(m[2].replace(/[^\d.-]/g, ""));
  return Number.isFinite(num) ? { claim: c, label: m[1].trim(), value: m[2], num, note: m[3] ?? null } : null;
}

/** Groups figures that share everything but their last ", dimension value" part into one breakdown. */
function arrange(claims: Claim[]) {
  const singles: Fig[] = [], text: Claim[] = [];
  const groups = new Map<string, { fig: Fig; tail: string }[]>();
  for (const c of claims) {
    const f = figureOf(c);
    if (!f) { text.push(c); continue; }
    const cut = f.label.lastIndexOf(", ");
    if (cut < 0) { singles.push(f); continue; }
    const head = f.label.slice(0, cut);
    groups.set(head, [...(groups.get(head) ?? []), { fig: f, tail: f.label.slice(cut + 2) }]);
  }
  const breakdowns: { title: string; rows: { fig: Fig; label: string }[] }[] = [];
  for (const [head, rows] of groups) {
    const first = rows[0].tail.split(" ")[0];
    const shared = rows.every((r) => r.tail.split(" ")[0] === first && /^[a-z_]+ \S/.test(r.tail));
    // A "dimension value" suffix (e.g. "department Testing") is a breakdown even with one row; otherwise two needed.
    if (rows.length < 2 && !shared) { singles.push(...rows.map((r) => r.fig)); continue; }
    breakdowns.push({ title: shared ? `${head}, by ${first}` : head,
                      rows: rows.map((r) => ({ fig: r.fig, label: shared ? r.tail.slice(first.length + 1) : r.tail })) });
  }
  // Identical passages from different records read as one statement with all their citations.
  const merged: Claim[] = [];
  for (const c of text) {
    const same = merged.find((m) => clean(m.text).trim() === clean(c.text).trim());
    if (same) same.evidence = [...new Set([...same.evidence, ...c.evidence])];
    else merged.push({ ...c, evidence: [...c.evidence] });
  }
  return { singles, breakdowns, text: merged };
}

function where(e: Evidence): string {
  const loc = (e.locator ?? {}) as Record<string, unknown>;
  const chunk = loc.chunk as Record<string, unknown> | undefined;
  const parts: string[] = [];
  if (loc.sheet) parts.push(`sheet ${loc.sheet}`);
  if (loc.row) parts.push(`row ${loc.row}`);
  if (chunk?.page) parts.push(`page ${chunk.page}`);
  if (chunk?.paragraph) parts.push(`paragraph ${chunk.paragraph}`);
  return parts.join(", ") || "whole document";
}

export function AnswerView({ answer }: { answer: Answer }) {
  const [open, setOpen] = useState(false);
  const evidence = useEvidence();
  const index = new Map(answer.evidence.map((e, i) => [e.record_id, i + 1]));
  const { subject } = readInterpretation(answer.interpretation);
  const { singles, breakdowns, text } = arrange(answer.claims);
  const st = STATUS[answer.status] ?? { label: answer.status, tone: "" };
  const partial = answer.unknowns.some((u) => /not shown|incomplete|missing/i.test(u));
  const scope = answer.qualifiers.filter((q) => !FACTS_ONLY.test(q));

  // At most three citation numbers per claim; the rest stay one click away in the Evidence list.
  const Cites = ({ c }: { c: Claim }) => {
    const ids = c.evidence.filter((id) => index.has(id));
    return <>{ids.slice(0, 3).map((id) => (
      <button key={id} className="cite" onClick={() => evidence.open(id, clean(c.text))}
              aria-label={`Show source ${index.get(id)}`}>{index.get(id)}</button>))}
      {ids.length > 3 && <button className="cite" onClick={() => setOpen(true)}
                                 aria-label={`${ids.length - 3} more sources`}>+{ids.length - 3}</button>}</>;
  };

  // Every answer that is not a refusal is explained automatically (a refusal keeps its own policy notice).
  const explainable = answer.status !== "refused" && !!answer.audit_id;
  const ex = useExplanation(async () => (await product.explainAnswer(answer.audit_id!)).explanation,
                            [answer.audit_id], explainable);

  // The direct answer: the single verified claim itself, or what was answered, or the status in plain words.
  let lead: string, leadTone = "";
  if (answer.status === "refused") { lead = answer.refusal?.title ?? "Veloxa can't answer this request."; leadTone = "bad"; }
  else if (answer.status === "abstained") {
    lead = cap(answer.answer.replace(/^I don't know:\s*/i, "")) || "There isn't enough evidence to answer this.";
    leadTone = "muted";
  } else if (answer.status === "conflict") { lead = "The sources disagree. Veloxa shows each value and chooses none."; leadTone = "warn"; }
  else if ((answer as { diagnostics?: { stage?: string } }).diagnostics?.stage === "routed" && answer.answer)
    lead = answer.answer;                                     // routed: the subsystem's own plain answer
  else if (answer.claims.length === 1 && text.length === 1) lead = clean(answer.claims[0].text);
  else {
    const search = subject && /^a text search for (.*)$/i.exec(subject);
    lead = search ? `What the records say about ${search[1]}` : subject ? cap(subject) : `${answer.claims.length} verified facts`;
  }

  return (
    <article className="brief" aria-label="Answer">
      <div className="brief-status">
        <Badge value={answer.status} label={st.label} />
        {answer.confidence && (
          <span className={`conf conf-${answer.confidence.level}`} title={`${answer.confidence.reason}. `
            + "Deterministic: based on source authority, corroboration and conflicts; not a probability."}>
            Confidence: {answer.confidence.label}</span>)}
        {partial && <Badge value="open" label="Partial" title="Some matching information is not shown" />}
        <Badge value={answer.grade.value} label={`Grade ${answer.grade.value}`}
               title={`${answer.grade.label}: ${answer.grade.reasons.join("; ")}`} />
      </div>

      <p className={`brief-lead ${leadTone}`}>{lead}</p>
      {explainable && <ExplanationView ex={ex} compact />}

      {answer.refusal && (
        <div className="callout bad" role="note">
          <strong>{answer.refusal.policy}{answer.refusal.title ? `: ${answer.refusal.title}` : ""}</strong>
          <p style={{ marginTop: 4 }}>{answer.refusal.reason}</p>
          {answer.refusal.alternative && <p className="small" style={{ marginTop: 6 }}>Instead: {answer.refusal.alternative}</p>}
        </div>
      )}

      {singles.length > 0 && (
        <section>
          {(breakdowns.length > 0 || text.length > 0) && <h4>Key figures</h4>}
          <ul className="plain figures" aria-label="Key figures">
            {singles.map((f, i) => (
              <li key={i} className="figure">
                <span className="sr-only">{clean(f.claim.text)}</span>
                <span className="lbl" aria-hidden="true">{f.label}</span>
                <span className="val" aria-hidden="true">{f.value}</span>
                <span className="note">{f.note && <span aria-hidden="true">{f.note}</span>}<Cites c={f.claim} /></span>
              </li>))}
          </ul>
        </section>
      )}

      {breakdowns.map((b) => (
        <section key={b.title} className="breakdown">
          <h4>{b.title}</h4>
          <BarChart label={b.title} series={["value"]} listLabel="Values"
                    rows={b.rows.map((r) => ({ label: r.label, values: [r.fig.num] }))} />
          {(() => {
            const ids = [...new Set(b.rows.flatMap((r) => r.fig.claim.evidence))].filter((id) => index.has(id));
            return (
              <p className="small muted" style={{ marginTop: 4 }}>
                {ids.length} source record{ids.length === 1 ? "" : "s"}
                {ids.slice(0, 3).map((id) => <button key={id} className="cite" onClick={() => evidence.open(id, b.title)}
                                                     aria-label={`Show source ${index.get(id)}`}>{index.get(id)}</button>)}
                {ids.length > 3 && <button className="link small" style={{ marginLeft: 8 }} onClick={() => setOpen(true)}>
                  and {ids.length - 3} more</button>}
              </p>);
          })()}
          <span className="sr-only">{b.rows.map((r) => clean(r.fig.claim.text)).join(". ")}</span>
        </section>
      ))}

      {text.length > 0 && !(answer.claims.length === 1 && text.length === 1 && answer.status !== "conflict") && (
        <section>
          {(singles.length > 0 || breakdowns.length > 0) && <h4>What the records say</h4>}
          <ul className="plain statements" aria-label="Statements">
            {text.map((c, i) => {
              const t = c.text.replace(/\s*\n\s*/g, " ");
              let cut = t.indexOf(": ");
              const next = cut > 0 ? t.indexOf(": ", cut + 2) : -1;          // "Org: Document title (…): text"
              if (next > 0 && next < 110 && /[ (]/.test(t.slice(cut + 2, next)) && !/[⟦;]/.test(t.slice(cut + 2, next))) cut = next;
              const titled = cut > 0 && cut < 120 && !t.slice(0, cut).includes("⟦");
              return (
                <li key={i}>
                  {titled && <span className="passage-src">{t.slice(0, cut)}</span>}
                  <Highlighted text={titled ? t.slice(cut + 2) : t} /><Cites c={c} />
                  {titled && <span className="sr-only"> (from {t.slice(0, cut)})</span>}
                </li>);
            })}
          </ul>
        </section>
      )}
      {answer.claims.length === 1 && text.length === 1 && answer.status !== "conflict" && (
        <p className="small muted">Supported by <Cites c={text[0]} /></p>
      )}

      {answer.disagreements.length > 0 && (
        <section className="stack">
          {mergeDisagreements(answer.disagreements).map((d, i) => (
            <div key={i}>
              <h4>{cap(d.subject)}{d.period && d.period !== "period_unconfirmed" ? `, ${d.period}`
                : d.period ? ": reporting periods are not confirmed" : ""}</h4>
              <div className="conflict-grid">
                {d.claims.map((c) => (
                  <div key={c.record_id} className="conflict-side">
                    <div className="val">{c.value}{c.unit && !(c.unit === "INR" && /Rs\.?|\u20b9|crore|lakh|Cr/i.test(c.value))
                      ? ` ${c.unit}` : ""}</div>
                    <div className="small muted">{c.source}{c.period ? `, ${c.period}` : ""}</div>
                    {index.has(c.record_id) && <button className="cite" onClick={() => evidence.open(c.record_id, d.subject)}
                      aria-label={`Show source ${index.get(c.record_id)}`}>{index.get(c.record_id)}</button>}
                  </div>))}
              </div>
              <p className="small muted" style={{ marginTop: 6 }}>{d.metric_match === "same_metric"
                ? "These figures measure the same thing." : "These figures may not measure the same thing"}
                {d.definition === "unconfirmed" ? ", and no source defines it, so Veloxa doesn't pick one."
                  : `; definition: ${d.definition}.`}</p>
            </div>))}
        </section>
      )}

      {!ex.e && (answer.unknowns.length > 0 || scope.length > 0) && (   /* shown by the explanation once loaded */
        <div className={`caveats${partial ? "" : " neutral"}`}>
          <strong>{partial ? "Some information is missing" : "Scope and caveats"}</strong>
          <ul className="small">
            {answer.unknowns.map((u, i) => <li key={`u${i}`}>{plain(u)}</li>)}
            {scope.map((q, i) => <li key={`q${i}`}>{plain(q)}</li>)}
          </ul>
        </div>
      )}

      {(answer.specialists?.length ?? 0) > 0 && (
        <p className="small muted specialists">
          Specialist areas consulted: {[...new Set(answer.specialists!.map((x) => x.department.replace("_", " ")))]
            .join(", ")}. Why: {answer.specialists![0].reason}.
        </p>
      )}
      {(answer.follow_ups?.length ?? 0) > 0 && <FollowUps items={answer.follow_ups!} />}

      <footer className="brief-foot">
        {answer.evidence.length > 0 && (
          <button className="btn" onClick={() => setOpen(true)}>Evidence ({answer.evidence.length})</button>)}
        <span>{answer.read_basis.banner ?? `Knowledge point K=${answer.read_basis.batch_seq}`}</span>
        <details className="small" style={{ flexBasis: "100%" }}>
          <summary>How Veloxa read the question</summary>
          <p style={{ marginTop: 4 }}>{answer.interpretation}</p>
        </details>
      </footer>

      {open && (
        <Drawer title="Evidence" onClose={() => setOpen(false)}>
          <ul className="plain source-list">
            {answer.evidence.map((e, i) => (
              <li key={e.record_id}>
                <span className="source-n">{i + 1}</span>
                <span style={{ minWidth: 0 }}>
                  <strong style={{ fontWeight: 500 }}>{(e.source_path ?? e.system).split("/").pop()}</strong>
                  <div className="small muted">{e.system.replace(/_/g, " ")}, {where(e)}, batch K={e.batch_seq}, {e.data_class}</div>
                  <div className="mono small muted" style={{ overflowWrap: "anywhere" }}>{e.record_id}</div>
                </span>
                <button className="btn" onClick={() => evidence.open(e.record_id)}>View</button>
              </li>))}
          </ul>
        </Drawer>
      )}
    </article>
  );
}

/** Search highlights arrive as ⟦word⟧ (verbatim from the evidence excerpt); show them as marks. */
export function Highlighted({ text }: { text: string }) {
  const parts = text.split(/(⟦[^⟧]*⟧)/g);
  return <>{parts.map((p, i) => p.startsWith("⟦")
    ? <mark key={i} className="hl">{p.slice(1, -1)}</mark> : <span key={i}>{p}</span>)}</>;
}

function FollowUps({ items }: { items: string[] }) {
  const ask = useAsk();
  return (
    <div className="follow-ups" role="group" aria-label="Suggested follow-ups">
      {items.map((q) => <button key={q} className="chip" onClick={() => ask.ask(q)}>{q}</button>)}
    </div>
  );
}


/** One block per subject: pairwise disagreements about the same figure are merged, each distinct value shown once. */
function mergeDisagreements<T extends { subject: string; claims: { record_id: string }[] }>(ds: T[]): T[] {
  const by = new Map<string, T>();
  for (const d of ds) {
    const prev = by.get(d.subject);
    if (!prev) { by.set(d.subject, { ...d, claims: [...d.claims] }); continue; }
    for (const c of d.claims) if (!prev.claims.some((x) => x.record_id === c.record_id)) prev.claims.push(c);
  }
  return [...by.values()];
}
