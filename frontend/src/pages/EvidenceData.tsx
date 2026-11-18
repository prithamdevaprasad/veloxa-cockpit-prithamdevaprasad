import { useMemo, useState } from "react";
import { ApiError } from "../api/client";
import { fileToBase64, product } from "../api/product";
import type { Submission } from "../api/product-types";
import { useApi } from "../api/useApi";
import { Guidance, Tabs } from "../ui/kit";
import { Badge, Button, Drawer, ErrorState, Loading, PageHead, Section } from "../ui";

const STEPS = ["uploaded", "validating", "awaiting_review", "awaiting_authorisation", "authorised", "admitting",
               "admitted", "sealed", "projected"];
const LABEL: Record<string, string> = {
  uploaded: "Uploaded", validating: "Validating", validation_failed: "Validation failed",
  awaiting_review: "Awaiting review", rejected: "Rejected", awaiting_authorisation: "Awaiting authorisation",
  declined: "Declined", authorised: "Authorised", admitting: "Admitting", admission_failed: "Admission failed",
  admitted: "Admitted", sealed: "Sealed", projected: "Searchable",
};

export function EvidenceDataPage() {
  const [list, reload] = useApi((s) => product.submissions(undefined, s), []);
  const [tab, setTab] = useState<"mine" | "review" | "authorise">("mine");
  const [open, setOpen] = useState<string | null>(null);
  const data = list.state === "ready" ? list.data : null;
  const review = data ? data.submissions.filter((s) => s.state === "awaiting_review"
                                                && data.can_review.includes(s.department_id)) : [];
  const authorise = data && data.can_authorise ? data.submissions.filter((s) => s.state === "awaiting_authorisation") : [];
  return (
    <>
      <PageHead title="Evidence & Data" intro="Contribute departmental data. Uploading is not admission: a file becomes
        searchable only after validation, a department review and a separate admission authorisation." />
      <UploadForm onDone={reload} />
      <Section title="Submissions">
        <Tabs label="Submission queues" value={tab} onChange={setTab} tabs={[
          { value: "mine", label: "All visible", count: data?.submissions.length },
          { value: "review", label: "Review queue", count: review.length },
          { value: "authorise", label: "Authorisation queue", count: authorise.length }]} />
        {list.state === "loading" && <Loading />}
        {list.state === "error" && <ErrorState error={list.error} onRetry={reload} />}
        {data && (() => {
          const rows = tab === "mine" ? data.submissions : tab === "review" ? review : authorise;
          if (rows.length === 0) {
            return <Guidance title={tab === "mine" ? "No submissions yet" : "Nothing waiting for you"}>
              {tab === "review" ? "Submissions you may review (as a department reviewer) appear here once validated."
                : tab === "authorise" ? (data.can_authorise ? "Reviewed submissions appear here for admission authorisation."
                  : "Authorising admission needs the admission-authoriser relation.")
                : "Upload a file above to start."}</Guidance>;
          }
          return <SubmissionTable rows={rows} onOpen={setOpen} />;
        })()}
      </Section>
      {open && <SubmissionDrawer id={open} onClose={() => setOpen(null)} onChanged={reload} />}
    </>
  );
}

function SubmissionTable({ rows, onOpen }: { rows: Submission[]; onOpen: (id: string) => void }) {
  return (
    <div className="table-wrap"><table>
      <thead><tr><th>Title</th><th>Department</th><th>Source type</th><th>Submitted</th><th>Status</th><th /></tr></thead>
      <tbody>{rows.map((s) => (
        <tr key={s.submission_id} className="clickable" tabIndex={0} onClick={() => onOpen(s.submission_id)}
            onKeyDown={(e) => e.key === "Enter" && onOpen(s.submission_id)}>
          <td>{s.title}</td><td>{s.department_id}</td><td>{s.source_type.replace(/_/g, " ")}</td>
          <td className="small">{new Date(s.submitted_at).toLocaleString()} · {s.submitted_by.replace("person:", "")}</td>
          <td><Badge value={s.state === "projected" ? "active" : /failed|rejected|declined/.test(s.state) ? "denied"
                       : "pending_admission"} label={LABEL[s.state] ?? s.state} /></td>
          <td><Button variant="ghost">Open</Button></td>
        </tr>))}</tbody>
    </table></div>
  );
}

function UploadForm({ onDone }: { onDone: () => void }) {
  const [types] = useApi((s) => product.sourceTypes(s), []);
  const [st, setSt] = useState("");
  const [title, setTitle] = useState("");
  const [period, setPeriod] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);
  const chosen = useMemo(() => types.state === "ready" ? types.data.source_types.find((t) => t.source_type === st) : undefined,
                         [types, st]);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!chosen || files.length === 0) return;
    setBusy(true); setErr(null); setMsg(null);
    try {
      const payload = await Promise.all(files.map(async (f) => ({ file_name: f.name, content_base64: await fileToBase64(f) })));
      const r = await product.submit({ department_id: chosen.department_id, source_type: chosen.source_type,
                                       title: title || files[0].name, data_period: period || undefined, files: payload });
      setMsg(`Submitted (${r.submission_id.slice(0, 8)}). Validation runs next; nothing is searchable until it is reviewed and authorised.`);
      setFiles([]); setTitle(""); setPeriod("");
      onDone();
    } catch (x) {
      setErr(x instanceof ApiError ? x : new ApiError(0, "unknown", String(x)));
    } finally { setBusy(false); }
  }
  return (
    <Section title="Upload a file" subtitle="Choose an approved source type; its department, classification and checks come from the approved catalogue.">
      {types.state === "loading" && <Loading lines={2} />}
      {types.state === "error" && <ErrorState error={types.error} />}
      {types.state === "ready" && (types.data.source_types.length === 0
        ? <Guidance title="No approved source types">An admission authoriser approves each source type (its
            department and data classification) before anyone can contribute it.</Guidance>
        : <form className="stack" onSubmit={submit}>
            <div className="row">
              <label className="field">Source type
                <select className="input" value={st} onChange={(e) => setSt(e.target.value)} required aria-label="Source type">
                  <option value="" disabled>Select</option>
                  {types.data.source_types.map((t) => <option key={t.source_type} value={t.source_type}>
                    {t.title} ({t.department_id})</option>)}
                </select></label>
              <label className="field">Title<input className="input" value={title} onChange={(e) => setTitle(e.target.value)}
                       placeholder="e.g. Q3 vendor review" /></label>
              <label className="field">Data period<input className="input" value={period} onChange={(e) => setPeriod(e.target.value)}
                       placeholder="e.g. 2026-Q3" /></label>
            </div>
            {chosen && <p className="small muted">Department {chosen.department_id} · classification {chosen.data_class} ·
              accepted files {chosen.file_glob} · {chosen.records === "rows" ? "one record per row" : "one record per document"}
              {chosen.key_columns.length ? ` · business key ${chosen.key_columns.join(", ")}` : ""}</p>}
            <label className="field">Files<input className="input" type="file" multiple aria-label="Files"
                   onChange={(e) => setFiles(Array.from(e.target.files ?? []))} /></label>
            <div className="row"><Button variant="primary" type="submit" disabled={busy || !chosen || files.length === 0}>
              {busy ? "Uploading…" : "Submit for review"}</Button></div>
            {msg && <p className="callout ok small" role="status">{msg}</p>}
            {err && <ErrorState error={err} />}
          </form>)}
    </Section>
  );
}

function SubmissionDrawer({ id, onClose, onChanged }: { id: string; onClose: () => void; onChanged: () => void }) {
  const [d, reload] = useApi((s) => product.submission(id, s), [id]);
  const [reason, setReason] = useState("");
  const [err, setErr] = useState<ApiError | null>(null);
  async function decide(decision: "accept" | "reject" | "authorise" | "decline") {
    setErr(null);
    try { await product.decide(id, decision, reason || decision); reload(); onChanged(); }
    catch (x) { setErr(x instanceof ApiError ? x : new ApiError(0, "unknown", String(x))); }
  }
  return (
    <Drawer title={d.state === "ready" ? d.data.title : "Submission"} onClose={onClose}>
      {d.state === "loading" && <Loading />}
      {d.state === "error" && <ErrorState error={d.error} />}
      {d.state === "ready" && (() => {
        const s = d.data;
        const rep = s.validation?.report;
        const reached = new Set(s.events.map((e) => e.state));
        return (
          <div className="stack">
            <ol className="steps" aria-label="Progress">{STEPS.map((x) => (
              <li key={x} className={reached.has(x) ? "done" : ""}>{LABEL[x]}</li>))}</ol>
            {/failed|rejected|declined/.test(s.state) && <p className="callout bad small">{LABEL[s.state]}:{" "}
              {s.events[s.events.length - 1]?.reason ?? ""}</p>}
            <dl className="meta small"><dt>Department</dt><dd>{s.department_id}</dd><dt>Source type</dt>
              <dd>{s.source_type}</dd><dt>Submitted by</dt><dd>{s.submitted_by}</dd>
              <dt>Files</dt><dd>{s.files.map((f) => `${f.file_name} (${Math.ceil(f.size_bytes / 1024)} KB)`).join(", ")}</dd></dl>
            {rep && (
              <section aria-label="Validation report" className="stack">
                <h3 className="h3">Validation {s.validation?.ok ? "passed" : "failed"} · {rep.records ?? 0} record(s)</h3>
                {rep.errors.map((e, i) => <p key={i} className="callout bad small">{e.reason}: {e.detail}</p>)}
                {rep.warnings.map((w, i) => <p key={i} className="callout warn small">{w}</p>)}
                {Object.entries(rep.by_file ?? {}).map(([f, info]) => (
                  <details key={f} open><summary className="small">{f}: {info.records} record(s) · {info.data_class}
                    {info.sheets.length ? ` · sheets ${info.sheets.join(", ")}` : ""}</summary>
                    <div className="table-wrap"><table className="small"><tbody>{info.preview.map((p) => (
                      <tr key={p.locator}><td className="mono">{p.locator}</td>
                        <td>{Object.entries(p.fields).slice(0, 6).map(([k, v]) => `${k}: ${v ?? "—"}`).join(" · ")}</td></tr>))}
                    </tbody></table></div></details>))}
              </section>)}
            {(s.state === "awaiting_review" || s.state === "awaiting_authorisation") && (
              <div className="stack">
                <label className="field">Reason<input className="input" value={reason} onChange={(e) => setReason(e.target.value)}
                       placeholder="Recorded with your decision" /></label>
                <div className="row">{s.state === "awaiting_review"
                  ? <><Button variant="primary" onClick={() => decide("accept")}>Accept for authorisation</Button>
                      <Button onClick={() => decide("reject")}>Reject</Button></>
                  : <><Button variant="primary" onClick={() => decide("authorise")}>Authorise admission</Button>
                      <Button onClick={() => decide("decline")}>Decline</Button></>}</div>
                <p className="small muted">The server checks your relation and separation of duties: a submitter cannot
                  review their own submission, and the authoriser must differ from the submitter and the reviewer.</p>
              </div>)}
            {err && <ErrorState error={err} />}
            <details className="small"><summary>History</summary><ul className="plain">{s.events.map((e, i) => (
              <li key={i}>{new Date(e.at).toLocaleString()} · {LABEL[e.state] ?? e.state} · {e.actor}{e.reason ? ` · ${e.reason}` : ""}</li>))}</ul></details>
          </div>
        );
      })()}
    </Drawer>
  );
}
