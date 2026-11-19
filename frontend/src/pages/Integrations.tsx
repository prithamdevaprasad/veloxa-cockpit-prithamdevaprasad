// Admin > Integrations > Jira (OAuth 2.0). The browser never sees a token or the client secret: it only receives
// status, the authorize URL to navigate to, issue data, and write PREVIEWS. Every write needs explicit confirmation.
import { Fragment, useEffect, useState } from "react";
import { ApiError, get, request } from "../api/client";
import { useApi } from "../api/useApi";
import { Badge, Button, ErrorState, Loading, Section } from "../ui";

interface Site { id: string; name: string | null; url: string | null }
interface JiraStatus {
  client_configured: boolean; connected: boolean; expired?: boolean; site?: Site | null; sites?: Site[];
  needs_site_choice?: boolean; scopes?: string[]; requested_scopes?: string[]; callback_url?: string | null;
  callback_path?: string; last_sync?: string | null; last_error?: string | null; connected_by?: string | null;
  token_storage?: string; env: Record<string, boolean>;
}
interface Issue { key: string; url: string | null; summary: string | null; status: string | null; assignee: string | null;
  due_date: string | null; updated: string | null; source: { site: string; retrieved_at: string } }
interface Preview { preview_id: string; operation: "create" | "update"; project?: string; issue_type?: string;
  key?: string; url?: string; fields: Record<string, unknown>; description?: string | null; comment?: string | null }

const J = "/v1/integrations/jira";
const FLASH: Record<string, string> = {
  connected: "Jira is connected.", denied: "Access was not granted in Atlassian, so nothing changed.",
  bad_state: "The sign-in link expired or was already used. Connect again.",
  auth_refused: "Atlassian refused the authorization. Connect again.",
  no_site: "This Atlassian account has no Jira site with the requested access.",
  not_configured: "Jira OAuth is not configured on the server.",
};
const when = (s?: string | null) => (s ? new Date(s).toLocaleString() : "Never");
const err = (e: unknown) => (e instanceof ApiError ? e : new ApiError(0, "unknown", String(e)));

export function JiraIntegration() {
  const [st, reload] = useApi((s) => get<JiraStatus>(`${J}/status`, s), []);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const flash = new URLSearchParams(window.location.hash.split("?")[1] ?? "").get("jira");

  async function act(fn: () => Promise<unknown>) {
    setBusy(true); setError(null);
    try { await fn(); reload(); } catch (e) { setError(err(e)); } finally { setBusy(false); }
  }
  const connect = () => act(async () => {
    const r = await request<{ authorize_url: string }>("POST", `${J}/connect`);
    window.location.assign(r.authorize_url);                    // Atlassian consent, then back to the callback
  });

  return (
    <Section title="Integrations: Jira" aria-label="Jira integration"
             subtitle="Connect a Jira Cloud site with Atlassian OAuth. Reads are audited; Veloxa never creates or edits
               an issue without your explicit confirmation, and never deletes anything.">
      {flash && FLASH[flash] && <p className={`callout ${flash === "connected" ? "ok" : "warn"} small`}>{FLASH[flash]}</p>}
      {error && <ErrorState error={error} />}
      {st.state === "loading" ? <Loading lines={3} /> : st.state === "error" ? <ErrorState error={st.error} />
        : !st.data.client_configured ? (
          <div className="stack small">
            <p>Jira OAuth is not configured on the server.</p>
            <p className="muted">Required: {Object.entries(st.data.env).map(([k, v]) => `${k} ${v ? "present" : "missing"}`)
              .join(", ")}.</p>
          </div>)
        : <JiraBody s={st.data} busy={busy} connect={connect} act={act} />}
    </Section>
  );
}

function JiraBody({ s, busy, connect, act }: { s: JiraStatus; busy: boolean; connect: () => void;
                                                act: (fn: () => Promise<unknown>) => Promise<void> }) {
  const state = s.expired ? "Access expired or revoked" : s.connected ? (s.needs_site_choice ? "Choose a site" : "Connected")
    : "Not connected";
  return (
    <div className="stack">
      <dl className="meta small">
        <dt>Status</dt><dd><Badge value={s.connected && !s.expired ? "ok" : s.expired ? "bad" : "open"} label={state} /></dd>
        <dt>Site</dt><dd>{s.site ? `${s.site.name ?? ""} (${s.site.url ?? ""})` : "None"}</dd>
        <dt>Scopes</dt><dd className="mono">{(s.scopes?.length ? s.scopes : s.requested_scopes ?? []).join(" ")}</dd>
        <dt>Last sync</dt><dd>{when(s.last_sync)}</dd>
        <dt>Last error</dt><dd>{s.last_error ?? "None"}</dd>
        <dt>Callback URL</dt><dd className="mono">{s.callback_url ?? "Not set: configure VELOXA_PUBLIC_URL"}</dd>
        <dt>Token storage</dt><dd>{s.token_storage}</dd>
      </dl>
      {s.expired && <p className="callout bad small">Jira access has expired or was revoked. Reconnect to continue.</p>}
      <div className="row" style={{ gap: 8 }}>
        <Button variant="primary" disabled={busy || !s.callback_url} onClick={connect}>
          {s.connected || s.expired ? "Reconnect" : "Connect to Jira"}</Button>
        {(s.connected || s.expired) && <Button disabled={busy} onClick={() => act(() => request("POST", `${J}/disconnect`))}>
          Disconnect</Button>}
      </div>
      {s.needs_site_choice && (s.sites ?? []).length > 1 && (
        <div className="stack small"><strong>This account can reach several Jira sites. Choose one:</strong>
          {(s.sites ?? []).map((x) => <Button key={x.id} disabled={busy}
            onClick={() => act(() => request("POST", `${J}/site`, { cloud_id: x.id }))}>{x.name} ({x.url})</Button>)}
        </div>)}
      {s.connected && !s.expired && !s.needs_site_choice && <><JiraSearch /><JiraWrite /><MigrationPreview /></>}
    </div>
  );
}

function JiraSearch() {
  const [jql, setJql] = useState("order by updated DESC");
  const [rows, setRows] = useState<Issue[] | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  async function run(e: React.FormEvent) {
    e.preventDefault(); setError(null);
    try { setRows((await get<{ issues: Issue[] }>(`${J}/search?jql=${encodeURIComponent(jql)}&limit=20`)).issues); }
    catch (x) { setError(err(x)); }
  }
  return (
    <div className="stack">
      <h3>Read issues</h3>
      <form className="row" onSubmit={run}>
        <input className="input" style={{ flex: 1 }} value={jql} onChange={(e) => setJql(e.target.value)} aria-label="JQL" />
        <Button type="submit">Search</Button>
      </form>
      {error && <ErrorState error={error} />}
      {rows && (rows.length === 0 ? <p className="small muted">No issues match.</p> : (
        <div className="table-wrap"><table className="crit-table small">
          <thead><tr><th>Key</th><th>Summary</th><th>Status</th><th>Assignee</th><th>Due</th><th>Updated</th></tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={r.key}><td>{r.url ? <a href={r.url} target="_blank" rel="noreferrer">{r.key}</a> : r.key}</td>
              <td>{r.summary}</td><td>{r.status}</td><td>{r.assignee ?? "Unassigned"}</td><td>{r.due_date ?? "—"}</td>
              <td>{when(r.updated)}</td></tr>))}</tbody>
        </table>
        <p className="small muted">Live from Jira ({rows[0]?.source.site}), retrieved {when(rows[0]?.source.retrieved_at)}.
          Not admitted evidence.</p></div>))}
    </div>
  );
}

function JiraWrite() {
  const [mode, setMode] = useState<"create" | "update">("create");
  const [projects, setProjects] = useState<{ key: string; name: string }[]>([]);
  const [types, setTypes] = useState<{ id: string; name: string }[]>([]);
  const [f, setF] = useState({ project: "", type: "", summary: "", description: "", key: "", comment: "" });
  const [preview, setPreview] = useState<Preview | null>(null);
  const [ok, setOk] = useState(false);
  const [done, setDone] = useState<{ key: string; url: string } | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  useEffect(() => { get<{ projects: typeof projects }>(`${J}/projects`).then((r) => setProjects(r.projects))
    .catch((e) => setError(err(e))); }, []);
  useEffect(() => { if (!f.project) { setTypes([]); return; }
    get<{ issue_types: typeof types }>(`${J}/projects/${encodeURIComponent(f.project)}/issue-types`)
      .then((r) => setTypes(r.issue_types)).catch((e) => setError(err(e))); }, [f.project]);
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
    { setF({ ...f, [k]: e.target.value }); setPreview(null); setOk(false); };

  async function doPreview(e: React.FormEvent) {
    e.preventDefault(); setError(null); setDone(null); setOk(false);
    try {
      setPreview(mode === "create"
        ? await request<Preview>("POST", `${J}/preview/create`, { project: f.project, issue_type_id: f.type,
            summary: f.summary, description: f.description })
        : await request<Preview>("POST", `${J}/preview/update`, { key: f.key.trim().toUpperCase(),
            fields: f.summary ? { summary: f.summary } : {}, comment: f.comment }));
    } catch (x) { setError(err(x)); }
  }
  async function commit() {
    if (!preview || !ok) return;
    try { setDone(await request("POST", `${J}/commit`, { preview_id: preview.preview_id, confirm: true })); setPreview(null); }
    catch (x) { setError(err(x)); }
  }
  return (
    <div className="stack">
      <h3>Create or update an issue</h3>
      <div className="seg" role="group" aria-label="Write mode">
        {(["create", "update"] as const).map((m) => <button key={m} aria-pressed={mode === m}
          onClick={() => { setMode(m); setPreview(null); }}>{m === "create" ? "Create" : "Update"}</button>)}
      </div>
      <form className="stack" onSubmit={doPreview}>
        {mode === "create" ? <>
          <label className="small">Project <select className="input" value={f.project} onChange={set("project")} required>
            <option value="">Choose a project</option>{projects.map((p) => <option key={p.key} value={p.key}>{p.key}: {p.name}</option>)}
          </select></label>
          <label className="small">Issue type <select className="input" value={f.type} onChange={set("type")} required>
            <option value="">Choose a type</option>{types.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select></label>
          <label className="small">Summary <input className="input" value={f.summary} onChange={set("summary")} required maxLength={255} /></label>
          <label className="small">Description <textarea className="input" value={f.description} onChange={set("description")} rows={4} /></label>
        </> : <>
          <label className="small">Issue key <input className="input" value={f.key} onChange={set("key")} required placeholder="e.g. ENG-12" /></label>
          <label className="small">New summary (optional) <input className="input" value={f.summary} onChange={set("summary")} maxLength={255} /></label>
          <label className="small">Comment (optional) <textarea className="input" value={f.comment} onChange={set("comment")} rows={3} /></label>
        </>}
        <div><Button type="submit">Preview change</Button></div>
      </form>
      {error && <ErrorState error={error} />}
      {preview && (
        <div className="callout info stack small" aria-label="Write preview">
          <strong>{preview.operation === "create" ? `Create in ${preview.project} (${preview.issue_type})`
            : `Update ${preview.key}`}</strong>
          <dl className="meta">{Object.entries(preview.fields).map(([k, v]) => (
            <Fragment key={k}><dt>{k}</dt><dd>{typeof v === "object" && v
              ? `${String((v as { current: unknown }).current ?? "—")} → ${String((v as { proposed: unknown }).proposed)}`
              : String(v)}</dd></Fragment>))}</dl>
          {preview.description && <><strong>Description</strong><p style={{ whiteSpace: "pre-wrap" }}>{preview.description}</p></>}
          {preview.comment && <><strong>Comment</strong><p style={{ whiteSpace: "pre-wrap" }}>{preview.comment}</p></>}
          <label><input type="checkbox" checked={ok} onChange={(e) => setOk(e.target.checked)} /> I confirm this exact
            change should be written to Jira.</label>
          <div><Button variant="primary" disabled={!ok} onClick={commit}>Write to Jira</Button></div>
        </div>)}
      {done && <p className="callout ok small">Written: {done.url ? <a href={done.url} target="_blank" rel="noreferrer">{done.key}</a>
        : done.key}.</p>}
    </div>
  );
}

interface Migration {
  summary: Record<string, number>; live_duplicate_check: boolean; not_migrated: string;
  projects: { name: string; due: string | null; epics: { key: string; issues: number }[]; proposed: string }[];
  candidates: { summary: string; type: string; context: string; department: string | null; due: string | null;
    evidence: string[]; duplicates: { where: string; key: string; url?: string }[]; missing: string[];
    proposed_destination: { testing: string; production: string } }[];
  excluded: { source: string; reason: string; records: number | null }[];
}

/** What company records would become in Jira. Read-only: nothing is written from this view. */
function MigrationPreview() {
  const [m, setM] = useState<Migration | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  async function load() {
    setBusy(true); setError(null);
    try { setM(await get<Migration>(`${J}/migration-preview`)); } catch (e) { setError(err(e)); } finally { setBusy(false); }
  }
  return (
    <div className="stack">
      <h3>Migration preview: company records to Jira</h3>
      <p className="small muted">Proposes projects and actionable work only (never documents, employees, metrics or
        history). Read-only: nothing is written; each write later needs your approval and confirmation.</p>
      <div><Button onClick={load} disabled={busy}>{busy ? "Preparing…" : m ? "Refresh preview" : "Prepare preview"}</Button></div>
      {error && <ErrorState error={error} />}
      {m && <div className="stack small">
        <p>{m.summary.projects} project(s), {m.summary.candidates} candidate task(s), {m.summary.duplicates} with a
          duplicate, {m.summary.missing_owner} without an owner, {m.summary.missing_deadline} without a recorded deadline,
          {" "}{m.summary.excluded_sensitive} sensitive record group(s) excluded.
          {m.live_duplicate_check ? " Duplicates were also checked in the connected Jira site." : ""}</p>
        {m.projects.map((p) => <div key={p.name} className="callout info"><strong>{p.name}</strong>{p.due ? `, due ${p.due}` : ""}.
          {" "}{p.proposed} {p.epics.length > 0 && <>Epics: {p.epics.map((e) => `${e.key} (${e.issues})`).join(", ")}.</>}</div>)}
        <div className="table-wrap"><table className="crit-table">
          <thead><tr><th>Candidate task</th><th>Owner</th><th>Deadline</th><th>Evidence</th><th>Duplicates</th><th>Destination</th></tr></thead>
          <tbody>{m.candidates.map((c) => (
            <tr key={c.summary}><td>{c.summary}<div className="muted">{c.context}</div></td>
              <td>{c.department ?? "missing"}</td><td>{c.due ?? "not recorded"}</td><td>{c.evidence.length} record(s)</td>
              <td>{c.duplicates.length ? c.duplicates.map((d) => `${d.key} (${d.where})`).join("; ") : "none found"}</td>
              <td>Testing: {c.proposed_destination.testing}<br />Production: {c.proposed_destination.production}</td></tr>))}
          </tbody></table></div>
        <strong>Excluded</strong>
        <ul className="bullets">{m.excluded.map((e) => <li key={e.source}>{e.source.replace(/_/g, " ")}: {e.reason}</li>)}</ul>
      </div>}
    </div>
  );
}
