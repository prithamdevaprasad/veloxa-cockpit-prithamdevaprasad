import { useState } from "react";
import { api } from "../api/client";
import { useApi } from "../api/useApi";
import type { Answer } from "../api/types";
import { AnswerView } from "../ui/AnswerView";
import { BarList, ErrorState, Loading, PageHead, Section, Segmented } from "../ui";

type Dept = "engineering" | "finance" | "people_ops" | "marketing";

// Each view is a fixed set of governed questions answered by the Ask pipeline, with its citations and grade.
// No department view reads payroll, private messages, pulse comments or leave reasons, and none ranks people.
const VIEWS: Record<Dept, { label: string; questions: string[]; note: string }> = {
  engineering: { label: "Engineering", note: "Work-level only: commit activity is never shown as productivity.",
    questions: ["How many employees are in Engineering?", "What did the board updates report?"] },
  finance: { label: "Finance", note: "Ledger spend excludes payroll lines; payroll aggregates stay withheld (C19 release is off).",
    questions: ["What is the ledger spend by cost centre?", "What is the ARR?"] },
  people_ops: { label: "People Operations", note: "Recorded status only: no attrition prediction, no reasons for personal records.",
    questions: ["What is the headcount by department?"] },
  marketing: { label: "Marketing", note: "Department records as admitted; nothing is inferred about individuals.",
    questions: ["How many employees are in Marketing?"] },
};

/** Numeric facts the backend returned, grouped by one labelled dimension (no client-side invention). */
function bars(a: Answer, dim: string): { label: string; value: number }[] {
  const out = new Map<string, number>();
  for (const f of (a as unknown as { facts?: { display: string; value: unknown }[] }).facts ?? []) {
    const m = new RegExp(`${dim} ([^,:)]+)`).exec(f.display);
    if (m && typeof f.value === "number") out.set(m[1].trim(), (out.get(m[1].trim()) ?? 0) + f.value);
  }
  return [...out.entries()].map(([label, value]) => ({ label, value })).sort((x, y) => y.value - x.value);
}

function Question({ q }: { q: string }) {
  const [a, reload] = useApi((s) => api.ask(q, undefined, s), [q]);
  const chart = a.state === "ready" && a.data.status !== "refused"
    ? (q.includes("cost centre") ? { rows: bars(a.data, "cost_centre"), unit: "INR" }
      : q.includes("by department") ? { rows: bars(a.data, "department"), unit: "people" } : null)
    : null;
  return (
    <Section title={q}>
      {a.state === "loading" && <Loading />}
      {a.state === "error" && <ErrorState error={a.error} onRetry={reload} />}
      {a.state === "ready" && (
        <div className="stack">
          {chart && chart.rows.length > 0 && <BarList rows={chart.rows} unit={chart.unit} />}
          <AnswerView answer={a.data} />
        </div>
      )}
    </Section>
  );
}

export function DepartmentsPage() {
  const [dept, setDept] = useState<Dept>("engineering");
  const v = VIEWS[dept];
  return (
    <>
      <PageHead title="Departments" intro="Governed answers per department, each with its evidence and grade." />
      <div className="row" style={{ marginBottom: 12 }}>
        <Segmented label="Department" value={dept} onChange={setDept}
                   options={(Object.keys(VIEWS) as Dept[]).map((d) => ({ value: d, label: VIEWS[d].label }))} />
      </div>
      <p className="small muted" style={{ marginBottom: 12 }}>{v.note}</p>
      {v.questions.map((q) => <Question key={q} q={q} />)}
    </>
  );
}
