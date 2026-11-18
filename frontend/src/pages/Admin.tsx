import { useState } from "react";
import { ApiError, getToken, setToken } from "../api/client";
import { product } from "../api/product";
import { useApi } from "../api/useApi";
import { Guidance } from "../ui/kit";
import { Badge, Button, ErrorState, Loading, PageHead, Section } from "../ui";

/** Sign-in (local fixture until the workspace's own identity provider is configured), who you are, and workspace
 * administration. Every write is checked by the database (active admin, no self-escalation). */
export function AdminPage({ onAuthChange }: { onAuthChange: () => void }) {
  const signedIn = Boolean(getToken());
  return (
    <>
      <PageHead title="Administration & Access" intro="Sign-in, your access, and workspace administration." />
      <SignIn signedIn={signedIn} onChange={onAuthChange} />
      {signedIn && <WhoAmI />}
      {signedIn && <Workspace />}
    </>
  );
}

function SignIn({ signedIn, onChange }: { signedIn: boolean; onChange: () => void }) {
  const [value, setValue] = useState("");
  return (
    <Section title={signedIn ? "Signed in" : "Not signed in"} subtitle="Workspaces with their own identity provider sign
      in with it. Locally and in the synthetic Demo Lab, paste the development fixture token (kept for this tab only).">
      <form className="row" onSubmit={(e) => { e.preventDefault(); setToken(value.trim() || null); setValue(""); onChange(); }}>
        <input className="input" type="password" autoComplete="off" value={value} style={{ flex: 1, minWidth: 220 }}
               onChange={(e) => setValue(e.target.value)} aria-label="Bearer token" placeholder="Bearer token" />
        <Button variant="primary" type="submit" disabled={!value.trim()}>Use token</Button>
        {signedIn && <Button type="button" onClick={() => { setToken(null); onChange(); }}>Sign out</Button>}
      </form>
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
