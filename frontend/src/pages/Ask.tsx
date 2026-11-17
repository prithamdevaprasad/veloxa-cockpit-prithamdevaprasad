import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api/client";
import type { Answer } from "../api/types";
import { AnswerView } from "../ui/AnswerView";
import { Button, ErrorState, Loading, PageHead, Section } from "../ui";

interface Turn { question: string; asKnownAt?: string; answer?: Answer; error?: ApiError }

export function AskPage({ initial, onConsumed }: { initial?: string; onConsumed: () => void }) {
  const [question, setQuestion] = useState("");
  const [asKnownAt, setAsKnownAt] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const ctrl = useRef<AbortController | null>(null);

  useEffect(() => {
    if (initial) { setQuestion(initial); onConsumed(); }
  }, [initial, onConsumed]);
  useEffect(() => () => ctrl.current?.abort(), []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const q = question.trim();
    if (!q || busy) return;
    ctrl.current?.abort();
    ctrl.current = new AbortController();
    setBusy(true);
    const turn: Turn = { question: q, asKnownAt: asKnownAt || undefined };
    setTurns((t) => [turn, ...t]);
    try {
      turn.answer = await api.ask(q, asKnownAt || undefined, ctrl.current.signal);
    } catch (err) {
      if ((err as Error).name === "AbortError") return;
      turn.error = err instanceof ApiError ? err : new ApiError(0, "unknown", String(err));
    } finally {
      setTurns((t) => t.map((x) => (x === turn ? { ...turn } : x)));
      setBusy(false);
    }
    setQuestion("");
  }

  return (
    <>
      <PageHead title="Ask Veloxa" intro="Answers cite the records behind them, carry an evidence grade, and say
        “I don't know” or “the sources disagree” when that is the truth. Some requests are refused by policy." />
      <Section aria-label="Ask a question">
        <form onSubmit={submit} className="stack">
          <label className="field">Question
            <textarea className="input" rows={2} value={question} maxLength={1000}
                      onChange={(e) => setQuestion(e.target.value)} placeholder="e.g. What did the board updates report?" />
          </label>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <label className="field" style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
              Known as at
              <input className="input" type="date" value={asKnownAt} onChange={(e) => setAsKnownAt(e.target.value)}
                     aria-label="Answer with knowledge as at date (optional)" />
            </label>
            <Button variant="primary" type="submit" disabled={busy || !question.trim()}>{busy ? "Asking…" : "Ask"}</Button>
          </div>
        </form>
      </Section>
      {turns.map((t, i) => (
        <Section key={turns.length - i} title={t.question}
                 subtitle={t.asKnownAt ? `Known as at ${t.asKnownAt}` : undefined}>
          {t.answer ? <AnswerView answer={t.answer} /> : t.error ? <ErrorState error={t.error} /> : <Loading />}
        </Section>
      ))}
    </>
  );
}
