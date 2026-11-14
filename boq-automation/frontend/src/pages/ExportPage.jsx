import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { CheckCircle2, Download, FileOutput, FileSpreadsheet, XCircle } from "lucide-react";
import { api, errorText } from "../services/api";
import { useWork } from "../context/WorkContext";
import { useFeedback } from "../components/feedback";
import { dateText, Disclosure, Empty, ErrorNote, Guide, PageHeader, Spinner, Status } from "../components/ui";
import { inScope, live } from "../utils/status";

function Downloads({ rec }) {
  return (
    <div className="flex flex-wrap gap-2">
      {rec.status === "verified" && <a className="btn btn-primary" href={api.downloadUrl(rec.id, "boq")}><Download size={15} /> Updated BOQ</a>}
      <a className="btn btn-secondary" href={api.downloadUrl(rec.id, "mapping")}><Download size={15} /> Mapping report</a>
      <a className="btn btn-secondary" href={api.downloadUrl(rec.id, "unmatched")}><Download size={15} /> Unmatched report</a>
    </div>
  );
}

function Latest({ rec }) {
  const s = rec.summary;
  const c = rec.verification.checks ?? {};
  const ok = rec.status === "verified";
  return (
    <div className="card overflow-hidden">
      <div className={`flex items-start gap-4 px-6 py-5 ${ok ? "bg-gradient-to-br from-emerald-50/70 to-white" : "bg-rose-50/60"}`}>
        {ok ? <CheckCircle2 className="mt-0.5 shrink-0 text-emerald-600" size={22} /> : <XCircle className="mt-0.5 shrink-0 text-rose-600" size={22} />}
        <div className="min-w-0 flex-1">
          <div className="text-[15px] font-semibold text-slate-900">{ok ? "Export verified and ready to download" : "Export blocked by verification"}</div>
          <div className="mt-0.5 text-sm text-slate-500">Export #{rec.id} · {dateText(rec.created_at)}</div>
          {ok ? (
            <p className="mt-2 text-sm text-slate-600">
              {String(c.imported_rows_verified ?? 0)} components written and checked against the Item Master. All {String(c.original_rows_verified ?? 0)} original
              rows unchanged{c.vba_identical ? ", macros intact" : ""}, {String(c.data_validations ?? 0)} dropdown lists kept.
            </p>
          ) : (
            <ul className="mt-2 list-disc space-y-0.5 pl-5 text-sm text-rose-800">{(rec.verification.errors ?? []).slice(0, 8).map((e, i) => <li key={i}>{e}</li>)}</ul>
          )}
        </div>
      </div>
      <div className="grid grid-cols-2 border-t border-slate-100 text-sm sm:grid-cols-4 sm:divide-x sm:divide-slate-100">
        {[["Lines processed", s.descriptions_processed], ["Components written", s.approved_mappings], ["Not yet approved", s.pending_mappings],
          ["Skipped: no unit/qty", s.descriptions_skipped_unit_qty]].map(([k, v]) => (
          <div key={k} className="px-6 py-3"><div className="text-slate-500">{k}</div><div className="mt-0.5 text-lg font-semibold tabular-nums text-slate-900">{v ?? 0}</div></div>
        ))}
      </div>
      <div className="border-t border-slate-100 px-6 py-4"><Downloads rec={rec} /></div>
    </div>
  );
}

export default function ExportPage() {
  const { jobId, setJobId, refreshProgress } = useWork();
  const { notify, confirm } = useFeedback();
  const [jobs, setJobs] = useState([]);
  const [records, setRecords] = useState(null);
  const [descs, setDescs] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const load = () => api.exports().then(setRecords).catch((e) => setError(errorText(e)));
  useEffect(() => { load(); api.jobs().then(setJobs).catch((e) => setError(errorText(e))); }, []);
  useEffect(() => { if (jobId) api.descriptions(jobId).then(setDescs).catch(() => setDescs([])); }, [jobId]);

  const mappings = descs.filter(inScope).flatMap(live);
  const approved = mappings.filter((m) => m.status === "approved").length;
  const pending = mappings.length - approved;

  async function run() {
    if (!jobId) return;
    if (pending > 0 && !(await confirm({
      title: "Export with unapproved components?", action: "Export approved only",
      text: <>{pending} component{pending > 1 ? "s are" : " is"} not approved and will be left out. Only the {approved} approved component{approved === 1 ? "" : "s"} will be written to the BOQ.</>,
    }))) return;
    setBusy(true); setError(null);
    try {
      const rec = await api.exportJob(jobId);
      await load(); refreshProgress();
      if (rec.status === "verified") notify(`Export #${rec.id} is verified and ready to download.`);
      else notify(`Export #${rec.id} failed verification. See the reasons on this page.`, "error");
    } catch (e) {
      setError(`Export for run #${jobId} failed: ${errorText(e)}`);
    } finally { setBusy(false); }
  }

  const completed = jobs.filter((j) => j.status === "completed");
  const mine = (records ?? []).filter((r) => r.job_id === jobId);
  const others = (records ?? []).filter((r) => r.job_id !== jobId);

  return (
    <>
      <PageHeader eyebrow="Step 4" title="Export BOQ" subtitle="Each export is verified before it can be downloaded. Your original workbook is never changed."
        action={<>
          <select className="input !w-auto text-[13px]" aria-label="Processing run" value={jobId ?? ""} onChange={(e) => setJobId(Number(e.target.value))}>
            <option value="" disabled>Select a run</option>
            {completed.map((j) => <option key={j.id} value={j.id}>Run #{j.id}</option>)}
          </select>
          <button className="btn btn-primary" disabled={!jobId || busy || !approved} onClick={run}><FileOutput size={15} /> {busy ? "Exporting…" : "Export approved"}</button>
        </>} />
      <ErrorNote text={error} />
      {jobId && <Guide id="export">Exporting inserts every approved component directly below its BOQ line, highlighted yellow, with its description and
        Item No. from the Item Master and its reviewed Cost Sheet Type. Everything else in the workbook stays as it was.</Guide>}
      {jobId && !approved && descs.length > 0 && (
        <div className="mb-5 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          Run #{jobId} has no approved components yet. <Link className="font-medium underline" to="/review">Review and approve</Link> them first.
        </div>
      )}

      {!records ? <Spinner /> : !jobId ? <Empty icon={<FileSpreadsheet size={20} />} title="Select a run to export" /> : mine.length === 0 ? (
        <Empty icon={<FileSpreadsheet size={20} />} title={`No export yet for run #${jobId}`}
          text={approved ? `${approved} approved component${approved === 1 ? " is" : "s are"} ready. Click Export approved to create the BOQ.` : undefined} />
      ) : (
        <>
          <Latest rec={mine[0]} />
          {mine.length > 1 && (
            <div className="card mt-5 p-5">
              <Disclosure title={`Earlier exports of run #${jobId} (${mine.length - 1})`}>
                <ul className="divide-y divide-slate-100">
                  {mine.slice(1).map((r) => (
                    <li key={r.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
                      <div className="flex items-center gap-3 text-sm"><Status status={r.status} /><span className="text-slate-500">#{r.id} · {dateText(r.created_at)}</span></div>
                      <Downloads rec={r} />
                    </li>
                  ))}
                </ul>
              </Disclosure>
            </div>
          )}
        </>
      )}
      {others.length > 0 && (
        <div className="card mt-5 p-5">
          <Disclosure title={`Exports from other runs (${others.length})`}>
            <ul className="divide-y divide-slate-100">
              {others.map((r) => (
                <li key={r.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
                  <div className="flex items-center gap-3 text-sm"><Status status={r.status} /><span className="text-slate-700">Run #{r.job_id}</span><span className="text-slate-500">#{r.id} · {dateText(r.created_at)}</span></div>
                  <Downloads rec={r} />
                </li>
              ))}
            </ul>
          </Disclosure>
        </div>
      )}
    </>
  );
}
