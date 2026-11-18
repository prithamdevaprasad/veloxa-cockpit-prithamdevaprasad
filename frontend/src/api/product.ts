// Client for the workspace, insights, threads, agent-update, data-room and evidence APIs (backend 0017-0020).
import { ApiError, get, getToken, qs, request } from "./client";
import type * as P from "./product-types";

export const enc = (id: string) => encodeURIComponent(id);

export const product = {
  me: (s?: AbortSignal) => get<P.Me>("/v1/me", s),
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
  sourceTypes: (s?: AbortSignal) => get<{ source_types: P.SourceType[] }>("/v1/source-types", s),
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
