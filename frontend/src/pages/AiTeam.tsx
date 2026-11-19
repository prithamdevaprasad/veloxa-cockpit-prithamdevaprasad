// The CEO's AI team: the 14 people from the HR record, each with an AI assistant that can be interviewed.
// People = employee records; agents = AI assistants of those people; interviews = conversations with them.
// The person -> agent link is the explicit owner-approved mapping returned by /v1/people (never inferred here).
import { useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import { product } from "../api/product";
import type { Person } from "../api/product-types";
import type { AgentSession, AgentSummary, SessionDetail } from "../api/types";
import { useApi } from "../api/useApi";
import { AgentAvatar } from "../ui/art";
import { Guidance } from "../ui/kit";
import { Button, Drawer, ErrorState, Loading, PageHead, Section, Segmented } from "../ui";
import { Conversation, HistoryView } from "./Agents";
import { Profile } from "./People";

export const DISCLOSURE = "This is an AI assistant grounded in approved company records. It is not the actual employee.";
const DEPTS = [["Engineering", "engineering"], ["Finance", "finance"], ["People & Ops", "people_ops"],
               ["Marketing", "marketing"]] as const;
const HELP: Record<string, string> = {
  "self.profile": "their own role, department and reporting line", "people.headcount": "headcount",
  "people.profile": "colleagues' recorded roles", "finance.ledger_spend": "ledger spend",
  "delivery.timeline": "project dates and events", "delivery.board_statements": "figures reported to the board",
  "conflicts.register": "where sources disagree", "briefing.today": "what needs attention",
};
const canInterview = (a?: AgentSummary) => !!a && (a.activation === "active" || a.activation === "synthetic_test");

export function helpsWith(a?: AgentSummary): string {
  const items = (a?.tools ?? []).map((t) => HELP[t]).filter(Boolean);
  return items.length ? `Can answer from records about ${items.slice(0, 4).join(", ")}.` : "No interview tools are enabled.";
}

/** Query parameters on the hash route, e.g. #/agents?interview=agent-eng-01 */
export function hashParam(name: string): string | null {
  const q = window.location.hash.split("?")[1] ?? "";
  return new URLSearchParams(q).get(name);
}

export function useTeam() {
  const [ppl, reloadP] = useApi((s) => product.people(s), []);
  const [reg, reloadR] = useApi((s) => api.agents(s), []);
  const agents = reg.state === "ready" ? reg.data.agents : [];
  const mapped = ppl.state === "ready" ? ppl.data.people : [];
  const DEPT_OF: Record<string, string> = { engineering: "Engineering", finance: "Finance", people_ops: "People & Ops",
                                             marketing: "Marketing" };
  const people: Person[] = mapped.length ? mapped : agents.filter((a) => a.agent_id.startsWith("syn-")).map((a) => ({
    agent_id: a.agent_id, name: a.agent_id.replace(/^syn-/, "Synthetic ").replace(/-/g, " "),
    title: "Synthetic test agent (represents no employee)", department: DEPT_OF[a.category] ?? a.category,
    person_record: null, verified_facts: [], assumptions: [], workload: null,
    unknowns: ["This synthetic agent represents no employee; it exists for testing."] } as unknown as Person));
  const byId = new Map(agents.map((a) => [a.agent_id, a]));
  const loading = ppl.state === "loading" || reg.state === "loading";
  const error = ppl.state === "error" ? ppl.error : reg.state === "error" ? reg.error : null;
  return { people, byId, loading, error, reload: () => { reloadP(); reloadR(); } };
}

export function AiTeamPage() {
  const team = useTeam();
  const [dept, setDept] = useState<string>("all");
  const [profile, setProfile] = useState<Person | null>(null);
  const [interview, setInterview] = useState<Person | null>(null);
  useEffect(() => {                                    // deep links from Overview and People profiles
    const apply = () => {
      const want = hashParam("interview") ?? hashParam("profile");
      const p = want ? team.people.find((x) => x.agent_id === want) : undefined;
      if (!p) return;
      if (hashParam("interview")) setInterview(p); else setProfile(p);
    };
    apply();
    window.addEventListener("hashchange", apply);
    return () => window.removeEventListener("hashchange", apply);
  }, [team.people.length]);  // eslint-disable-line react-hooks/exhaustive-deps
  const shown = team.people.filter((p) => dept === "all" || p.department === dept);
  return (
    <>
      <PageHead title="AI team" intro="The people in your company, and the AI assistant of each one that you can
        interview. Every answer comes from approved company records and shows its sources." />
      <p className="callout info small" role="note">{DISCLOSURE}</p>
      {team.loading && <Section><Loading lines={4} /></Section>}
      {team.error && <Section><ErrorState error={team.error} onRetry={team.reload} /></Section>}
      {!team.loading && !team.error && team.people.length === 0 && (
        <Section><Guidance title="No AI assistants yet">An AI assistant is created only after the owner
          approves which HR record it represents. Administrators can see the configuration under Admin.</Guidance></Section>)}
      {team.people.length > 0 && <>
        <div className="row" style={{ marginBottom: 12 }}>
          <Segmented label="Department" value={dept} onChange={setDept}
                     options={[{ value: "all", label: `All (${team.people.length})` },
                               ...DEPTS.map(([d]) => ({ value: d, label: `${d} (${team.people.filter((p) => p.department === d).length})` }))]} />
        </div>
        {DEPTS.filter(([d]) => dept === "all" || dept === d).map(([d]) => {
          const group = shown.filter((p) => p.department === d);
          if (!group.length) return null;
          return (
            <Section key={d} title={d} aria-label={`${d} team`} subtitle={`${group.length} ${group.length === 1 ? "person" : "people"}`}>
              <div className="person-grid">{group.map((p) => {
                const a = team.byId.get(p.agent_id);
                return (
                  <article key={p.agent_id} className="team-card" aria-label={p.name ?? "Team member"}>
                    <div className="row" style={{ flexWrap: "nowrap", gap: 12 }}>
                      <AgentAvatar id={p.name ?? p.agent_id} size={42} />
                      <div style={{ minWidth: 0 }}><strong>{p.name}</strong>
                        <div className="small muted">{p.title}, {p.department}</div></div>
                    </div>
                    <p className="small" style={{ margin: "8px 0" }}>{helpsWith(a)}</p>
                    <div className="row" style={{ gap: 8 }}>
                      <Button onClick={() => setProfile(p)}>Open profile</Button>
                      {canInterview(a) && <Button variant="primary" onClick={() => setInterview(p)}>Start interview</Button>}
                    </div>
                  </article>);
              })}</div>
            </Section>);
        })}
      </>}
      {profile && <Profile p={profile} onClose={() => setProfile(null)}
                           onInterview={canInterview(team.byId.get(profile.agent_id))
                             ? () => { setProfile(null); setInterview(profile); } : undefined} />}
      {interview && <InterviewDrawer person={interview} agent={team.byId.get(interview.agent_id)}
                                     onClose={() => setInterview(null)} />}
    </>
  );
}

/** An interview with ONE person's AI assistant; its history and sessions stay with that agent only. */
export function InterviewDrawer({ person, agent, onClose }: { person: Person; agent?: AgentSummary; onClose: () => void }) {
  const [session, setSession] = useState<AgentSession | null>(null);
  const [resume, setResume] = useState<SessionDetail | null>(null);
  const [tab, setTab] = useState<"interview" | "history">("interview");
  const [error, setError] = useState<ApiError | null>(null);
  async function start() {
    setError(null);
    try { setSession(await api.openSession(person.agent_id)); }
    catch (e) { setError(e instanceof ApiError ? e : new ApiError(0, "unknown", String(e))); }
  }
  // One click opens the interview: the session starts as soon as the modal mounts (no second button).
  const ok = canInterview(agent);
  useEffect(() => { if (ok && !session) void start(); }, [person.agent_id, ok]); // eslint-disable-line react-hooks/exhaustive-deps
  const opening = `Hello, I'm ${person.name ? `${person.name}'s` : "this team member's"} AI assistant`
    + `${person.title ? `, ${person.title}` : ""}${person.department ? ` in ${person.department}` : ""}. I answer from `
    + "approved company records only. Ask me about my role, my work, or what the records do and don't show.";
  return (
    <Drawer modal title={`Interview: ${person.name ?? "AI assistant"}`} onClose={onClose}
            header={<div className="row" style={{ flexWrap: "nowrap", gap: 12 }}>
              <AgentAvatar id={person.name ?? person.agent_id} size={44} />
              <div><h2>{person.name}</h2><div className="small muted">{person.title}, {person.department}</div></div>
            </div>}>
      <div className="interview">
        <p className="callout info small" role="note">{DISCLOSURE}</p>
        <Segmented label="View" value={tab} onChange={setTab}
                   options={[{ value: "interview", label: "Interview" }, { value: "history", label: "Earlier interviews" }]} />
        {tab === "history" ? <HistoryView agentId={person.agent_id} onOpen={(d) => {
            setResume(d);
            setSession({ session_id: d.session_id, agent_id: d.agent_id, config_version: d.config_version,
                         batch_seq: d.batch_seq, label: d.turns[0]?.label ?? DISCLOSURE });
            setTab("interview");
          }} />
          : !canInterview(agent) ? <p className="callout small">Interviews with this AI assistant are not available
              to you.</p>
          : session ? <Conversation key={session.session_id} session={session} history={resume}
                                    opening={resume ? undefined : opening}
                                    onClosed={() => { setSession(null); setResume(null); void start(); }} />
          : error ? <><ErrorState error={error} /><Button onClick={start}>Try again</Button></>
          : <Loading lines={3} />}
      </div>
    </Drawer>
  );
}

/** Overview: a quick way to meet the AI team without the admin directory. */
export function MeetYourAiTeam({ go }: { go: (r: string) => void }) {
  const team = useTeam();
  const [dept, setDept] = useState<string>(DEPTS[0][0]);
  const group = team.people.filter((p) => p.department === dept);
  return (
    <Section title="Meet your AI team" aria-label="Meet your AI team"
             subtitle="Non-human assistants grounded in employee records. They are not the employees and act for no one."
             actions={<button className="link small" onClick={() => go("agents")}>See everyone</button>}>
      {team.loading ? <Loading lines={2} /> : team.people.length === 0
        ? <p className="small muted">No AI assistants yet: each needs an owner-approved HR mapping.</p>
        : <>
            <Segmented label="Department" value={dept} onChange={setDept}
                       options={DEPTS.map(([d]) => ({ value: d, label: d }))} />
            <ul className="plain team-list">{group.map((p) => (
              <li key={p.agent_id}>
                <AgentAvatar id={p.name ?? p.agent_id} size={32} />
                <span style={{ minWidth: 0, flex: 1 }}><strong className="small">{p.name}</strong>
                  <span className="small muted" style={{ display: "block" }}>{p.title}</span></span>
                <button className="link small" onClick={() => { window.location.hash = `/agents?profile=${p.agent_id}`; }}>
                  Profile</button>
                <button className="link small" onClick={() => { window.location.hash = `/agents?interview=${p.agent_id}`; }}>
                  Interview</button>
              </li>))}</ul>
          </>}
    </Section>
  );
}
