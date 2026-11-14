import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle, BookOpenCheck, ChevronRight, Eye, FileUp, FolderSearch, ListTree, RefreshCw, RotateCcw, Search, Trash2,
} from "lucide-react";
import { api, errorText } from "../services/api";
import { useFeedback } from "../components/feedback";
import {
  dateText, Disclosure, Drawer, Empty, ErrorNote, FileDrop, Guide, Menu, MetricStrip, PageHeader, Progress, Segmented,
  Spinner, Status,
} from "../components/ui";

const BUSY = ["queued", "processing"];
const VIEWABLE = ["ingested", "partial", "no_mappings", "indexed"];
const ROOT_KEY = "knowledge.rootFolder";

function TypeLine({ c }) {
  const differs = c.master_cost_sheet_type && c.cost_sheet_type && c.master_cost_sheet_type !== c.cost_sheet_type;
  return (
    <span className="text-xs text-slate-500">
      <span className="font-mono">{c.item_no}</span> · {c.resolved_cost_sheet_type || c.cost_sheet_type || "No Cost Sheet Type"}
      {c.row_number ? ` · row ${c.row_number}` : ""}
      {c.type_source === "item_master" && <span className="ml-1.5 rounded bg-slate-100 px-1.5 py-0.5 font-medium text-slate-600">Blank in BOQ – Item Master type</span>}
      {differs && <span className="ml-1.5 rounded bg-amber-50 px-1.5 py-0.5 font-medium text-amber-800">Item Master: {c.master_cost_sheet_type}</span>}
      {!c.in_scope && <span className="ml-1.5 rounded bg-slate-100 px-1.5 py-0.5 font-medium text-slate-500">Outside Highside scope</span>}
      {!c.in_master && <span className="ml-1.5 rounded bg-rose-50 px-1.5 py-0.5 font-medium text-rose-700">Not in the Item Master</span>}
    </span>
  );
}

function ParentList({ items, open, toggle, showSource }) {
  return (
    <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200">
      {items.map((p) => (
        <li key={p.id}>
          <button className="flex w-full items-start gap-2 px-3 py-2.5 text-left hover:bg-slate-50" onClick={() => toggle(p.id)} aria-expanded={open.has(p.id)}>
            <ChevronRight size={15} className={`mt-0.5 shrink-0 text-slate-400 transition-transform ${open.has(p.id) ? "rotate-90" : ""}`} />
            <span className="min-w-0 flex-1">
              <span className="block text-sm text-slate-900">{p.description}</span>
              <span className="block text-xs text-slate-400">
                {showSource && p.source ? `${p.source.relative_path || p.source.filename} · ` : ""}{p.sheet} · row {p.row_number}
                {p.unique_no ? ` · ${p.unique_no}` : ""}{p.context?.[0] ? ` · under ${p.context[0]}` : ""}
              </span>
            </span>
            <span className="shrink-0 text-xs text-slate-500">{p.components.length}</span>
          </button>
          {open.has(p.id) && (
            <ul className="space-y-1.5 bg-slate-50/60 px-9 pb-3 pt-1">
              {p.components.map((c) => (
                <li key={`${c.item_no}-${c.row_number}`}><div className="text-sm text-slate-800">{c.item_brief}</div><TypeLine c={c} /></li>
              ))}
            </ul>
          )}
        </li>
      ))}
    </ul>
  );
}

function Pager({ total, page, size, setPage }) {
  if (total <= size) return null;
  return (
    <div className="mt-3 flex items-center justify-between text-sm text-slate-500">
      <span>{page * size + 1}–{Math.min(total, (page + 1) * size)} of {total}</span>
      <div className="flex gap-2">
        <button className="btn btn-secondary btn-sm" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button>
        <button className="btn btn-secondary btn-sm" disabled={(page + 1) * size >= total} onClick={() => setPage(page + 1)}>Next</button>
      </div>
    </div>
  );
}

function useToggleSet() {
  const [open, setOpen] = useState(new Set());
  const toggle = (id) => setOpen((prev) => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n; });
  return [open, toggle];
}

function Fact({ label, value, tone }) {
  return (
    <div className="rounded-lg bg-slate-50 px-3 py-2.5"><div className="text-slate-500">{label}</div>
      <div className={`mt-0.5 truncate text-lg font-semibold tabular-nums ${tone ?? ""}`} title={String(value)}>{value}</div></div>
  );
}

/** One historical BOQ: where it came from, how each worksheet was read, and what was learned. */
function Mappings({ h, onClose }) {
  const [q, setQ] = useState("");
  const [page, setPage] = useState(0);
  const [scope, setScope] = useState("highside");
  const [data, setData] = useState(null);
  const [open, toggle] = useToggleSet();
  const [error, setError] = useState(null);
  const size = 25;

  useEffect(() => {
    const t = setTimeout(() => {
      api.historicalMappings(h.id, q, size, page * size, scope).then(setData).catch((e) => setError(errorText(e)));
    }, 200);
    return () => clearTimeout(t);
  }, [h.id, q, page, scope]);

  const s = h.summary ?? {};
  return (
    <Drawer title={h.filename} subtitle={`${h.parents_count} Highside descriptions · ${h.highside_supply_count} supply · ${h.highside_installation_count} installation`} onClose={onClose}>
      <ErrorNote text={error} />
      <div className="mb-4 space-y-1 text-sm">
        <div className="flex items-center gap-2"><Status status={h.status} />{h.attempts > 1 && <span className="text-xs text-slate-400">attempt {h.attempts}</span>}</div>
        {h.source_path && <div className="break-all text-xs text-slate-500">Source: {h.source_path}</div>}
        {h.processed_at && <div className="text-xs text-slate-500">Extracted {dateText(h.processed_at)}{s.item_master ? ` · checked against ${s.item_master}` : ""}</div>}
        {h.error && <div className="flex gap-1.5 text-xs text-amber-800"><AlertTriangle size={13} className="mt-0.5 shrink-0" />{h.error}</div>}
        {s.repaired && <div className="text-xs text-slate-500">{s.repaired}</div>}
        {(s.notes ?? []).map((n) => <div key={n} className="text-xs text-slate-500">{n}</div>)}
      </div>
      <div className="mb-5 grid gap-3 text-sm sm:grid-cols-3">
        <Fact label="Reclassified vs master" value={s.classification_differs_from_master ?? 0} />
        <Fact label="Not in Item Master" value={s.not_in_item_master ?? 0} tone={s.not_in_item_master ? "text-amber-700" : ""} />
        <Fact label="Outside Highside scope" value={h.out_of_scope_count ?? 0} />
      </div>
      {(s.sheets ?? []).length > 0 && (
        <div className="mb-5"><Disclosure title="Worksheets read">
          <ul className="space-y-1.5 text-sm">
            {s.sheets.map((x) => (
              <li key={x.name} className="flex justify-between gap-3">
                <span className="truncate text-slate-700">{x.name}{x.format === "sales_bom" ? " (Sales BOM)" : ""}</span>
                <span className="shrink-0 text-right text-slate-500">{x.parents ? `${x.parents} descriptions, ${x.components} components${x.method ? ` · ${x.method}` : ""}` : x.note}</span>
              </li>
            ))}
          </ul>
        </Disclosure></div>
      )}
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <Segmented value={scope} onChange={(v) => { setScope(v); setPage(0); }} options={[
          { value: "highside", label: "Highside mappings" }, { value: "all", label: "All read" }]} />
        <div className="relative min-w-0 flex-1">
          <Search size={15} className="absolute left-3 top-2.5 text-slate-400" />
          <input className="input pl-9" placeholder="Search descriptions" aria-label="Search descriptions" value={q}
            onChange={(e) => { setQ(e.target.value); setPage(0); }} />
        </div>
      </div>
      {!data ? <Spinner /> : data.items.length === 0 ? <p className="text-sm text-slate-500">No descriptions match.</p>
        : <ParentList items={data.items} open={open} toggle={toggle} />}
      {data && <Pager total={data.total} page={page} size={size} setPage={setPage} />}
    </Drawer>
  );
}

/** The complete file inventory of one folder scan. */
function Inventory({ scanId, onClose, onOpen }) {
  const [data, setData] = useState(null);
  const [status, setStatus] = useState("");
  useEffect(() => { api.scan(scanId).then(setData).catch(() => setData({ inventory: [] })); }, [scanId]);
  if (!data) return <Drawer title="Folder inventory" onClose={onClose}><Spinner /></Drawer>;
  const counts = {};
  data.inventory.forEach((e) => { counts[e.status] = (counts[e.status] ?? 0) + 1; });
  const shown = data.inventory.filter((e) => !status || e.status === status);
  return (
    <Drawer title="Folder inventory" subtitle={data.root_path} onClose={onClose}>
      <div className="mb-4 grid grid-cols-2 gap-3 text-sm sm:grid-cols-3">
        <Fact label="Folders scanned" value={data.folders_scanned} />
        <Fact label="Files discovered" value={data.files_discovered} />
        <Fact label="Supported (.xlsx/.xlsm)" value={data.supported_files} />
        <Fact label="Duplicates" value={data.duplicate_files} />
        <Fact label="Unsupported" value={data.unsupported_files} />
        <Fact label="Unreadable" value={data.unreadable_files} tone={data.unreadable_files ? "text-rose-700" : ""} />
      </div>
      <div className="mb-3"><Segmented value={status} onChange={setStatus} options={[
        { value: "", label: "All", count: data.inventory.length },
        ...Object.entries(counts).sort().map(([k, n]) => ({ value: k, label: k.replace("_", " "), count: n }))]} /></div>
      <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200 text-sm">
        {shown.map((e) => (
          <li key={e.relative_path} className="px-3 py-2">
            <div className="flex items-start justify-between gap-3">
              <button className="min-w-0 text-left text-slate-900 hover:text-brand-700 disabled:hover:text-slate-900" disabled={!e.historical_id}
                onClick={() => onOpen(e.historical_id)} title={e.relative_path}>
                <span className="block truncate">{e.filename}</span>
                <span className="block truncate text-xs text-slate-400">{e.folder || "(root)"} · {e.extension} · {(e.size_bytes / 1024).toFixed(0)} KB{e.change ? ` · ${e.change}` : ""}</span>
              </button>
              <Status status={e.status} />
            </div>
            {e.reason && <div className="mt-0.5 text-xs text-slate-500">{e.reason}</div>}
          </li>
        ))}
      </ul>
    </Drawer>
  );
}

function FolderScan({ latest, onStarted }) {
  const { notify } = useFeedback();
  const [root, setRoot] = useState(() => { try { return localStorage.getItem(ROOT_KEY) ?? ""; } catch { return ""; } });
  const [reprocess, setReprocess] = useState(true);
  const [busy, setBusy] = useState(false);
  const running = latest && ["scanning", "processing"].includes(latest.status);

  async function start(e) {
    e.preventDefault();
    if (!root.trim()) return;
    setBusy(true);
    try {
      try { localStorage.setItem(ROOT_KEY, root.trim()); } catch { /* convenience only */ }
      await api.startScan(root.trim(), reprocess);
      notify("Folder scan started. New and changed BOQs are ingested in the background.");
      onStarted();
    } catch (err) { notify(errorText(err), "error"); } finally { setBusy(false); }
  }

  return (
    <form onSubmit={start} className="space-y-3">
      <div className="text-sm font-medium text-slate-800">Bulk folder ingestion</div>
      <div className="flex flex-col gap-2 sm:flex-row">
        <input className="input" value={root} onChange={(e) => setRoot(e.target.value)} aria-label="Root folder"
          placeholder="Folder on the server, e.g. C:\Users\…\Kytes Projects" />
        <button className="btn btn-primary shrink-0" disabled={busy || running || !root.trim()}><FolderSearch size={15} /> Scan folder</button>
      </div>
      <label className="flex items-center gap-2 text-xs text-slate-600">
        <input type="checkbox" checked={reprocess} onChange={(e) => setReprocess(e.target.checked)} />
        Reprocess files that changed since they were ingested (otherwise they are only flagged)
      </label>
      <p className="text-xs text-slate-500">Every subfolder is scanned. Files are only read; unchanged files are skipped by their content hash, so scanning the same folder again only picks up what is new or changed.</p>
      {latest && (
        <div className="rounded-lg border border-slate-200 px-3 py-2.5 text-sm">
          <div className="mb-1.5 flex items-center justify-between gap-3">
            <span className="truncate text-slate-700" title={latest.root_path}>Scan #{latest.id} · {latest.root_path}</span>
            <Status status={latest.status} />
          </div>
          {latest.to_process > 0 && <Progress value={(latest.processed / latest.to_process) * 100} tone={latest.status === "completed" ? "good" : "brand"} />}
          <div className="mt-1.5 text-xs text-slate-500">
            {latest.folders_scanned} folders · {latest.files_discovered} files · {latest.processed}/{latest.to_process} processed
            {latest.new_highside_mappings != null ? ` · ${latest.new_highside_mappings} new Highside mappings` : ""}
            {latest.finished_at ? ` · finished ${dateText(latest.finished_at)}` : ""}
          </div>
        </div>
      )}
    </form>
  );
}

/** Search every stored historical mapping, with its source. */
function MappingSearch() {
  const [q, setQ] = useState("");
  const [itemNo, setItemNo] = useState("");
  const [type, setType] = useState("");
  const [page, setPage] = useState(0);
  const [data, setData] = useState(null);
  const [open, toggle] = useToggleSet();
  const size = 20;
  useEffect(() => {
    const t = setTimeout(() => {
      api.knowledgeMappings({ q, item_no: itemNo, cost_sheet_type: type, limit: size, offset: page * size })
        .then(setData).catch(() => setData({ total: 0, items: [] }));
    }, 250);
    return () => clearTimeout(t);
  }, [q, itemNo, type, page]);
  return (
    <div className="space-y-3">
      <div className="flex flex-col gap-2 sm:flex-row">
        <input className="input" placeholder="Description words, e.g. outdoor unit" aria-label="Description words" value={q}
          onChange={(e) => { setQ(e.target.value); setPage(0); }} />
        <input className="input sm:w-40" placeholder="Item No." aria-label="Item number" value={itemNo}
          onChange={(e) => { setItemNo(e.target.value); setPage(0); }} />
        <select className="input sm:w-52" aria-label="Cost Sheet Type" value={type} onChange={(e) => { setType(e.target.value); setPage(0); }}>
          <option value="">Both Highside types</option><option>Highside Supply</option><option>Highside Installation</option>
        </select>
      </div>
      {!data ? <Spinner /> : data.items.length === 0 ? <p className="text-sm text-slate-500">No stored mapping matches.</p>
        : <ParentList items={data.items} open={open} toggle={toggle} showSource />}
      {data && <Pager total={data.total} page={page} size={size} setPage={setPage} />}
    </div>
  );
}

function TryIt() {
  const [q, setQ] = useState("");
  const [items, setItems] = useState(null);
  const [busy, setBusy] = useState(false);
  async function search(e) {
    e.preventDefault();
    if (q.trim().length < 2) return;
    setBusy(true);
    try { setItems((await api.knowledgeSearch(q)).items); } catch { setItems([]); } finally { setBusy(false); }
  }
  return (
    <div className="space-y-3">
      <form onSubmit={search} className="flex gap-2">
        <input className="input" value={q} onChange={(e) => setQ(e.target.value)} aria-label="BOQ description"
          placeholder="Paste a BOQ description, e.g. Supply of VRV outdoor unit 38 HP RXQ38BRY16" />
        <button className="btn btn-secondary" disabled={busy}><Search size={14} /> Find</button>
      </form>
      {items && (items.length === 0 ? <p className="text-sm text-slate-500">No similar, technically compatible verified description found.</p> : (
        <ul className="space-y-2">
          {items.map((x, i) => (
            <li key={i} className="rounded-lg border border-slate-200 px-3 py-2.5">
              <div className="flex justify-between gap-3 text-sm"><span className="text-slate-900">{x.description}</span>
                <span className="shrink-0 tabular-nums text-slate-500">{Math.round(x.similarity * 100)}%</span></div>
              <div className="mt-0.5 text-xs text-slate-400">{x.source === "historical" ? x.file : "Approved review"}{x.row ? ` · row ${x.row}` : ""}
                {x.copies > 1 ? ` · same mapping in ${x.copies} files` : ""}{x.agrees_on?.length ? ` · ${x.agrees_on.join(", ")}` : ""}</div>
              <ul className="mt-1.5 space-y-0.5">{x.components.map((c) => <li key={c.item_no} className="text-xs text-slate-600"><span className="font-mono">{c.item_no}</span> {c.item_brief} · {c.cost_sheet_type || "type from Item Master"}</li>)}</ul>
            </li>
          ))}
        </ul>
      ))}
    </div>
  );
}

const FILTERS = [
  ["", "All"], ["ingested", "Ingested"], ["partial", "Partial"], ["pending", "Processing"], ["failed", "Failed"],
  ["no_mappings", "No Highside mappings"], ["duplicate", "Duplicates"], ["changed", "Changed"],
];

export default function KnowledgePage() {
  const { notify, confirm } = useFeedback();
  const [files, setFiles] = useState(null);
  const [stats, setStats] = useState(null);
  const [error, setError] = useState(null);
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const [uploading, setUploading] = useState(null); // { done, total, pct }
  const [viewing, setViewing] = useState(null);
  const [inventory, setInventory] = useState(null);
  const replaceInput = useRef(null);
  const replacing = useRef(null);

  const load = useCallback(() => {
    api.historical().then(setFiles).catch((e) => setError(errorText(e)));
    api.knowledgeStats().then(setStats).catch(() => undefined);
  }, []);
  useEffect(() => { load(); }, [load]);

  const scanRunning = ["scanning", "processing"].includes(stats?.latest_scan?.status);
  const busy = scanRunning || (files ?? []).some((f) => BUSY.includes(f.status));
  useEffect(() => { // processing happens in the background; refresh until it settles
    if (!busy) return undefined;
    const t = setInterval(load, 2000);
    return () => clearInterval(t);
  }, [busy, load]);

  async function uploadAll(list) {
    setError(null);
    const failed = [];
    for (let i = 0; i < list.length; i++) {
      setUploading({ done: i, total: list.length, pct: 0 });
      try { await api.uploadHistorical(list[i], (pct) => setUploading({ done: i, total: list.length, pct })); }
      catch (e) { failed.push(`${list[i].name}: ${errorText(e)}`); }
    }
    setUploading(null);
    load();
    const ok = list.length - failed.length;
    if (ok) notify(`${ok} file${ok > 1 ? "s" : ""} uploaded. Extracting and indexing their mappings…`);
    if (failed.length) setError(failed.join(" · "));
  }

  async function replaceWith(f) {
    const h = replacing.current;
    if (!h || !f) return;
    try { await api.replaceHistorical(h.id, f); notify(`${h.filename} replaced with ${f.name}; reprocessing.`); load(); }
    catch (e) { notify(errorText(e), "error"); }
  }

  async function remove(h) {
    if (!(await confirm({ title: "Remove from the knowledge base?", action: "Remove", danger: true,
      text: <>“{h.filename}” and the {h.components_count} Highside mappings learned from it will no longer be used for future mapping. The original file{h.source_path ? " in its folder" : ""} is not touched; completed runs keep their results.</> }))) return;
    try { await api.deleteHistorical(h.id); notify(`${h.filename} removed`); load(); } catch (e) { notify(errorText(e), "error"); }
  }

  const openById = (id) => {
    const h = (files ?? []).find((f) => f.id === id);
    if (h && VIEWABLE.includes(h.status)) { setInventory(null); setViewing(h); }
  };

  const act = (fn, done) => fn().then(() => { notify(done); load(); }).catch((e) => notify(errorText(e), "error"));
  const needle = q.trim().toLowerCase();
  const shown = (files ?? []).filter((f) => (!status || (status === "pending" ? BUSY.includes(f.status) : f.status === status))
    && (!needle || f.filename.toLowerCase().includes(needle) || (f.relative_path ?? "").toLowerCase().includes(needle)));
  const st = stats?.files ?? {};
  const scan = stats?.latest_scan;
  const count = (k) => (k === "pending" ? st.pending : k === "" ? files?.length : st[k]) ?? 0;

  return (
    <>
      <PageHeader title="Knowledge base" subtitle="Completed, verified BOQs teach the engine which Highside components and Cost Sheet Types are used in practice."
        action={files?.length ? <>
          {st.failed > 0 && <button className="btn btn-secondary" onClick={() => act(api.retryFailedHistorical, "Failed files queued again")}><RotateCcw size={15} /> Retry failed ({st.failed})</button>}
          <button className="btn btn-secondary" onClick={() => act(api.reprocessAllHistorical, "All historical BOQs queued for reprocessing")}><RefreshCw size={15} /> Reprocess all</button>
        </> : null} />
      <Guide id="knowledge">Ingest BOQs that were already mapped and checked: upload files one by one, or scan a whole folder. Each workbook is read in its own layout; parent descriptions with their Highside Supply and Highside Installation components are stored and indexed. Lowside and other components are only recorded as out of scope.
        When a new BOQ is mapped, verified examples with the same technical specification take priority over the Item Master's own classification.</Guide>
      <ErrorNote text={error} />

      <MetricStrip items={[
        { label: "Folders scanned", value: scan?.folders_scanned ?? 0, hint: "In the latest folder scan" },
        { label: "Files discovered", value: scan?.files_discovered ?? st.total ?? 0, hint: "Every file in the latest folder scan, any type" },
        { label: "Ingested", value: (st.ingested ?? 0) + (st.partial ?? 0), tone: st.ingested ? "good" : undefined, hint: "Stored, indexed and used by every new mapping run (partially ingested files included)" },
        { label: "Processing", value: st.pending ?? 0 },
        { label: "Partial", value: st.partial ?? 0, tone: st.partial ? "warn" : undefined, hint: "Ingested, with some rows that could not be learned; the file says why" },
        { label: "Failed", value: st.failed ?? 0, tone: st.failed ? "bad" : undefined },
      ]} />
      <div className="mt-3"><MetricStrip items={[
        { label: "Descriptions", value: stats?.historical_parents ?? 0, hint: "Historical BOQ descriptions with Highside components" },
        { label: "HS Supply", value: stats?.highside_supply ?? 0, hint: "Component mappings stored" },
        { label: "HS Install.", value: stats?.highside_installation ?? 0, hint: "Component mappings stored" },
        { label: "Item Nos.", value: stats?.distinct_items ?? 0, hint: "Unique Item Numbers referenced by Highside mappings" },
        { label: "New", value: stats?.new_mappings_7d ?? 0, hint: "Highside mappings added in the last 7 days" },
        { label: "Last ingestion", value: stats?.last_ingested_at ? new Date(stats.last_ingested_at).toLocaleDateString(undefined, { day: "numeric", month: "short" }) : "—",
          hint: stats?.last_ingested_at ? `Last successful ingestion: ${dateText(stats.last_ingested_at)}` : undefined },
      ]} /></div>

      <div className="card mt-6 grid gap-6 p-6 lg:grid-cols-2">
        <FolderScan latest={scan} onStarted={load} />
        <div className="space-y-3">
          <div className="text-sm font-medium text-slate-800">Individual upload</div>
          <FileDrop accept=".xlsx,.xlsm" label="Drop completed BOQs here, or browse" hint="Several .xlsx or .xlsm files at once, in any layout. Processed automatically."
            busy={Boolean(uploading)} progress={uploading ? uploading.pct : 0} compact onFiles={uploadAll} />
          {uploading && <p className="text-center text-xs text-slate-500">File {uploading.done + 1} of {uploading.total}</p>}
          {scan && <button className="btn btn-secondary btn-sm" onClick={() => setInventory(scan.id)}><ListTree size={14} /> View latest folder inventory</button>}
        </div>
      </div>

      <div className="mb-3 mt-6 flex flex-wrap items-center gap-3">
        <Segmented value={status} onChange={setStatus} options={FILTERS.filter(([k]) => !k || count(k) || k === status)
          .map(([value, label]) => ({ value, label, count: count(value) }))} />
        <div className="relative ml-auto w-full sm:w-64">
          <Search size={15} className="absolute left-3 top-2.5 text-slate-400" />
          <input className="input pl-9" placeholder="Search files or folders" aria-label="Search files" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
      </div>

      {!files ? <Spinner /> : files.length === 0 ? (
        <Empty icon={<BookOpenCheck size={20} />} title="No historical BOQs yet"
          text="Scan a folder or upload completed BOQs with their component rows. The more verified BOQs the knowledge base holds, the more accurate new mappings become." />
      ) : shown.length === 0 ? <Empty title="No files match" text="Try a different filter or search." /> : (
        <div className="card overflow-hidden">
          <table className="w-full">
            <thead className="table-head border-b border-slate-200 bg-slate-50/70">
              <tr><th>File</th><th className="hidden !text-right lg:table-cell">Descriptions</th>
                <th className="hidden !text-right xl:table-cell">HS Supply</th><th className="hidden !text-right xl:table-cell">HS Install.</th>
                <th>Status</th><th className="hidden 2xl:table-cell">Processed</th><th /></tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {shown.map((h) => (
                <tr key={h.id} className="table-row">
                  <td className="max-w-0">
                    <button className="block max-w-full truncate text-left font-medium text-slate-900 hover:text-brand-700" title={h.source_path || h.filename}
                      disabled={!VIEWABLE.includes(h.status)} onClick={() => setViewing(h)}>{h.filename}</button>
                    {h.folder && <div className="truncate text-xs text-slate-400" title={h.folder}>{h.folder}</div>}
                    {h.error && <div className={`mt-0.5 flex gap-1.5 text-xs ${h.status === "failed" ? "text-rose-700" : "text-slate-500"}`}><AlertTriangle size={13} className="mt-0.5 shrink-0" /><span className="line-clamp-2">{h.error}</span></div>}
                  </td>
                  <td className="hidden w-28 text-right tabular-nums text-slate-700 lg:table-cell">{h.parents_count}</td>
                  <td className="hidden w-24 text-right tabular-nums text-slate-700 xl:table-cell">{h.highside_supply_count}</td>
                  <td className="hidden w-24 text-right tabular-nums text-slate-700 xl:table-cell">{h.highside_installation_count}</td>
                  <td className="w-44"><Status status={h.status} /></td>
                  <td className="hidden w-44 whitespace-nowrap text-slate-500 2xl:table-cell">{h.processed_at ? dateText(h.processed_at) : "—"}</td>
                  <td className="w-12 text-right">
                    <Menu items={[
                      { label: "View source & mappings", icon: <Eye size={15} />, onClick: () => setViewing(h), hidden: !VIEWABLE.includes(h.status) },
                      { label: h.status === "changed" ? "Reprocess updated file" : "Reprocess", icon: <RefreshCw size={15} />,
                        hidden: BUSY.includes(h.status) || h.status === "duplicate" || !h.sha256,
                        onClick: () => act(() => api.reprocessHistorical(h.id), `${h.filename} queued for reprocessing`) },
                      { label: "Retry", icon: <RotateCcw size={15} />, hidden: h.status !== "failed",
                        onClick: () => act(api.retryFailedHistorical, "Failed files queued again") },
                      { label: "Replace file", icon: <FileUp size={15} />, hidden: BUSY.includes(h.status) || Boolean(h.source_path),
                        onClick: () => { replacing.current = h; replaceInput.current?.click(); } },
                      { label: "Remove", icon: <Trash2 size={15} />, danger: true, hidden: BUSY.includes(h.status), onClick: () => remove(h) },
                    ]} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <input ref={replaceInput} type="file" accept=".xlsx,.xlsm" hidden
        onChange={(e) => { replaceWith(e.target.files?.[0]); e.target.value = ""; }} />

      {(stats?.historical_parents ?? 0) > 0 && (
        <div className="card mt-6 p-5">
          <Disclosure title="Search historical mappings"><MappingSearch /></Disclosure>
        </div>
      )}
      {(stats?.historical_parents ?? 0) + (stats?.feedback_parents ?? 0) > 0 && (
        <div className="card mt-3 p-5">
          <Disclosure title="Check what the knowledge base would suggest for a new description"><TryIt /></Disclosure>
        </div>
      )}
      {viewing && <Mappings h={viewing} onClose={() => setViewing(null)} />}
      {inventory && <Inventory scanId={inventory} onClose={() => setInventory(null)} onOpen={openById} />}
    </>
  );
}
