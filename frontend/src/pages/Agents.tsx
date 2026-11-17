import { useRef, useState } from "react";
import { api, ApiError, newIdempotencyKey } from "../api/client";
import { useApi } from "../api/useApi";
import type { AgentSession, AgentSummary, AgentTurn, SessionDetail } from "../api/types";
import { Badge, Button, EmptyState, ErrorState, EvidenceList, Loading, PageHead, Section, Segmented } from "../ui";

type Cat = "all" | AgentSummary["category"];
const CATS: { value: Cat; label: string }[] = [
  { value: "all", label: "All" }, { value: "engineering", label: "Engineering" }, { value: "finance", label: "Finance" },
  { value: "people_ops", label: "People Ops" }, { value: "marketing", label: "Marketing" },
];
const canInterview = (a: AgentSummary) => a.activation === "synthetic_test" || a.activation === "active";

function describe(a: AgentSummary): string {
  if (a.agent_id.startsWith("syn-")) return "Synthetic test agent. It represents no employee.";
  if (!a.assigned) return "Unassigned slot. No employee is mapped until the owner approves the mapping.";
  return "Mapped to an employee by an owner decision.";
}

export function AgentsPage() {
  const [cat, setCat] = useState<Cat>("all");
  const [reg, reload] = useApi((s) => api.agents(s), []);
  const [selected, setSelected] = useState<AgentSummary | null>(null);
  const [dirPage, setDirPage] = useState(0);

  return (
    <>
      <PageHead title="Employee agents" intro="One shared runtime. Each agent answers only from evidence that is in its
        knowledge boundary, that you are authorised to see, and that interview policy allows. Agents take no action." />
      <div className="row" style={{ marginBottom: 12 }}>
        <Segmented label="Category" value={cat} options={CATS} onChange={(c) => { setCat(c); setDirPage(0); }} />
      </div>
      <div className="grid-2" style={{ alignItems: "start" }}>
        <Section title="Directory" subtitle={reg.state === "ready" ? `Runtime ${reg.data.runtime_version}` : undefined}>
          {reg.state === "loading" && <Loading />}
          {reg.state === "error" && <ErrorState error={reg.error} onRetry={reload} />}
          {reg.state === "ready" && (() => {
            const all = reg.data.agents.filter((a) => cat === "all" || a.category === cat);
            const rows = all.slice(dirPage * 10, dirPage * 10 + 10);
            return rows.length === 0 ? <EmptyState title="No agents in this category" /> : (
              <div className="table-wrap"><table>
                <thead><tr><th>Agent</th><th>Category</th><th>State</th></tr></thead>
                <tbody>{rows.map((a) => (
                  <tr key={a.agent_id} className="clickable" tabIndex={0} aria-selected={selected?.agent_id === a.agent_id}
                      onClick={() => setSelected(a)} onKeyDown={(e) => e.key === "Enter" && setSelected(a)}>
                    <td className="mono">{a.agent_id}</td>
                    <td>{a.category.replace("_", " ")}</td>
                    <td><Badge value={a.activation} label={a.activation === "synthetic_test" ? "synthetic test" : a.activation} />
                      {!a.assigned && !a.agent_id.startsWith("syn-") && <> <Badge value="unassigned" /></>}</td>
                  </tr>
                ))}</tbody>
              </table>
              {all.length > 10 && (
                <nav className="row" aria-label="Directory pages" style={{ justifyContent: "flex-end", marginTop: 8 }}>
                  <span className="small muted">Page {dirPage + 1} of {Math.ceil(all.length / 10)}</span>
                  <Button disabled={dirPage === 0} onClick={() => setDirPage(dirPage - 1)}>Previous</Button>
                  <Button disabled={(dirPage + 1) * 10 >= all.length} onClick={() => setDirPage(dirPage + 1)}>Next</Button>
                </nav>
              )}</div>
            );
          })()}
        </Section>
        {selected ? <AgentDetail key={selected.agent_id} agent={selected} /> : (
          <Section title="Agent"><EmptyState title="Select an agent">Choose an agent to see its configuration.</EmptyState></Section>
        )}
      </div>
    </>
  );
}

function AgentDetail({ agent }: { agent: AgentSummary }) {
  const [session, setSession] = useState<AgentSession | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [tab, setTab] = useState<"interview" | "history" | "memory">("interview");
  const [resume, setResume] = useState<SessionDetail | null>(null);

  async function start() {
    setError(null);
    try { setSession(await api.openSession(agent.agent_id)); }
    catch (e) { setError(e instanceof ApiError ? e : new ApiError(0, "unknown", String(e))); }
  }

  return (
    <Section title={agent.agent_id} subtitle={describe(agent)}>
      <dl className="small" style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "4px 12px" }}>
        <dt className="muted">Category</dt><dd style={{ margin: 0 }}>{agent.category.replace("_", " ")}</dd>
        <dt className="muted">Activation</dt><dd style={{ margin: 0 }}><Badge value={agent.activation} /></dd>
        <dt className="muted">Configuration</dt><dd style={{ margin: 0 }}>version {agent.config_version} (read-only here)</dd>
        <dt className="muted">Declared tools</dt><dd className="chips" style={{ margin: 0 }}>{agent.tools.map((t) => <code key={t}>{t}</code>)}</dd>
      </dl>
      <div style={{ marginTop: 12 }}>
        <Segmented label="View" value={tab} onChange={setTab}
                   options={[{ value: "interview", label: "Interview" }, { value: "history", label: "History" },
                             { value: "memory", label: "My memories" }]} />
      </div>
      <div style={{ marginTop: 12 }}>
        {tab === "memory" ? <MemoryView agentId={agent.agent_id} /> : tab === "history" ? (
          <HistoryView agentId={agent.agent_id} onOpen={(d) => {
            setResume(d);
            setSession({ session_id: d.session_id, agent_id: d.agent_id, config_version: d.config_version,
                         batch_seq: d.batch_seq, label: d.turns[0]?.label ?? "AI representation of an employee role." });
            setTab("interview");
          }} />
        ) : !canInterview(agent) ? (
          <div className="callout info small">Interviews are closed: this agent is <strong>{agent.activation}</strong>.
            Activation needs an owner-approved employee mapping and configuration.</div>
        ) : session ? <Conversation key={session.session_id} session={session} history={resume}
                                    onClosed={() => { setSession(null); setResume(null); }} /> : (
          <>
            {error && <ErrorState error={error} />}
            <Button variant="primary" onClick={start}>Start interview</Button>
          </>
        )}
      </div>
    </Section>
  );
}

type Entry = { q: string; key: string; turn?: AgentTurn; error?: ApiError; cancelled?: boolean };

function Conversation({ session, history, onClosed }: { session: AgentSession; history: SessionDetail | null;
                                                          onClosed: () => void }) {
  const [msg, setMsg] = useState("");
  const [log, setLog] = useState<Entry[]>(() => (history?.turns ?? []).map((t) => ({
    q: t.message ?? "(question not recorded)", key: "", turn: t })));
  const [busy, setBusy] = useState(false);
  const [closed, setClosed] = useState<number | null>(null);
  const ctrl = useRef<AbortController | null>(null);

  async function run(q: string, key: string) {
    setBusy(true);
    ctrl.current = new AbortController();
    const entry: Entry = { q, key };
    setLog((l) => [...l.filter((x) => !(x.key === key && x.cancelled)), entry]);
    try { entry.turn = await api.sendMessage(session.session_id, q, key, ctrl.current.signal); }
    catch (err) {
      if ((err as Error).name === "AbortError") entry.cancelled = true;
      else entry.error = err instanceof ApiError ? err : new ApiError(0, "unknown", String(err));
    }
    setLog((l) => l.map((x) => (x === entry ? { ...entry } : x)));
    setBusy(false);
  }

  async function send(e: React.FormEvent) {
    e.preventDefault();
    const q = msg.trim();
    if (!q || busy) return;
    setMsg("");
    await run(q, newIdempotencyKey());               // one key per message: a retry can never duplicate the turn
  }

  async function close() {
    const r = await api.closeSession(session.session_id);
    setClosed(r.working_memory_closed);
  }

  return (
    <div className="stack">
      <p className="label-ai">{session.label} Session at K={session.batch_seq}, configuration v{session.config_version}.</p>
      <div className="chat" aria-live="polite">
        {log.map((l, i) => (
          <div key={i} className="stack">
            <div className="bubble me">{l.q}</div>
            {l.cancelled ? (
              <div className="callout warn small">Cancelled. The server may still finish this turn; Retry sends the
                same request, which returns that turn instead of creating a new one.
                <Button variant="ghost" onClick={() => run(l.q, l.key)} disabled={busy}>Retry</Button></div>
            ) : l.error ? <ErrorState error={l.error} /> : l.turn ? <TurnView turn={l.turn} /> : <Loading lines={2} />}
          </div>
        ))}
      </div>
      {closed === null ? (
        <form onSubmit={send} className="row">
          <input className="input" style={{ flex: 1 }} value={msg} maxLength={2000} onChange={(e) => setMsg(e.target.value)}
                 placeholder="Ask the agent about the work" aria-label="Message to the agent" />
          {busy ? <Button type="button" onClick={() => ctrl.current?.abort()}>Cancel</Button>
            : <Button variant="primary" type="submit" disabled={!msg.trim()}>Send</Button>}
          <Button onClick={close} disabled={busy}>Close session</Button>
        </form>
      ) : (
        <div className="callout info small">Session closed; {closed} working-memory item(s) ended.
          <Button variant="ghost" onClick={onClosed}>New session</Button></div>
      )}
    </div>
  );
}

function TurnView({ turn }: { turn: AgentTurn }) {
  const [showEvidence, setShowEvidence] = useState(false);
  return (
    <div className="bubble stack">
      <div className="row"><Badge value={turn.status} />{turn.replayed && <Badge value="replayed" />}
        <span className="label-ai">{turn.label}</span></div>
      {turn.refusal && <div className="callout bad small"><strong>{turn.refusal.policy}</strong> {turn.refusal.reason}</div>}
      {turn.claims.length > 0 && (
        <ul className="plain">{turn.claims.map((c) => (
          <li key={c.fact_id} className="claim">{c.text} <Badge value={c.knowledge_basis} title="knowledge basis" /></li>
        ))}</ul>
      )}
      {turn.unknowns.length > 0 && <ul className="bullets small">{turn.unknowns.map((u, i) => <li key={i}>{u}</li>)}</ul>}
      {turn.escalation && <p className="small muted">Escalation: {turn.escalation.to}, through {turn.escalation.how}.</p>}
      <details className="small">
        <summary>Tools and boundary</summary>
        <ul className="bullets">{turn.tools.map((t, i) => (
          <li key={i}><code>{t.tool}</code> {t.decision}{t.decision === "denied" ? ` (${t.reason})` : t.status ? ` · ${t.status}` : ""}</li>
        ))}</ul>
        <p className="muted">Excluded evidence: policy {turn.excluded.policy ?? 0}, authorisation {turn.excluded.authorization ?? 0},
          knowledge boundary {turn.excluded.knowledge ?? 0}. Execution <code>{turn.execution_id}</code>.</p>
      </details>
      {turn.evidence.length > 0 && (
        <div><Button variant="ghost" onClick={() => setShowEvidence((s) => !s)}>{showEvidence ? "Hide" : "Show"} evidence ({turn.evidence.length})</Button>
          {showEvidence && <EvidenceList items={turn.evidence} />}</div>
      )}
    </div>
  );
}

function MemoryView({ agentId }: { agentId: string }) {
  const [mem, reload] = useApi((s) => api.memories(agentId, s), [agentId]);
  if (mem.state === "loading") return <Loading />;
  if (mem.state === "error") return <ErrorState error={mem.error} onRetry={reload} />;
  return (
    <div className="stack">
      <p className="small muted">Only memories in your own scope (this agent, you as interviewer) at K={mem.data.batch_seq}.
        {mem.data.withheld > 0 && ` ${mem.data.withheld} item(s) withheld: you are no longer authorised to see their sources.`}</p>
      {mem.data.items.length === 0 ? <EmptyState title="No memories" /> : (
        <div className="table-wrap"><table>
          <thead><tr><th>Tier</th><th>Label</th><th>Content</th></tr></thead>
          <tbody>{mem.data.items.map((m) => (
            <tr key={m.memory_id}><td><Badge value={m.tier} /></td>
              <td><Badge value={m.label} label={m.label === "inference" ? "inference (never evidence)" : m.label.replace(/_/g, " ")} /></td>
              <td className="small mono" style={{ wordBreak: "break-word" }}>{JSON.stringify(m.content).slice(0, 220)}</td></tr>
          ))}</tbody>
        </table></div>
      )}
    </div>
  );
}

function HistoryView({ agentId, onOpen }: { agentId: string; onOpen: (d: SessionDetail) => void }) {
  const [offset, setOffset] = useState(0);
  const [list, reload] = useApi((s) => api.sessions(agentId, offset, 10, s), [agentId, offset]);
  const [error, setError] = useState<ApiError | null>(null);
  if (list.state === "loading") return <Loading />;
  if (list.state === "error") return <ErrorState error={list.error} onRetry={reload} />;
  if (list.data.sessions.length === 0) {
    return <EmptyState title="No sessions yet">Your interviews with this agent appear here.</EmptyState>;
  }
  return (
    <div className="stack">
      {error && <ErrorState error={error} />}
      <ul className="plain stack" aria-label="Your sessions">
        {list.data.sessions.map((s) => (
          <li key={s.session_id} className="row" style={{ justifyContent: "space-between" }}>
            <span className="small">{new Date(s.opened_at).toLocaleString()} · {s.turns} turn(s) · K={s.batch_seq}</span>
            <Button onClick={async () => {
              try { onOpen(await api.session(s.session_id)); }
              catch (e) { setError(e instanceof ApiError ? e : new ApiError(0, "unknown", String(e))); }
            }}>Open</Button>
          </li>
        ))}
      </ul>
      <nav className="row" aria-label="Session pages" style={{ justifyContent: "flex-end" }}>
        <Button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 10))}>Newer</Button>
        <Button disabled={!list.data.has_more} onClick={() => setOffset(offset + 10)}>Older</Button>
      </nav>
    </div>
  );
}
