import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ArrowRight, Eye, FileSpreadsheet, Library, ShieldCheck } from "lucide-react";
import { api, errorText } from "../services/api";
import { useWork } from "../context/WorkContext";
import { useFeedback } from "../components/feedback";
import { ErrorNote, FileDrop, FileSummary, Guide, Modal, PageHeader, PreviewTable, Spinner } from "../components/ui";
import ColumnMapping, { layoutOf, LayoutStatus } from "../components/ColumnMapping";

const mb = (n) => `${(n / 1024 / 1024).toFixed(2)} MB`;

function Panel({ icon, title, text, children }) {
  return (
    <section className="card flex flex-col p-6">
      <div className="mb-5 flex items-start gap-3">
        <div className="rounded-lg bg-brand-50 p-2 text-brand-600">{icon}</div>
        <div><h2 className="text-[15px] font-semibold">{title}</h2><p className="mt-0.5 text-sm text-slate-500">{text}</p></div>
      </div>
      <div className="flex flex-1 flex-col gap-4">{children}</div>
    </section>
  );
}

function Previous({ items, value, onPick, label }) {
  if (items.length < 2 && items.some((i) => i.id === value)) return null;
  if (!items.length) return null;
  return (
    <select className="input text-[13px]" aria-label="Use an earlier upload" value={value ?? ""} onChange={(e) => onPick(Number(e.target.value))}>
      <option value="" disabled>Or choose an earlier upload</option>
      {items.map((u) => <option key={u.id} value={u.id}>{label(u)}</option>)}
    </select>
  );
}

export default function UploadPage() {
  const { boqId, setBoqId, masterId, setMasterId, refreshProgress } = useWork();
  const { notify } = useFeedback();
  const navigate = useNavigate();
  const [boqs, setBoqs] = useState([]);
  const [masters, setMasters] = useState([]);
  const [busy, setBusy] = useState(null);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState(null);
  const [replacing, setReplacing] = useState(null);
  const [preview, setPreview] = useState(null);
  const [previewing, setPreviewing] = useState(false);
  const [mapping, setMapping] = useState(false);

  const loadMasters = () => api.itemMasters().then(setMasters).catch((e) => setError(errorText(e)));
  useEffect(() => { api.boqUploads().then(setBoqs).catch((e) => setError(errorText(e))); loadMasters(); }, []); // eslint-disable-line

  const boq = boqs.find((u) => u.id === boqId) ?? null;
  const master = masters.find((u) => u.id === masterId) ?? null;

  useEffect(() => { // an Item Master is parsed in the background after upload
    if (master?.status !== "processing") return;
    const t = setInterval(loadMasters, 1500);
    return () => clearInterval(t);
  }, [master?.status]); // eslint-disable-line
  useEffect(() => { if (master?.status === "ready") refreshProgress(); }, [master?.status]); // eslint-disable-line

  async function upload(kind, f) {
    setBusy(kind); setError(null); setProgress(0);
    try {
      if (kind === "boq") {
        const u = await api.uploadBoq(f, setProgress);
        setBoqs([u, ...boqs]); setBoqId(u.id);
        const lay = layoutOf(u);
        if (lay?.needs_mapping) { setMapping(true); notify(`${u.filename} uploaded. Please confirm its columns.`); }
        else notify(`${u.filename} uploaded. Worksheet "${u.selected_sheet}" will be processed.`);
      } else {
        const u = await api.uploadItemMaster(f, setProgress);
        setMasters([u, ...masters]); setMasterId(u.id);
        notify(`${u.filename} uploaded. Reading its records…`);
      }
      setReplacing(null);
      refreshProgress();
    } catch (e) { setError(errorText(e)); } finally { setBusy(null); }
  }

  async function openPreview() {
    if (!boq) return;
    setPreviewing(true);
    try { setPreview(await api.boqPreview(boq.id, boq.selected_sheet)); } catch (e) { setError(errorText(e)); } finally { setPreviewing(false); }
  }

  const ready = boq && !layoutOf(boq)?.needs_mapping && master?.status === "ready";
  return (
    <>
      <PageHeader eyebrow="Step 1" title="Upload files" subtitle="Your originals are never modified; the app works on copies."
        action={<button className="btn btn-primary" disabled={!ready} onClick={() => navigate("/mapping")}>Continue to mapping <ArrowRight size={15} /></button>} />
      <Guide id="upload">Upload the BOQ to map and the Item Master. Completed, verified BOQs go to the <Link className="font-medium underline" to="/knowledge">knowledge base</Link> instead,
        where they guide future mappings.</Guide>
      <ErrorNote text={error} />

      <div className="grid gap-5 lg:grid-cols-2">
        <Panel icon={<FileSpreadsheet size={18} />} title="BOQ workbook" text="Any BOQ layout with a description column. Worksheet and columns are detected automatically.">
          {boq && replacing !== "boq" ? (
            <>
              <FileSummary name={boq.filename} status={layoutOf(boq)?.needs_mapping ? "review" : "ready"}
                meta={`${mb(boq.size_bytes)} · Worksheet: ${boq.selected_sheet}`}
                action={<button className="btn btn-ghost btn-sm" onClick={() => setReplacing("boq")}>Change</button>} />
              <LayoutStatus upload={boq} onEdit={() => setMapping(true)} />
              <div className="flex flex-wrap items-center justify-between gap-2 text-[13px] text-slate-500">
                {boq.has_macros ? <span className="flex items-center gap-1.5"><ShieldCheck size={14} className="text-emerald-600" /> Macros detected; exports keep them intact</span> : <span />}
                <button className="btn btn-ghost btn-sm" onClick={openPreview} disabled={previewing}><Eye size={14} /> {previewing ? "Loading" : "Preview"}</button>
              </div>
            </>
          ) : (
            <>
              <FileDrop accept=".xlsx,.xlsm" label="Drop the BOQ here, or browse" hint=".xlsx or .xlsm" busy={busy === "boq"} progress={progress} onFile={(f) => upload("boq", f)} />
              <Previous items={boqs} value={boqId} onPick={(id) => { setBoqId(id); setReplacing(null); }} label={(u) => `${u.filename} (#${u.id})`} />
            </>
          )}
        </Panel>

        <Panel icon={<Library size={18} />} title="Item Master" text="Item Nos and descriptions come only from here. Its Cost Sheet Types apply unless verified BOQs say otherwise.">
          {master && replacing !== "master" ? (
            <FileSummary name={master.filename} status={master.status}
              meta={master.status === "ready" ? `${mb(master.size_bytes)} · ${master.record_count.toLocaleString()} records` : master.status === "failed" ? master.error ?? "Could not be read" : "Reading records…"}
              action={<button className="btn btn-ghost btn-sm" onClick={() => setReplacing("master")}>Change</button>} />
          ) : (
            <>
              <FileDrop accept=".xlsx" label="Drop the Item Master here, or browse" hint=".xlsx" busy={busy === "master"} progress={progress} onFile={(f) => upload("master", f)} />
              <Previous items={masters} value={masterId} onPick={(id) => { setMasterId(id); setReplacing(null); }}
                label={(u) => `${u.filename} (#${u.id}, ${u.record_count.toLocaleString()} records)`} />
            </>
          )}
        </Panel>
      </div>

      {mapping && boq && (
        <ColumnMapping upload={boq} onClose={() => setMapping(false)}
          onSaved={(u, sum) => { setBoqs(boqs.map((x) => (x.id === u.id ? u : x))); refreshProgress();
            notify(`${sum.lines} BOQ items found in "${u.selected_sheet}".`); }} />
      )}
      {preview && (
        <Modal title={`Preview: ${preview.sheet}`} subtitle="First rows of the worksheet that will be processed" size="lg" onClose={() => setPreview(null)}>
          {preview.rows.length ? <PreviewTable rows={preview.rows} /> : <Spinner />}
        </Modal>
      )}
    </>
  );
}
