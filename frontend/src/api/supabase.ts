// Supabase Auth as Veloxa's identity provider (authentication ONLY). Supabase proves who someone is; Veloxa's own
// database decides membership, roles and evidence access (the backend verifies the Supabase JWT against the
// tenant's registered issuer: ES256 signature via JWKS, issuer, audience, expiry; then maps the subject to an
// invited principal). Only the PUBLISHABLE key is used here; no service-role key ever reaches the browser.
// - Email + password (`signInWithPassword`). Passwords go only to Supabase; Veloxa never sees or stores them.
// - Public sign-up is never called. The recovery e-mail is used only to set or reset a password of an EXISTING
//   account (Supabase does not create accounts from recovery), never as a way to sign in.
// - The session lives in this tab (sessionStorage), refreshes automatically, and is cleared on sign-out.
import { createClient, type Session, type SupabaseClient } from "@supabase/supabase-js";
import { setToken } from "./client";

const URL_ = (import.meta.env.VITE_SUPABASE_URL as string | undefined)?.replace(/\/$/, "");
const KEY = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY as string | undefined;
const GOOGLE = import.meta.env.VITE_SUPABASE_GOOGLE === "1";
export const RESET_ROUTE = "#/reset-password";

export const supabaseConfigured = () => Boolean(URL_ && KEY);
export const googleEnabled = () => supabaseConfigured() && GOOGLE;

let client: SupabaseClient | null = null;
let recovery = false;
function sb(): SupabaseClient {
  if (!client) {
    client = createClient(URL_!, KEY!, { auth: {
      storage: window.sessionStorage, persistSession: true, autoRefreshToken: true,
      detectSessionInUrl: true, flowType: "implicit" } });
    client.auth.onAuthStateChange((event, s) => {
      if (event === "PASSWORD_RECOVERY") recovery = true;
      if (s) apply(s);
      else if (event === "SIGNED_OUT") setToken(null);
    });
  }
  return client;
}

function apply(s: Session) {
  if (recovery) return;                       // a recovery session may only set a password, never sign in
  setToken(s.access_token);
}

/** Generic on purpose: never reveals whether an account exists or is invited. */
const GENERIC = "The e-mail or password is not correct, or this account has no access.";

// Client-side throttle in addition to Supabase's own server-side rate limits.
let failures = 0, lockedUntil = 0;
export function lockedFor(): number { return Math.max(0, Math.ceil((lockedUntil - Date.now()) / 1000)); }

export async function signIn(email: string, password: string): Promise<void> {
  if (lockedFor() > 0) throw new Error(`Too many attempts. Try again in ${lockedFor()} seconds.`);
  recovery = false;
  const { data, error } = await sb().auth.signInWithPassword({ email: email.trim(), password });
  if (error || !data.session) {
    failures += 1;
    if (failures >= 5) { lockedUntil = Date.now() + 30_000 * 2 ** Math.min(failures - 5, 4); }
    throw new Error(error?.status === 429 ? "Too many attempts. Wait a minute and try again." : GENERIC);
  }
  failures = 0;
  apply(data.session);
}

/** On page load: restore this tab's session (the SDK refreshes it if needed) or take a recovery link. */
export async function restore(): Promise<"recovery" | "session" | "none"> {
  if (!supabaseConfigured()) return "none";
  const isRecovery = /type=recovery/.test(window.location.hash);
  if (isRecovery) recovery = true;
  const { data } = await sb().auth.getSession();
  if (recovery && data.session) {
    history.replaceState(null, "", window.location.pathname + RESET_ROUTE);
    return "recovery";
  }
  if (data.session) apply(data.session);         // no Supabase session: leave any other sign-in alone
  return data.session ? "session" : "none";
}

/** Reports success whether or not the account exists (no enumeration). Only a refusal to send at all (e.g. the
 * mailer's hourly limit) is reported, because it says nothing about any account. */
export async function requestReset(email: string): Promise<void> {
  const { error } = await sb().auth.resetPasswordForEmail(email.trim(), { redirectTo: window.location.origin + "/" });
  if (error && (error.status === 429 || /rate limit/i.test(error.message)))
    throw new Error("E-mail sending is temporarily limited. Wait an hour and try again, or ask your administrator.");
}

export function passwordProblems(p: string, email = ""): string[] {
  const out = [];
  if (p.length < 12) out.push("at least 12 characters");
  if (!/[a-z]/.test(p) || !/[A-Z]/.test(p)) out.push("upper- and lower-case letters");
  if (!/\d/.test(p)) out.push("a number");
  if (!/[^A-Za-z0-9]/.test(p)) out.push("a symbol");
  const local = email.split("@")[0]?.toLowerCase();
  if (local && local.length >= 4 && p.toLowerCase().includes(local)) out.push("not your e-mail name");
  return out;
}

/** Set the new password with the recovery session, then end that session: the person signs in normally. */
export async function setNewPassword(password: string): Promise<void> {
  const { error } = await sb().auth.updateUser({ password });
  if (error) throw new Error(/same/i.test(error.message) ? "Choose a password you have not used here before."
    : /weak|short/i.test(error.message) ? "That password is too weak." : "The reset link has expired. Request a new one.");
  await sb().auth.signOut({ scope: "local" });
  recovery = false;
  setToken(null);
}

export async function signOut(): Promise<void> {
  if (supabaseConfigured()) { try { await sb().auth.signOut(); } catch { /* cleared locally below */ } }
  setToken(null);
}

export function googleSignIn(): void {
  void sb().auth.signInWithOAuth({ provider: "google", options: { redirectTo: window.location.origin + "/" } });
}
