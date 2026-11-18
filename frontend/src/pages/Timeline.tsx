import { useMemo, useState } from "react";
import { api } from "../api/client";
import { useApi } from "../api/useApi";
import { useEvidence } from "../ui/EvidenceDrawer";
import { DemoBadge, Guidance } from "../ui/kit";
import { ErrorState, Loading, PageHead, Section } from "../ui";

const CATEGORIES: { key: string; label: string; test: (d: string) => boolean }[] = [
  { key: "people", label: "People", test: (d) => /\bjoined\b|recorded exit/.test(d) },
  { key: "documents", label: "Formal documents", test: (d) => /formal update|issued/.test(d) },
  { key: "email", label: "E-mail", test: (d) => /\bemail\b/i.test(d) },
];

/** Dated events exactly as admitted records state them; nothing is inferred or derived. */
export function TimelinePage({ synthetic = false }: { synthetic?: boolean }) {
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [cat, setCat] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [t, reload] = useApi((s) => api.timeline(start || undefined, end || undefined, s), [start, end]);
  const evidence = useEvidence();
  const groups = useMemo(() => {
    if (t.state !== "ready") return [];
    const c = CATEGORIES.find((x) => x.key === cat);
    const rows = t.data.events.filter((e) => (!c || c.test(e.display))
                                     && (!text || e.display.toLowerCase().includes(text.toLowerCase())));
    const by = new Map<string, typeof rows>();
    for (const e of rows) {
      const month = /^(\d{4}-\d{2})/.exec(e.display)?.[1] ?? "Undated";
      by.set(month, [...(by.get(month) ?? []), e]);
    }
    return [...by.entries()];
  }, [t, cat, text]);
  return (
    <>
      <PageHead title="Timeline" intro="Dated events stated by admitted records: recorded events only, never inferred
        ones. Each event links to its source.">
        <div className="row">
          <label className="field">From<input className="input" type="date" value={start} onChange={(e) => setStart(e.target.value)} /></label>
          <label className="field">To<input className="input" type="date" value={end} onChange={(e) => setEnd(e.target.value)} /></label>
          <label className="field">Filter<input className="input" value={text} onChange={(e) => setText(e.target.value)}
                 placeholder="Search events" aria-label="Filter events" /></label>
        </div>
      </PageHead>
      <div className="row" role="group" aria-label="Event categories">
        <button className="chip" aria-pressed={cat === null} onClick={() => setCat(null)}>All</button>
        {CATEGORIES.map((c) => <button key={c.key} className="chip" aria-pressed={cat === c.key}
                                       onClick={() => setCat(c.key)}>{c.label}</button>)}
        <DemoBadge show={synthetic} />
      </div>
      <Section title="Events" subtitle={t.state === "ready" ? t.data.read_basis.banner : undefined}>
        {t.state === "loading" && <Loading />}
        {t.state === "error" && <ErrorState error={t.error} onRetry={reload} />}
        {t.state === "ready" && (groups.length === 0
          ? <Guidance title="No events">{t.data.gaps[0] ?? ("No dated record matches these filters. Dated events "
              + "come from admitted HR records, formal documents and e-mails you were party to.")}</Guidance>
          : <ol className="plain timeline" aria-label="Timeline events">
              {groups.map(([month, rows]) => (
                <li key={month}>
                  <h3 className="h3 month">{month}</h3>
                  <ol className="plain">{rows.map((ev, i) => (
                    <li key={i} className="tl-item">
                      <span className="tl-dot" aria-hidden="true" />
                      <span className="tl-text">{ev.display}</span>
                      <span className="small muted">recorded</span>
                      {ev.evidence.map((id, j) => (
                        <button key={id} className="cite" onClick={() => evidence.open(id, ev.display)}
                                aria-label={`Show source ${j + 1}`}>{j + 1}</button>))}
                    </li>))}</ol>
                </li>))}
            </ol>)}
      </Section>
    </>
  );
}
