import { useState } from "react";
import { api } from "../api/client";
import { product } from "../api/product";
import type { AgentUpdate, ChartSource } from "../api/product-types";
import { useApi } from "../api/useApi";
import { useAsk } from "../ui/AskPill";
import { useEvidence } from "../ui/EvidenceDrawer";
import { Avatar, BarChart, DemoBadge, Guidance, KpiCard, LineChart, Tabs } from "../ui/kit";
import { Badge, Button, Drawer, ErrorState, Loading, Section } from "../ui";

// Charts are only drawn for sources the viewer may read; each says what it was computed from.
const CHARTS: { system: string; title: string; x: string; agg: "sum" | "count"; y?: string; kind: "line" | "bar" }[] = [
  { system: "revenue_reporting", title: "ARR by month", x: "Month", agg: "sum", y: "ARR", kind: "line" },
  { system: "fp_and_a", title: "Spend variance by department (actual − budget)", x: "Department", agg: "sum",
    y: "Variance", kind: "bar" },
  { system: "crm", title: "Pipeline value by stage", x: "Stage", agg: "sum", y: "Amount", kind: "bar" },
  { system: "issue_tracker", title: "Issues by status", x: "Status", agg: "count", kind: "bar" },
  { system: "people_metrics", title: "Headcount snapshots by month", x: "Month", agg: "sum", y: "Headcount", kind: "line" },
  { system: "incident_log", title: "Incidents by month", x: "Month", agg: "count", kind: "bar" },
  { system: "zoho_books", title: "Ledger lines by cost centre", x: "Cost Centre", agg: "count", kind: "bar" },
];

function Chart({ c }: { c: (typeof CHARTS)[number] }) {
  const [s] = useApi((sig) => product.series({ system: c.system, x: c.x, agg: c.agg, y: c.y }, sig), [c.system]);
  if (s.state === "loading") return <Loading lines={4} />;
  if (s.state === "error") return <Guidance title="Not available">{s.error.message}</Guidance>;
  const pts = s.data.points.map((p) => ({ x: p.x, value: p.value }));
  return (
    <div className="stack">
      {c.kind === "line" ? <LineChart points={pts} label={c.title} />
        : <BarChart rows={pts.map((p) => ({ label: p.x, values: [p.value] }))} series={[c.y ?? "count"]} label={c.title} />}
      <p className="small muted">{s.data.basis} · {s.data.sources.join(", ")}</p>
    </div>
  );
}

export function OverviewPage({ go }: { go: (r: string) => void }) {
  const [me] = useApi((s) => product.me(s), []);
  const [ov, reloadOv] = useApi((s) => product.overview(s), []);
  const [srcs] = useApi((s) => product.chartSources(s), []);
  const [deps] = useApi((s) => api.dependencies(s), []);
  const [today] = useApi((s) => api.today(s), []);
  const ask = useAsk();
  const name = me.state === "ready" ? (me.data.principal.display_name ?? "there") : "";
  const available = srcs.state === "ready" ? new Set(srcs.data.sources.map((x: ChartSource) => x.system)) : new Set<string>();
  const charts = CHARTS.filter((c) => available.has(c.system)).slice(0, 2);
  return (
    <>
      <header className="page-top">
        <div>
          <h1>Welcome{name ? `, ${name}` : ""}</h1>
          <p className="muted small">
            {me.state === "ready" && <>{me.data.tenant.display_name} · </>}
            {ov.state === "ready" ? ov.data.read_basis.banner : "Loading the knowledge point…"}{" "}
            <DemoBadge show={ov.state === "ready" && ov.data.synthetic} />
          </p>
        </div>
        <div className="row">
          <Button onClick={() => ask.ask("")}>Ask Veloxa</Button>
          <Button onClick={() => go("evidence")}>Upload evidence</Button>
        </div>
      </header>

      <Section aria-label="Key figures">
        {ov.state === "loading" && <Loading lines={2} />}
        {ov.state === "error" && <ErrorState error={ov.error} onRetry={reloadOv} />}
        {ov.state === "ready" && (ov.data.kpis.length === 0
          ? <Guidance title="Nothing to show yet">{ov.data.notes[0] ?? "No admitted evidence you may read."}</Guidance>
          : <div className="kpi-strip">{ov.data.kpis.map((k, i) => (
              <KpiCard key={k.key} label={k.label} value={typeof k.value === "number" ? k.value.toLocaleString() : k.value}
                       basis={k.basis} dark={i === 0} />))}</div>)}
      </Section>

      <div className="grid-2">
        {charts.length === 0 ? (
          <Section title="Analytics"><Guidance title="No chartable sources">
            Charts appear once departments contribute exports you are authorised to read (revenue, budgets, pipeline,
            issues, incidents). Upload them in Evidence &amp; Data.</Guidance></Section>
        ) : charts.map((c) => <Section key={c.system} title={c.title}><Chart c={c} /></Section>)}
      </div>

      <div className="grid-2">
        <Section title="Needs attention" subtitle="Evidence-backed signals. Not ranked: ranking needs the owner's D-15 values.">
          {deps.state === "loading" && <Loading lines={3} />}
          {deps.state === "error" && <ErrorState error={deps.error} />}
          {deps.state === "ready" && (
            deps.data.warnings.length === 0 && (today.state !== "ready" || today.data.today.items.length === 0)
              ? <Guidance title="Nothing flagged">No admitted record states a delay, a missing date, a single-source
                  commitment or a conflict right now.</Guidance>
              : <ul className="plain stack">
                  {deps.data.warnings.slice(0, 6).map((w, i) => (
                    <li key={i} className="attention">
                      <Badge value="open" label={w.kind.replace(/_/g, " ")} />
                      <span>{w.statement}</span>
                      <Button variant="ghost" onClick={() => ask.ask(`What do we know about ${w.commitment}?`)}>Ask</Button>
                    </li>))}
                  {today.state === "ready" && today.data.today.items.map((t, i) => (
                    <li key={`t${i}`} className="attention"><Badge value="open" label="watch" /><span>{t.title}</span></li>))}
                </ul>)}
        </Section>
        <Section title="Quick access">
          <div className="quick">
            <button onClick={() => ask.ask("")}>Ask Veloxa</button>
            <button onClick={() => go("evidence")}>Upload evidence</button>
            <button onClick={() => go("evidence")}>Review submissions</button>
            <button onClick={() => go("agents")}>View agents</button>
            <button onClick={() => go("departments")}>View departments</button>
            <button onClick={() => go("intelligence")}>Review conflicts</button>
          </div>
        </Section>
      </div>

      <RecentUpdates go={go} />
    </>
  );
}

const KIND_LABEL: Record<string, string> = {
  new_finding: "New finding", commitment_changed: "Commitment changed", dependency_identified: "Dependency",
  source_disagreement: "Source disagreement", incident_update: "Incident", department_update: "Department update",
  unresolved_question: "Unresolved question", new_evidence: "New evidence",
};

function RecentUpdates({ go }: { go: (r: string) => void }) {
  const [tab, setTab] = useState<"agents" | "conversations" | "activity">("agents");
  const [ups] = useApi((s) => product.updates(undefined, s), []);
  const [threads] = useApi((s) => product.threads(false, s), []);
  const [subs] = useApi((s) => product.submissions(undefined, s), []);
  const [open, setOpen] = useState<AgentUpdate | null>(null);
  const ask = useAsk();
  const evidence = useEvidence();
  return (
    <Section title="Recent Agent Updates" subtitle="Summaries of persisted agent sessions, with their evidence.">
      <Tabs label="Recent activity" value={tab} onChange={setTab} tabs={[
        { value: "agents", label: "Agents", count: ups.state === "ready" ? ups.data.updates.length : undefined },
        { value: "conversations", label: "Conversations",
          count: threads.state === "ready" ? threads.data.threads.length : undefined },
        { value: "activity", label: "Activity", count: subs.state === "ready" ? subs.data.submissions.length : undefined }]} />
      {tab === "agents" && (ups.state === "loading" ? <Loading /> : ups.state === "error" ? <ErrorState error={ups.error} />
        : ups.data.updates.length === 0
          ? <Guidance title="No agent updates yet">An update is published from a completed agent interview. Open an
              agent, interview it, then publish its update.</Guidance>
          : <ul className="plain updates">{ups.data.updates.slice(0, 8).map((u) => (
              <li key={u.update_id}>
                <button className="update" onClick={() => setOpen(u)}>
                  <Avatar name={u.agent_id} />
                  <span className="update-body">
                    <span className="update-top"><strong>{u.agent_id.replace(/^syn-/, "")}</strong>
                      <span className="muted small">{u.department.replace("_", " ")}</span>
                      <DemoBadge show={u.synthetic} /></span>
                    <span className="update-title">{u.title}</span>
                    <span className="small muted">{KIND_LABEL[u.kind] ?? u.kind}{u.project ? ` · ${u.project}` : ""} ·
                      {" "}{u.evidence.length} source(s) · {new Date(u.created_at).toLocaleString()}</span>
                  </span>
                  <Badge value={u.status} />
                </button>
              </li>))}</ul>)}
      {tab === "conversations" && (threads.state !== "ready" ? <Loading /> : threads.data.threads.length === 0
        ? <Guidance title="No saved conversations">Ask Veloxa from the pill below; conversations are saved here.</Guidance>
        : <ul className="plain">{threads.data.threads.slice(0, 8).map((t) => (
            <li key={t.thread_id} className="row-item"><span>{t.title}</span>
              <span className="small muted">{t.answers} answer(s)</span></li>))}</ul>)}
      {tab === "activity" && (subs.state !== "ready" ? <Loading /> : subs.data.submissions.length === 0
        ? <Guidance title="No submissions">Department contributions appear here as they move through review.</Guidance>
        : <RecentEvidence rows={subs.data.submissions.slice(0, 8)} onOpen={() => go("evidence")} />)}
      {open && (
        <Drawer title={open.title} onClose={() => setOpen(null)}>
          <div className="stack">
            <div className="row"><Avatar name={open.agent_id} /><strong>{open.agent_id}</strong><DemoBadge show={open.synthetic} />
              <Badge value={open.status} /></div>
            <p>{open.summary}</p>
            <p className="small muted">{KIND_LABEL[open.kind] ?? open.kind}{open.project ? ` · ${open.project}` : ""} ·
              session {open.session_id.slice(0, 8)} · {new Date(open.created_at).toLocaleString()}</p>
            <div className="row">
              <Button variant="primary" onClick={async () => {
                const t = await product.updateThread(open.update_id); setOpen(null); ask.openThread(t.thread_id); }}>
                Ask about this</Button>
              <Button disabled={open.evidence.length === 0} onClick={() => evidence.open(open.evidence[0], open.title)}>
                View evidence</Button>
              <Button onClick={() => { setOpen(null); go("agents"); }}>View agent</Button>
            </div>
            {open.evidence.length > 1 && <ul className="plain small">{open.evidence.map((r) => (
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
      <thead><tr><th>Source</th><th>Department</th><th>Uploaded</th><th>Period</th><th>Contributor</th><th>Status</th><th /></tr></thead>
      <tbody>{rows.map((r) => (
        <tr key={r.submission_id}>
          <td>{r.title}<div className="small muted">{r.source_type.replace(/_/g, " ")}</div></td>
          <td>{r.department_id}</td><td>{new Date(r.submitted_at).toLocaleDateString()}</td>
          <td>{r.data_period ?? "—"}</td><td className="small">{r.submitted_by.replace("person:", "")}</td>
          <td><Badge value={r.state === "projected" ? "active" : "pending_admission"} label={r.state.replace(/_/g, " ")} /></td>
          <td><Button variant="ghost" onClick={() => onOpen(r.submission_id)}>View</Button></td>
        </tr>))}</tbody>
    </table></div>
  );
}
