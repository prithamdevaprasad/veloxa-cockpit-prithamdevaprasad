import { useState } from "react";
import { api } from "../api/client";
import { useApi } from "../api/useApi";
import { Drawer, EmptyState, ErrorState, EvidenceList, Loading, PageHead, Pager, Section, usePage } from "../ui";

/** Dated events exactly as admitted records state them (no inferred events). */
export function TimelinePage() {
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [t, reload] = useApi((s) => api.timeline(start || undefined, end || undefined, s), [start, end]);
  const [focus, setFocus] = useState<string[] | null>(null);
  const events = t.state === "ready" ? t.data.events : [];
  const page = usePage(events, 15);
  return (
    <>
      <PageHead title="Timeline" intro="Dated events from admitted records: joins, recorded exits, documents issued and
        e-mails you were party to. Nothing is inferred.">
        <div className="row">
          <label className="field">From<input className="input" type="date" value={start} onChange={(e) => setStart(e.target.value)} /></label>
          <label className="field">To<input className="input" type="date" value={end} onChange={(e) => setEnd(e.target.value)} /></label>
        </div>
      </PageHead>
      <Section title="Events" subtitle={t.state === "ready" ? t.data.read_basis.banner : undefined}>
        {t.state === "loading" && <Loading />}
        {t.state === "error" && <ErrorState error={t.error} onRetry={reload} />}
        {t.state === "ready" && (events.length === 0 ? (
          <EmptyState title="No events">{t.data.gaps[0] ?? "No dated record falls in this range."}</EmptyState>
        ) : (
          <>
            <ol className="plain stack" aria-label="Timeline events">
              {page.items.map((ev, i) => (
                <li key={i} className="row" style={{ justifyContent: "space-between", flexWrap: "nowrap" }}>
                  <span>{ev.display}</span>
                  <button className="cite" onClick={() => setFocus(ev.evidence)} aria-label="Show sources">sources</button>
                </li>
              ))}
            </ol>
            <Pager page={page} />
          </>
        ))}
      </Section>
      {focus && t.state === "ready" && (
        <Drawer title="Sources" onClose={() => setFocus(null)}>
          <EvidenceList items={t.data.evidence.filter((e) => focus.includes(e.record_id))} />
        </Drawer>
      )}
    </>
  );
}
