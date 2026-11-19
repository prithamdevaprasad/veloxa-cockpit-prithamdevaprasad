// "What should I worry about?": an executive decision workspace on the approved D-15 v1 ranking.
// Left: the ranked concerns (rank or shared rank, exposure range, confidence, status), then the concerns there is not
// enough evidence to assess. Right: the selected concern with everything needed to act on it together: why it
// matters, evidence, the D-15 calculation, unknowns, follow-ups (turned into Jira tasks), notifications and the
// investigation history. Finding state, notification acknowledgements and follow-up actions are separate records.
// Severity is an ordinal interval, never a probability; nothing here is invented.
import { useEffect, useMemo, useState } from "react";
import { ApiError, get } from "../api/client";
import { product } from "../api/product";
import type { Finding, FollowUp, WorryItem } from "../api/product-types";
import { useApi } from "../api/useApi";
import { useAsk } from "../ui/Ask";
import { useEvidence } from "../ui/EvidenceDrawer";
import { ExplanationView, useExplanation } from "../ui/Explanation";
import { Guidance, Icon } from "../ui/kit";
import { Button, ErrorState, Loading, PageHead } from "../ui";

const CRIT: Record<string, string> = { C1: "Delivery", C2: "Financial", C3: "Continuity", C4: "Reporting integrity" };
const pct = (x: number) => `${Math.round(x * 100)}%`;
const cap = (t: string) => t.charAt(0).toUpperCase() + t.slice(1);
const NO_MEASURE = "no evidence-backed measure";       // a criterion this concern never declared (not its unknown)
const WHY: Record<string, string> = {
  C1: "A committed delivery date has moved.", C2: "Revenue or contract value is exposed.",
  C3: "The work depends on very few people.",
  C4: "Reported figures disagree, so decisions may rest on an unconfirmed number.",
};
export const groupLabel = (rank: number) =>
  rank === 1 ? "Highest attention group" : rank === 2 ? "Next attention group" : "Further attention";

type Unrankable = NonNullable<import("../api/product-types").Worry["unrankable"]>[number];
type Concern = { kind: "ranked"; x: WorryItem; size: number } | { kind: "unassessed"; x: Unrankable };
const idOf = (c: Concern) => c.x.item_id;
const STATE_LABEL: Record<string, string> = { detected: "Detected", verified: "Verified", notified: "Open",
  investigating: "Under investigation", resolved: "Resolved", dismissed: "Dismissed", reassessed: "No longer supported",
  reopened: "Reopened" };
const FU_LABEL: Record<string, string> = { created: "Task created", assigned: "Assigned", in_progress: "In progress",
  completed: "Completed", blocked: "Blocked" };
const when = (iso: string) => new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
const who = (p: string) => p.startsWith("queue:") ? `${cap(p.slice(6))} queue` : p.replace(/^person:/, "");

/** Severity interval on a 0–1 track (an ordering of exposure, not a probability). */
function Range({ low, high }: { low: number; high: number }) {
  return <span className="range" role="img" aria-label={`Possible impact: ${level(low)} to ${level(high)}`}>
    <span className="range-bar" style={{ left: `${low * 100}%`, width: `${Math.max(2, (high - low) * 100)}%` }} /></span>;
}

const level = (v: number) => (v >= 0.5 ? "high" : v >= 0.25 ? "medium" : "low");

function reasonOf(x: { severity?: WorryItem["severity"]; unknowns: string[] }) {
  const m = x.severity ? Object.values(x.severity.criteria).find((c) => c.status === "measured") : undefined;
  const u = x.unknowns[0];
  return m ? cap(m.reason) + "." : u ? cap(u) : "The records show this but do not yet measure its impact.";
}

function ConcernRow({ c, finding, active, onSelect, pos }: { c: Concern; finding?: Finding; active: boolean;
  onSelect: () => void; pos?: number }) {
  const st = finding ? STATE_LABEL[finding.state] ?? finding.state : "No finding yet";
  return (
    <li>
      <button className={`ws-row${active ? " active" : ""}`} onClick={onSelect} aria-current={active ? "true" : undefined}>
        {c.kind === "ranked"
          ? <span className={`ws-rank r${Math.min(pos ?? c.x.rank, 3)}`}
                  title={c.size > 1 ? "Close call: the evidence puts these concerns near each other" : undefined}>
              #{pos ?? c.x.rank}{c.size > 1 && <small>close call</small>}</span>
          : <span className="ws-rank none" title="Not enough evidence to assess">–</span>}
        <span className="ws-main">
          <strong>{c.x.title}</strong>
          <span className="small muted">{[c.x.department, c.x.project].filter(Boolean).join(" · ") || "Company-wide"}</span>
          <span className="small">{c.kind === "ranked" ? reasonOf(c.x) : (c.x.unknowns[0] ? cap(c.x.unknowns[0]) : "Its impact is not measured in the records.")}</span>
        </span>
        <span className="ws-meta">
          {c.kind === "ranked" && <><Range low={c.x.severity.low} high={c.x.severity.high} />
            <span className="small muted">Impact {level(c.x.severity.low) === level(c.x.severity.high)
              ? level(c.x.severity.high) : `${level(c.x.severity.low)} to ${level(c.x.severity.high)}`}</span></>}
          {c.kind === "ranked" && <span className={`pill conf-${c.x.confidence}`}>{c.x.confidence === "conflicting" ? "Sources conflict" : `${cap(c.x.confidence)} confidence`}</span>}
          <span className={`pill st-${finding?.state ?? "none"}`}>{st}</span>
        </span>
      </button>
    </li>);
}

function Assessment({ x, size }: { x: WorryItem; size: number }) {
  const s = x.severity;
  return (
    <details className="small assess">
      <summary>How this was scored</summary>
      <p>Each concern is scored on four areas: delivery, money, keeping services running, and accuracy of reported
        figures. Where the records don't say, the score allows for the full range rather than assuming zero.
        {size > 1 ? " This is a close call: the evidence puts it near the other top concerns, so treat them together." : ""}
        {x.sensitive ? " Its place could change with small changes in how the areas are weighted." : ""}</p>
      <div className="table-wrap"><table className="crit-table">
        <thead><tr><th>Area</th><th>What the records show</th><th>Importance</th><th>Detail</th></tr></thead>
        <tbody>{Object.entries(s.criteria).map(([c, v]) => (
          <tr key={c}>
            <td>{CRIT[c] ?? c}</td>
            <td>{v.status === "measured" ? "Measured" : v.status === "not_applicable" ? "Doesn't apply" : "Not known yet"}</td>
            <td>{pct(v.weight)}</td>
            <td className="small muted">{v.reason === NO_MEASURE ? "No record measures this" : cap(v.reason)}</td>
          </tr>))}</tbody>
      </table></div>

    </details>);
}

/** Recommendations for one concern. Ticking only SELECTS a follow-up for a Jira task; it does not mark anything done. */
function FollowUps({ c, finding, actions, reload }: { c: Concern; finding?: Finding; actions: FollowUp[]; reload: () => void }) {
  const ask = useAsk();
  const recs = c.x.follow_up ? [c.x.follow_up] : [];
  const [picked, setPicked] = useState<Set<string>>(new Set());
  useEffect(() => setPicked(new Set()), [c.x.item_id]);
  const chosen = recs.filter((r) => picked.has(r));
  const linked = (r: string) => actions.filter((a) => a.recommendation === r);
  function create() {
    const text = chosen.join("\n");
    ask.task({
      summary: chosen.length === 1 ? chosen[0].slice(0, 250) : `Follow-ups: ${c.x.title}`.slice(0, 250),
      description: `From the Veloxa concern "${c.x.title}"${finding ? ` (finding ${finding.finding_id})` : ""}.\n${chosen.map((r) => `- ${r}`).join("\n")}`,
      source: `Follow-up for “${c.x.title}”. Pick the owner from Jira's list: Veloxa does not assume who is responsible.`,
      link: { item_id: c.x.item_id, finding_id: finding?.finding_id, recommendation: text },
      onWritten: (r, assignee) => {
        if (!r.key) return;
        void product.recordFollowUp({ item_id: c.x.item_id, finding_id: finding?.finding_id, recommendation: text,
          status: assignee ? "assigned" : "created", jira_key: r.key, jira_assignee: assignee ?? undefined }).then(reload, reload);
      },
    });
  }
  return (
    <section className="ws-block" aria-label="Follow-ups">
      <h3>Suggested follow-ups</h3>
      {recs.length === 0 ? <p className="small muted">The records suggest no follow-up yet.</p> : <>
        <p className="small muted">Select follow-ups to create Jira tasks. Selecting does not mark anything as done.</p>
        <ul className="plain ws-recs">{recs.map((r) => (
          <li key={r}><label><input type="checkbox" checked={picked.has(r)} onChange={() => {
              const n = new Set(picked); if (n.has(r)) n.delete(r); else n.add(r); setPicked(n); }}
              aria-label={`Select for a Jira task: ${r}`} /><span>{r}</span></label>
            {linked(r).length > 0 && <span className="small muted">{linked(r).length} task{linked(r).length > 1 ? "s" : ""} linked</span>}</li>))}</ul>
        <Button variant="primary" disabled={!chosen.length} onClick={create}>
          {chosen.length ? "Create Jira task from selection" : "Select a follow-up to create a Jira task"}</Button>
      </>}
      {actions.length > 0 && <ActionList actions={actions} />}
    </section>);
}

/** Linked follow-up actions, with the Jira issue's status read live (never assumed). */
function ActionList({ actions }: { actions: FollowUp[] }) {
  const keys = [...new Set(actions.map((a) => a.jira_key).filter(Boolean))] as string[];
  const [live, setLive] = useState<Record<string, { status?: string | null; assignee?: string | null }> | null>(null);
  const [liveErr, setLiveErr] = useState<string | null>(null);
  useEffect(() => {
    if (!keys.length) return;
    get<{ issues: { key: string; status: string | null; assignee: string | null }[] }>(
      `/v1/integrations/jira/search?jql=${encodeURIComponent(`key in (${keys.join(",")})`)}&limit=50`)
      .then((r) => setLive(Object.fromEntries(r.issues.map((i) => [i.key, i]))),
            (e) => setLiveErr(e instanceof ApiError ? e.message : String(e)));
  }, [keys.join(",")]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <>
      <h4>Linked work</h4>
      <ul className="plain ws-actions">{actions.map((a) => (
        <li key={a.action_id}>
          <span className={`pill fu-${a.status}`}>{FU_LABEL[a.status] ?? a.status}</span>
          <span className="mono">{a.jira_key ?? "no issue"}</span>
          <span className="small">{a.jira_key && live?.[a.jira_key] ? `Jira now: ${live[a.jira_key].status ?? "no status"}, ${live[a.jira_key].assignee ?? "unassigned"}`
            : a.jira_assignee ? `Assigned to ${a.jira_assignee} when created` : "Unassigned when created"}</span>
        </li>))}</ul>
      {liveErr && <p className="small muted">Couldn't read the current Jira status: {liveErr}</p>}
      <p className="small muted">Creating a task doesn't complete the follow-up, and completing it doesn't resolve the concern.</p>
    </>);
}

/** Alerts about the finding: in-app records only. Acknowledging = "I've seen this alert". */
function Notifications({ f, reload }: { f: Finding; reload: () => void }) {
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  return (
    <section className="ws-block" aria-label="Notifications">
      <h3>Notifications</h3>
      <div className="table-wrap"><table className="ws-table small">
        <thead><tr><th>Recipient</th><th>Channel</th><th>Created</th><th>Delivery</th><th>Seen</th></tr></thead>
        <tbody>{f.notified.map((n) => (
          <tr key={n.notification_id}>
            <td>{n.basis.startsWith("CEO") ? `CEO (${who(n.recipient)})` : who(n.recipient)}</td><td>In-app</td><td className="tnum">{when(n.created_at)}</td>
            <td className="muted">In Veloxa only (no e-mail or chat channel)</td>
            <td>{n.acknowledged.length ? n.acknowledged.map((a) => `${who(a.actor)}, ${when(a.at)}`).join("; ")
              : <Button disabled={busy === n.notification_id} onClick={async () => {
                  setBusy(n.notification_id); setErr(null);
                  try { await product.ackNotification(n.notification_id); reload(); }
                  catch (e) { setErr(e instanceof ApiError ? e.message : String(e)); }
                  finally { setBusy(null); } }}>Acknowledge</Button>}</td>
          </tr>))}</tbody>
      </table></div>
      {err && <p className="callout bad small" role="alert">{err}</p>}
      <p className="small muted">Acknowledging records that you've seen the alert. It doesn't resolve the concern.</p>
    </section>);
}

/** The human response to the finding: investigate (optionally naming who is responsible), resolve with an outcome,
 *  dismiss with a reason, reopen with what shows it has returned. */
function Investigation({ f, reload }: { f: Finding; reload: () => void }) {
  const [act, setAct] = useState<null | "investigating" | "resolved" | "dismissed" | "reopened">(null);
  const [note, setNote] = useState("");
  const [resp, setResp] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const closed = ["resolved", "dismissed", "reassessed"].includes(f.state);
  const needs = act && act !== "investigating";
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!act) return;
    setErr(null);
    try { await product.findingEvent(f.finding_id, act, note.trim() || undefined, resp.trim() || undefined);
          setAct(null); setNote(""); setResp(""); reload(); }
    catch (x) { setErr(x instanceof ApiError ? x.message : String(x)); }
  }
  const human = f.history.filter((h) => !["detected", "verified", "notified"].includes(h.state));
  return (
    <section className="ws-block" aria-label="Investigation">
      <h3>Investigation <span className={`pill st-${f.state}`}>{STATE_LABEL[f.state] ?? f.state}</span></h3>
      {!act && <div className="row" style={{ gap: 8 }}>
        {closed ? <Button onClick={() => setAct("reopened")}>Reopen</Button> : <>
          {f.state !== "investigating" && <Button onClick={() => setAct("investigating")}>Mark as investigating</Button>}
          <Button onClick={() => setAct("resolved")}>Resolve</Button>
          <Button variant="ghost" onClick={() => setAct("dismissed")}>Dismiss</Button></>}
      </div>}
      {act && <form className="ws-form" onSubmit={submit}>
        {act === "investigating" && <label>Who is responsible? <span className="muted">(optional; you are recorded as the
          person who started it, not as the owner)</span>
          <input className="input" value={resp} maxLength={200} onChange={(e) => setResp(e.target.value)}
                 placeholder="e.g. Engineering release owner" /></label>}
        <label>{act === "investigating" ? "Note (optional)" : act === "resolved" ? "Outcome or evidence (required)"
          : act === "dismissed" ? "Reason (required)" : "What shows it has returned? (required)"}
          <textarea className="input" rows={2} value={note} maxLength={2000} onChange={(e) => setNote(e.target.value)} /></label>
        <div className="row" style={{ gap: 8 }}>
          <Button variant="primary" type="submit" disabled={Boolean(needs) && note.trim().length < 5}>
            {act === "investigating" ? "Start investigation" : act === "resolved" ? "Resolve" : act === "dismissed" ? "Dismiss" : "Reopen"}</Button>
          <Button variant="ghost" onClick={() => { setAct(null); setErr(null); }}>Cancel</Button></div>
        {err && <p className="callout bad small" role="alert">{err}</p>}
      </form>}
      {human.length > 0 ? <ol className="plain ws-history small">{human.map((h, i) => (
        <li key={i}><span className="tnum muted">{when(h.at)}</span> <strong>{h.state === "acknowledged" ? "Acknowledged (earlier record)" : STATE_LABEL[h.state] ?? h.state}</strong>
          {" "}by {who(h.actor)}{h.responsible ? `; responsible: ${h.responsible}` : ""}{h.note ? `: ${h.note}` : ""}</li>))}</ol>
        : <p className="small muted">Nobody has started an investigation.</p>}
    </section>);
}

function Detail({ c, finding, actions, reload, pos }: { c: Concern; finding?: Finding; actions: FollowUp[];
  reload: () => void; pos?: number }) {
  const [explain, setExplain] = useState(false);
  const evidence = useEvidence();
  const ask = useAsk();
  const ex = useExplanation(async () => (await product.explainWorry(c.x.item_id)).explanation, [c.x.item_id], explain);
  useEffect(() => setExplain(false), [c.x.item_id]);
  const x = c.x;
  const crit = c.kind === "ranked" ? Object.entries(c.x.severity.criteria) : [];
  const established = crit.filter(([, v]) => v.status === "measured").map(([, v]) => cap(v.reason));
  const why = crit.filter(([, v]) => v.status === "measured").map(([k]) => WHY[k]).filter(Boolean);
  const unknown = [...new Set([...x.unknowns, ...crit.filter(([, v]) => ["missing", "unquantified", "unmapped"].includes(v.status)
    && v.reason !== NO_MEASURE).map(([, v]) => v.reason)])];
  return (
    <article className="ws-detail card" aria-label="Concern details" id={`worry-${x.item_id}`}>
      <header>
        <div className="small muted">{c.kind === "ranked" ? `Priority #${pos ?? c.x.rank}${c.size > 1 ? " (close call)" : ""}`
          : "Not enough evidence to assess"}{x.department ? ` · ${x.department}` : ""}{x.project ? ` · ${x.project}` : ""}</div>
        <h2>{x.title}</h2>
        <div className="row" style={{ gap: 8 }}>
          <Button onClick={() => setExplain(!explain)} aria-expanded={explain}>{explain ? "Hide explanation" : "Explain"}</Button>
          <Button variant="ghost" disabled={!x.evidence.length} onClick={() => evidence.open(x.evidence[0], x.title)}>
            Evidence ({x.evidence.length})</Button>
          <Button variant="ghost" onClick={() => ask.ask(`Explain this concern: "${x.title}". Why does it matter, what `
            + "evidence supports it, and what is still unknown?", { kind: "concern", subject: x.title }, true)}>Discuss in chat</Button>
        </div>
      </header>
      {explain && <ExplanationView ex={ex} />}
      {c.kind === "ranked" ? <>
        {(established.length > 0 || why.length > 0) && <section className="ws-block"><h3>Why it matters</h3>
          {established.length > 0 && <p className="small">{established.join(". ")}.</p>}
          {why.length > 0 && <p className="small">{why.join(" ")}</p>}
          {c.x.confidence === "conflicting" && <p className="small"><strong>Sources disagree:</strong> the records give different
            figures and no approved rule says which one is right.</p>}</section>}
        <Assessment x={c.x} size={c.size} />
      </> : <section className="ws-block"><h3>Why it can't be assessed yet</h3>
        <p className="small">{c.x.reasons.map(cap).join(". ")}.</p>
        <p className="small muted">The records show this concern exists, but not enough of its impact is measured to place it
          yet. That is not the same as unimportant, low priority, zero risk, or hidden from you.</p></section>}
      {unknown.length > 0 && <section className="ws-block"><h3>{c.kind === "ranked" ? "What remains unknown" : "Missing evidence"}</h3>
        <ul className="bullets small">{unknown.map((u) => <li key={u}>{cap(u)}</li>)}</ul></section>}
      <FollowUps c={c} finding={finding} actions={actions} reload={reload} />
      {finding ? <><Investigation f={finding} reload={reload} /><Notifications f={finding} reload={reload} /></>
        : <p className="small muted ws-block">No finding has been recorded for this concern yet, so there are no notifications
          or investigation. Findings are created when evidence is admitted or the daily review runs.</p>}
    </article>);
}

function Activity({ findings, actions }: { findings: Finding[]; actions: FollowUp[] }) {
  const rows = [
    ...findings.flatMap((f) => f.history.map((h) => ({ at: h.at, text: `${STATE_LABEL[h.state] ?? cap(h.state)}: ${f.title}`,
      by: h.actor }))),
    ...actions.flatMap((a) => a.history.map((h) => ({ at: h.at, text: `${FU_LABEL[h.status] ?? h.status}${h.jira_key ? ` ${h.jira_key}` : ""}: ${a.recommendation.split("\n")[0]}`, by: h.actor }))),
  ].sort((a, b) => b.at.localeCompare(a.at)).slice(0, 12);
  return (
    <section className="card ws-activity" aria-label="Findings"><details>
      <summary><h2>Recent activity</h2><span className="small muted">{findings.length} finding{findings.length === 1 ? "" : "s"}, latest changes first</span></summary>
      {rows.length === 0 ? <p className="small muted">No activity yet.</p>
        : <ol className="plain ws-history small">{rows.map((r, i) => <li key={i}><span className="tnum muted">{when(r.at)}</span> {r.text}
            <span className="muted"> · {who(r.by)}</span></li>)}</ol>}
    </details></section>);
}

export function WorryPage() {
  const [w, reload] = useApi((s) => product.worry(undefined, s), []);
  const [f, reloadF] = useApi((s) => product.findings(s), []);
  const [fu, reloadFu] = useApi((s) => product.followUps(s), []);
  const ready = w.state === "ready" ? w.data : null;
  const ranked = ready && ready.ranking_mode !== "pending" ? ready.ranked : [];
  const unrankable = ready?.unrankable ?? [];
  const findings = f.state === "ready" ? f.data.findings : [];
  const actions = fu.state === "ready" ? fu.data.follow_ups : [];
  const concerns: Concern[] = useMemo(() => [
    ...ranked.map((x) => ({ kind: "ranked" as const, x, size: ranked.filter((y) => y.rank === x.rank).length })),
    ...unrankable.map((x) => ({ kind: "unassessed" as const, x }))], [ranked, unrankable]);
  const findingOf = (id: string) => findings.find((x) => x.item_id === id);
  const [sel, setSel] = useState<string | null>(() => new URLSearchParams(window.location.hash.split("?")[1] ?? "").get("item"));
  const current = concerns.find((c) => idOf(c) === sel) ?? concerns[0];
  const refresh = () => { reloadF(); reloadFu(); };
  const active = findings.filter((x) => !["resolved", "dismissed", "reassessed"].includes(x.state));
  const lastEval = findings.flatMap((x) => x.history.filter((h) => ["detected", "reassessed", "reopened"].includes(h.state)).map((h) => h.at)).sort().pop();
  const conflicting = ranked.filter((x) => x.confidence === "conflicting").length;
  const tied = ranked.filter((x) => x.tied).length;
  return (
    <div className="ws">
      <div className="worry-hero">
        <PageHead title="What should I worry about?" intro="The concerns in your company records, in priority order, with what
          to do about each one." />
        {ready && <div className="basis-card small"><Icon name="timeline" size={18} /><span>{ready.read_basis.banner}</span></div>}
      </div>
      {ready && <section className="ws-summary" aria-label="In short">
        <div><strong className="tnum">{f.state === "ready" ? active.length : "…"}</strong><span>Active findings</span></div>
        <div className="high"><strong className="tnum">{ranked.filter((x) => x.rank === 1).length}</strong><span>Highest attention</span></div>
        <div><strong className="tnum">{f.state === "ready" ? findings.filter((x) => x.state === "investigating").length : "…"}</strong><span>Under investigation</span></div>
        <div><strong className="tnum">{unrankable.length}</strong><span>Need more evidence</span></div>
        <div className="wide"><strong className="tnum">{lastEval ? when(lastEval) : "Not yet"}</strong><span>Last evaluated (latest finding review)</span></div>
      </section>}
      <div className="ws-grid">
        <div className="ws-list">
          <section className="card" aria-label="Ranked worries">
            <div className="ws-list-head"><h2>Ranked concerns{ranked.length ? ` (${ranked.length})` : ""}</h2>
              {ranked.length > 0 && <span className="small muted">{tied ? `${tied} close calls` : "Clear order"}{conflicting ? ` · ${conflicting} with conflicting sources` : ""}</span>}</div>
            {w.state === "loading" && <Loading lines={5} />}
            {w.state === "error" && <ErrorState error={w.error} onRetry={reload} />}
            {ready && (ready.ranking_mode === "pending" ? <Guidance title="Not ranked yet">{ready.note}</Guidance>
              : ranked.length === 0 ? <Guidance title="Nothing to rank">No item meets the evidence gate.</Guidance>
              : <ul className="plain ws-rows">{concerns.filter((c) => c.kind === "ranked").map((c, i) => (
                  <ConcernRow key={idOf(c)} c={c} pos={i + 1} finding={findingOf(idOf(c))} active={current === c} onSelect={() => setSel(idOf(c))} />))}</ul>)}
            {tied > 1 && <p className="small muted ws-note">Close calls are ordered by their worst possible impact. The records
              can't fully separate them yet, so look at them together.</p>}
          </section>
          <section className="card" aria-label="Unrankable">
            <div className="ws-list-head"><h2>Needs more evidence{unrankable.length ? ` (${unrankable.length})` : ""}</h2></div>
            <p className="small muted ws-note">The evidence shows these concerns exist but doesn't measure enough of their impact to
              place them in order. That doesn't mean they're unimportant, low priority, zero risk, or hidden from you.</p>
            {ready && (unrankable.length + (ready.unranked?.length ?? 0) === 0 ? <p className="small muted">None.</p>
              : <ul className="plain ws-rows">{concerns.filter((c) => c.kind === "unassessed").map((c) => (
                  <ConcernRow key={idOf(c)} c={c} finding={findingOf(idOf(c))} active={current === c} onSelect={() => setSel(idOf(c))} />))}
                {(ready.unranked ?? []).map((u) => <li key={u.item_id} className="small">{u.title}</li>)}</ul>)}
            {ready && ready.hidden_for_access > 0 && <p className="small muted ws-note">{ready.hidden_for_access} more concern
              {ready.hidden_for_access === 1 ? " cites" : "s cite"} records you can't read, so {ready.hidden_for_access === 1 ? "it is" : "they are"} not shown (restricted).</p>}
          </section>
          <Activity findings={findings} actions={actions} />
        </div>
        <div className="ws-side">
          {current ? <Detail pos={current && current.kind === "ranked" ? concerns.filter((k) => k.kind === "ranked").indexOf(current) + 1 : undefined} c={current} finding={findingOf(idOf(current))}
                             actions={actions.filter((a) => a.item_id === idOf(current))} reload={refresh} />
            : ready && <section className="card"><Guidance title="Nothing to show">No concerns in the records you can read.</Guidance></section>}
          {f.state === "error" && <ErrorState error={f.error} onRetry={reloadF} />}
          {ready && <p className="small muted ws-note">Weights: {Object.entries((ready.policy as { weights?: Record<string, number> }).weights ?? {})
            .map(([k, v]) => `${CRIT[k]} ${pct(v)}`).join(", ") || "not recorded"}. Severity is an ordering, never a probability.</p>}
        </div>
      </div>
    </div>
  );
}
