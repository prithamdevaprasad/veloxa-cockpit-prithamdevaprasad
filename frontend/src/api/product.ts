// Client for the workspace, insights, threads, agent-update, data-room and evidence APIs (backend 0017-0020).
import { ApiError, get, getToken, qs, request } from "./client";
import type * as P from "./product-types";

export const enc = (id: string) => encodeURIComponent(id);

export const product = {
  me: (s?: AbortSignal) => get<P.Me>("/v1/me", s),
  session: (s?: AbortSignal) => get<{ method: string; provider: string | null; expires_at: string }>(
    "/v1/auth/session", s),
  logout: () => request<{ signed_out: boolean }>("POST", "/v1/auth/logout"),
  overview: (s?: AbortSignal) => get<P.Overview>("/v1/overview", s),
  chartSources: (s?: AbortSignal) => get<{ sources: P.ChartSource[] }>("/v1/analytics/sources", s),
  series: (q: { system: string; x: string; agg?: "count" | "sum"; y?: string; split?: string }, s?: AbortSignal) =>
    get<P.Series>("/v1/analytics/series" + qs({ system: q.system, x: q.x, agg: q.agg, y: q.y, split: q.split }), s),
  updates: (department?: string, s?: AbortSignal) =>
    get<{ updates: P.AgentUpdate[] }>("/v1/agent-updates" + qs({ department }), s),
  update: (id: string, s?: AbortSignal) => get<P.AgentUpdate>(`/v1/agent-updates/${enc(id)}`, s),
  updateThread: (id: string) => request<{ thread_id: string }>("POST", `/v1/agent-updates/${enc(id)}/thread`),
  threads: (archived = false, s?: AbortSignal) =>
    get<{ threads: P.ThreadSummary[] }>("/v1/threads" + qs({ archived: archived ? "true" : undefined }), s),
  thread: (id: string, s?: AbortSignal) => get<P.Thread>(`/v1/threads/${enc(id)}`, s),
  newThread: (title: string, context: Record<string, unknown> = {}) =>
    request<{ thread_id: string }>("POST", "/v1/threads", { title, context }),
  threadAsk: (id: string, question: string, asKnownAt?: string, s?: AbortSignal) =>
    request<P.ThreadTurn>("POST", `/v1/threads/${enc(id)}/ask`,
      { question, ...(asKnownAt ? { as_known_at: asKnownAt } : {}) }, s),
  renameThread: (id: string, title: string) => request("POST", `/v1/threads/${enc(id)}/rename`, { title }),
  archiveThread: (id: string, archive = true) =>
    request("POST", `/v1/threads/${enc(id)}/${archive ? "archive" : "restore"}`),
  evidence: (recordId: string, s?: AbortSignal) => get<P.EvidenceDetail>(`/v1/evidence/${enc(recordId)}`, s),
  departments: (s?: AbortSignal) => get<{ departments: P.Department[] }>("/v1/departments", s),
  sourceTypes: (s?: AbortSignal) => get<{ source_types: P.SourceType[]; release_packs?: { sealed_releases: number; catalogue_rules: number } }>("/v1/source-types", s),
  submissions: (state?: string, s?: AbortSignal) => get<P.SubmissionList>("/v1/submissions" + qs({ state }), s),
  submission: (id: string, s?: AbortSignal) => get<P.SubmissionDetail>(`/v1/submissions/${enc(id)}`, s),
  submit: (body: { department_id: string; source_type: string; title: string; data_period?: string;
                   files: { file_name: string; content_base64: string }[] }) =>
    request<{ submission_id: string; state: string }>("POST", "/v1/submissions", body),
  decide: (id: string, decision: "accept" | "reject" | "authorise" | "decline", reason: string) =>
    request<{ state: string }>("POST", `/v1/submissions/${enc(id)}/decision`, { decision, reason }),
  members: (s?: AbortSignal) => get<P.Members>("/v1/admin/members", s),
  createDepartment: (department_id: string, name: string) =>
    request("POST", "/v1/admin/departments", { department_id, name }),
  invite: (email: string, display_name: string, roles: P.Role[]) =>
    request<{ principal_id: string; invitation_token: string; delivery: string }>(
      "POST", "/v1/admin/invitations", { email, display_name, roles }),
  grant: (b: { resource_type: string; resource_id: string; relation: string; subject: string;
               subject_relation?: string; reason: string }) =>
    request<{ tuple_id: string }>("POST", "/v1/admin/grants", b),
  revoke: (tupleId: string) => request("POST", `/v1/admin/grants/${enc(tupleId)}/revoke`),
  clarifications: (s?: AbortSignal) => get<{ requests: P.Clarification[] }>("/v1/clarifications", s),
  workforce: (s?: AbortSignal) => get<P.Workforce>("/v1/workforce", s),
  brief: (s?: AbortSignal) => get<P.Brief>("/v1/brief", s),
  explainAnswer: (answerId: string, model = true) =>
    request<{ answer_id: string; wording: P.Wording; explanation: P.Explanation }>(
      "POST", `/v1/answers/${enc(answerId)}/explain` + qs({ model: model ? "true" : "false" })),
  explainScenario: (business_object: string, shift_days: number, model = false) =>
    request<{ scenario: import("./types").Scenario; explanation: P.Explanation }>(
      "POST", "/v1/scenarios/commitment-shift/explain" + qs({ model: model ? "true" : undefined }),
      { business_object, shift_days }),
  explainWorry: (itemId: string, model = false) => get<{ item_id: string; explanation: P.Explanation }>(
    `/v1/worry/${enc(itemId)}/explain` + qs({ model: model ? "true" : undefined })),
  registry: (s?: AbortSignal) => get<{ conflicts: { conflict_id: string; subject: string; predicate: string;
    resolution: string; claims: { claim_id: string; object: unknown; display?: string; record_id: string }[] }[];
    hidden_for_access: number }>("/v1/knowledge/conflicts", s),
  explainConflict: (id: string, model = false) => get<{ conflict_id: string; explanation: P.Explanation }>(
    `/v1/knowledge/conflicts/${enc(id)}/explain` + qs({ model: model ? "true" : undefined })),
  people: (s?: AbortSignal) => get<{ people: P.Person[]; count: number }>("/v1/people", s),
  worry: (asKnownAt?: string, s?: AbortSignal) => get<P.Worry>("/v1/worry" + qs({ as_known_at: asKnownAt }), s),
  findings: (s?: AbortSignal) => get<{ findings: P.Finding[]; hidden_for_access: number }>("/v1/findings", s),
  findingEvent: (id: string, state: string, note?: string, responsible?: string) =>
    request<{ state: string }>("POST", `/v1/findings/${enc(id)}/events`,
      { state, ...(note ? { note } : {}), ...(responsible ? { responsible } : {}) }),
  ackNotification: (nid: string) =>
    request<{ at: string; already: boolean }>("POST", `/v1/notifications/${enc(nid)}/ack`),
  followUps: (s?: AbortSignal) => get<{ follow_ups: P.FollowUp[] }>("/v1/follow-ups", s),
  recordFollowUp: (body: { item_id: string; recommendation: string; status: P.FollowUp["status"]; action_id?: string;
                           finding_id?: string; jira_key?: string; jira_assignee?: string; note?: string }) =>
    request<{ action_id: string; status: string }>("POST", "/v1/follow-ups", body),
  connectors: (s?: AbortSignal) => get<P.ConnectorStatus>("/v1/admin/connectors", s),
  clarify: (department_id: string, question: string, context: Record<string, unknown>, submit: boolean) =>
    request<P.ClarificationCreated>("POST", "/v1/clarifications", { department_id, question, context, submit }),
};

/** Fetch an authorised original (the token stays in a header, never in a URL) and hand it to the browser. */
export async function openOriginal(href: string, fileName: string, download: boolean): Promise<void> {
  const token = getToken();
  const res = await fetch(href + (download ? "" : "?download=false"),
                          { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (!res.ok) {
    let detail = `Request failed (${res.status}).`;
    try { detail = ((await res.json()) as { detail?: string }).detail ?? detail; } catch { /* not JSON */ }
    throw new ApiError(res.status, `http_${res.status}`, detail);
  }
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  if (download) a.download = fileName;
  else a.target = "_blank";
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

export async function fileToBase64(f: File): Promise<string> {
  const buf = new Uint8Array(await f.arrayBuffer());
  let bin = "";
  for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode(...buf.subarray(i, i + 0x8000));
  return btoa(bin);
}
