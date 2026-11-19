import { useState } from "react";
import { ApiError, getToken } from "../api/client";
import { product } from "../api/product";
import { AgentAdministration } from "./Agents";
import { JiraIntegration } from "./Integrations";
import { JiraPeople } from "./JiraPeople";
import { useApi } from "../api/useApi";
import { Guidance } from "../ui/kit";
import { Badge, Button, ErrorState, Loading, PageHead, Section } from "../ui";

/** Sign-in (local fixture until the workspace's own identity provider is configured), who you are, and workspace
 * administration. Every write is checked by the database (active admin, no self-escalation). */
export function AdminPage({ onSignOut }: { onSignOut: () => void }) {
  return (
    <>
      <PageHead title="Administration & Access" intro="Your account and access, and workspace administration." />
      <Account onSignOut={onSignOut} />
      <WhoAmI />
      <Workspace />
      <JiraIntegration />
      <JiraPeople />
      <Connectors />
      <AgentAdministration />
    </>
  );
}

function Account({ onSignOut }: { onSignOut: () => void }) {
  const [s] = useApi((sig) => (getToken() ? Promise.resolve(null) : product.session(sig).catch(() => null)), []);
  const info = s.state === "ready" ? s.data : null;
  const how = info?.method === "oidc" ? `single sign-on (${info.provider === "entra" ? "Microsoft Entra ID"
    : info.provider === "google" ? "Google Workspace" : info.provider})`
    : info?.method === "demo" ? "the local demo workspace" : getToken() ? "a developer token (this tab only)" : "—";
  return (
    <Section title="Signed in" aria-label="Account">
      <p className="small">Signed in with {how}.{info && ` Session ends ${new Date(info.expires_at).toLocaleString()}`
        + " or after 30 minutes without activity."}</p>
      <p style={{ marginTop: 8 }}><Button onClick={onSignOut}>Sign out</Button></p>
    </Section>
  );
}

function WhoAmI() {
  const [me] = useApi((s) => product.me(s), []);
  if (me.state === "loading") return <Section><Loading lines={2} /></Section>;
  if (me.state === "error") return <Section><ErrorState error={me.error} /></Section>;
  const d = me.data;
  return (
    <Section title="Your access">
      <dl className="meta small">
        <dt>Workspace</dt><dd>{d.tenant.display_name} {d.tenant.synthetic && <span className="demo-badge">DEMO</span>}</dd>
        <dt>You</dt><dd>{d.principal.display_name ?? d.principal.principal_id} ({d.principal.status})</dd>
        <dt>Evidence access</dt><dd>{d.access.mode === "tenant_wide" ? "tenant-wide (pre-workspace tenant)"
          : d.access.company ? "company-wide (explicit grant)" : `${d.access.sources.length} source(s) via ${d.access.departments.join(", ") || "direct grants"}`}</dd>
        <dt>Roles</dt><dd>{d.relations.length ? d.relations.map((r) => `${r.relation} on ${r.resource.split(":")[0]}`).join("; ") : "none"}</dd>
      </dl>
    </Section>
  );
}

function Workspace() {
  const [m, reload] = useApi((s) => product.members(s), []);
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [token, setTok] = useState<string | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);
  if (m.state === "loading") return <Section><Loading /></Section>;
  if (m.state === "error") {
    return <Section title="Workspace administration">{m.error.status === 403
      ? <Guidance title="Administrators only">Workspace administration needs the administrator relation.</Guidance>
      : <ErrorState error={m.error} />}</Section>;
  }
  async function invite(e: React.FormEvent) {
    e.preventDefault(); setErr(null);
    try { const r = await product.invite(email, name, []); setTok(r.invitation_token); setEmail(""); setName(""); reload(); }
    catch (x) { setErr(x instanceof ApiError ? x : new ApiError(0, "unknown", String(x))); }
  }
  return (
    <>
      <Section title="Members">
        <div className="table-wrap"><table>
          <thead><tr><th>Member</th><th>Status</th><th>Roles</th></tr></thead>
          <tbody>{m.data.members.map((x) => (
            <tr key={x.principal_id}><td>{x.display_name}<div className="small muted">{x.email ?? x.principal_id}</div></td>
              <td><Badge value={x.status === "active" ? "active" : "pending_admission"} label={x.status} /></td>
              <td className="small">{x.relations.map((r) => `${r.relation} · ${r.resource}`).join("; ") || "—"}</td></tr>))}</tbody>
        </table></div>
      </Section>
      <Section title="Invite a member" subtitle="Creates a one-time invitation token. Veloxa sends nothing; give the token to the invitee yourself.">
        <form className="row" onSubmit={invite}>
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Name" aria-label="Invitee name" required />
          <input className="input" type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="Work e-mail"
                 aria-label="Invitee e-mail" required />
          <Button variant="primary" type="submit">Create invitation</Button>
        </form>
        {token && <p className="callout warn small" role="status">One-time token (shown once): <code>{token}</code></p>}
        {err && <ErrorState error={err} />}
      </Section>
    </>
  );
}

function Connectors() {
  const [c, reload] = useApi((s) => product.connectors(s), []);
  return (
    <Section title="Connectors and model experiment" subtitle="Configured is not connected until a live test passes;
      synchronised records are pending review until they are admitted. No credential is ever shown.">
      {c.state === "loading" && <Loading lines={3} />}
      {c.state === "error" && <ErrorState error={c.error} onRetry={reload} />}
      {c.state === "ready" && (
        <div className="stack">
          <div className="table-wrap"><table>
            <thead><tr><th>Application</th><th>State</th></tr></thead>
            <tbody>{c.data.connectors.map((x) => (
              <tr key={x.connector}><td>{x.connector}</td><td className="small">{x.state}
                {x.reason && <div className="muted">{x.reason}</div>}</td></tr>))}</tbody>
          </table></div>
          {c.data.runs.length === 0 ? <p className="small muted">No synchronisation has run.</p> : (
            <div className="table-wrap"><table>
              <thead><tr><th>Run</th><th>Status</th><th>Fetched</th><th>Changed</th><th>Submitted for review</th></tr></thead>
              <tbody>{c.data.runs.map((r) => (
                <tr key={r.started_at}><td>{r.connector} {r.mode}<div className="small muted">{r.started_at}</div></td>
                  <td><Badge value={r.status === "ok" ? "active" : "failed"} label={r.status} /></td>
                  <td>{r.fetched}</td><td>{r.changed}</td><td>{r.submitted}</td></tr>))}</tbody>
            </table></div>)}
          {c.data.ai_wording && <AiWordingPanel a={c.data.ai_wording} keyConfigured={c.data.gemini_experiment.key_configured} />}
        </div>)}
    </Section>
  );
}

/** What the CEO's explanations are actually using, from the API this page is connected to (diagnostics live here only). */
function AiWordingPanel({ a, keyConfigured }: { a: import("../api/product-types").AiWording; keyConfigured: boolean }) {
  const reason = String(a.configuration.reason ?? "");
  return (
    <div className="callout" aria-label="AI-assisted wording">
      <strong>AI-assisted wording: {a.active ? "on for this workspace" : "off for this workspace"}</strong>
      <dl className="small" style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "2px 12px", marginTop: 8 }}>
        <dt className="muted">Model</dt><dd style={{ margin: 0 }}>{a.model ? `${a.provider} ${a.model}` : "none configured"}</dd>
        <dt className="muted">Workspace</dt><dd style={{ margin: 0 }}>{a.workspace} ({a.workspace_synthetic ? "synthetic"
          : "real company data"}): {a.workspace_in_scope ? "in scope" : "not in scope"}. {a.scope_reason}.</dd>
        <dt className="muted">API key</dt><dd style={{ margin: 0 }}>{keyConfigured ? "set on the backend (never shown)" : "not set"}</dd>
        {reason && <><dt className="muted">Diagnostic</dt><dd className="mono" style={{ margin: 0 }}>{reason}</dd></>}
        <dt className="muted">Connected API</dt><dd className="mono" style={{ margin: 0 }}>{window.location.host} → /v1 (proxied)</dd>
      </dl>
      <p className="small muted" style={{ marginTop: 6 }}>{a.what_it_does}. When it is off or a wording fails the
        check, the verified explanation is shown.</p>
    </div>
  );
}
