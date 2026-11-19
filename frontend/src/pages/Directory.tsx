// People: the real HR directory (work facts from the HR export). AI representations live in the AI team section.
import { useState } from "react";
import { get } from "../api/client";
import { useApi } from "../api/useApi";
import { ErrorState, Loading, PageHead, Section } from "../ui";

interface Row { name: string; title: string; department: string; status: string; join_date: string | null;
  manager: string | null; has_ai_representation: boolean }

export function DirectoryPage() {
  const [d] = useApi((s) => get<{ people: Row[]; count: number; withheld: number }>("/v1/directory", s), []);
  const [q, setQ] = useState("");
  const rows = d.state === "ready" ? d.data.people.filter((r) =>
    `${r.name} ${r.title} ${r.department}`.toLowerCase().includes(q.toLowerCase())) : [];
  const depts = [...new Set(rows.map((r) => r.department))].sort();
  return (
    <>
      <PageHead title="People" intro="The company's people as recorded in the HR export: role, department, reporting
        line and recorded status. Pay, leave and personal details are not shown. AI representations are separate
        assistants; see AI team." />
      <Section title="Directory" subtitle={d.state === "ready" ? `${d.data.count} people in the HR export` +
               (d.data.withheld ? ` (${d.data.withheld} not visible to you)` : "") : undefined}>
        {d.state === "loading" && <Loading lines={6} />}
        {d.state === "error" && <ErrorState error={d.error} />}
        {d.state === "ready" && <>
          <input className="input" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter by name, title or department"
                 aria-label="Filter people" style={{ maxWidth: 360, marginBottom: 12 }} />
          {depts.map((dep) => (
            <div key={dep} className="stack" style={{ marginBottom: 16 }}>
              <h3>{dep} <span className="small muted">({rows.filter((r) => r.department === dep).length})</span></h3>
              <div className="table-wrap"><table className="crit-table">
                <thead><tr><th>Name</th><th>Title</th><th>Reports to</th><th>Recorded status</th><th>Joined</th><th>AI representation</th></tr></thead>
                <tbody>{rows.filter((r) => r.department === dep).map((r) => (
                  <tr key={r.name + r.title}><td>{r.name}</td><td>{r.title}</td><td>{r.manager ?? "—"}</td>
                    <td>{r.status}</td><td>{r.join_date ?? "—"}</td>
                    <td>{r.has_ai_representation ? <a href="#/agents">Available (not the person)</a> : <span className="muted">None</span>}</td></tr>))}
                </tbody></table></div>
            </div>))}
        </>}
      </Section>
    </>
  );
}
