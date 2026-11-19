// Admin > Integrations > GitHub (read-only GitHub App). Nothing is read until repositories are explicitly selected;
// every token Veloxa uses is narrowed to those repositories and read permissions. Synced objects go to the data room
// as a pending submission for review and admission.
import { useState } from "react";
import { ApiError, get, request } from "../api/client";
import { useApi } from "../api/useApi";
import { Badge, Button, ErrorState, Loading, Section } from "../ui";

interface Repo { id: number; full_name: string; private: boolean; default_branch: string | null }
interface Status { configured: boolean; app_slug: string; installation_id: number; account: string;
  repository_selection: string; excess_permissions: string[]; allowlist: number[];
  last_run: Record<string, string | null> | null }
interface SyncOut { dry_run?: boolean; fetched: number; changed?: number; pages: number; by_kind: Record<string, number>;
  submission_id?: string | null; status?: string }

const B = "/v1/integrations/github";
const err = (e: unknown) => (e instanceof ApiError ? e : new ApiError(0, "unknown", String(e)));

export function GitHubIntegration() {
  const [st, reload] = useApi((s) => get<Status>(`${B}/status`, s), []);
  return (
    <Section title="Integrations: GitHub" aria-label="GitHub integration"
             subtitle="Read-only GitHub App. Veloxa reads only the repositories you select, never writes, and treats a
               commit or a green build as activity, not as proof that work is done or deployed.">
      {st.state === "loading" ? <Loading lines={3} /> : st.state === "error" ? <ErrorState error={st.error} />
        : <GitHubBody s={st.data} reload={reload} />}
    </Section>
  );
}

function GitHubBody({ s, reload }: { s: Status; reload: () => void }) {
  const [repos] = useApi((sig) => get<{ repositories: Repo[] }>(`${B}/repositories`, sig), []);
  const [pick, setPick] = useState<number[]>(s.allowlist);
  const [out, setOut] = useState<SyncOut | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  async function act(fn: () => Promise<unknown>) {
    setBusy(true); setError(null);
    try { await fn(); } catch (e) { setError(err(e)); } finally { setBusy(false); }
  }
  const saved = JSON.stringify([...pick].sort()) === JSON.stringify([...s.allowlist].sort());
  const sync = (dry: boolean) => act(async () => { setOut(await request<SyncOut>("POST", `${B}/sync`, { dry_run: dry })); if (!dry) reload(); });
  return (
    <div className="stack">
      <dl className="meta small">
        <dt>App</dt><dd className="mono">{s.app_slug}</dd>
        <dt>Installation</dt><dd className="mono">{s.installation_id} ({s.account}, {s.repository_selection} repositories)</dd>
        <dt>Last sync</dt><dd>{s.last_run ? `${s.last_run.status}, ${s.last_run.fetched} fetched, ${s.last_run.changed} new or changed (${s.last_run.finished_at})` : "Never"}</dd>
      </dl>
      {s.excess_permissions.length > 0 && <p className="callout warn small">The App holds more than Veloxa needs
        ({s.excess_permissions.length} extra permissions, including {s.excess_permissions.slice(0, 3).join(", ")}).
        Veloxa never uses them: each token is narrowed to read access. Consider removing them in the App settings.</p>}
      {error && <ErrorState error={error} />}
      <h3>Repositories Veloxa may read</h3>
      {repos.state === "loading" ? <Loading lines={2} /> : repos.state === "error" ? <ErrorState error={repos.error} />
        : repos.data.repositories.length === 0 ? <p className="small">The installation can see no repositories. Add one in the App installation settings.</p>
        : <div className="stack small">{repos.data.repositories.map((r) => (
            <label key={r.id}><input type="checkbox" checked={pick.includes(r.id)}
              onChange={(e) => setPick(e.target.checked ? [...pick, r.id] : pick.filter((x) => x !== r.id))} />
              {" "}<span className="mono">{r.full_name}</span> {r.private ? <Badge value="open" label="private" /> : null}</label>))}
          </div>}
      <div className="row" style={{ gap: 8 }}>
        <Button disabled={busy || saved} onClick={() => act(async () => { await request("PUT", `${B}/allowlist`, { repository_ids: pick }); reload(); })}>
          Save selection</Button>
        <Button disabled={busy || !s.allowlist.length} onClick={() => sync(true)}>Preview sync</Button>
        <Button variant="primary" disabled={busy || !out?.dry_run} onClick={() => sync(false)}>Submit for review</Button>
      </div>
      {out && <p className="small">{out.dry_run ? "Preview" : "Submitted"}: {out.fetched} objects in {out.pages} page(s)
        ({Object.entries(out.by_kind).map(([k, v]) => `${v} ${k.replace(/_/g, " ")}`).join(", ")})
        {out.dry_run ? "" : `; ${out.changed ?? 0} new or changed${out.submission_id ? `, submission ${out.submission_id}` : ", nothing new to submit"}`}.</p>}
    </div>
  );
}
