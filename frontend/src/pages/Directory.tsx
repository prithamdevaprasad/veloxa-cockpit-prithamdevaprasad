// People: the real HR directory (work facts from the HR export), grouped by department. Each person may also have an
// AI assistant that can be interviewed about their area of work.
import { useState } from "react";
import { get } from "../api/client";
import { useApi } from "../api/useApi";
import { Icon } from "../ui/kit";
import { ErrorState, Loading, PageHead } from "../ui";

interface Row { name: string; title: string; department: string; status: string; join_date: string | null;
  manager: string | null; has_ai_representation: boolean; assistant_id: string | null }

function Stat({ icon, value, label, tone }: { icon: string; value: number; label: string; tone: string }) {
  return (
    <div className="stat-tile">
      <span className={`stat-icon ${tone}`}><Icon name={icon} size={22} /></span>
      <span><strong className="stat-value">{value}</strong><span className="stat-label">{label}</span></span>
    </div>);
}

export function DirectoryPage() {
  const [d] = useApi((s) => get<{ people: Row[]; count: number; withheld: number }>("/v1/directory", s), []);
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const all = d.state === "ready" ? d.data.people : [];
  const rows = all.filter((r) => `${r.name} ${r.title} ${r.department}`.toLowerCase().includes(q.toLowerCase()));
  const depts = [...new Set(rows.map((r) => r.department))].sort();
  const isOpen = (dep: string, i: number) => open[dep] ?? (q.trim() !== "" || i === 0);
  return (
    <>
      <PageHead title="People" intro="Everyone in the HR export: role, department, reporting line and recorded status.
        Pay, leave and personal details are not shown." />
      {d.state === "loading" && <Loading lines={6} />}
      {d.state === "error" && <ErrorState error={d.error} />}
      {d.state === "ready" && <>
        <div className="stat-row">
          <Stat icon="agents" value={d.data.count} label="People in the HR export" tone="plum" />
          <Stat icon="departments" value={new Set(all.map((r) => r.department)).size} label="Departments" tone="lav" />
          <Stat icon="check" value={all.filter((r) => /active/i.test(r.status)).length} label="Recorded as active" tone="ok" />
          <Stat icon="chat" value={all.filter((r) => r.assistant_id).length} label="AI assistants available" tone="lav" />
        </div>
        <section className="card dir-card" aria-label="Directory">
          <div className="dir-head">
            <h2>Directory</h2>
            <label className="search-field"><Icon name="search" size={16} />
              <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search people by name, title or department"
                     aria-label="Filter people" /></label>
          </div>
          {d.data.withheld > 0 && <p className="small muted">{d.data.withheld} people are not visible to you.</p>}
          {depts.length === 0 && <p className="muted">No one matches “{q}”.</p>}
          {depts.map((dep, i) => {
            const people = rows.filter((r) => r.department === dep);
            const shown = isOpen(dep, i);
            return (
              <div key={dep} className={`dept-group${shown ? " open" : ""}`}>
                <button className="dept-toggle" aria-expanded={shown} onClick={() => setOpen({ ...open, [dep]: !shown })}>
                  <span className="chev" aria-hidden="true">›</span>
                  <span className="dept-icon"><Icon name="agents" size={18} /></span>
                  <strong>{dep}</strong> <span className="muted">({people.length})</span>
                </button>
                {shown && <div className="table-wrap"><table className="dir-table">
                  <thead><tr><th>Name</th><th>Title</th><th>Reports to</th><th>Status</th><th>Joined</th><th>AI assistant</th></tr></thead>
                  <tbody>{people.map((r) => (
                    <tr key={r.name + r.title}>
                      <td><strong className="person-name">{r.name}</strong></td><td>{r.title}</td><td>{r.manager ?? "—"}</td>
                      <td><span className={`pill ${/active/i.test(r.status) ? "ok" : "neutral"}`}>{r.status}</span></td>
                      <td className="num">{r.join_date ?? "—"}</td>
                      <td>{r.assistant_id
                        ? <a className="link" href={`#/agents?interview=${r.assistant_id}`}>Interview</a>
                        : <span className="muted">Not set up</span>}</td>
                    </tr>))}
                  </tbody></table></div>}
              </div>);
          })}
        </section>
      </>}
    </>
  );
}
