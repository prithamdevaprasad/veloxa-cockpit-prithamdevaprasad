import { useEffect, useState } from "react";
import { ApiError, get, request, setToken } from "../api/client";
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
  rejected_not_a_member: "You're signed in with your provider, but you aren't a member of a Veloxa workspace. "
    + "Use the invitation link your administrator sent you.",
  mfa_required: "This workspace requires multi-factor authentication. Sign in again with MFA.",
  session_expired: "Your session has expired. Sign in again.",
};
const errorText = (code: string) => ERRORS[code]
  ?? (code.startsWith("rejected_") ? "Your sign-in couldn't be verified, so it was refused. Try again or contact "
    + "your administrator." : "Sign-in failed. Try again.");

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
  useEffect(() => {
    get<Options>("/v1/auth/options").then(setOpts, (e) => setOptsErr(e instanceof ApiError ? e : null));
  }, []);
  const invite = /[?&]invite=([^&]+)/.exec(window.location.hash)?.[1];
  const nothing = opts && opts.providers.length === 0 && !opts.demo && !opts.developer_token;
  return (
    <div className="signin">
      <div className="signin-card" role="main" aria-labelledby="signin-title">
        <div className="signin-brand"><BrandMark /><span>Veloxa</span></div>
        <h1 id="signin-title">Sign in to your workspace</h1>
        <p className="muted">Executive intelligence grounded in your company's records.</p>
        {err && <div className="callout bad" role="alert">{err}</div>}
        {optsErr && <div className="callout bad" role="alert">The Veloxa service can't be reached right now.
          Check your connection and try again.</div>}
        {invite && <p className="small">You're accepting an invitation. Sign in with the account your administrator
          invited.</p>}
        <div className="signin-actions">
          {opts?.providers.map((p) => (
            <a key={p.id} className="btn primary signin-sso" href={`/v1/auth/login/${p.id}${invite ? `?invite=${invite}` : ""}`}>
              Continue with {p.label}</a>))}
          {opts?.demo && (
            <Button variant={opts.providers.length ? undefined : "primary"} disabled={busy} onClick={async () => {
              setBusy(true); setErr(null);
              try { await request("POST", "/v1/auth/demo"); onSignedIn(); }
              catch (e) { setErr(e instanceof ApiError ? e.message : "The demo workspace couldn't be opened."); }
              finally { setBusy(false); }
            }}>{busy ? "Opening…" : "Enter Demo Workspace"}</Button>)}
        </div>
        {opts?.demo && <p className="small muted">The demo workspace contains fictional company data only.</p>}
        {nothing && <p className="small">No sign-in method is configured for this deployment. An administrator
          needs to connect your organisation's identity provider.</p>}
        {opts?.developer_token && (
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
