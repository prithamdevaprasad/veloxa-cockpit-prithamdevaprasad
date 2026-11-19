// People in Jira: which employee each Jira assignee is. Links are made only by a verified exact identifier (Jira's
// e-mail for the account equals one HR work e-mail; compared on the server, never shown) or by an administrator's
// explicit mapping with a reason. Name-based suggestions are hints, never links. Every change is a new versioned
// decision; the history stays visible. Only administrators holding `resolve` can open this.
import { useState } from "react";
import { ApiError, get, request } from "../api/client";
import { useApi } from "../api/useApi";
import { Button, ErrorState, Loading, Section } from "../ui";

interface Emp { emp_id: string; name: string | null; title?: string; department?: string }
interface Ident {
  identity_id: string; context: string; value: string; jira_name: string | null; issues: number;
  state: "mapped" | "suggested" | "ambiguous" | "unmapped"; mapped_to: Emp | null;
  mapped_by: { kind: string; by: string; at: string; rationale: string } | null;
  suggestions: { classification: string; employee: Emp; approvable: boolean }[];
  history: { version: number; outcome: string; kind: string; employee: Emp; by: string; at: string; rationale: string }[];
}
interface Out { identities: Ident[]; employees: Emp[]; batch_seq: number }

const STATE: Record<Ident["state"], [string, string]> = {
  mapped: ["Linked", "ok"], suggested: ["Needs confirmation", "warn"], ambiguous: ["Ambiguous", "warn"],
  unmapped: ["Not linked", "neutral"],
};
const HOW: Record<string, string> = { exact_identifier: "verified e-mail match", administrator_mapping: "administrator" };
const err = (e: unknown) => (e instanceof ApiError ? e.message : String(e));

function Row({ i, employees, onDone }: { i: Ident; employees: Emp[]; onDone: () => void }) {
  const [emp, setEmp] = useState(i.suggestions.length === 1 ? i.suggestions[0].employee.emp_id : "");
  const [reason, setReason] = useState("");
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const label = i.context === "jira.account_id" ? (i.jira_name ?? "Jira account") : `Code “${i.value}”`;
  async function save(empId: string | null) {
    setBusy(true); setError(null);
    try {
      await request("POST", "/v1/identity/jira/map", { identity_id: i.identity_id, emp_id: empId,
        reason: reason.trim() || (empId ? "Confirmed by an administrator" : "Mapping withdrawn by an administrator") });
      setOpen(false); onDone();
    } catch (e) { setError(err(e)); } finally { setBusy(false); }
  }
  const [stateLabel, tone] = STATE[i.state];
  return (
    <tr>
      <td><strong>{label}</strong><div className="small muted">{i.context === "jira.account_id"
        ? "Jira Cloud account" : "Assignee code in exported issues"} · {i.issues} issue{i.issues === 1 ? "" : "s"}</div></td>
      <td><span className={`pill ${tone === "ok" ? "ok" : tone === "warn" ? "warn" : "neutral"}`}>{stateLabel}</span></td>
      <td>{i.mapped_to ? <><strong>{i.mapped_to.name}</strong><div className="small muted">{i.mapped_to.title} · by{" "}
            {HOW[i.mapped_by!.kind] ?? i.mapped_by!.kind}, {new Date(i.mapped_by!.at).toLocaleDateString()}</div></>
          : i.suggestions.length ? <span className="small">Suggestion: {i.suggestions.map((s) => s.employee.name).join(" or ")}
              <span className="muted"> ({i.suggestions[0].classification === "unique_initials_in_context"
                ? "initials match one person" : "name pattern"}; not a link until confirmed)</span></span>
          : <span className="muted small">No verified identifier</span>}
        {open && <div className="map-form">
          <select className="input" value={emp} onChange={(e) => setEmp(e.target.value)} aria-label="Employee">
            <option value="">Choose the employee…</option>
            {employees.map((e) => <option key={e.emp_id} value={e.emp_id}>{e.name} · {e.title} ({e.department})</option>)}
          </select>
          <input className="input" value={reason} onChange={(e) => setReason(e.target.value)}
                 placeholder="Why you are sure (kept in the audit history)" aria-label="Reason" />
          <div className="row" style={{ gap: 8 }}>
            <Button variant="primary" disabled={!emp || busy} onClick={() => save(emp)}>Link</Button>
            <Button onClick={() => setOpen(false)}>Cancel</Button>
          </div>
        </div>}
        {error && <p className="callout bad small">{error}</p>}
        {i.history.length > 0 && <details className="small"><summary>History ({i.history.length})</summary>
          <ul className="bullets">{i.history.map((h) => <li key={h.version + h.at}>{new Date(h.at).toLocaleString()}:{" "}
            {h.outcome === "approved" ? "linked to" : "unlinked from"} {h.employee.name} by {h.by} ({HOW[h.kind] ?? h.kind}) — {h.rationale}</li>)}</ul>
        </details>}
      </td>
      <td style={{ whiteSpace: "nowrap" }}>{!open && (i.mapped_to
        ? <><Button onClick={() => setOpen(true)}>Change</Button>{" "}
            <button className="link small" disabled={busy} onClick={() => save(null)}>Unlink</button></>
        : <Button variant={i.suggestions.length === 1 ? "primary" : undefined} onClick={() => setOpen(true)}>
            {i.suggestions.length === 1 ? "Review & link" : "Link to employee"}</Button>)}</td>
    </tr>);
}

export function JiraPeople() {
  const [d, reload] = useApi((s) => get<Out>("/v1/identity/jira", s), []);
  const [verify, setVerify] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function check() {
    setBusy(true); setVerify(null);
    try {
      const r = await request<{ jira_users: number; with_email: number; matched: number; already: number;
        ambiguous: number; no_hr_match: number; not_assignees: number }>("POST", "/v1/identity/jira/verify");
      setVerify(`Jira returned ${r.jira_users} user${r.jira_users === 1 ? "" : "s"}; ${r.with_email} share an e-mail with `
        + `Veloxa. Linked ${r.matched} by an exact HR e-mail match${r.already ? ` (${r.already} already linked)` : ""}. `
        + `${r.no_hr_match} had no matching HR e-mail, ${r.ambiguous} matched more than one record and were left `
        + `unlinked, and ${r.not_assignees} are not assignees of any admitted issue.`);
      reload();
    } catch (e) { setVerify(err(e)); } finally { setBusy(false); }
  }
  if (d.state === "error" && d.error.status === 403) return null;      // only identity administrators see this
  return (
    <Section title="People in Jira" aria-label="People in Jira"
             subtitle="Link each Jira assignee to the employee it belongs to, so Veloxa can say who owns open and blocked work."
             actions={<Button onClick={check} disabled={busy}>{busy ? "Checking Jira…" : "Check Jira for verified matches"}</Button>}>
      <p className="small muted" style={{ maxWidth: 900 }}>Veloxa links a Jira assignee to an employee only when Jira's
        e-mail for the account equals exactly one HR work e-mail, or when you link it yourself with a reason. Names and
        initials are shown as hints but never create a link. Ambiguous cases stay unlinked. Every change is kept in the
        history.</p>
      {verify && <p className="callout info small" role="status">{verify}</p>}
      {d.state === "loading" && <Loading lines={3} />}
      {d.state === "error" && <ErrorState error={d.error} onRetry={reload} />}
      {d.state === "ready" && (d.data.identities.length === 0
        ? <p className="muted">No Jira assignees in the admitted records yet.</p>
        : <div className="table-wrap"><table className="dir-table">
            <thead><tr><th>Jira assignee</th><th>Status</th><th>Employee</th><th /></tr></thead>
            <tbody>{d.data.identities.map((i) => <Row key={i.identity_id} i={i} employees={d.data.employees} onDone={reload} />)}</tbody>
          </table></div>)}
    </Section>
  );
}
