// Typed client for the Veloxa API. One place for authentication, cancellation and error handling.
// The bearer token is the caller's: it is kept for this browser tab only (sessionStorage) and never logged.
import type {
  AgentRegistry, AgentSession, AgentTurn, Answer, ConflictRegister, Memories, Releases, RiskHistory, Today,
} from "./types";

const TOKEN_KEY = "veloxa.token";

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) {
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

async function request<T>(method: string, path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
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
    const d = (data ?? {}) as { error?: string; detail?: unknown };
    const detail = typeof d.detail === "string" ? d.detail : `Request failed (${res.status}).`;
    throw new ApiError(res.status, d.error ?? `http_${res.status}`, detail);
  }
  return data as T;
}

/** GET requests are safe to retry once on a network failure; writes are never retried automatically. */
async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  try {
    return await request<T>("GET", path, undefined, signal);
  } catch (e) {
    if (e instanceof ApiError && e.code === "network" && !signal?.aborted) {
      return request<T>("GET", path, undefined, signal);
    }
    throw e;
  }
}

function qs(params: Record<string, string | undefined>): string {
  const p = Object.entries(params).filter(([, v]) => v);
  return p.length ? "?" + p.map(([k, v]) => `${k}=${encodeURIComponent(v as string)}`).join("&") : "";
}

export const api = {
  releases: (s?: AbortSignal) => get<Releases>("/v1/meta/releases", s),
  today: (s?: AbortSignal) => get<Today>("/v1/today", s),
  riskHistory: (s?: AbortSignal) => get<RiskHistory>("/v1/risks/history", s),
  ask: (question: string, asKnownAt?: string, s?: AbortSignal) =>
    request<Answer>("POST", "/v1/ask", { question, ...(asKnownAt ? { as_known_at: asKnownAt } : {}) }, s),
  conflicts: (asKnownAt?: string, s?: AbortSignal) =>
    get<ConflictRegister>("/v1/conflicts" + qs({ as_known_at: asKnownAt }), s),
  agents: (s?: AbortSignal) => get<AgentRegistry>("/v1/agents", s),
  openSession: (agentId: string, asKnownAt?: string) =>
    request<AgentSession>("POST", `/v1/agents/${encodeURIComponent(agentId)}/sessions`,
      asKnownAt ? { as_known_at: asKnownAt } : {}),
  sendMessage: (sessionId: string, message: string, idempotencyKey: string) =>
    request<AgentTurn>("POST", `/v1/sessions/${sessionId}/messages`, { message, idempotency_key: idempotencyKey }),
  closeSession: (sessionId: string) =>
    request<{ session_id: string; working_memory_closed: number }>("POST", `/v1/sessions/${sessionId}/close`),
  memories: (agentId: string, s?: AbortSignal) => get<Memories>(`/v1/agents/${encodeURIComponent(agentId)}/memories`, s),
};

export function newIdempotencyKey(): string {
  return `ui-${crypto.randomUUID()}`;
}
