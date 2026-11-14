import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { AlertTriangle, ArrowRight, Cpu, Play, Plus } from "lucide-react";
import { api, errorText } from "../services/api";
import { useWork } from "../context/WorkContext";
import { useFeedback } from "../components/feedback";
import {
  COST_SHEET_TIP, Disclosure, Drawer, Empty, engineName, ErrorNote, Guide, InfoTip, MetricStrip, Modal, PageHeader, Progress,
  Spinner, Status,
} from "../components/ui";
import { inScope, live, lineStatus, missingUnitQty } from "../utils/status";

function RunSettings({ onStarted, onCancel }) {
  const { options, boqId, masterId } = useWork();
  const [boq, setBoq] = useState(null);
  const [master, setMaster] = useState(null);
  const [types, setTypes] = useState([]);
  const [threshold, setThreshold] = useState(75);
  const [maxCandidates, setMaxCandidates] = useState(5);
  const [inferred, setInferred] = useState(true);
  const [manualLow, setManualLow] = useState(true);
  const [zeroQty, setZeroQty] = useState("review");
  const [error, setError] = useState(null);
  const [starting, setStarting] = useState(false);

  useEffect(() => {
    if (!options) return;
    setTypes(options.cost_sheet_types.map((t) => t.name));
    setThreshold(options.defaults.confidence_threshold);
    setMaxCandidates(options.defaults.max_candidates);
  }, [options]);
  useEffect(() => {
    api.boqUploads().then((l) => setBoq(l.find((u) => u.id === boqId) ?? null));
    api.itemMasters().then((l) => setMaster(l.find((u) => u.id === masterId) ?? null));
  }, [boqId, masterId]);

  if (!boq || master?.status !== "ready") {
    return <Empty icon={<Cpu size={20} />} title="Upload both workbooks first" text="Mapping needs a BOQ and a ready Item Master."
      action={<Link className="btn btn-primary" to="/upload">Go to uploads</Link>} />;
  }

  async function start() {
    setStarting(true); setError(null);
    try {
      onStarted(await api.createJob({
        boq_upload_id: boq.id, item_master_upload_id: master.id, cost_sheet_types: types,
        confidence_threshold: threshold, max_candidates: maxCandidates, suggest_inferred: inferred, manual_low_confidence: manualLow, zero_quantity: zeroQty,
      }));
    } catch (e) { setError(errorText(e)); } finally { setStarting(false); }
  }

  return (
    <div className="space-y-6">
      <ErrorNote text={error} />
      <div className="grid gap-3 text-sm sm:grid-cols-2">
        <div className="rounded-lg bg-slate-50 px-4 py-3"><div className="eyebrow">BOQ</div><div className="mt-1 truncate font-medium text-slate-900">{boq.filename}</div></div>
        <div className="rounded-lg bg-slate-50 px-4 py-3"><div className="eyebrow">Item Master</div><div className="mt-1 truncate font-medium text-slate-900">{master.filename}</div></div>
      </div>
      <div>
        <div className="label flex items-center gap-1.5">Cost Sheet Types to map <InfoTip text={COST_SHEET_TIP} /></div>
        <div className="flex flex-wrap gap-2">
          {options?.cost_sheet_types.map((t) => {
            const on = types.includes(t.name);
            return (
              <button key={t.name} aria-pressed={on} onClick={() => setTypes(on ? types.filter((x) => x !== t.name) : [...types, t.name])}
                className={`rounded-lg border px-3.5 py-2 text-sm font-medium transition ${on ? "border-brand-500 bg-brand-50 text-brand-800 ring-1 ring-brand-500" : "border-slate-300 bg-white text-slate-600 hover:border-slate-400"}`}>
                {t.name}
              </button>
            );
          })}
        </div>
      </div>
      <Disclosure title="Advanced settings">
        <div className="grid gap-5 rounded-lg border border-slate-200 p-4 sm:grid-cols-2">
          <label className="text-sm"><span className="label">Confidence threshold: {threshold}%</span>
            <input type="range" min={0} max={100} className="w-full accent-brand-600" value={threshold} onChange={(e) => setThreshold(Number(e.target.value))} /></label>
          <label className="text-sm"><span className="label">Alternatives kept per item</span>
            <input type="number" min={1} max={20} className="input" value={maxCandidates} onChange={(e) => setMaxCandidates(Number(e.target.value))} /></label>
          <label className="text-sm sm:col-span-2"><span className="label flex items-center gap-1">Items with quantity 0 <InfoTip text="Items need both a unit and a quantity to be mapped. A quantity of 0 is never treated as a valid quantity silently." /></span>
            <select className="input" value={zeroQty} onChange={(e) => setZeroQty(e.target.value)}>
              <option value="review">Map them, and mark every suggestion for review</option>
              <option value="skip">Skip them, like a missing quantity</option>
            </select></label>
          <label className="flex items-center gap-2.5 text-sm text-slate-700"><input type="checkbox" className="accent-brand-600" checked={inferred} onChange={(e) => setInferred(e.target.checked)} /> Suggest inferred components</label>
          <label className="flex items-center gap-2.5 text-sm text-slate-700"><input type="checkbox" className="accent-brand-600" checked={manualLow} onChange={(e) => setManualLow(e.target.checked)} /> Low confidence needs individual approval</label>
        </div>
      </Disclosure>
      <div className="flex items-center justify-between gap-3 border-t border-slate-100 pt-5">
        <span className="text-xs text-slate-500">Engine: {options?.ai_mode === "mock" ? "rule-based" : `${engineName(options?.ai_mode ?? "")} (${options?.model})`}</span>
        <div className="flex gap-2">
          {onCancel && <button className="btn btn-secondary" onClick={onCancel}>Cancel</button>}
          <button className="btn btn-primary" disabled={!types.length || starting} onClick={start}><Play size={14} /> {starting ? "Starting" : "Start mapping"}</button>
        </div>
      </div>
    </div>
  );
}

function LineDrawer({ d, onClose }) {
  const navigate = useNavigate();
  const ms = live(d);
  return (
    <Drawer title={d.description} subtitle={`Row ${d.row_number} · ${d.cost_sheet_type}`} onClose={onClose}
      footer={missingUnitQty(d) ? undefined : <button className="btn btn-primary" onClick={() => navigate(`/review?line=${d.id}`)}>Review this line <ArrowRight size={15} /></button>}>
      {d.context.length > 0 && <p className="mb-5 text-sm text-slate-500">Under: {d.context[0]}</p>}
      {d.flagged && d.flag_note && (
        <div className="mb-5 flex gap-2 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <AlertTriangle size={15} className="mt-0.5 shrink-0" />{d.flag_note}
        </div>
      )}
      {missingUnitQty(d) && (
        <div className="mb-5 rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-700">
          <div className="font-medium text-slate-900">Not mapped</div>
          <div className="mt-0.5">{d.skip_reason.replace(/^Skipped – Unit\/Quantity Missing: /, "This BOQ item was skipped because its ")}.</div>
          <div className="mt-1.5 text-xs text-slate-500">Unit: {d.uom || "blank"} · Quantity: {d.quantity || "blank or 0"}. Components are only added to items with both; nothing was assumed.</div>
        </div>
      )}
      <div className="eyebrow mb-2">Components found</div>
      {ms.length === 0 ? <p className="text-sm text-slate-500">No Item Master component was found for this line.</p> : (
        <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200">
          {ms.map((m) => (
            <li key={m.id} className="flex items-start justify-between gap-3 px-4 py-3">
              <div className="min-w-0">
                <div className="text-sm font-medium text-slate-900">{m.item_brief}</div>
                <div className="mt-0.5 text-xs text-slate-500"><span className="font-mono">{m.item_no}</span> · {m.cost_sheet_type}</div>
              </div>
              <Status status={m.status === "review" ? "review" : m.status === "approved" ? "approved" : "matched"} />
            </li>
          ))}
        </ul>
      )}
      {(d.unmatched.length > 0 || d.notes.length > 0) && (
        <div className="mt-6">
          <div className="eyebrow mb-2">Needs attention</div>
          <ul className="space-y-2">
            {[...d.unmatched, ...d.notes].map((n, i) => (
              <li key={i} className="flex gap-2 text-sm text-amber-800"><AlertTriangle size={15} className="mt-0.5 shrink-0" />{n}</li>
            ))}
          </ul>
        </div>
      )}
    </Drawer>
  );
}

export default function MappingPage() {
  const { jobId, setJobId, refreshProgress } = useWork();
  const { notify } = useFeedback();
  const navigate = useNavigate();
  const [jobs, setJobs] = useState(null);
  const [job, setJob] = useState(null);
  const [descs, setDescs] = useState([]);
  const [error, setError] = useState(null);
  const [configuring, setConfiguring] = useState(false);
  const [open, setOpen] = useState(null);
  const [showAll, setShowAll] = useState(false);

  useEffect(() => { api.jobs().then(setJobs).catch((e) => setError(errorText(e))); }, [jobId]);

  const poll = useCallback(() => {
    if (!jobId) return () => undefined;
    let live = true;
    let timer;
    const tick = async () => {
      try {
        const j = await api.job(jobId);
        if (!live) return;
        setJob(j);
        setDescs(await api.descriptions(jobId));
        if (j.status === "queued" || j.status === "running") timer = setTimeout(tick, 1200);
        else { refreshProgress(); api.jobs().then((l) => { if (live) setJobs(l); }).catch(() => undefined); }
      } catch (e) { if (live) setError(errorText(e)); }
    };
    tick();
    return () => { live = false; clearTimeout(timer); };
  }, [jobId]); // eslint-disable-line
  useEffect(() => poll(), [poll]);

  const scoped = useMemo(() => descs.filter(inScope), [descs]);
  const counts = useMemo(() => {
    const s = scoped.map(lineStatus);
    return {
      mapped: s.filter((x) => x === "matched" || x === "approved").length,
      review: s.filter((x) => x === "review").length,
      scope: s.filter((x) => x === "scope_review").length,
      unmatched: s.filter((x) => x === "unmatched").length,
    };
  }, [scoped]);

  const started = (j) => { setConfiguring(false); setJob(j); setDescs([]); setJobId(j.id); notify(`Run #${j.id} started.`); };
  const running = job?.status === "queued" || job?.status === "running";
  const incomplete = useMemo(() => descs.filter(missingUnitQty), [descs]);
  const listed = useMemo(() => descs.filter((d) => inScope(d) || missingUnitQty(d)), [descs]);
  const rows = showAll ? descs : listed;
  const hidden = descs.length - listed.length;

  if (jobs === null || (jobId && !job && !error)) return <Spinner />;
  if (!jobId || !job) {
    return (<>
      <PageHeader eyebrow="Step 2" title="Run mapping" subtitle="Choose what to map, then the engine matches every BOQ line to the Item Master." />
      <div className="card max-w-3xl p-6"><RunSettings onStarted={started} /></div>
    </>);
  }

  return (
    <>
      <PageHeader eyebrow="Step 2" title="Run mapping"
        subtitle={<>Run #{job.id} · {engineName(job.mode)} · {job.config.cost_sheet_types.join(", ")}
          {job.summary?.knowledge ? ` · ${job.summary.knowledge.historical_descriptions} verified descriptions in the knowledge base` : ""}</>}
        action={<>
          <select className="input !w-auto text-[13px]" aria-label="Processing run" value={jobId} onChange={(e) => setJobId(Number(e.target.value))}>
            {jobs.map((j) => <option key={j.id} value={j.id}>Run #{j.id}{j.status !== "completed" ? ` (${j.status})` : ""}</option>)}
          </select>
          <button className="btn btn-secondary" onClick={() => setConfiguring(true)}><Plus size={15} /> New run</button>
          {job.status === "completed" && <button className="btn btn-primary" onClick={() => navigate("/review")}>Review components <ArrowRight size={15} /></button>}
        </>} />
      <ErrorNote text={error ?? job.error} />
      {job.status === "completed" && <Guide id="mapping">The engine has identified components for each BOQ line. Open a line to see what was found, then review and approve.</Guide>}

      {running ? (
        <div className="card p-6">
          <div className="mb-3 flex items-center justify-between text-sm">
            <Status status="running" />
            <span className="tabular-nums text-slate-500">{job.processed} of {job.total || "…"} lines</span>
          </div>
          <Progress value={job.total ? (job.processed / job.total) * 100 : 3} />
          <p className="mt-3 text-xs text-slate-500">You can leave this page; mapping continues in the background.</p>
        </div>
      ) : (
        <MetricStrip items={[
          { label: "Identified", value: descs.length, hint: "BOQ items found in the worksheet" },
          { label: "Processed", value: scoped.length, hint: "Items with both a unit and a quantity, in the selected Cost Sheet Types" },
          { label: "Skipped: no unit/qty", value: incomplete.length, hint: "Items whose own unit or quantity is blank or not valid. They are never given components." },
          { label: "Mapped", value: counts.mapped, tone: "good" },
          { label: "Review required", value: counts.review, tone: counts.review ? "warn" : undefined },
          { label: "Outside Highside scope", value: counts.scope, tone: counts.scope ? "warn" : undefined,
            hint: "Lines that look like Lowside or other work. Nothing is added to them automatically; review them manually." },
          { label: "Unmatched", value: counts.unmatched, tone: counts.unmatched ? "bad" : undefined },
        ]} />
      )}

      {rows.length > 0 && (
        <div className="card mt-6 overflow-hidden">
          <table className="w-full">
            <thead className="table-head border-b border-slate-200 bg-slate-50/70">
              <tr><th>BOQ description</th><th className="!text-right">Components</th><th>Status</th><th /></tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {rows.map((d) => (
                <tr key={d.id} className="table-row cursor-pointer hover:bg-slate-50/70" onClick={() => setOpen(d)}>
                  <td className="max-w-0">
                    <div className="truncate font-medium text-slate-900" title={d.description}>{d.description}</div>
                    {d.context[0] && <div className="truncate text-xs text-slate-400" title={d.context[0]}>{d.context[0]}</div>}
                  </td>
                  <td className="w-28 text-right tabular-nums text-slate-700">{inScope(d) ? live(d).length : "-"}</td>
                  <td className="w-40" title={d.skip_reason}><Status status={lineStatus(d)} /></td>
                  <td className="w-20 text-right"><button className="btn btn-ghost btn-sm" onClick={(e) => { e.stopPropagation(); setOpen(d); }}>View</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {hidden > 0 && !running && (
        <button className="btn btn-ghost btn-sm mt-3" onClick={() => setShowAll(!showAll)}>
          {showAll ? "Hide lines outside this run's scope" : `Show ${hidden} line${hidden > 1 ? "s" : ""} out of scope or already mapped`}
        </button>
      )}
      {!running && job.status === "completed" && scoped.length === 0 && (
        <Empty title="Nothing to map in this scope" text="Every line in this BOQ is outside the selected Cost Sheet Types or already has components. Start a new run with a different scope." />
      )}

      {open && <LineDrawer d={open} onClose={() => setOpen(null)} />}
      {configuring && (
        <Modal title="New mapping run" subtitle="The current run and its approvals are kept." size="lg" onClose={() => setConfiguring(false)}>
          <RunSettings onStarted={started} onCancel={() => setConfiguring(false)} />
        </Modal>
      )}
    </>
  );
}
