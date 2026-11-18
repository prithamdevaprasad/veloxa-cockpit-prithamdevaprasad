import { useState } from "react";
import type { Answer } from "../api/types";
import { useEvidence } from "./EvidenceDrawer";
import { Badge, Button, Drawer, EvidenceList } from "./index";

const STATUS_TEXT: Record<string, string> = {
  answered: "Answered", qualified: "Qualified answer", conflict: "The sources disagree",
  abstained: "I don't know", refused: "Refused",
};

/** Renders an Answer exactly as the API returned it: status, grade, claims with citations, disagreements,
 * unknowns and refusals. Nothing is added, reworded or hidden. */
export function AnswerView({ answer }: { answer: Answer }) {
  const [focus, setFocus] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const show = (id: string | null) => { setFocus(id); setOpen(true); };
  const evidence = useEvidence();
  return (
    <article className="stack" aria-label="Answer">
      <div className="row">
        <Badge value={answer.status} label={STATUS_TEXT[answer.status] ?? answer.status} />
        <Badge value={answer.grade.value} label={`Grade ${answer.grade.value}`} title={answer.grade.label} />
        <span className="basis">{answer.read_basis.banner ?? `K=${answer.read_basis.batch_seq}`}</span>
      </div>
      <p className="small muted">{answer.interpretation}</p>

      {answer.refusal && (
        <div className="callout bad" role="note">
          <strong>{answer.refusal.policy}{answer.refusal.title ? ` · ${answer.refusal.title}` : ""}</strong>
          <p>{answer.refusal.reason}</p>
          {answer.refusal.alternative && <p className="small" style={{ marginTop: 6 }}>Instead: {answer.refusal.alternative}</p>}
        </div>
      )}

      {answer.claims.length > 0 && (
        <ul className="plain" aria-label="Claims">
          {answer.claims.map((c, i) => (
            <li key={i} className="claim">
              {c.text}
              {c.evidence.filter((id) => answer.evidence.some((e) => e.record_id === id)).map((id, j) => (
                <button key={id} className="cite" onClick={() => evidence.open(id, c.text)}
                        aria-label={`Show source ${j + 1}`}>
                  {j + 1}
                </button>
              ))}
            </li>
          ))}
        </ul>
      )}

      {answer.disagreements.length > 0 && (
        <div className="callout warn">
          <strong>Disagreements (no value is chosen)</strong>
          {answer.disagreements.map((d, i) => (
            <div key={i} className="small" style={{ marginTop: 6 }}>
              <div>{d.subject} · metric match {d.metric_match.replace(/_/g, " ")} · definition {d.definition}</div>
              <ul className="bullets">
                {d.claims.map((c) => <li key={c.record_id}>{c.value} ({c.source}{c.period ? `, ${c.period}` : ""})</li>)}
              </ul>
            </div>
          ))}
        </div>
      )}

      {(answer.unknowns.length > 0 || answer.qualifiers.length > 0) && (
        <div className="small">
          {answer.unknowns.length > 0 && (<><strong>Unknown</strong><ul className="bullets">{answer.unknowns.map((u, i) => <li key={i}>{u}</li>)}</ul></>)}
          {answer.qualifiers.length > 0 && (<><strong>Qualifiers</strong><ul className="bullets">{answer.qualifiers.map((u, i) => <li key={i}>{u}</li>)}</ul></>)}
        </div>
      )}

      {answer.evidence.length > 0 && (
        <div><button className="btn" onClick={() => show(null)}>Evidence ({answer.evidence.length})</button></div>
      )}
      {open && (
        <Drawer title="Evidence" onClose={() => setOpen(false)}>
          <EvidenceList items={answer.evidence} focus={focus} />
          <div className="stack" style={{ marginTop: 10 }}>
            {answer.evidence.map((e) => (
              <Button key={e.record_id} onClick={() => evidence.open(e.record_id)}>
                View source: {(e.source_path ?? e.record_id).split("/").pop()}
              </Button>
            ))}
          </div>
        </Drawer>
      )}
    </article>
  );
}
