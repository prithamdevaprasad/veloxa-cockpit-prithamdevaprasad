// One card for Jira work: create a task, or change an existing one (title, dates, assignee, comment) in one step.
// Type the request in plain words to fill the form, or fill it directly. Every value comes from what the person
// typed or from Jira itself (projects, types, the date fields this site really has, and Jira's own list of people
// who may be assigned). Nothing is written until the preview is confirmed, and success is reported only after the
// issue is read back from Jira.
import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError, get, request } from "../api/client";
import type { JiraPreview } from "../api/types";
import { JiraConfirm, type JiraWriteResult } from "./AnswerView";
import { Button, Segmented } from "./index";
import { parseTaskRequest } from "./taskParse";

interface Project { key: string; name: string }
interface IssueType { id: string; name: string }
interface User { account_id: string; name: string | null }
interface DateFields { due: boolean; start_field: string | null; start_name: string | null }
interface Current { summary?: string | null; assignee?: string | null; due_date?: string | null; status?: string | null;
  url?: string | null }
export interface TaskLink { item_id: string; finding_id?: string; recommendation: string }
export interface TaskDraft {
  summary?: string; description?: string; key?: string; mode?: "create" | "update"; project?: string;
  /** Plain-words request: parsed into the form when the card opens. */
  request?: string;
  /** An exact Jira account proposed by a test fixture; used only if Jira lists it as assignable. */
  assigneeAccountId?: string; assigneeBasis?: string;
  /** The recommendation this task follows up (recorded once Jira confirms the issue). */
  link?: TaskLink; source?: string;
  onWritten?: (r: JiraWriteResult, assignee: string | null) => void;
}

const err = (e: unknown) => (e instanceof ApiError ? e.message : String(e));
const KEY = /^[A-Z][A-Z0-9_]{0,19}-\d{1,9}$/;

function useAssignable(project?: string, issueKey?: string) {
  const [users, setUsers] = useState<User[] | null>(null);
  useEffect(() => {
    setUsers(null);
    if (!project && !issueKey) return;
    const url = issueKey ? `/v1/integrations/jira/issue/${encodeURIComponent(issueKey)}/assignable`
      : `/v1/integrations/jira/projects/${encodeURIComponent(project!)}/assignable`;
    get<{ users: User[] }>(`${url}?query=`).then((r) => setUsers(r.users), () => setUsers([]));
  }, [project, issueKey]);
  return users;
}

export function JiraTaskCard({ draft, onClose }: { draft?: TaskDraft; onClose?: () => void }) {
  const [mode, setMode] = useState<"create" | "update">(draft?.mode ?? (draft?.key ? "update" : "create"));
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [project, setProject] = useState(draft?.project ?? "");
  const [types, setTypes] = useState<IssueType[]>([]);
  const [type, setType] = useState("");
  const [dates, setDates] = useState<DateFields | null>(null);
  const [summary, setSummary] = useState(draft?.summary ?? "");
  const [description, setDescription] = useState(draft?.description ?? "");
  const [due, setDue] = useState("");
  const [start, setStart] = useState("");
  const [assigneeId, setAssigneeId] = useState(draft?.assigneeAccountId ?? "");
  const [wantName, setWantName] = useState<string | null>(null);
  const [selfNote, setSelfNote] = useState(false);
  const [key, setKey] = useState(draft?.key ?? "");
  const [current, setCurrent] = useState<Current | null>(null);
  const [comment, setComment] = useState("");
  const [typed, setTyped] = useState(draft?.request ?? "");
  const [filled, setFilled] = useState<string | null>(null);
  const [preview, setPreview] = useState<JiraPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => { box.current?.scrollIntoView?.({ block: "nearest", behavior: "smooth" }); }, []);

  const issueKey = mode === "update" && KEY.test(key.trim().toUpperCase()) ? key.trim().toUpperCase() : undefined;
  const users = useAssignable(mode === "create" ? project || undefined : undefined, issueKey);
  const assignee = useMemo(() => users?.find((u) => u.account_id === assigneeId) ?? null, [users, assigneeId]);

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
  }, [project]);
  useEffect(() => {
    setDates(null);
    if (!project || !type) return;
    get<DateFields>(`/v1/integrations/jira/projects/${project}/issue-types/${type}/date-fields`)
      .then(setDates, () => setDates({ due: true, start_field: null, start_name: null }));
  }, [project, type]);
  useEffect(() => {                                     // updating: show what Jira holds now
    setCurrent(null);
    if (!issueKey) return;
    get<Current>(`/v1/integrations/jira/issue/${encodeURIComponent(issueKey)}`).then(setCurrent, () => setCurrent(null));
  }, [issueKey]);
  useEffect(() => {                                     // a name typed in the request: match Jira's own list only
    if (!wantName || !users) return;
    const n = wantName.toLowerCase();
    const exact = users.filter((u) => (u.name ?? "").toLowerCase() === n);
    const starts = users.filter((u) => (u.name ?? "").toLowerCase().split(/\s+/).some((p) => p === n || p.startsWith(n)));
    const hit = exact.length === 1 ? exact : starts.length === 1 ? starts : [];
    if (hit.length === 1) { setAssigneeId(hit[0].account_id); setWantName(null); }
  }, [wantName, users]);
  useEffect(() => { if (draft?.request) fill(draft.request); }, []);  // eslint-disable-line react-hooks/exhaustive-deps

  function fill(text: string) {
    const p = parseTaskRequest(text);
    const got: string[] = [];
    if (p.key) { setMode("update"); setKey(p.key); got.push(`issue ${p.key}`); }
    if (p.summary && !draft?.summary) { setSummary(p.summary.slice(0, 255)); got.push("title"); }
    if (p.due) { setDue(p.due); got.push(`due ${p.due}`); }
    if (p.start) { setStart(p.start); got.push(`start ${p.start}`); }
    if (p.assigneeName && !draft?.assigneeAccountId) { setWantName(p.assigneeName); got.push(`assignee “${p.assigneeName}”`); }
    if (p.assigneeSelf && !draft?.assigneeAccountId) { setAssigneeId(""); setWantName(null); got.push("assignee: you (pick your account)"); }
    setSelfNote(Boolean(p.assigneeSelf) && !draft?.assigneeAccountId);
    setFilled(got.length ? `Filled in: ${got.join(", ")}. Check the form below.` : "Couldn't pick out any details. Fill in the form below.");
    setPreview(null);
  }

  const canPreview = mode === "create" ? Boolean(project && type && summary.trim())
    : Boolean(issueKey) && Boolean(summary.trim() || due || start || assigneeId || comment.trim());
  const proposedMissing = Boolean(draft?.assigneeAccountId) && users !== null && !users.some((u) => u.account_id === draft!.assigneeAccountId);

  async function makePreview(e: React.FormEvent) {
    e.preventDefault();
    if (!canPreview) return;
    setBusy(true); setError(null); setPreview(null);
    const fields: Record<string, unknown> = {};
    if (due) fields.duedate = due;
    if (start && dates?.start_field) fields[dates.start_field] = start;
    if (assigneeId) fields.assignee = { accountId: assigneeId };
    try {
      if (mode === "create") {
        setPreview(await request<JiraPreview>("POST", "/v1/integrations/jira/preview/create", {
          project, issue_type_id: type, summary: summary.trim(),
          description: description.trim() || "Created from Veloxa.", fields }));
      } else {
        if (summary.trim()) fields.summary = summary.trim();
        setPreview(await request<JiraPreview>("POST", "/v1/integrations/jira/preview/update", {
          key: issueKey, fields, comment: comment.trim() }));
      }
    } catch (x) { setError(err(x)); }
    finally { setBusy(false); }
  }

  if (loadErr) return (
    <div className="task-card" role="region" aria-label="Jira task">
      <div className="task-head"><h3>Jira task</h3>{onClose && <button className="link small" onClick={onClose}>Close</button>}</div>
      <p className="callout small">Jira isn't available: {loadErr} An administrator can connect it in Admin.</p>
    </div>);
  const datesLine = [due && `due ${due}`, start && (dates?.start_field ? `start ${start}` : null)].filter(Boolean).join(" and ");
  return (
    <div className="task-card" role="region" aria-label="Jira task" ref={box}>
      <div className="task-head">
        <h3>{mode === "create" ? "New Jira task" : "Change a Jira issue"}</h3>
        <Segmented label="Task action" value={mode} onChange={(m) => { setMode(m); setPreview(null); }}
                   options={[{ value: "create", label: "Create" }, { value: "update", label: "Update or assign" }]} />
        {onClose && <button className="link small" onClick={onClose}>Close</button>}
      </div>
      {draft?.source && <p className="small muted task-source">{draft.source}</p>}
      <div className="task-quick">
        <label htmlFor="task-quick">Describe it in your own words <span className="muted">(optional)</span></label>
        <div className="row" style={{ gap: 8, flexWrap: "nowrap" }}>
          <input id="task-quick" className="input" value={typed} onChange={(e) => setTyped(e.target.value)}
                 onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); fill(typed); } }}
                 placeholder="e.g. Confirm the Atlas GA date with the release owner, assign to Alex, due Friday" />
          <Button onClick={() => fill(typed)} disabled={!typed.trim()}>Fill the form</Button>
        </div>
        {filled && <p className="small muted" role="status" style={{ margin: "4px 0 0" }}>{filled}</p>}
      </div>
      <form className="task-form" onSubmit={makePreview}>
        {mode === "create" ? <>
          <label>Project
            <select className="input" value={project} onChange={(e) => { setProject(e.target.value); setAssigneeId(""); }} disabled={!projects}>
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
                   placeholder="VIT-2" required />
            {current && <span className="now small muted">Now: {current.summary ?? "untitled"} · {current.status ?? "no status"}</span>}</label>
          <label>New title <span className="muted">(optional)</span>
            <input className="input" value={summary} onChange={(e) => setSummary(e.target.value)} maxLength={255} /></label>
          <label className="wide">Comment <span className="muted">(optional)</span>
            <textarea className="input" rows={2} value={comment} onChange={(e) => setComment(e.target.value)} /></label>
        </>}
        <label>Due date <span className="muted">(optional)</span>
          <input className="input" type="date" value={due} onChange={(e) => setDue(e.target.value)} />
          {current && <span className="now small muted">Now: {current.due_date ?? "none"}</span>}</label>
        {dates?.start_field ? (
          <label>{dates.start_name ?? "Start date"} <span className="muted">(optional)</span>
            <input className="input" type="date" value={start} onChange={(e) => setStart(e.target.value)} /></label>
        ) : (
          <div className="field-note small muted">{dates ? "This Jira site has no start date field for this issue type, so only the due date can be set."
            : "Checking which date fields this Jira site has…"}</div>
        )}
        <label className="wide">Assignee <span className="muted">(people Jira allows for {mode === "create" ? project || "this project" : issueKey ?? "this issue"})</span>
          <select className="input" value={assigneeId} onChange={(e) => { setAssigneeId(e.target.value); setWantName(null); setSelfNote(false); }}
                  disabled={users === null} aria-label="Assignee">
            <option value="">{users === null ? "Loading people from Jira…" : "Unassigned"}</option>
            {(users ?? []).map((u) => <option key={u.account_id} value={u.account_id}>{u.name ?? u.account_id}</option>)}
          </select>
          {current && <span className="now small muted">Now: {current.assignee ?? "unassigned"}</span>}
          {assignee && draft?.assigneeAccountId === assignee.account_id && draft.assigneeBasis &&
            <span className="small muted">Proposed: {assignee.name}. {draft.assigneeBasis}</span>}
          {proposedMissing && <span className="small bad-text" role="alert">The proposed Jira account is not assignable here, so
            nobody is selected. Pick someone from Jira's list.</span>}
          {wantName && users && <span className="small muted">Nobody in Jira's list matches “{wantName}” exactly. Pick the person.</span>}
          {selfNote && !assigneeId && <span className="small muted">Pick your own account from Jira's list: Veloxa doesn't
            assume which Jira account is yours.</span>}
        </label>
        <div className="wide row" style={{ justifyContent: "space-between", alignItems: "center" }}>
          <span className="small muted">{datesLine ? `Dates to write: ${datesLine}. ` : "No dates will be written. "}
            Nothing changes in Jira until you confirm the preview.</span>
          <Button variant="primary" type="submit" disabled={!canPreview || busy}>{busy ? "Preparing…" : "Preview"}</Button>
        </div>
      </form>
      {error && <p className="callout bad small" role="alert">{error}</p>}
      {preview && <div className="task-preview"><h4>Preview: what will be written to Jira</h4>
        <JiraConfirm p={preview} onDone={(r) => draft?.onWritten?.(r, assignee?.name ?? null)} /></div>}
    </div>
  );
}
