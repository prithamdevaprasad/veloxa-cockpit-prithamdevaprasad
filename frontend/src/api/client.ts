// Typed client for the Veloxa API. One place for authentication, cancellation and error handling.
// The bearer token is the caller's: it is kept for this browser tab only (sessionStorage) and never logged.
import type {
  AgentRegistry, AgentSession, AgentTurn, Answer, ConflictRegister, DependencyGraph, Memories, Releases, RiskHistory, Scenario, ScenarioSubjects, SessionDetail, SessionList, TimelineResult, Today,
} from "./types";

const TOKEN_KEY = "veloxa.token";

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public requestId?: string) {
    super(message);
  }
  get isAuth(): boolean {
    return this.status === 401 || this.status === 403;
  }
}

export function getToken(): string | null {
  try {
    return sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null): void {
  try {
    if (token) sessionStorage.setItem(TOKEN_KEY, token);
    else sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable: the session simply is not remembered */
  }
}

export async function request<T>(method: string, path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  let res: Response;
  try {
    res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body), signal });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError(0, "network", "The Veloxa API could not be reached.");
  }
  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    throw new ApiError(res.status, "invalid_response", "The API returned a response that is not JSON.");
  }
  if (!res.ok) {
    const d = (data ?? {}) as { error?: string; detail?: unknown; request_id?: string };
    const rid = d.request_id ?? res.headers.get("X-Request-ID") ?? undefined;
    const detail = typeof d.detail === "string" ? d.detail
      : d.error === "internal_error" ? "Veloxa could not complete this request because of an internal error. "
        + "Nothing was changed, and the error was logged for the operator."
      : `The server answered with status ${res.status}.`;
    throw new ApiError(res.status, d.error ?? `http_${res.status}`, detail, rid);
  }
  return data as T;
}

/** GET requests are safe to retry once on a network failure; writes are never retried automatically. */
export async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  try {
    return await request<T>("GET", path, undefined, signal);
  } catch (e) {
    if (e instanceof ApiError && e.code === "network" && !signal?.aborted) {
      return request<T>("GET", path, undefined, signal);
    }
    throw e;
  }
}

export function qs(params: Record<string, string | undefined>): string {
  const p = Object.entries(params).filter(([, v]) => v);
  return p.length ? "?" + p.map(([k, v]) => `${k}=${encodeURIComponent(v as string)}`).join("&") : "";
}

export const api = {
  releases: (s?: AbortSignal) => get<Releases>("/v1/meta/releases", s),
  today: (s?: AbortSignal) => get<Today>("/v1/today", s),
  riskHistory: (s?: AbortSignal) => get<RiskHistory>("/v1/risks/history", s),
  scenarioSubjects: (s?: AbortSignal) => get<ScenarioSubjects>("/v1/scenarios/commitments", s),
  dependencies: (s?: AbortSignal) => get<DependencyGraph>("/v1/dependencies", s),
  commitmentShift: (businessObject: string, shiftDays: number) =>
    request<Scenario>("POST", "/v1/scenarios/commitment-shift", { business_object: businessObject, shift_days: shiftDays }),
  ask: (question: string, asKnownAt?: string, s?: AbortSignal) =>
    request<Answer>("POST", "/v1/ask", { question, ...(asKnownAt ? { as_known_at: asKnownAt } : {}) }, s),
  conflicts: (asKnownAt?: string, s?: AbortSignal) =>
    get<ConflictRegister>("/v1/conflicts" + qs({ as_known_at: asKnownAt }), s),
  agents: (s?: AbortSignal) => get<AgentRegistry>("/v1/agents", s),
  openSession: (agentId: string, asKnownAt?: string) =>
    request<AgentSession>("POST", `/v1/agents/${encodeURIComponent(agentId)}/sessions`,
      asKnownAt ? { as_known_at: asKnownAt } : {}),
  sendMessage: (sessionId: string, message: string, idempotencyKey: string, s?: AbortSignal) =>
    request<AgentTurn>("POST", `/v1/sessions/${sessionId}/messages`, { message, idempotency_key: idempotencyKey }, s),
  timeline: (start?: string, end?: string, s?: AbortSignal) =>
    get<TimelineResult>("/v1/timeline" + qs({ start, end }), s),
  sessions: (agentId: string, offset = 0, limit = 10, s?: AbortSignal) =>
    get<SessionList>(`/v1/agents/${encodeURIComponent(agentId)}/sessions` + qs({ offset: String(offset), limit: String(limit) }), s),
  session: (sessionId: string, s?: AbortSignal) => get<SessionDetail>(`/v1/sessions/${sessionId}`, s),
  closeSession: (sessionId: string) =>
    request<{ session_id: string; working_memory_closed: number }>("POST", `/v1/sessions/${sessionId}/close`),
  memories: (agentId: string, s?: AbortSignal) => get<Memories>(`/v1/agents/${encodeURIComponent(agentId)}/memories`, s),
};

export function newIdempotencyKey(): string {
  return `ui-${crypto.randomUUID()}`;
}
