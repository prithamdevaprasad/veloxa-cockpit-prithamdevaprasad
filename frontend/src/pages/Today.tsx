import { api } from "../api/client";
import { useApi } from "../api/useApi";
import { Badge, Button, EmptyState, ErrorState, Loading, PageHead, Section } from "../ui";

/** The CEO home: the Today view (unranked while D-15 is pending), with the state of the evidence behind it. */
export function TodayPage({ onAsk }: { onAsk: (q: string) => void }) {
  const [today, reloadToday] = useApi((s) => api.today(s), []);
  const [releases] = useApi((s) => api.releases(s), []);
  const [conflicts] = useApi((s) => api.conflicts(undefined, s), []);
  const [agents] = useApi((s) => api.agents(s), []);
  const [history] = useApi((s) => api.riskHistory(s), []);

  return (
    <>
      <PageHead title="Today" intro="What the records say deserves attention now. Items are never ranked until the
        owner records the D-15 ranking decision; every item shows its evidence and what is still unknown.">
        {today.state === "ready" && <span className="basis">{today.data.read_basis.banner}</span>}
      </PageHead>

      <div className="stats" style={{ marginBottom: 16 }}>
        <div className="stat">
          <div className="k">Evidence admitted</div>
          <div className="v">{releases.state === "ready" ? releases.data.packs.filter((p) => p.admitted).length : "–"}
            <span className="s"> of {releases.state === "ready" ? releases.data.packs.length : "–"} packs</span></div>
        </div>
        <div className="stat">
          <div className="k">Open source disagreements</div>
          <div className="v">{conflicts.state === "ready" ? conflicts.data.conflicts.length : "–"}</div>
        </div>
        <div className="stat">
          <div className="k">Employee agents active</div>
          <div className="v">{agents.state === "ready" ? agents.data.agents.filter((a) => a.activation === "active").length : "–"}
            <span className="s"> of {agents.state === "ready" ? agents.data.agents.filter((a) => a.agent_id.startsWith("agent-")).length : "–"} slots</span></div>
        </div>
        <div className="stat">
          <div className="k">Ranking</div>
          <div className="v" style={{ fontSize: 15 }}>{today.state === "ready" ? (today.data.top_three === null ? "Unranked" : "Ranked") : "–"}</div>
          <div className="s">{today.state === "ready" ? `D-15: ${today.data.owner_decision_status.replace(/_/g, " ")}` : ""}</div>
        </div>
      </div>

      <Section title="Attention items" subtitle={today.state === "ready" ? today.data.today.explanation : undefined}
               actions={<Button variant="ghost" onClick={reloadToday}>Refresh</Button>}>
        {today.state === "loading" && <Loading />}
        {today.state === "error" && <ErrorState error={today.error} onRetry={reloadToday} />}
        {today.state === "ready" && (
          today.data.today.items.length === 0 ? (
            <EmptyState title={today.data.today.candidates?.length ? "More than three candidates" : "Nothing qualifies"}>
              {today.data.today.candidates?.length
                ? `Candidates: ${today.data.today.candidates.join(", ")}. Picking three needs the D-15 decision.`
                : "No item qualifies on the current evidence. Nothing is added to fill the list."}
            </EmptyState>
          ) : (
            <ol className="plain stack" aria-label="Attention items (unranked)">
              {today.data.today.items.map((it) => (
                <li key={it.business_object} className="evidence-item">
                  <div className="row" style={{ justifyContent: "space-between" }}>
                    <h3>{it.title}</h3>
                    <Badge value="unranked" />
                  </div>
                  <p className="small muted" style={{ marginTop: 4 }}>{it.why_shown}</p>
                  {it.unknowns.length > 0 && (
                    <><div className="small" style={{ marginTop: 8 }}><strong>Unknown</strong></div>
                      <ul className="bullets small">{it.unknowns.map((u, i) => <li key={i}>{u}</li>)}</ul></>
                  )}
                  <div className="small muted" style={{ marginTop: 8 }}>
                    Evidence: {it.evidence.map((e) => <code key={e} style={{ marginRight: 8, wordBreak: "break-all" }}>{e}</code>)}
                  </div>
                  <div style={{ marginTop: 8 }}>
                    <Button onClick={() => onAsk(`What do the records say about ${it.business_object}?`)}>Ask a follow-up</Button>
                  </div>
                </li>
              ))}
            </ol>
          )
        )}
      </Section>

      <Section title="Key changes" subtitle="How risks and adverse evidence appeared, changed, resolved or reopened
        from one admitted knowledge point to the next. Derived from the evidence; never ranked.">
        {history.state === "loading" && <Loading lines={2} />}
        {history.state === "error" && <ErrorState error={history.error} />}
        {history.state === "ready" && (history.data.items.length === 0 ? (
          <EmptyState title="No changes">Nothing has appeared or changed across knowledge points {history.data.knowledge_points.join(", ")}.</EmptyState>
        ) : (
          <div className="table-wrap"><table aria-label="Key changes">
            <thead><tr><th>Item</th><th>History</th></tr></thead>
            <tbody>{history.data.items.map((it) => (
              <tr key={it.item_id}><td>{it.title}<div className="small muted">{it.kind.replace("_", " ")}</div></td>
                <td>{it.events.map((e, i) => (
                  <div key={i} className="small"><Badge value={e.event === "resolved" ? "answered" : e.event === "changed" ? "qualified" : "open"}
                    label={e.event} /> in release {e.batch_seq}{e.fields ? ` (${e.fields.join(", ")})` : ""}</div>
                ))}</td></tr>
            ))}</tbody>
          </table></div>
        ))}
      </Section>

      <div className="grid-2">
        <Section title="Not assessable yet" subtitle="Risk criteria the current evidence cannot measure.">
          {today.state === "ready" ? (
            <ul className="bullets small">{today.data.today.not_shown_because_unmeasurable.map((u, i) => <li key={i}>{u}</li>)}</ul>
          ) : <Loading lines={2} />}
        </Section>
        <Section title="Evidence packs" subtitle="Release is not admission: a pack is used only once it is authorised and sealed.">
          {releases.state === "ready" ? (
            <table><thead><tr><th>Pack</th><th>Status</th><th>Records</th></tr></thead>
              <tbody>{releases.data.packs.map((p) => (
                <tr key={p.pack}><td>{p.pack}</td><td><Badge value={p.status} /></td><td>{p.record_count ?? "–"}</td></tr>
              ))}</tbody></table>
          ) : releases.state === "error" ? <ErrorState error={releases.error} /> : <Loading lines={2} />}
        </Section>
      </div>
    </>
  );
}
