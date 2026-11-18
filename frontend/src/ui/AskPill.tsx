// The global "Ask Veloxa" pill: always reachable (bottom centre, Ctrl/Cmd+K). Every question is asked inside a
// persisted thread (POST /v1/threads/{id}/ask), so conversations are saved, listed and reopened from the backend.
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { ApiError, getToken } from "../api/client";
import { product } from "../api/product";
import type { Thread, ThreadSummary } from "../api/product-types";
import { AnswerView } from "./AnswerView";
import { Icon } from "./kit";
import { Button, ErrorState, Loading } from "./index";

type AskCtx = { openThread: (threadId: string) => void; ask: (question: string) => void };
const Ctx = createContext<AskCtx>({ openThread: () => undefined, ask: () => undefined });
export const useAsk = () => useContext(Ctx);

const SUGGESTED = ["What is our headcount by department?", "What is the ledger spend by vendor?",
                   "What changed in the latest board update?", "Where do the sources disagree?"];

export function AskProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [seed, setSeed] = useState<string | null>(null);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setOpen((o) => !o); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  const ctx: AskCtx = {
    openThread: (id) => { setThreadId(id); setOpen(true); },
    ask: (q) => { setSeed(q); setThreadId(null); setOpen(true); },
  };
  return (
    <Ctx.Provider value={ctx}>
      {children}
      {getToken() && (open
        ? <AskPanel threadId={threadId} setThreadId={setThreadId} seed={seed} clearSeed={() => setSeed(null)}
                    onClose={() => setOpen(false)} />
        : <button className="ask-pill" onClick={() => setOpen(true)} aria-label="Ask Veloxa (Ctrl+K)">
            <Icon name="search" /> <span>Ask Veloxa anything about the company</span>
            <kbd>Ctrl K</kbd>
          </button>)}
    </Ctx.Provider>
  );
}

function AskPanel({ threadId, setThreadId, seed, clearSeed, onClose }:
  { threadId: string | null; setThreadId: (id: string | null) => void; seed: string | null; clearSeed: () => void;
    onClose: () => void }) {
  const [threads, setThreads] = useState<ThreadSummary[] | null>(null);
  const [thread, setThread] = useState<Thread | null>(null);
  const [q, setQ] = useState("");
  const [asOf, setAsOf] = useState("");
  const [busy, setBusy] = useState(false);
  const [showList, setShowList] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const end = useRef<HTMLDivElement>(null);

  const loadThreads = useCallback(async () => {
    try { setThreads((await product.threads()).threads); } catch { setThreads([]); }
  }, []);
  useEffect(() => { void loadThreads(); input.current?.focus(); }, [loadThreads]);
  useEffect(() => {
    if (!threadId) { setThread(null); return; }
    product.thread(threadId).then(setThread, (e) => setError(e));
  }, [threadId]);
  useEffect(() => { if (seed) { setQ(seed); clearSeed(); } }, [seed, clearSeed]);
  useEffect(() => { end.current?.scrollIntoView?.({ block: "end" }); }, [thread?.turns.length, busy]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function send(e?: React.FormEvent) {
    e?.preventDefault();
    const question = q.trim();
    if (!question || busy) return;
    setBusy(true); setError(null);
    try {
      let id = threadId;
      if (!id) {
        id = (await product.newThread(question.slice(0, 80))).thread_id;
        setThreadId(id);
      }
      await product.threadAsk(id, question, asOf || undefined);
      setThread(await product.thread(id));
      setQ("");
      void loadThreads();
    } catch (err) {
      setError(err instanceof ApiError ? err : new ApiError(0, "unknown", String(err)));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="ask-panel" role="dialog" aria-modal="false" aria-label="Ask Veloxa">
      <aside className={`ask-threads${showList ? " open" : ""}`} aria-label="Conversations">
        <Button variant="primary" onClick={() => { setThreadId(null); setQ(""); input.current?.focus(); }}>New conversation</Button>
        <h3 className="h3">Saved conversations</h3>
        {threads === null ? <Loading lines={2} /> : threads.length === 0
          ? <p className="small muted">Your conversations are saved here.</p>
          : <ul className="plain">{threads.map((t) => (
              <li key={t.thread_id}>
                <button className={`thread-link${t.thread_id === threadId ? " on" : ""}`}
                        onClick={() => { setThreadId(t.thread_id); setShowList(false); }}>
                  <span>{t.title}</span><span className="small muted">{t.answers} answer(s)</span>
                </button>
              </li>))}</ul>}
      </aside>
      <section className="ask-main" aria-label="Conversation">
        <header className="ask-head">
          <button className="chip saved-toggle" aria-expanded={showList} onClick={() => setShowList((v) => !v)}>
            Saved</button>
          <strong>{thread ? thread.title : "Ask Veloxa"}</strong>
          {thread?.context && (thread.context as { kind?: string }).kind === "agent_update" &&
            <span className="small muted">About an agent update · follow-ups keep this context</span>}
          <span className="spacer" />
          <label className="small">Known as at <input className="input slim" type="date" value={asOf}
                 onChange={(e) => setAsOf(e.target.value)} aria-label="Knowledge-point date" /></label>
          <button className="icon-btn" onClick={onClose} aria-label="Close Ask"><Icon name="close" /></button>
        </header>
        <div className="ask-turns">
          {!thread && !busy && (
            <div className="stack">
              <p className="muted">Answers cite the admitted records behind them, say when sources disagree, and say
                &ldquo;I don&rsquo;t know&rdquo; when the evidence does not support an answer.</p>
              <div className="row">{SUGGESTED.map((s) => <button key={s} className="chip" onClick={() => setQ(s)}>{s}</button>)}</div>
            </div>
          )}
          {thread?.turns.map((t) => (
            <div key={t.seq} className="turn">
              <div className="you">{t.question}</div>
              <AnswerView answer={t.answer} />
            </div>
          ))}
          {busy && <Loading lines={3} label="Answering" />}
          {error && <ErrorState error={error} />}
          <div ref={end} />
        </div>
        <form className="ask-input" onSubmit={send}>
          <textarea ref={input} value={q} rows={2} placeholder="Ask about the company..." aria-label="Question"
                    onChange={(e) => setQ(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void send(); } }} />
          <Button variant="primary" type="submit" disabled={!q.trim() || busy} aria-label="Send">
            <Icon name="send" /> Send
          </Button>
        </form>
      </section>
    </div>
  );
}
