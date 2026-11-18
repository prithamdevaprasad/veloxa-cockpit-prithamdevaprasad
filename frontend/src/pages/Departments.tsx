// Departments: the tenant's departments (from /v1/departments), each with governed questions answered by the Ask
// pipeline (with citations and grade) and the department's recent agent updates. No department view reads payroll,
// private messages, pulse comments or leave reasons, none ranks people, and none invents a health score.
import { useState } from "react";
import { api } from "../api/client";
import { product } from "../api/product";
import { useApi } from "../api/useApi";
import { AnswerView } from "../ui/AnswerView";
import { DeptArt } from "../ui/art";
import { useAsk } from "../ui/Ask";
import { Avatar, DemoBadge, Guidance } from "../ui/kit";
import { ErrorState, Loading, PageHead, Section } from "../ui";
import { relTime } from "./Overview";

type View = { label: string; questions: string[]; note: string };
const VIEWS: Record<string, View> = {
  engineering: { label: "Engineering", note: "Work-level only: commit activity is never shown as productivity.",
    questions: ["How many employees are in Engineering?", "What did the board updates report?"] },
  finance: { label: "Finance", note: "Ledger spend excludes payroll lines; payroll aggregates stay withheld (C19 release is off).",
    questions: ["What is the ledger spend by cost centre?", "What is the ARR?"] },
  people_ops: { label: "People Operations", note: "Recorded status only: no attrition prediction, no reasons for personal records.",
    questions: ["What is the headcount by department?"] },
  marketing: { label: "Marketing", note: "Department records as admitted; nothing is inferred about individuals.",
    questions: ["How many employees are in Marketing?"] },
};
const ALIAS: Record<string, string> = { people: "people_ops" };
const GENERIC = "Department records as admitted; nothing is inferred about individuals.";

function Question({ q }: { q: string }) {
  const [a, reload] = useApi((s) => api.ask(q, undefined, s), [q]);
  return (
    <Section title={q}>
      {a.state === "loading" && <Loading />}
      {a.state === "error" && <ErrorState error={a.error} onRetry={reload} />}
      {a.state === "ready" && <AnswerView answer={a.data} />}
    </Section>
  );
}

function Updates({ dept }: { dept: string }) {
  const [ups] = useApi((s) => product.updates(undefined, s), []);
  const ask = useAsk();
  if (ups.state === "loading") return <Loading lines={3} />;
  if (ups.state === "error") return <ErrorState error={ups.error} />;
  const rows = ups.data.updates.filter((u) => u.department === dept || ALIAS[u.department] === dept
                                              || u.department === ALIAS[dept]);
  return rows.length === 0 ? <Guidance title="No agent updates">None of this department's agents has a published update.</Guidance>
    : <ul className="plain updates">{rows.slice(0, 6).map((u) => (
        <li key={u.update_id} className="row-item" style={{ alignItems: "flex-start", padding: "10px 0" }}>
          <Avatar name={u.agent_id} size={32} />
          <span style={{ flex: 1, minWidth: 0 }}>
            <span className="small muted">{u.agent_id.replace(/^syn-/, "")}, {relTime(u.created_at)} <DemoBadge show={u.synthetic} /></span>
            <span style={{ display: "block" }}>{u.title}</span>
            <button className="link small" onClick={async () => {
              const t = await product.updateThread(u.update_id); ask.openThread(t.thread_id); }}>Ask about this</button>
          </span>
        </li>))}</ul>;
}

export function DepartmentsPage({ go }: { go?: (r: string) => void }) {
  const [deps] = useApi((s) => product.departments(s), []);
  const listed = deps.state === "ready" ? deps.data.departments.map((d) => ({ id: d.department_id, name: d.name })) : [];
  const names = new Set(listed.map((d) => d.name.toLowerCase()));
  const all = [...listed, ...Object.entries(VIEWS).filter(([, v]) => !names.has(v.label.toLowerCase()))
                                                  .map(([id, v]) => ({ id, name: v.label }))];
  const [sel, setSel] = useState<string | null>(null);
  const cur = all.find((d) => d.id === sel) ?? all[0];
  const view: View | undefined = cur && (VIEWS[cur.id] ?? VIEWS[ALIAS[cur.id] ?? ""]);
  const questions = view?.questions ?? (cur ? [`How many employees are in ${cur.name}?`] : []);
  return (
    <>
      <PageHead title="Departments" intro="Governed answers for each department, with their evidence, and what the
        department's agents have reported recently." />
      {deps.state === "loading" && <Loading lines={2} />}
      {cur && (
        <>
          <div className="seg" role="group" aria-label="Department" style={{ marginBottom: 16 }}>
            {all.map((d) => (
              <button key={d.id} aria-pressed={d.id === cur.id} onClick={() => setSel(d.id)}>
                <DeptArt id={d.id} size={22} /><span>{d.name}</span>
              </button>))}
          </div>
          <div className="row" style={{ gap: 14, marginBottom: 16, flexWrap: "nowrap" }}>
            <DeptArt id={cur.id} size={52} />
            <div><h2 style={{ fontSize: 18 }}>{cur.name}</h2>
              <p className="small muted" style={{ maxWidth: "70ch" }}>{view?.note ?? GENERIC}</p></div>
          </div>
          <div className="ov-grid">
            <div className="span-8">{questions.map((q) => <Question key={q} q={q} />)}</div>
            <Section title="Recent agent updates" className="span-4">
              <Updates dept={cur.id} />
              {go && <p className="small" style={{ marginTop: 10 }}>
                <button className="link" onClick={() => go("agents")}>See all agents</button></p>}
            </Section>
          </div>
        </>
      )}
    </>
  );
}
