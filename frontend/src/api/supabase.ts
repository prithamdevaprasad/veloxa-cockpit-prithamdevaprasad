// Supabase Auth as Veloxa's identity provider (authentication ONLY). Supabase proves who someone is; Veloxa's own
// database decides membership, roles and evidence access (the backend verifies the Supabase JWT against the
// tenant's registered issuer: ES256 signature via JWKS, issuer, audience, expiry; then maps the subject to an
// invited principal). Only the PUBLISHABLE key is used here; no service-role key ever reaches the browser.
// - Email one-time code with `create_user: false`: a code never creates an account (invite-only).
// - Google sign-in is prepared but OFF until VITE_SUPABASE_GOOGLE=1 (its own OAuth client, not the Gmail one).
// - The session lives in this tab (sessionStorage), is refreshed before expiry, and is cleared on sign-out/failure.
import { setToken } from "./client";

const URL_ = (import.meta.env.VITE_SUPABASE_URL as string | undefined)?.replace(/\/$/, "");
const KEY = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY as string | undefined;
const GOOGLE = import.meta.env.VITE_SUPABASE_GOOGLE === "1";
const REFRESH = "veloxa.supabase.refresh";
const EXPIRES = "veloxa.supabase.expires_at";

export const supabaseConfigured = () => Boolean(URL_ && KEY);
export const googleEnabled = () => supabaseConfigured() && GOOGLE;

interface Session { access_token: string; refresh_token: string; expires_in: number; expires_at?: number }
let timer: ReturnType<typeof setTimeout> | undefined;

async function call<T>(path: string, body?: unknown, bearer?: string): Promise<T> {
  const r = await fetch(`${URL_}/auth/v1${path}`, {
    method: "POST", headers: { apikey: KEY!, "Content-Type": "application/json",
                               ...(bearer ? { Authorization: `Bearer ${bearer}` } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body) });
  if (!r.ok) {
    const e = await r.json().catch(() => ({}));
    const code = (e.error_code ?? e.code ?? "") as string;
    throw new Error(code === "otp_disabled" || code === "user_not_found" || /signups? not allowed/i.test(e.msg ?? "")
      ? "This e-mail address has not been invited. Ask your administrator for an invitation."
      : code === "otp_expired" ? "That code is wrong or has expired. Request a new one."
      : r.status === 429 ? "Too many attempts. Wait a minute and try again."
      : "Sign-in failed. Try again.");
  }
  return (r.status === 204 ? {} : await r.json()) as T;
}

function store(s: Session) {
  const at = s.expires_at ?? Math.floor(Date.now() / 1000) + s.expires_in;
  setToken(s.access_token);
  try { sessionStorage.setItem(REFRESH, s.refresh_token); sessionStorage.setItem(EXPIRES, String(at)); } catch { /* */ }
  schedule(at);
}

function schedule(at: number) {
  if (timer) clearTimeout(timer);
  const ms = Math.max(5_000, (at - 60) * 1000 - Date.now());
  timer = setTimeout(() => { void refresh(); }, ms);
}

export function clearSession() {
  if (timer) clearTimeout(timer);
  try { sessionStorage.removeItem(REFRESH); sessionStorage.removeItem(EXPIRES); } catch { /* */ }
}

/** Refresh with the stored refresh token. On failure the session is cleared (the app shows "session expired"). */
export async function refresh(): Promise<boolean> {
  let rt: string | null = null;
  try { rt = sessionStorage.getItem(REFRESH); } catch { /* */ }
  if (!rt || !supabaseConfigured()) return false;
  try { store(await call<Session>("/token?grant_type=refresh_token", { refresh_token: rt })); return true; }
  catch { clearSession(); setToken(null); return false; }
}

/** On page load: restore this tab's session, refreshing now if it is about to expire. */
export async function restore(): Promise<void> {
  let at = 0;
  try { at = Number(sessionStorage.getItem(EXPIRES) ?? 0); } catch { /* */ }
  if (!at) return;
  if (at - 60 <= Date.now() / 1000) await refresh(); else schedule(at);
}

export const sendCode = (email: string) => call("/otp", { email, create_user: false });

export async function verifyCode(email: string, code: string): Promise<void> {
  store(await call<Session>("/verify", { type: "email", email, token: code }));
}

export function googleUrl(): string {
  return `${URL_}/auth/v1/authorize?provider=google&redirect_to=${encodeURIComponent(window.location.origin + "/")}`;
}

/** Google returns the session in the URL fragment; take it, then remove it from the address bar. */
export function takeRedirectSession(): boolean {
  const h = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  const at = h.get("access_token"), rt = h.get("refresh_token"), exp = h.get("expires_in");
  if (!at || !rt) return false;
  store({ access_token: at, refresh_token: rt, expires_in: Number(exp ?? 3600) });
  history.replaceState(null, "", window.location.pathname + "#/");
  return true;
}

export async function signOut(accessToken: string | null): Promise<void> {
  try { if (accessToken && supabaseConfigured()) await call("/logout", undefined, accessToken); } catch { /* */ }
  clearSession();
}
