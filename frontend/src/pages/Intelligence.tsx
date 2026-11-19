import { useState } from "react";
import { ApiError, api } from "../api/client";
import { product } from "../api/product";
import { useApi } from "../api/useApi";
import { Guidance, Tabs } from "../ui/kit";
import { Badge, Button, ErrorState, Loading, PageHead, Section } from "../ui";
import { ConflictsPage } from "./Conflicts";
import { Dependencies } from "./Scenarios";

/** One place for conflicts, changes, dependencies, early warnings, missing evidence and open questions. Every signal
 * shows its evidence; nothing is ranked or given a severity (D-15 is pending). */
export function IntelligencePage() {
  const [tab, setTab] = useState<"conflicts" | "changes" | "dependencies" | "questions">("conflicts");
  return (
    <>
      <PageHead title="Intelligence" intro="Where the evidence disagrees, what changed between knowledge points, what
        depends on what, and what is still unknown. Signals are not ranked." />
      <Tabs label="Intelligence views" value={tab} onChange={setTab} tabs={[
        { value: "conflicts", label: "Conflicts" }, { value: "changes", label: "Changes" },
        { value: "dependencies", label: "Dependencies & warnings" }, { value: "questions", label: "Open questions" }]} />
      {tab === "conflicts" && <ConflictsPage />}
      {tab === "changes" && <Changes />}
      {tab === "dependencies" && <Dependencies />}
      {tab === "questions" && <Questions />}
    </>
  );
}

function Changes() {
  const [h, reload] = useApi((s) => api.riskHistory(s), []);
  return (
    <Section title="Changes across knowledge points" subtitle="How evidence-backed items appeared, changed, resolved or reopened.">
      {h.state === "loading" && <Loading />}
      {h.state === "error" && <ErrorState error={h.error} onRetry={reload} />}
      {h.state === "ready" && (() => {
        const events = (h.data as unknown as { events?: { batch_seq: number; change: string; subject?: string;
          risk_id?: string; fields?: string[] }[] }).events ?? [];
        return events.length === 0
          ? <Guidance title="No changes recorded">Changes appear when a later admitted batch alters an earlier signal.</Guidance>
          : <ul className="plain stack">{events.map((e, i) => (
              <li key={i} className="row-item"><Badge value="open" label={e.change} />
                <span>{e.subject ?? e.risk_id}</span><span className="small muted">release {e.batch_seq}
                  {e.fields?.length ? ` · ${e.fields.join(", ")}` : ""}</span></li>))}</ul>;
      })()}
    </Section>
  );
}

function Questions() {
  const [list, reload] = useApi((s) => product.clarifications(s), []);
  const [depts] = useApi((s) => product.departments(s), []);
  const [dept, setDept] = useState("");
  const [q, setQ] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);
  async function send(submit: boolean) {
    setErr(null); setMsg(null);
    try {
      const r = await product.clarify(dept, q, {}, submit);
      setMsg(`${submit ? "Submitted" : "Saved as draft"}. Suggested recipient: ${r.suggested_recipient?.display_name ?? "no department head on record"}. ${r.delivery}.`);
      setQ(""); reload();
    } catch (x) { setErr(x instanceof ApiError ? x : new ApiError(0, "unknown", String(x))); }
  }
  return (
    <>
      <Section title="Ask a department" subtitle="Request a clarification. Veloxa contacts nobody; an answer meant as
        evidence must arrive as a data-room submission.">
        {depts.state === "ready" && depts.data.departments.length === 0
          ? <Guidance title="No departments">A workspace administrator creates departments first.</Guidance>
          : <div className="stack">
              <div className="row">
                <label className="field">Department<select className="input" value={dept} onChange={(e) => setDept(e.target.value)}
                       aria-label="Department"><option value="" disabled>Select</option>
                  {depts.state === "ready" && depts.data.departments.map((d) => <option key={d.department_id}
                    value={d.department_id}>{d.name}</option>)}</select></label>
              </div>
              <textarea className="input" rows={3} value={q} onChange={(e) => setQ(e.target.value)}
                        aria-label="Clarification question" placeholder="e.g. Can Engineering clarify why the milestone changed?" />
              <div className="row"><Button variant="primary" disabled={!dept || !q.trim()} onClick={() => send(true)}>Submit request</Button>
                <Button disabled={!dept || !q.trim()} onClick={() => send(false)}>Save draft</Button></div>
              {msg && <p className="callout ok small" role="status">{msg}</p>}
              {err && <ErrorState error={err} />}
            </div>}
      </Section>
      <Section title="Open questions">
        {list.state === "loading" && <Loading />}
        {list.state === "error" && <ErrorState error={list.error} />}
        {list.state === "ready" && (list.data.requests.length === 0
          ? <Guidance title="No requests">Clarification requests you can see are listed here with their status.</Guidance>
          : <ul className="plain stack">{list.data.requests.map((r) => (
              <li key={r.request_id} className="row-item"><Badge value={r.state === "answered" ? "active" : "open"}
                label={r.state} /><span>{r.question}</span><span className="small muted">{r.department_id}
                {r.suggested_recipient ? ` · to ${r.suggested_recipient.replace("person:", "")}` : ""}</span></li>))}</ul>)}
      </Section>
    </>
  );
}
