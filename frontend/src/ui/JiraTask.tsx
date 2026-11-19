// One card for Jira work: create a task, or change an existing one (title, due date, assignee, comment) in a
// single step. Every value comes from what the person typed or from Jira itself (projects, types, assignable
// people). Nothing is written until the preview below the form is confirmed.
import { useEffect, useRef, useState } from "react";
import { ApiError, get, request } from "../api/client";
import type { JiraPreview } from "../api/types";
import { JiraConfirm } from "./AnswerView";
import { Button, Segmented } from "./index";

interface Project { key: string; name: string }
interface IssueType { id: string; name: string }
interface User { account_id: string; name: string | null }
export interface TaskDraft { summary?: string; description?: string; key?: string; mode?: "create" | "update" }

const err = (e: unknown) => (e instanceof ApiError ? e.message : String(e));

function AssigneePicker({ project, issueKey, value, onChange }: { project?: string; issueKey?: string;
  value: User | null; onChange: (u: User | null) => void }) {
  const [q, setQ] = useState("");
  const [users, setUsers] = useState<User[]>([]);
  useEffect(() => {
    if (!q.trim() || (!project && !issueKey)) { setUsers([]); return; }
    const t = setTimeout(() => {
      const url = issueKey ? `/v1/integrations/jira/issue/${encodeURIComponent(issueKey)}/assignable`
        : `/v1/integrations/jira/projects/${encodeURIComponent(project!)}/assignable`;
      get<{ users: User[] }>(`${url}?query=${encodeURIComponent(q)}`).then((r) => setUsers(r.users), () => setUsers([]));
    }, 250);
    return () => clearTimeout(t);
  }, [q, project, issueKey]);
  if (value) return <div className="row" style={{ gap: 8, alignItems: "center" }}><span>{value.name}</span>
    <button type="button" className="link small" onClick={() => onChange(null)}>Change</button></div>;
  return (
    <div className="picker">
      <input className="input" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Type a name (people in Jira)"
             aria-label="Assignee" />
      {users.length > 0 && <ul className="plain picker-list">{users.map((u) => (
        <li key={u.account_id}><button type="button" onClick={() => { onChange(u); setQ(""); }}>{u.name}</button></li>))}</ul>}
      {q.trim() && users.length === 0 && <p className="small muted" style={{ margin: "4px 0 0" }}>Nobody in Jira matches yet.</p>}
    </div>
  );
}

export function JiraTaskCard({ draft, onClose }: { draft?: TaskDraft; onClose?: () => void }) {
  const [mode, setMode] = useState<"create" | "update">(draft?.mode ?? (draft?.key ? "update" : "create"));
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [project, setProject] = useState("");
  const [types, setTypes] = useState<IssueType[]>([]);
  const [type, setType] = useState("");
  const [summary, setSummary] = useState(draft?.summary ?? "");
  const [description, setDescription] = useState(draft?.description ?? "");
  const [due, setDue] = useState("");
  const [assignee, setAssignee] = useState<User | null>(null);
  const [key, setKey] = useState(draft?.key ?? "");
  const [comment, setComment] = useState("");
  const [preview, setPreview] = useState<JiraPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => { box.current?.scrollIntoView?.({ block: "nearest", behavior: "smooth" }); }, []);

  useEffect(() => {
    get<{ projects: Project[] }>("/v1/integrations/jira/projects").then((r) => {
      setProjects(r.projects);
      setProject((p) => p || (r.projects.find((x) => x.key === "VIT") ?? r.projects[0])?.key || "");
    }, (e) => setLoadErr(err(e)));
  }, []);
  useEffect(() => {
    if (!project) return;
    get<{ issue_types: IssueType[] }>(`/v1/integrations/jira/projects/${project}/issue-types`).then((r) => {
      setTypes(r.issue_types);
      setType((r.issue_types.find((t) => t.name.toLowerCase() === "task") ?? r.issue_types[0])?.id ?? "");
    }, () => setTypes([]));
    setAssignee(null);
  }, [project]);

  const validKey = /^[A-Z][A-Z0-9_]{0,19}-\d{1,9}$/.test(key.trim().toUpperCase());
  const canPreview = mode === "create" ? Boolean(project && type && summary.trim())
    : validKey && Boolean(summary.trim() || due || assignee || comment.trim());

  async function makePreview(e: React.FormEvent) {
    e.preventDefault();
    if (!canPreview) return;
    setBusy(true); setError(null); setPreview(null);
    try {
      if (mode === "create") {
        const fields: Record<string, unknown> = {};
        if (due) fields.duedate = due;
        if (assignee) fields.assignee = { accountId: assignee.account_id };
        setPreview(await request<JiraPreview>("POST", "/v1/integrations/jira/preview/create", {
          project, issue_type_id: type, summary: summary.trim(),
          description: description.trim() || "Created from Veloxa.", fields }));
      } else {
        const fields: Record<string, unknown> = {};
        if (summary.trim()) fields.summary = summary.trim();
        if (due) fields.duedate = due;
        if (assignee) fields.assignee = { accountId: assignee.account_id };
        setPreview(await request<JiraPreview>("POST", "/v1/integrations/jira/preview/update", {
          key: key.trim().toUpperCase(), fields, comment: comment.trim() }));
      }
    } catch (x) { setError(err(x)); }
    finally { setBusy(false); }
  }

  if (loadErr) return (
    <div className="task-card" role="region" aria-label="Jira task">
      <div className="task-head"><h3>Jira task</h3>{onClose && <button className="link small" onClick={onClose}>Close</button>}</div>
      <p className="callout small">Jira isn't available: {loadErr} An administrator can connect it in Admin.</p>
    </div>);
  return (
    <div className="task-card" role="region" aria-label="Jira task" ref={box}>
      <div className="task-head">
        <h3>{mode === "create" ? "New Jira task" : "Change a Jira issue"}</h3>
        <Segmented label="Task action" value={mode} onChange={(m) => { setMode(m); setPreview(null); }}
                   options={[{ value: "create", label: "Create" }, { value: "update", label: "Update or assign" }]} />
        {onClose && <button className="link small" onClick={onClose}>Close</button>}
      </div>
      <form className="task-form" onSubmit={makePreview}>
        {mode === "create" ? <>
          <label>Project
            <select className="input" value={project} onChange={(e) => setProject(e.target.value)} disabled={!projects}>
              {(projects ?? []).map((p) => <option key={p.key} value={p.key}>{p.key} · {p.name}</option>)}
            </select></label>
          <label>Type
            <select className="input" value={type} onChange={(e) => setType(e.target.value)}>
              {types.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select></label>
          <label className="wide">Title
            <input className="input" value={summary} onChange={(e) => setSummary(e.target.value)} required maxLength={255}
                   placeholder="e.g. Confirm the replacement GA date with the release owner" /></label>
          <label className="wide">Description <span className="muted">(optional)</span>
            <textarea className="input" rows={3} value={description} onChange={(e) => setDescription(e.target.value)} /></label>
        </> : <>
          <label>Issue key
            <input className="input mono" value={key} onChange={(e) => setKey(e.target.value.toUpperCase())}
                   placeholder="VIT-2" required /></label>
          <label>New title <span className="muted">(optional)</span>
            <input className="input" value={summary} onChange={(e) => setSummary(e.target.value)} maxLength={255} /></label>
          <label className="wide">Comment <span className="muted">(optional)</span>
            <textarea className="input" rows={2} value={comment} onChange={(e) => setComment(e.target.value)} /></label>
        </>}
        <label>Due date <span className="muted">(optional)</span>
          <input className="input" type="date" value={due} onChange={(e) => setDue(e.target.value)} /></label>
        <label>Assignee <span className="muted">(optional)</span>
          <AssigneePicker project={mode === "create" ? project : undefined}
                          issueKey={mode === "update" && validKey ? key.trim().toUpperCase() : undefined}
                          value={assignee} onChange={setAssignee} /></label>
        <div className="wide row" style={{ justifyContent: "space-between", alignItems: "center" }}>
          <span className="small muted">You'll see exactly what will be written before anything changes in Jira.</span>
          <Button variant="primary" type="submit" disabled={!canPreview || busy}>{busy ? "Preparing…" : "Preview"}</Button>
        </div>
      </form>
      {error && <p className="callout bad small" role="alert">{error}</p>}
      {preview && <div className="task-preview"><h4>Preview</h4><JiraConfirm p={preview} /></div>}
    </div>
  );
}
