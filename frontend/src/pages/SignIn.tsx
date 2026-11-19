import { useEffect, useState } from "react";
import { ApiError, get, request, setToken } from "../api/client";
import * as sb from "../api/supabase";
import { BrandMark } from "../ui/kit";
import { Button } from "../ui";

interface Options { providers: { id: string; label: string }[]; demo: boolean; developer_token: boolean;
  sso_ready: boolean }

/** Reader-facing messages for the server's sign-in outcomes (codes come from /v1/auth/callback). */
const ERRORS: Record<string, string> = {
  sso_not_configured: "Single sign-on isn't configured for this deployment yet. Ask your administrator.",
  provider_unavailable: "Your identity provider couldn't be reached. Try again in a moment.",
  provider_refused: "Sign-in was cancelled or refused by your identity provider.",
  login_expired: "That sign-in attempt expired or was opened in another browser. Start again.",
  token_exchange_failed: "Your identity provider didn't complete the sign-in. Try again.",
  invalid_invitation: "This invitation is invalid, expired or already used. Ask your administrator for a new one.",
  rejected_not_a_member: "The e-mail or password is not correct, or this account has no access.",
  mfa_required: "This workspace requires multi-factor authentication. Sign in again with MFA.",
  session_expired: "Your session has expired. Sign in again.",
};
const errorText = (code: string) => ERRORS[code]
  ?? (code.startsWith("rejected_") ? "The e-mail or password is not correct, or this account has no access."
    : "Sign-in failed. Try again.");
/** The developer token is shown only in builds that opt in (local development) AND when the server offers it. */
const DEV_UI = import.meta.env.VITE_DEV_TOKEN_UI === "1";

/** The Veloxa sign-in screen: only methods this deployment can genuinely offer are shown. */
export function SignInPage({ onSignedIn, reason }: { onSignedIn: () => void; reason?: string }) {
  const [opts, setOpts] = useState<Options | null>(null);
  const [optsErr, setOptsErr] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(() => {
    const m = /[?&]error=([a-z_]+)/.exec(window.location.hash);
    return m ? errorText(m[1]) : reason ? errorText(reason) : null;
  });
  const [token, setTok] = useState("");
  const [view, setView] = useState<"signin" | "forgot" | "reset">(
    () => (window.location.hash.startsWith(sb.RESET_ROUTE) ? "reset" : "signin"));
  useEffect(() => {
    get<Options>("/v1/auth/options").then(setOpts, (e) => setOptsErr(e instanceof ApiError ? e : null));
  }, []);
  const invite = /[?&]invite=([^&]+)/.exec(window.location.hash)?.[1];
  const nothing = opts && opts.providers.length === 0 && !opts.demo && !(opts.developer_token && DEV_UI)
    && !sb.supabaseConfigured();
  return (
    <div className="signin">
      <div className="signin-card" role="main" aria-labelledby="signin-title">
        <div className="signin-brand"><BrandMark /><span>Veloxa</span></div>
        <h1 id="signin-title">{view === "forgot" ? "Reset your password" : view === "reset" ? "Choose a new password"
          : "Sign in to your workspace"}</h1>
        {view === "signin" && <p className="muted">Executive intelligence grounded in your company's records.</p>}
        {err && view === "signin" && <div className="callout bad" role="alert">{err}</div>}
        {optsErr && <div className="callout bad" role="alert">The Veloxa service can't be reached right now.
          Check your connection and try again.</div>}
        {invite && view === "signin" && <p className="small">You're accepting an invitation. Sign in with the account
          your administrator invited.</p>}
        {sb.supabaseConfigured() && view === "signin" &&
          <PasswordForm invite={invite} onSignedIn={onSignedIn} onForgot={() => setView("forgot")} />}
        {view === "forgot" && <Forgot onBack={() => setView("signin")} />}
        {view === "reset" && <Reset onDone={() => { window.location.hash = "/"; setView("signin");
          setErr(null); }} />}
        {view === "signin" && <div className="signin-actions">
          {opts?.providers.map((p) => (
            <a key={p.id} className="btn signin-sso" href={`/v1/auth/login/${p.id}${invite ? `?invite=${invite}` : ""}`}>
              Continue with {p.label}</a>))}
          {sb.googleEnabled() && <Button onClick={sb.googleSignIn}>Continue with Google</Button>}
          {opts?.demo && (
            <Button disabled={busy} onClick={async () => {
              setBusy(true); setErr(null);
              try { await request("POST", "/v1/auth/demo"); onSignedIn(); }
              catch (e) { setErr(e instanceof ApiError ? e.message : "The demo workspace couldn't be opened."); }
              finally { setBusy(false); }
            }}>{busy ? "Opening…" : "Enter Demo Workspace"}</Button>)}
        </div>}
        {opts?.demo && view === "signin" && <p className="small muted">The demo workspace contains fictional company
          data only.</p>}
        {nothing && <p className="small">No sign-in method is configured for this deployment. An administrator
          needs to connect your organisation's identity provider.</p>}
        {opts?.developer_token && DEV_UI && view === "signin" && (
          <details className="signin-dev">
            <summary>Developer access (local only)</summary>
            <form className="row" onSubmit={(e) => { e.preventDefault(); setToken(token.trim() || null); setTok("");
                                                     onSignedIn(); }}>
              <input className="input" type="password" autoComplete="off" value={token} aria-label="Bearer token"
                     placeholder="Development fixture token" onChange={(e) => setTok(e.target.value)} />
              <Button type="submit" disabled={!token.trim()}>Use token</Button>
            </form>
          </details>)}
      </div>
    </div>
  );
}

function PasswordForm({ invite, onSignedIn, onForgot }: { invite?: string; onSignedIn: () => void;
                                                          onForgot: () => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  async function submit() {
    if (busy) return;                                            // no repeated submissions
    setBusy(true); setErr(null);
    try {
      await sb.signIn(email, password);
      setPassword("");
      if (invite) {
        try { await request("POST", "/v1/invitations/accept", { invitation_token: decodeURIComponent(invite) }); }
        catch { await sb.signOut(); throw new Error(errorText("invalid_invitation")); }
      }
      onSignedIn();
    } catch (e) { setErr(e instanceof Error ? e.message : "Sign-in failed. Try again."); }
    finally { setBusy(false); }
  }
  return (
    <form className="stack" style={{ gap: 8 }} aria-label="Sign in with e-mail and password"
          onSubmit={(e) => { e.preventDefault(); void submit(); }}>
      {err && <div className="callout bad small" role="alert">{err}</div>}
      <label className="small">Work e-mail
        <input className="input" type="email" autoComplete="username" required value={email}
               onChange={(e) => setEmail(e.target.value)} /></label>
      <label className="small">Password
        <span className="row" style={{ gap: 8 }}>
          <input className="input" type={show ? "text" : "password"} autoComplete="current-password" required
                 value={password} onChange={(e) => setPassword(e.target.value)} style={{ flex: 1 }} />
          <Button type="button" onClick={() => setShow(!show)} aria-pressed={show}>{show ? "Hide" : "Show"}</Button>
        </span></label>
      <Button type="submit" variant="primary" disabled={busy || !email.includes("@") || !password}>
        {busy ? "Signing in…" : "Sign in"}</Button>
      <button type="button" className="link small" onClick={onForgot}>Forgot password?</button>
    </form>
  );
}

function Forgot({ onBack }: { onBack: () => void }) {
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);
  return sent ? (
    <div className="stack" style={{ gap: 8 }}>
      <p className="callout ok small" role="status">If an account exists for {email}, a link to set a new password is
        on its way. It works once and expires; it does not sign you in.</p>
      <Button onClick={onBack}>Back to sign in</Button>
    </div>
  ) : (
    <form className="stack" style={{ gap: 8 }} onSubmit={async (e) => {
      e.preventDefault(); if (busy) return; setBusy(true);
      await sb.requestReset(email); setBusy(false); setSent(true); }}>
      <p className="small muted">Enter your work e-mail. This also sets your first password if your administrator
        has provisioned your account.</p>
      <label className="small">Work e-mail
        <input className="input" type="email" autoComplete="username" required value={email}
               onChange={(e) => setEmail(e.target.value)} /></label>
      <Button type="submit" variant="primary" disabled={busy || !email.includes("@")}>
        {busy ? "Sending…" : "Send reset link"}</Button>
      <button type="button" className="link small" onClick={onBack}>Back to sign in</button>
    </form>
  );
}

function Reset({ onDone }: { onDone: () => void }) {
  const [p1, setP1] = useState("");
  const [p2, setP2] = useState("");
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const problems = sb.passwordProblems(p1);
  if (done) return (
    <div className="stack" style={{ gap: 8 }}>
      <p className="callout ok small" role="status">Your password was changed. Sign in with your new password.</p>
      <Button variant="primary" onClick={onDone}>Go to sign in</Button>
    </div>);
  return (
    <form className="stack" style={{ gap: 8 }} onSubmit={async (e) => {
      e.preventDefault(); if (busy) return; setBusy(true); setErr(null);
      try { await sb.setNewPassword(p1); setDone(true); }
      catch (x) { setErr(x instanceof Error ? x.message : "The password couldn't be changed."); }
      finally { setBusy(false); } }}>
      {err && <div className="callout bad small" role="alert">{err}</div>}
      <label className="small">New password
        <input className="input" type={show ? "text" : "password"} autoComplete="new-password" value={p1}
               onChange={(e) => setP1(e.target.value)} /></label>
      <label className="small">Repeat new password
        <input className="input" type={show ? "text" : "password"} autoComplete="new-password" value={p2}
               onChange={(e) => setP2(e.target.value)} /></label>
      <label className="small"><input type="checkbox" checked={show} onChange={(e) => setShow(e.target.checked)} />
        {" "}Show passwords</label>
      {p1 && problems.length > 0 && <p className="small muted">Needs {problems.join(", ")}.</p>}
      {p2 && p1 !== p2 && <p className="small muted">The two passwords don't match.</p>}
      <Button type="submit" variant="primary" disabled={busy || problems.length > 0 || p1 !== p2}>
        {busy ? "Saving…" : "Set new password"}</Button>
    </form>
  );
}
