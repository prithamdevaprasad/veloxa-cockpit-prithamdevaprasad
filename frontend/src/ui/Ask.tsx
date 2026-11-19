// Ask Veloxa: ONE conversation panel with two states. "overlay" floats over the current page; "full" is the
// dedicated workspace at #/ask. The panel element stays mounted across the switch, so the selected thread, the
// unsent draft and the scroll position survive; only its geometry animates. Every question is asked inside a
// persisted thread (POST /v1/threads/{id}/ask), so conversations are saved, listed and reopened from the backend.
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ApiError, getToken } from "../api/client";
import { product } from "../api/product";
import type { Thread, ThreadSummary } from "../api/product-types";
import { AnswerView } from "./AnswerView";
import { JiraTaskCard, type TaskDraft } from "./JiraTask";
import { Icon } from "./kit";
import { Button, ErrorState, Loading } from "./index";

export type AskContext = { kind: string; subject: string };
type AskCtx = { openThread: (threadId: string, draft?: string) => void;
                ask: (question: string, context?: AskContext, send?: boolean) => void; open: () => void;
                task: (draft?: TaskDraft) => void; full: boolean };
const Ctx = createContext<AskCtx>({ openThread: () => undefined, ask: () => undefined, open: () => undefined,
                                   task: () => undefined, full: false });
export const useAsk = () => useContext(Ctx);

const isAskRoute = () => /^#\/?ask$/.test(window.location.hash);
const EXAMPLES = ["What is our headcount by department?", "What is the ARR?", "What changed in the latest board update?",
                  "Where do the sources disagree?"];

export function AskProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const [full, setFull] = useState(isAskRoute());
  const [threadId, setThreadId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [focusTick, setFocusTick] = useState(0);
  const [askContext, setAskContext] = useState<AskContext | null>(null);
  const [sendTick, setSendTick] = useState(0);
  const [taskDraft, setTaskDraft] = useState<TaskDraft | null>(null);
  const expandedHere = useRef(false);        // true when this session pushed #/ask, so Back returns to the page
  const cameFrom = useRef<string | null>(null);  // the page the overlay expanded from

  useEffect(() => {
    const on = () => {
      const r = isAskRoute();
      setFull((was) => {
        // Leaving the workspace: back to the page it expanded from keeps the overlay; any other page closes Ask.
        if (was && !r && window.location.hash !== cameFrom.current) setOpen(false);
        return r;
      });
      if (r) setOpen(true);
    };
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        if (open && !full) setOpen(false);
        else { setOpen(true); setFocusTick((t) => t + 1); }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, full]);

  const leaveFull = useCallback(() => {
    if (expandedHere.current) { expandedHere.current = false; window.history.back(); }
    else { cameFrom.current = "#/overview"; window.location.hash = "/overview"; }
  }, []);
  const expand = useCallback(() => {
    expandedHere.current = true; cameFrom.current = window.location.hash || "#/overview"; window.location.hash = "/ask";
  }, []);
  const close = useCallback(() => { setOpen(false); if (isAskRoute()) leaveFull(); }, [leaveFull]);

  const ctx = useMemo<AskCtx>(() => ({
    openThread: (id, q) => { setThreadId(id); if (q) setDraft(q); setOpen(true); setFocusTick((t) => t + 1); },
    ask: (q, context, send) => {
      if (q) { setDraft(q); setThreadId(null); setAskContext(context ?? null); }
      setOpen(true); setFocusTick((t) => t + 1);
      if (q && send) setSendTick((t) => t + 1);
    },
    task: (d) => { setTaskDraft(d ?? {}); setOpen(true); },
    open: () => { setOpen(true); setFocusTick((t) => t + 1); },
    full,
  }), [full]);

  const visible = Boolean(getToken()) && (open || full);
  return (
    <Ctx.Provider value={ctx}>
      {children}
      {visible && !full && <div className="ask-backdrop" onClick={close} />}
      {visible && <AskPanel full={full} threadId={threadId} setThreadId={setThreadId} draft={draft} setDraft={setDraft}
                            focusTick={focusTick} askContext={askContext} sendTick={sendTick}
                            taskDraft={taskDraft} setTaskDraft={setTaskDraft} onClose={close} onExpand={expand} onCollapse={leaveFull} />}
    </Ctx.Provider>
  );
}

function AskPanel({ full, threadId, setThreadId, draft, setDraft, focusTick, askContext, sendTick, taskDraft,
                    setTaskDraft, onClose, onExpand, onCollapse }: {
  full: boolean; threadId: string | null; setThreadId: (id: string | null) => void; draft: string;
  setDraft: (s: string) => void; focusTick: number; askContext: AskContext | null; sendTick: number;
  taskDraft: TaskDraft | null; setTaskDraft: (d: TaskDraft | null) => void;
  onClose: () => void; onExpand: () => void; onCollapse: () => void }) {
  const [threads, setThreads] = useState<ThreadSummary[] | null>(null);
  const [thread, setThread] = useState<Thread | null>(null);
  const [filter, setFilter] = useState("");
  const [asOf, setAsOf] = useState("");
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<string | null>(null);
  const [showList, setShowList] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [threadsErr, setThreadsErr] = useState<ApiError | null>(null);   // never shown as an empty list
  const input = useRef<HTMLTextAreaElement>(null);
  const end = useRef<HTMLDivElement>(null);
  const created = useRef<string | null>(null);   // a thread this panel just created: its answer arrives via send()

  const loadThreads = useCallback(async () => {
    try { setThreads((await product.threads()).threads); setThreadsErr(null); }
    catch (e) { setThreadsErr(e instanceof ApiError ? e : new ApiError(0, "unknown", String(e))); setThreads(null); }
  }, []);
  useEffect(() => { void loadThreads(); }, [loadThreads]);
  useEffect(() => { input.current?.focus(); }, [focusTick, threadId]);
  useEffect(() => {
    if (!threadId) { setThread(null); return; }
    if (created.current === threadId) return;
    let live = true;
    product.thread(threadId).then((t) => live && setThread(t), (e) => live && setError(e));
    return () => { live = false; };
  }, [threadId]);
  useEffect(() => { end.current?.scrollIntoView?.({ block: "end" }); }, [thread?.turns.length, busy]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || full || document.querySelector(".drawer")) return;   // a drawer closes first
      onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, full]);

  async function send(e?: React.FormEvent) {
    e?.preventDefault();
    const question = draft.trim();
    if (!question || busy) return;
    setBusy(true); setError(null); setPending(question); setDraft("");
    try {
      let id = threadId;
      if (!id) {
        id = (await product.newThread((askContext?.subject ?? question).slice(0, 80),
                                      askContext ? { ...askContext } : {})).thread_id;
        created.current = id;
        setThreadId(id);
      }
      await product.threadAsk(id, question, asOf || undefined);
      setThread(await product.thread(id));
      void loadThreads();
    } catch (err) {
      setDraft(question);                                   // nothing is lost when a request fails
      setError(err instanceof ApiError ? err : new ApiError(0, "unknown", String(err)));
    } finally {
      setBusy(false); setPending(null);
    }
  }

  useEffect(() => { if (sendTick) void send(); }, [sendTick]); // eslint-disable-line react-hooks/exhaustive-deps

  const shown = (threads ?? []).filter((t) => t.title.toLowerCase().includes(filter.trim().toLowerCase()));
  const context = thread?.context as { kind?: string } | undefined;
  return (
    <div className={`ask-panel${full ? " full" : ""}`} role="dialog" aria-modal={full ? undefined : "false"}
         aria-label="Ask Veloxa">
      <aside className={`ask-threads${showList ? " open" : ""}`} aria-label="Conversations">
        <Button variant="primary" onClick={() => { setThreadId(null); setShowList(false); input.current?.focus(); }}>
          <Icon name="plus" size={16} /> New conversation</Button>
        <input className="input" type="search" placeholder="Search conversations" aria-label="Search conversations"
               value={filter} onChange={(e) => setFilter(e.target.value)} />
        <h3>Saved conversations</h3>
        {threadsErr ? <p className="small muted" role="alert" style={{ padding: "0 4px" }}>Saved conversations could not be loaded: {threadsErr.message}</p>
          : threads === null ? <Loading lines={3} /> : shown.length === 0
          ? <p className="small muted" style={{ padding: "0 4px" }}>
              {threads.length === 0 ? "Questions you ask are saved here." : "No conversation matches."}</p>
          : <ul className="plain">{shown.map((t) => (
              <li key={t.thread_id}>
                <button className={`thread-link${t.thread_id === threadId ? " on" : ""}`}
                        aria-current={t.thread_id === threadId ? "true" : undefined}
                        onClick={() => { setThreadId(t.thread_id); setShowList(false); }}>
                  <span>{t.title}</span>
                  <span className="small muted">{t.answers} answer{t.answers === 1 ? "" : "s"}
                    {t.last_at ? `, ${relative(t.last_at)}` : ""}</span>
                </button>
              </li>))}</ul>}
      </aside>
      <section className="ask-main" aria-label="Conversation">
        <header className="ask-head">
          <button className="icon-btn saved-toggle" aria-expanded={showList} onClick={() => setShowList((v) => !v)}
                  aria-label="Saved">
            <Icon name="menu" /></button>
          <button className="icon-btn saved-toggle" aria-label="Start a new conversation" title="New conversation"
                  onClick={() => { setThreadId(null); input.current?.focus(); }}><Icon name="plus" /></button>
          <strong>{thread ? thread.title : "Ask Veloxa"}</strong>
          {context?.kind === "agent_update" &&
            <span className="small muted">About an agent update. Follow-ups keep this context.</span>}
          <span className="spacer" />
          <Button onClick={() => setTaskDraft(taskDraft ? null : {})} aria-expanded={Boolean(taskDraft)}>
            <Icon name="plus" size={14} /> Jira task</Button>
          <label className="small">Known as at <input className="input slim" type="date" value={asOf}
                 onChange={(e) => setAsOf(e.target.value)} aria-label="Knowledge-point date" /></label>
          {full
            ? <button className="icon-btn size-btn" onClick={onCollapse} aria-label="Return to window" title="Return to window">
                <Icon name="collapse" /></button>
            : <button className="icon-btn size-btn" onClick={onExpand} aria-label="Full screen" title="Full screen">
                <Icon name="expand" /></button>}
          <button className="icon-btn" onClick={onClose} aria-label="Close Ask" title="Close"><Icon name="close" /></button>
        </header>
        <div className="ask-turns">
          <div className="ask-turns-inner">
            {!thread && !pending && !threadId && (
              <div className="ask-intro">
                <h2>Ask about the company</h2>
                <p>Answers are built from the records Veloxa has admitted and you are allowed to see. Each one cites
                  its sources, says when they disagree, and says so plainly when the evidence is not enough.</p>
                <div className="ask-examples" role="list" aria-label="Examples">
                  {EXAMPLES.map((s) => (
                    <button key={s} role="listitem" onClick={() => { setDraft(s); input.current?.focus(); }}>{s}</button>))}
                </div>
              </div>
            )}
            {threadId && !thread && !error && !pending && <Loading lines={4} label="Opening the conversation" />}
            {thread?.turns.map((t) => (
              <div key={t.seq} className="turn">
                <div className="you">{t.question}</div>
                <AnswerView answer={t.answer} />
              </div>
            ))}
            {pending && <div className="turn"><div className="you">{pending}</div><Loading lines={3} label="Answering" /></div>}
            {error && <ErrorState error={error} />}
            {taskDraft && <JiraTaskCard key={JSON.stringify(taskDraft)} draft={taskDraft} onClose={() => setTaskDraft(null)} />}
            <div ref={end} />
          </div>
        </div>
        <form className="ask-input" onSubmit={send}>
          <div className="composer">
            <textarea ref={input} value={draft} rows={1} placeholder="Ask about the company" aria-label="Question"
                      onChange={(e) => setDraft(e.target.value)}
                      onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void send(); } }} />
            <Button variant="primary" type="submit" disabled={!draft.trim() || busy} aria-label="Send">
              <Icon name="send" size={16} /> Send
            </Button>
          </div>
          <p className="composer-hint">Enter sends. Shift+Enter adds a line. Answers use only verified records.</p>
        </form>
      </section>
    </div>
  );
}

function relative(iso: string): string {
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  if (mins < 60 * 24) return `${Math.round(mins / 60)} h ago`;
  return new Date(iso).toLocaleDateString();
}
