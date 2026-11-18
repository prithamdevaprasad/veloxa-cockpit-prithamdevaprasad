// The 14 agent people: evidence-grounded work facts, workload from linked records, labelled persona assumptions and
// what is unknown. Real people are shown by initials (never an illustration or a generated portrait).
import { useState } from "react";
import { product } from "../api/product";
import type { Person } from "../api/product-types";
import { useApi } from "../api/useApi";
import { AgentAvatar } from "../ui/art";
import { useEvidence } from "../ui/EvidenceDrawer";
import { Guidance, Tabs } from "../ui/kit";
import { Drawer, ErrorState, Loading, Section } from "../ui";

function workloadLine(p: Person): string {
  const w = p.workload;
  if (!w) return "Workload unknown";
  const parts = [w.open_tasks === null ? "open tasks unknown" : `${w.open_tasks} open task(s)`];
  if (w.requisitions_owned.length) parts.push(`${w.requisitions_owned.length} requisition(s)`);
  if (w.repositories.length) parts.push(`${w.repositories.length} repository(ies)`);
  return parts.join(", ");
}

function Profile({ p, onClose }: { p: Person; onClose: () => void }) {
  const [tab, setTab] = useState<"overview" | "workload" | "personal" | "unknowns">("overview");
  const evidence = useEvidence();
  return (
    <Drawer title={p.name ?? p.agent_id} onClose={onClose}>
      <div className="stack">
        <div className="row" style={{ flexWrap: "nowrap", gap: 12 }}>
          <AgentAvatar id={p.person_record ?? p.agent_id} size={48} />
          <div><strong>{p.title}</strong><div className="small muted">{p.department}. AI representation of this
            person, grounded in company records; not the employee.</div></div>
        </div>
        <Tabs label="Profile" value={tab} onChange={setTab} tabs={[{ value: "overview", label: "Overview" },
          { value: "workload", label: "Workload" }, { value: "personal", label: "Personal" },
          { value: "unknowns", label: "Not yet known" }]} />
        {tab === "overview" && <dl className="meta">{(p.verified_facts ?? []).map((f) => (
          <><dt key={`k${f.fact}`}>{f.fact}</dt><dd key={`v${f.fact}`}>{f.value}{" "}
            <button className="link small" onClick={() => evidence.open(f.record, f.fact)}>source</button></dd></>))}</dl>}
        {tab === "workload" && p.workload && (
          <div className="stack small">
            <p>{workloadLine(p)}.</p>
            {p.workload.requisitions_owned.length > 0 && <p>Requisitions owned: {p.workload.requisitions_owned.join(", ")}</p>}
            {p.workload.repositories.length > 0 && <p>Repositories (allocation only, never activity counts):{" "}
              {p.workload.repositories.map((r) => `${r.repo} (${r.first} to ${r.last})`).join("; ")}</p>}
            <p className="muted">{p.workload.basis}.</p>
          </div>)}
        {tab === "personal" && ((p.assumptions ?? []).length === 0 ? <p className="small muted">No persona details.</p>
          : <ul className="plain stack">{p.assumptions!.map((a) => (
              <li key={a.assumption_id} className="assume small"><strong>{a.category.replace(/_/g, " ")}:</strong>{" "}
                {a.value}<div className="muted">{a.label}</div></li>))}</ul>)}
        {tab === "unknowns" && <ul className="bullets small">{p.unknowns.map((u) => <li key={u}>{u}</li>)}</ul>}
      </div>
    </Drawer>
  );
}

export function PeopleSection() {
  const [ppl, reload] = useApi((s) => product.people(s), []);
  const [open, setOpen] = useState<Person | null>(null);
  return (
    <Section title="People" aria-label="People" subtitle="The 14 people the agents represent, each grounded in the HR
      record and the systems that can be linked to them by employee ID or exact work e-mail.">
      {ppl.state === "loading" && <Loading lines={3} />}
      {ppl.state === "error" && <ErrorState error={ppl.error} onRetry={reload} />}
      {ppl.state === "ready" && (ppl.data.count === 0
        ? <Guidance title="No people mapped yet">Agents are mapped to people only by an owner-approved decision; until
            then the slots below stay unassigned.</Guidance>
        : <div className="person-grid">{ppl.data.people.map((p) => (
            <button key={p.agent_id} className="person-card" onClick={() => setOpen(p)}>
              <AgentAvatar id={p.person_record ?? p.agent_id} size={40} />
              <span style={{ minWidth: 0 }}><strong>{p.name}</strong><div className="t">{p.title}, {p.department}</div>
                <div className="w">{workloadLine(p)}</div></span>
            </button>))}</div>)}
      {open && <Profile p={open} onClose={() => setOpen(null)} />}
    </Section>
  );
}
