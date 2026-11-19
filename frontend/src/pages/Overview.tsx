// The CEO overview: how the company is doing (governed and as-reported figures), what changed (agent updates,
// submissions, clarifications), what needs attention (evidence-backed, unranked until the owner's D-15 values),
// and where to look further (departments). Every number comes from the API and states its basis.
import { useState } from "react";
import { api } from "../api/client";
import { product } from "../api/product";
import type { AgentUpdate, ChartSource } from "../api/product-types";
import { useApi } from "../api/useApi";
import { DeptArt } from "../ui/art";
import { useAsk } from "../ui/Ask";
import { useEvidence } from "../ui/EvidenceDrawer";
import { ExplanationView } from "../ui/Explanation";
import { Avatar, BarChart, DemoBadge, Donut, Guidance, Icon, KpiCard, LineChart, Tabs } from "../ui/kit";
import { Badge, Button, Drawer, ErrorState, Loading, Section } from "../ui";

// Charts are only drawn for sources the viewer may read; each says what it was computed from.
const CHARTS: { system: string; title: string; short: string; x: string; agg: "sum" | "count"; y?: string;
                kind: "line" | "bar" }[] = [
  { system: "revenue_reporting", title: "ARR by month", short: "ARR", x: "Month", agg: "sum", y: "ARR", kind: "line" },
  { system: "people_metrics", title: "Headcount snapshots by month", short: "Headcount", x: "Month", agg: "sum",
    y: "Headcount", kind: "line" },
  { system: "crm", title: "Pipeline value by stage", short: "Pipeline", x: "Stage", agg: "sum", y: "Amount", kind: "bar" },
  { system: "fp_and_a", title: "Spend variance by department (actual minus budget)", short: "Spend variance",
    x: "Department", agg: "sum", y: "Variance", kind: "bar" },
  { system: "incident_log", title: "Incidents by month", short: "Incidents", x: "Month", agg: "count", kind: "bar" },
  { system: "incident_log", title: "Incidents by severity", short: "Incidents", x: "Severity", agg: "count", kind: "bar" },
  { system: "issue_tracker", title: "Issues by status", short: "Issues", x: "Status", agg: "count", kind: "bar" },
  { system: "zoho_books", title: "Ledger lines by cost centre", short: "Ledger", x: "Cost Centre", agg: "count", kind: "bar" },
];

const KIND_LABEL: Record<string, string> = {
  new_finding: "New finding", commitment_changed: "Commitment changed", dependency_identified: "Dependency identified",
  source_disagreement: "Sources disagree", incident_update: "Incident update", department_update: "Department update",
  unresolved_question: "Open question", new_evidence: "New evidence",
};

function Chart({ c }: { c: (typeof CHARTS)[number] }) {
  const [s] = useApi((sig) => product.series({ system: c.system, x: c.x, agg: c.agg, y: c.y }, sig), [c.system]);
  if (s.state === "loading") return <Loading lines={5} />;
  if (s.state === "error") return <Guidance title="Not available">{s.error.message}</Guidance>;
  const pts = s.data.points.map((p) => ({ x: p.x, value: p.value }));
  return (
    <div>
      {c.kind === "line" ? <LineChart points={pts} label={c.title} />
        : <BarChart rows={pts.map((p) => ({ label: p.x, values: [p.value] }))} series={[c.y ?? "count"]} label={c.title} />}
      <p className="chart-foot">{cap(s.data.basis)}. Source: {s.data.sources.join(", ")}.</p>
    </div>
  );
}
const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

function greeting(): string {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

export function relTime(iso: string): string {
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (mins < 60) return mins <= 1 ? "just now" : `${mins} min ago`;
  if (mins < 60 * 24) return `${Math.round(mins / 60)} h ago`;
  if (mins < 60 * 24 * 7) return `${Math.round(mins / 1440)} d ago`;
  return new Date(iso).toLocaleDateString();
}

/** Plain source line for a headline figure ("as reported for Jul to Sep 2026, issued 2026-10-06" -> "Reported for
 * Jul to Sep 2026"; "governed metric (HR export)" -> "From HR records"). The full basis stays in the source view. */
export function plainBasis(b: string): string {
  const m = /as reported for ([^,]+)/i.exec(b);
  if (m) return `Reported for ${m[1]}`;
  if (/HR export/i.test(b)) return "From HR records";
  if (/admitted records/i.test(b)) return "Across all company records";
  return cap(b.replace(/\(.*?\)/g, "").trim());
}

export function OverviewPage({ go }: { go: (r: string) => void }) {
  const [me] = useApi((s) => product.me(s), []);
  const [ov, reloadOv] = useApi((s) => product.overview(s), []);
  const [srcs] = useApi((s) => product.chartSources(s), []);
  const ask = useAsk();
  const evidence = useEvidence();
  const name = me.state === "ready" ? (me.data.principal.display_name ?? "") : "";
  const sources = srcs.state === "ready" ? srcs.data.sources : [];
  // One chart per admitted source: the first definition whose columns that source actually has (exports differ).
  const cols = new Map(sources.map((x: ChartSource) => [x.system, new Set(x.columns)]));
  const charts = CHARTS.filter((c, i) => {
    const have = cols.get(c.system);
    const fits = (d: (typeof CHARTS)[number]) => !!have && have.has(d.x) && (!d.y || have.has(d.y));
    return fits(c) && !CHARTS.slice(0, i).some((d) => d.system === c.system && fits(d));
  });
  // Headline figures in one row. "Team" repeats headcount; evidence coverage is not a business figure.
  const kpis = ov.state === "ready" ? ov.data.kpis.filter((k) => !/^(team|evidence coverage)$/i.test(k.label)) : [];
  return (
    <div className="ov">
      <header className="ov-head">
        <div>
          <h1>{greeting()}{name ? `, ${name.split(" ")[0]}` : ""}</h1>
          <p className="ov-meta">{me.state === "ready" ? `Here is how ${me.data.tenant.display_name} stands today.` : ""}</p>
        </div>
        <div className="row">
          <Button variant="primary" onClick={() => ask.open()}><Icon name="ask" size={16} /> Ask Veloxa</Button>
          <Button onClick={() => go("evidence")}><Icon name="upload" size={16} /> Add documents</Button>
        </div>
      </header>

      <section aria-label="Key figures" className="ov-kpis">
        {ov.state === "loading" && <Loading lines={2} />}
        {ov.state === "error" && <ErrorState error={ov.error} onRetry={reloadOv} />}
        {ov.state === "ready" && (kpis.length === 0
          ? <Guidance title="Nothing to show yet">{ov.data.notes[0] ?? "No company records you may read yet."}</Guidance>
          : kpis.map((k) => (
              <KpiCard key={k.key} label={k.label.replace(/\s*\(recurring\)/i, "")} basis={plainBasis(k.basis)}
                       value={typeof k.value === "number" ? k.value.toLocaleString() : k.value}
                       onSource={k.evidence.length ? () => evidence.open(k.evidence[0], `${k.label}: ${k.basis}`)
                         : undefined} />)))}
      </section>

      <div className="ov-grid ov-2-1">
        <WorrySummary go={go} />
        <Attention />
      </div>

      <div className="ov-grid ov-3">
        <WorkforceCard />
        <HiringHealth sources={sources} loading={srcs.state === "loading"} />
        <Performance charts={charts} loading={srcs.state === "loading"} go={go} />
      </div>

      <div className="ov-grid ov-1-1">
        <BriefCard />
        <RecentIntelligence go={go} />
      </div>
    </div>
  );
}

function HiringHealth({ sources, loading }: { sources: ChartSource[]; loading: boolean }) {
  const sys = ["recruiting", "hiring_tracker"].find((s) => sources.some((x) => x.system === s));
  const [s] = useApi((sig) => sys ? product.series({ system: sys, x: "Status", agg: "count" }, sig)
    : Promise.resolve(null), [sys]);
  const pts = s.state === "ready" && s.data ? s.data.points : [];
  const total = pts.reduce((a, p) => a + p.value, 0);
  const filled = pts.filter((p) => /^(filled|hired)$/i.test(p.x.trim())).reduce((a, p) => a + p.value, 0);
  return (
    <Section title="Hiring health" aria-label="Hiring health"
             subtitle="Open roles by status.">
      {loading || s.state === "loading" ? <Loading lines={3} /> : !sys || !total
        ? <Guidance title="Not charted">No requisition counts are available to you. Ask Veloxa about hiring to get
            answers with their sources.</Guidance>
        : <>
            <div className="row" style={{ justifyContent: "space-between" }}>
              <span className="small muted">Roles filled</span>
              <strong className="num" style={{ fontSize: 22 }}>{filled}/{total}</strong>
            </div>
            <div className="progress" role="img" aria-label={`${filled} of ${total} requisitions filled`}>
              <span style={{ width: `${(filled / total) * 100}%` }} /></div>
            <ul className="plain small" style={{ marginTop: 12 }}>{pts.map((p) => (
              <li key={p.x} className="row" style={{ justifyContent: "space-between" }}><span>{p.x}</span>
                <span className="num">{p.value}</span></li>))}</ul>
            <p className="chart-foot">From the hiring tracker.</p>
          </>}
    </Section>
  );
}

/** What needs attention: ranked concerns and unranked ones, each with a text status, the reason it matters and
 * a way to open its explanation. No colour carries meaning on its own; unranked is never shown as ranked. */
function WorrySummary({ go }: { go: (r: string) => void }) {
  const [w] = useApi((s) => product.worry(undefined, s), []);
  const ask = useAsk();
  const open = (title: string) => ask.ask(`Explain this concern: "${title}". Why does it matter, what evidence `
    + "supports it, and what is still unknown?", { kind: "concern", subject: title }, true);
  return (
    <Section title="What needs attention" aria-label="What needs attention"
             subtitle="The concerns that need you most, in priority order."
             actions={<button className="link small" onClick={() => go("worry")}>View all concerns</button>}>
      {w.state === "loading" ? <Loading lines={3} /> : w.state === "error" ? <ErrorState error={w.error} />
        : (() => {
            const ranked = w.data.ranking_mode === "pending" ? [] : w.data.ranked;
            const unranked = [...(w.data.unrankable ?? []).map((u) => ({ id: u.item_id, title: u.title,
                                status: "Needs more evidence", why: u.unknowns[0] ?? u.follow_up ?? "",
                                n: u.evidence.length })),
                              ...(w.data.unranked ?? []).map((u) => ({ id: u.item_id, title: u.title,
                                status: "Needs review", why: w.data.note ?? "", n: 0 }))];
            if (!ranked.length && !unranked.length) {
              return <Guidance title="Nothing needs attention">No concern is supported by the records you may read.</Guidance>;
            }
            return (
              <ul className="plain attention-list">
                {ranked.slice(0, 4).map((x, i) => (
                  <li key={x.item_id}>
                    <span className="status-tag ranked"
                          title={x.tied ? "Close call: the records can't fully separate these concerns" : undefined}>
                      #{i + 1}{x.tied ? " · close call" : ""}</span>
                    <span className="what">{x.title}<span className="small muted" style={{ display: "block" }}>
                      {(x.severity.criteria.C1?.status === "measured" ? x.severity.criteria.C1.reason
                        : Object.values(x.severity.criteria).find((c) => c.status === "measured")?.reason) ?? ""}
                      </span></span>
                    <button className="link small" onClick={() => open(x.title)}>Explain</button>
                  </li>))}
                {unranked.slice(0, 3).map((u) => (
                  <li key={u.id}>
                    <span className="status-tag">{u.status}</span>
                    <span className="what">{u.title}<span className="small muted" style={{ display: "block" }}>{u.why}
                      </span></span>
                    <button className="link small" onClick={() => open(u.title)}>Explain</button>
                  </li>))}
              </ul>);
          })()}
    </Section>
  );
}

function WorkforceCard() {
  const [w] = useApi((s) => product.workforce(s), []);
  return (
    <Section title="People" subtitle="Headcount by department." aria-label="Workforce by department">
      {w.state === "loading" ? <Loading lines={5} /> : w.state === "error" ? <ErrorState error={w.error} />
        : w.data.state !== "ok"
          ? <Guidance title="Not available">{w.data.state === "denied" ? "You are not authorised to read the HR export."
              : "No HR export is admitted at this knowledge point."}</Guidance>
          : <>
              <Donut data={w.data.by_department ?? []} label="Workforce by department" centreLabel="people" />
              <p className="chart-foot">From HR records. {(w.data.by_status ?? [])
                .map((x) => `${x.label}: ${x.count}`).join(" · ")}.</p>
            </>}
    </Section>
  );
}

function BriefCard() {
  const [b] = useApi((s) => product.brief(s), []);
  return (
    <Section title="Key facts" aria-label="Executive brief"
             subtitle="What the verified records say right now.">
      {b.state === "loading" ? <Loading lines={4} /> : b.state === "error" ? <ErrorState error={b.error} />
        : b.data.lines.length === 0 || !b.data.explanation
          ? <Guidance title="Nothing to brief yet">No admitted evidence you may read.</Guidance>
          : <ExplanationView ex={{ e: b.data.explanation, error: null, loading: false, retry: () => undefined }} />}
    </Section>
  );
}

function Performance({ charts, loading, go }: { charts: typeof CHARTS; loading: boolean; go: (r: string) => void }) {
  const [sel, setSel] = useState<string | null>(null);
  const c = charts.find((x) => x.system === sel) ?? charts[0];
  return (
    <Section title="Business performance" subtitle={c ? c.title : "Charts from the exports you are authorised to read."}
             aria-label="Business performance"
             actions={charts.length > 1 ? (
               <div className="seg" role="group" aria-label="Chart">
                 {charts.slice(0, 5).map((x) => <button key={x.system} aria-pressed={x === c} onClick={() => setSel(x.system)}>
                   {x.short}</button>)}
               </div>) : undefined}>
      {loading ? <Loading lines={6} /> : c ? <Chart key={c.system} c={c} /> : (
        <Guidance title="No chartable sources yet">Charts appear once departments contribute exports you may read, such
          as revenue, budgets, pipeline or incidents. <button className="link" onClick={() => go("evidence")}>Upload them in
          Evidence &amp; Data</button>.</Guidance>)}
    </Section>
  );
}

function Attention() {
  const [deps] = useApi((s) => api.dependencies(s), []);
  const [today] = useApi((s) => api.today(s), []);
  const ask = useAsk();
  const warnings = deps.state === "ready" ? deps.data.warnings : [];
  const items = today.state === "ready" ? today.data.today.items : [];
  return (
    <Section title="Early warnings" aria-label="Early warnings"
             subtitle="Signals worth watching before they become problems.">
      {(deps.state === "loading" || today.state === "loading") && <Loading lines={4} />}
      {deps.state === "error" && <ErrorState error={deps.error} />}
      {deps.state === "ready" && today.state !== "loading" && (warnings.length === 0 && items.length === 0
        ? <Guidance title="Nothing flagged">No admitted record states a delay, a missing date, a single-source commitment
            or a conflict right now.</Guidance>
        : <ul className="plain attention-list">
            {warnings.slice(0, 5).map((w, i) => (
              <li key={i}>
                <span className="sig" aria-hidden="true" />
                <span className="what">{cap(w.statement)}
                  <span className="small muted" style={{ display: "block" }}>May affect a delivery date. The impact
                    isn't known yet.</span></span>
                <span className="acts"><Badge value="open" label={w.kind.replace(/_/g, " ")} />
                  <button className="link small" onClick={() => ask.ask(`What do the records say about ${w.commitment}, and what is still unknown?`)}>
                    Ask about this</button></span>
              </li>))}
          </ul>)}
    </Section>
  );
}

function RecentIntelligence({ go }: { go: (r: string) => void }) {
  const [tab, setTab] = useState<"agents" | "requests" | "activity">("agents");
  const [ups] = useApi((s) => product.updates(undefined, s), []);
  const [reqs] = useApi((s) => product.clarifications(s), []);
  const [subs] = useApi((s) => product.submissions(undefined, s), []);
  const [open, setOpen] = useState<AgentUpdate | null>(null);
  const ask = useAsk();
  const evidence = useEvidence();
  return (
    <Section title="What changed recently" aria-label="Recent intelligence"
             subtitle="Updates from your team, open questions and new documents.">
      <Tabs label="Recent intelligence" value={tab} onChange={setTab} tabs={[
        { value: "agents", label: "Agent updates", count: ups.state === "ready" ? ups.data.updates.length : undefined },
        { value: "requests", label: "Clarifications", count: reqs.state === "ready" ? reqs.data.requests.length : undefined },
        { value: "activity", label: "New evidence", count: subs.state === "ready" ? subs.data.submissions.length : undefined }]} />
      {tab === "agents" && (ups.state === "loading" ? <Loading /> : ups.state === "error" ? <ErrorState error={ups.error} />
        : ups.data.updates.length === 0
          ? <Guidance title="No agent updates yet">Updates appear after an interview is published.{" "}
              <button className="link" onClick={() => go("agents")}>Interview an agent</button></Guidance>
          : <ul className="plain updates">{ups.data.updates.slice(0, 6).map((u) => (
              <li key={u.update_id}>
                <button className="update" onClick={() => setOpen(u)}>
                  <Avatar name={u.agent_id} size={36} />
                  <span className="update-body">
                    <span className="update-top"><strong>{u.agent_id.replace(/^syn-/, "")}</strong>
                      <span>{u.department.replace(/_/g, " ")}</span><DemoBadge show={u.synthetic} /></span>
                    <span className="update-title">{u.title}</span>
                    <span className="update-why">{KIND_LABEL[u.kind] ?? u.kind}{u.project ? ` in ${u.project}` : ""}.
                      {" "}{u.evidence.length} source{u.evidence.length === 1 ? "" : "s"}.</span>
                  </span>
                  <span className="update-side"><span>{relTime(u.created_at)}</span><Badge value={u.status} /></span>
                </button>
              </li>))}</ul>)}
      {tab === "requests" && (reqs.state === "loading" ? <Loading /> : reqs.state === "error" ? <ErrorState error={reqs.error} />
        : reqs.data.requests.length === 0
          ? <Guidance title="No clarification requests">Ask a department from Intelligence. Veloxa records the request;
              it does not send notifications yet.</Guidance>
          : <ul className="plain updates">{reqs.data.requests.slice(0, 6).map((r) => (
              <li key={r.request_id} className="row-item" style={{ padding: "10px 4px" }}>
                <DeptArt id={r.department_id} size={28} />
                <span style={{ flex: 1, minWidth: 0 }}>{r.question}
                  <span className="small muted" style={{ display: "block" }}>{r.department_id}, {relTime(r.created_at)}</span></span>
                <Badge value={r.state === "open" ? "open" : r.state} label={r.state.replace(/_/g, " ")} />
              </li>))}</ul>)}
      {tab === "activity" && (subs.state === "loading" ? <Loading /> : subs.state === "error" ? <ErrorState error={subs.error} />
        : subs.data.submissions.length === 0
          ? <Guidance title="No submissions">Department contributions appear here as they move through review.</Guidance>
          : <RecentEvidence rows={subs.data.submissions.slice(0, 6)} onOpen={() => go("evidence")} />)}
      {open && (
        <Drawer title={open.title} onClose={() => setOpen(null)}>
          <div className="stack">
            <div className="row"><Avatar name={open.agent_id} size={40} /><div><strong>{open.agent_id}</strong>
              <div className="small muted">{open.department.replace(/_/g, " ")}</div></div>
              <DemoBadge show={open.synthetic} /><Badge value={open.status} /></div>
            <p style={{ maxWidth: "62ch" }}>{open.summary}</p>
            <dl className="meta small">
              <dt>What kind</dt><dd>{KIND_LABEL[open.kind] ?? open.kind}</dd>
              {open.project && <><dt>Project</dt><dd>{open.project}</dd></>}
              <dt>Recorded</dt><dd>{new Date(open.created_at).toLocaleString()}</dd>
              <dt>Session</dt><dd className="mono">{open.session_id.slice(0, 8)}</dd>
              {open.suggested_follow_up && <><dt>Follow-up</dt><dd>{open.suggested_follow_up}</dd></>}
            </dl>
            <div className="row">
              <Button variant="primary" onClick={async () => {
                const t = await product.updateThread(open.update_id); setOpen(null);
                ask.openThread(t.thread_id, `What does "${open.title}" mean for us, and what is still unknown?`); }}>
                Ask about this</Button>
              <Button disabled={open.evidence.length === 0} onClick={() => evidence.open(open.evidence[0], open.title)}>
                View evidence</Button>
              <Button onClick={() => { setOpen(null); go("agents"); }}>View agent</Button>
            </div>
            {open.evidence.length > 1 && <ul className="plain small stack">{open.evidence.map((r) => (
              <li key={r}><button className="link" onClick={() => evidence.open(r, open.title)}>{r.split(":").pop()}</button></li>))}</ul>}
          </div>
        </Drawer>
      )}
    </Section>
  );
}

export function RecentEvidence({ rows, onOpen }: { rows: { submission_id: string; title: string; department_id: string;
  source_type: string; submitted_at: string; data_period: string | null; submitted_by: string; state: string }[];
  onOpen: (id: string) => void }) {
  return (
    <div className="table-wrap"><table>
      <thead><tr><th>Source</th><th>Department</th><th>Uploaded</th><th>Period</th><th>Status</th><th /></tr></thead>
      <tbody>{rows.map((r) => (
        <tr key={r.submission_id}>
          <td>{r.title}<div className="small muted">{r.source_type.replace(/_/g, " ")}</div></td>
          <td>{r.department_id}</td><td>{new Date(r.submitted_at).toLocaleDateString()}</td>
          <td>{r.data_period ?? "Not stated"}</td>
          <td><Badge value={r.state === "projected" ? "active" : "pending_admission"} label={r.state.replace(/_/g, " ")} /></td>
          <td><Button variant="ghost" onClick={() => onOpen(r.submission_id)}>View</Button></td>
        </tr>))}</tbody>
    </table></div>
  );
}
