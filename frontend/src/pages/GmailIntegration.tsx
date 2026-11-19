// Admin > Integrations > Gmail (read-only). The browser never sees a token or the client secret. Nothing is read
// from the mailbox until an administrator saves an explicit scope; synced messages go to the data room as a pending
// submission (communication, not verified fact) and pass the normal review and admission.
import { useState } from "react";
import { ApiError, get, request } from "../api/client";
import { useApi } from "../api/useApi";
import { Badge, Button, ErrorState, Loading, Section } from "../ui";

interface Scope { labels: string[]; senders: string[]; after: string | null; limit: number }
interface GmailStatus {
  client_configured: boolean; connected: boolean; mailbox?: string | null; callback_url?: string | null;
  callback_path?: string; requested_scopes?: string[]; scopes?: string[]; token_storage?: string;
  scope?: Scope | null; scope_selected?: boolean; last_sync?: string | null; last_error?: string | null;
  needs_reconnect?: boolean; connected_at?: string | null;
}
interface SyncOut { dry_run?: boolean; mode: string; listed: number; kept: number; pages: number; changed?: number;
  submission_id?: string | null; preview?: { message_id: string; from: string; subject: string; date: string }[] }

const G = "/v1/integrations/gmail";
const FLASH: Record<string, string> = {
  connected: "Gmail is connected. Choose what Veloxa may read before anything is imported.",
  denied: "Access was not granted in Google, so nothing changed.",
  bad_state: "The sign-in link expired or was already used. Connect again.",
  auth_refused: "Google refused the authorization. Connect again.",
  wrong_scope: "Google granted a different access level than read-only, so nothing was stored.",
  not_configured: "Gmail OAuth is not configured on the server.",
};
const when = (s?: string | null) => (s ? new Date(s).toLocaleString() : "Never");
const err = (e: unknown) => (e instanceof ApiError ? e : new ApiError(0, "unknown", String(e)));
const list = (s: string) => s.split(/[,\n]/).map((x) => x.trim()).filter(Boolean);

export function GmailIntegration() {
  const [st, reload] = useApi((s) => get<GmailStatus>(`${G}/status`, s), []);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const flash = new URLSearchParams(window.location.hash.split("?")[1] ?? "").get("gmail");
  async function act(fn: () => Promise<unknown>) {
    setBusy(true); setError(null);
    try { await fn(); reload(); } catch (e) { setError(err(e)); } finally { setBusy(false); }
  }
  const connect = () => act(async () => {
    const r = await request<{ authorize_url: string }>("POST", `${G}/connect`);
    window.location.assign(r.authorize_url);
  });
  return (
    <Section title="Integrations: Gmail" aria-label="Gmail integration"
             subtitle="Read-only access to one mailbox. Veloxa reads nothing until you choose labels or senders, never
               downloads attachments, and treats e-mail as a record of communication, not as verified fact.">
      {flash && FLASH[flash] && <p className={`callout ${flash === "connected" ? "ok" : "warn"} small`}>{FLASH[flash]}</p>}
      {error && <ErrorState error={error} />}
      {st.state === "loading" ? <Loading lines={3} /> : st.state === "error" ? <ErrorState error={st.error} />
        : !st.data.client_configured ? <p className="small">Gmail OAuth is not configured on the server
            (set VELOXA_GMAIL_CLIENT_FILE and VELOXA_PUBLIC_URL).</p>
        : <GmailBody s={st.data} busy={busy} connect={connect} act={act} />}
    </Section>
  );
}

function GmailBody({ s, busy, connect, act }: { s: GmailStatus; busy: boolean; connect: () => void;
                                                 act: (fn: () => Promise<unknown>) => Promise<void> }) {
  const label = s.needs_reconnect ? "Reconnect needed" : s.connected ? "Connected" : "Not connected";
  return (
    <div className="stack">
      <dl className="meta small">
        <dt>Status</dt><dd><Badge value={s.needs_reconnect ? "bad" : s.connected ? "ok" : "open"} label={label} /></dd>
        <dt>Mailbox</dt><dd>{s.mailbox ?? "None"}</dd>
        <dt>Access</dt><dd className="mono">{(s.scopes?.length ? s.scopes : s.requested_scopes ?? []).join(" ")}</dd>
        <dt>Last sync</dt><dd>{when(s.last_sync)}</dd>
        <dt>Callback URL</dt><dd className="mono">{s.callback_url ?? "Not set: configure VELOXA_PUBLIC_URL"}</dd>
        <dt>Token storage</dt><dd>{s.token_storage}</dd>
      </dl>
      {s.needs_reconnect && <p className="callout bad small">Google stopped accepting this connection (testing apps
        expire Gmail access after 7 days, or access was revoked). Reconnect to continue; nothing is read meanwhile.</p>}
      <div className="row" style={{ gap: 8 }}>
        <Button variant="primary" disabled={busy || !s.callback_url} onClick={connect}>
          {s.connected ? "Reconnect" : "Connect Gmail (read-only)"}</Button>
        {s.connected && <Button disabled={busy} onClick={() => act(() => request("POST", `${G}/disconnect`))}>
          Disconnect and revoke</Button>}
      </div>
      {s.connected && !s.needs_reconnect && <GmailScope s={s} act={act} busy={busy} />}
      {s.connected && !s.needs_reconnect && s.scope_selected && <GmailSync />}
    </div>
  );
}

function GmailScope({ s, act, busy }: { s: GmailStatus; busy: boolean; act: (fn: () => Promise<unknown>) => Promise<void> }) {
  const [labels] = useApi((sig) => get<{ labels: { id: string; name: string; type: string }[] }>(`${G}/labels`, sig), []);
  const [pick, setPick] = useState<string[]>(s.scope?.labels ?? []);
  const [senders, setSenders] = useState((s.scope?.senders ?? []).join(", "));
  const [after, setAfter] = useState(s.scope?.after ?? "");
  const [limit, setLimit] = useState(s.scope?.limit ?? 25);
  const save = () => act(() => request("PUT", `${G}/scope`, { labels: pick, senders: list(senders),
                                                               after: after || null, limit }));
  return (
    <div className="stack">
      <h3>What Veloxa may read</h3>
      <p className="small muted">Choose at least one label or approved sender. Messages outside this scope are never
        stored. Saving a new scope starts a new bounded import.</p>
      {labels.state === "loading" ? <Loading lines={2} /> : labels.state === "error" ? <ErrorState error={labels.error} />
        : <fieldset className="stack small"><legend>Labels</legend>
            <div className="row" style={{ gap: 12, flexWrap: "wrap" }}>
              {labels.data.labels.map((l) => <label key={l.id}><input type="checkbox" checked={pick.includes(l.id)}
                onChange={(e) => setPick(e.target.checked ? [...pick, l.id] : pick.filter((x) => x !== l.id))} /> {l.name}</label>)}
            </div></fieldset>}
      <label className="small">Approved senders (addresses or domains, comma separated)
        <input className="input" value={senders} onChange={(e) => setSenders(e.target.value)} placeholder="name@example.test, example.test" /></label>
      <div className="row" style={{ gap: 16 }}>
        <label className="small">From date<input className="input" type="date" value={after} onChange={(e) => setAfter(e.target.value)} /></label>
        <label className="small">Initial import limit<input className="input" type="number" min={1} max={500} value={limit}
          onChange={(e) => setLimit(Number(e.target.value))} /></label>
      </div>
      <div><Button disabled={busy || (!pick.length && !list(senders).length)} onClick={save}>Save scope</Button></div>
    </div>
  );
}

function GmailSync() {
  const [out, setOut] = useState<SyncOut | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  async function run(dry: boolean) {
    setBusy(true); setError(null);
    try { setOut(await request<SyncOut>("POST", `${G}/sync`, { department_id: "engineering",
            source_type: "communications_gmail", dry_run: dry })); }
    catch (e) { setError(err(e)); } finally { setBusy(false); }
  }
  return (
    <div className="stack">
      <h3>Import</h3>
      <p className="small muted">Preview lists what would be imported without storing anything. Submitting sends the
        messages to the Data Room as a pending submission for review and admission.</p>
      <div className="row" style={{ gap: 8 }}>
        <Button disabled={busy} onClick={() => run(true)}>Preview import</Button>
        <Button variant="primary" disabled={busy || !out?.dry_run} onClick={() => run(false)}>Submit for review</Button>
      </div>
      {error && <ErrorState error={error} />}
      {out && <div className="small stack">
        <p>{out.dry_run ? "Preview" : "Submitted"} ({out.mode}): {out.listed} listed, {out.kept} in scope,
          {" "}{out.pages} page(s){out.dry_run ? "" : `, ${out.changed ?? 0} new or changed`}
          {out.submission_id ? `, submission ${out.submission_id}` : ""}.</p>
        {out.preview && <div className="table-wrap"><table className="crit-table"><thead><tr><th>From</th><th>Subject</th><th>Date</th></tr></thead>
          <tbody>{out.preview.map((m) => <tr key={m.message_id}><td>{m.from}</td><td>{m.subject}</td><td>{m.date}</td></tr>)}</tbody>
        </table></div>}
      </div>}
    </div>
  );
}
