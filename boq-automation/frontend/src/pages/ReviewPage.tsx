import { ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import {
  AlertTriangle, ArrowRight, Check, ChevronRight, ClipboardCheck, Eye, Flag, Pencil, Plus, RefreshCw, Replace, Search, ThumbsDown,
  Trash2,
} from "lucide-react";
import { api, errorText } from "../services/api";
import { useWork } from "../context/WorkContext";
import { useFeedback } from "../components/feedback";
import {
  Confidence, COST_SHEET_TIP, Drawer, Empty, engineName, ErrorNote, Guide, InfoTip, Menu, Modal, PageHeader, Progress,
  Segmented, Spinner, Status,
} from "../components/ui";
import { approvalText, componentStatus, inScope, lineStatus, live } from "../utils/status";
import type { Candidate, Description, Job, Mapping, SearchItem } from "../types";

type View = "all" | "attention" | "approved" | "unmatched";

const EXTRA_FILTERS: { key: string; label: string; test: (d: Description, threshold: number) => boolean }[] = [
  { key: "supply", label: "Highside Supply items", test: (d) => live(d).some((m) => m.cost_sheet_type === "Highside Supply") },
  { key: "install", label: "Highside Installation items", test: (d) => live(d).some((m) => m.cost_sheet_type === "Highside Installation") },
  { key: "low", label: "Low confidence", test: (d, t) => live(d).some((m) => m.confidence < t) },
  { key: "inferred", label: "Inferred components", test: (d) => live(d).some((m) => m.selection_type === "Inferred") },
  { key: "flagged", label: "Flagged lines", test: (d) => d.flagged },
];

/* ---------- details ---------- */

function specs(d: Description): [string, string][] {
  const r = d.requirements as Record<string, unknown>;
  const list = (k: string) => ((r[k] as unknown[]) ?? []).join(", ");
  const cap = Object.entries((r.capacity ?? {}) as Record<string, number>).map(([k, v]) => `${v} ${k.toUpperCase()}`).join(", ");
  const rows: [string, string][] = [
    ["Component", String(r.component ?? "")], ["Model", list("models")], ["Make", list("brands")], ["Capacity", cap],
    ["Size (mm)", list("sizes_mm")], ["Material", list("materials")], ["Equipment type", list("equipment_form").replace(/_/g, " ").toLowerCase()],
    ["Indoor / outdoor", list("unit_side")], ["Refrigerant", list("refrigerant")], ["Activities", list("activities")],
    ["Also requires", list("additional_requirements")], ["Included in price", list("included_in_line")],
    ["Engineer specification", String(r.specification ?? "")], ["Engineer note", String(r.engineer_note ?? "")],
  ];
  return rows.filter(([, v]) => v && v !== "undefined");
}

function Specs({ d }: { d: Description }) {
  return (
    <dl className="grid grid-cols-1 gap-x-6 gap-y-2.5 text-sm sm:grid-cols-2">
      {specs(d).map(([k, v]) => (
        <div key={k} className="flex gap-3"><dt className="w-32 shrink-0 text-slate-500">{k}</dt><dd className="text-slate-900">{v}</dd></div>
      ))}
    </dl>
  );
}

function Block({ title, children }: { title: string; children: ReactNode }) {
  return <section className="border-t border-slate-100 pt-5"><div className="eyebrow mb-3">{title}</div>{children}</section>;
}

function Candidates({ d, highlight }: { d: Description; highlight?: string }) {
  if (!d.candidates.length) return <p className="text-sm text-slate-500">No alternatives were recorded for this line.</p>;
  const chosen = new Set(live(d).map((m) => m.item_no));
  return (
    <div className="overflow-hidden rounded-lg border border-slate-200">
      <table className="w-full text-[13px]">
        <tbody className="divide-y divide-slate-100">
          {d.candidates.map((c, i) => {
            const decision = chosen.has(c.item_no) ? "Selected" : c.decision === "rejected" ? "Rejected" : "Alternative";
            const why = decision === "Rejected" ? c.conflicts ?? [] : [...c.reasons, ...c.warnings];
            return (
              <tr key={`${c.item_no}-${i}`} className={c.item_no === highlight ? "bg-brand-50/50" : ""}>
                <td className="px-3 py-2 align-top">
                  <div className="font-medium text-slate-900">{c.item_brief}{c.exact_model && <span className="ml-1.5 rounded bg-emerald-50 px-1.5 py-0.5 text-[11px] font-medium text-emerald-700">Exact model</span>}</div>
                  <div className="mt-0.5 text-slate-500">{why.join(" · ")}</div>
                </td>
                <td className="w-24 px-3 py-2 align-top font-mono text-xs text-slate-500">{c.item_no}</td>
                <td className={`w-24 px-3 py-2 text-right align-top font-medium ${decision === "Selected" ? "text-emerald-700" : decision === "Rejected" ? "text-rose-700" : "text-slate-500"}`}>{decision}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function ComponentDetails({ d, m, threshold, onClose }: { d: Description; m: Mapping; threshold: number; onClose: () => void }) {
  const cand = d.candidates.find((c) => c.item_no === m.item_no);
  const matched = cand?.reasons.length ? cand.reasons : m.reason ? [m.reason] : [];
  const concerns = [...m.flags, ...(cand?.warnings ?? [])].filter((x, i, a) => a.indexOf(x) === i);
  return (
    <Modal title={m.item_brief} subtitle={<span><span className="font-mono">{m.item_no}</span> · {m.cost_sheet_type}</span>} size="lg" onClose={onClose}>
      <div className="space-y-5">
        <div className="rounded-lg bg-slate-50 px-4 py-3">
          <div className="eyebrow">BOQ description</div>
          <div className="mt-1 text-sm text-slate-900">{d.description}</div>
          {d.context[0] && <div className="mt-0.5 text-xs text-slate-500">Under: {d.context[0]}</div>}
        </div>
        <div className="grid gap-4 sm:grid-cols-3">
          <div><div className="eyebrow mb-1.5">Confidence</div><Confidence value={m.confidence} threshold={threshold} /></div>
          <div><div className="eyebrow mb-1.5">Selected by</div><div className="text-sm text-slate-900">{engineName(m.source)} · {m.selection_type}</div></div>
          <div><div className="eyebrow mb-1.5 flex items-center gap-1">Cost Sheet Type <InfoTip text={COST_SHEET_TIP} /></div><div className="text-sm text-slate-900">{m.cost_sheet_type}</div></div>
        </div>
        <Block title="Why this component">
          <p className="text-sm text-slate-700">{m.reason || "No reason recorded."}</p>
        </Block>
        <div className="grid gap-5 sm:grid-cols-2">
          <Block title="Matched">
            {matched.length ? <ul className="space-y-1.5">{matched.map((x) => <li key={x} className="flex gap-2 text-sm text-slate-700"><Check size={15} className="mt-0.5 shrink-0 text-emerald-600" />{x}</li>)}</ul>
              : <p className="text-sm text-slate-500">Nothing recorded.</p>}
          </Block>
          <Block title="Check before approving">
            {concerns.length ? <ul className="space-y-1.5">{concerns.map((x) => <li key={x} className="flex gap-2 text-sm text-amber-800"><AlertTriangle size={15} className="mt-0.5 shrink-0" />{x}</li>)}</ul>
              : <p className="text-sm text-slate-500">No concerns.</p>}
          </Block>
        </div>
        <Block title="Extracted from the BOQ"><Specs d={d} /></Block>
        <Block title="Alternatives considered"><Candidates d={d} highlight={m.item_no} /></Block>
      </div>
    </Modal>
  );
}

function LineDetails({ d, onClose }: { d: Description; onClose: () => void }) {
  return (
    <Modal title={d.description} subtitle={`Row ${d.row_number} · ${d.unique_no}${d.context[0] ? ` · under ${d.context[0]}` : ""}`} size="lg" onClose={onClose}>
      <div className="space-y-5">
        <Specs d={d} />
        {(d.unmatched.length > 0 || d.notes.length > 0) && (
          <Block title="Needs attention">
            <ul className="space-y-2">{[...d.unmatched, ...d.notes].map((n, i) => <li key={i} className="flex gap-2 text-sm text-amber-800"><AlertTriangle size={15} className="mt-0.5 shrink-0" />{n}</li>)}</ul>
          </Block>
        )}
        <Block title="Candidates considered"><Candidates d={d} /></Block>
      </div>
    </Modal>
  );
}

/* ---------- add / replace ---------- */

function Picker({ masterId, types, d, replacing, onDone, onClose }: {
  masterId: number; types: string[]; d: Description; replacing: Mapping | null;
  onDone: (item: SearchItem) => Promise<void>; onClose: () => void;
}) {
  const [q, setQ] = useState("");
  const [items, setItems] = useState<SearchItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pick, setPick] = useState<SearchItem | null>(null);
  const [pickRaw, setPickRaw] = useState<Record<string, string> | null>(null);
  const [saving, setSaving] = useState(false);
  const taken = new Set(live(d).map((m) => m.item_no));
  const suggestions = d.candidates.filter((c) => c.decision !== "rejected" && !taken.has(c.item_no));

  useEffect(() => {
    if (!q.trim()) { setItems(null); return; }
    const t = setTimeout(() => {
      api.searchItems(masterId, q, "", 30, 0, types).then((r) => setItems(r.items)).catch((e) => setError(errorText(e)));
    }, 250);
    return () => clearTimeout(t);
  }, [q, masterId]); // eslint-disable-line

  useEffect(() => {
    setPickRaw(null);
    if (pick) api.itemDetail(masterId, pick.item_no).then((r) => setPickRaw(r.raw)).catch(() => setPickRaw({}));
  }, [pick, masterId]);

  const fromCandidate = (c: Candidate): SearchItem =>
    ({ item_no: c.item_no, item_brief: c.item_brief, cost_sheet_type: c.cost_sheet_type, uom: "", cost: 0, family: "" });
  const list: SearchItem[] = items ?? suggestions.map(fromCandidate);

  async function confirm() {
    if (!pick) return;
    setSaving(true);
    try { await onDone(pick); } catch (e) { setError(errorText(e)); setSaving(false); }
  }

  const facts = (raw: Record<string, string> | null) =>
    ["Family", "Make", "Model", "Size", "QuantityUom", "Description"].map((k) => [k === "QuantityUom" ? "UOM" : k, raw?.[k] ?? ""] as [string, string]).filter(([, v]) => v);

  return (
    <Drawer title={replacing ? "Replace component" : "Add component"} subtitle={d.description} onClose={onClose}
      footer={pick ? (<>
        <button className="btn btn-secondary" onClick={() => setPick(null)}>Back</button>
        <button className="btn btn-primary" disabled={saving} onClick={confirm}>{saving ? "Saving" : replacing ? "Replace" : "Add component"}</button>
      </>) : undefined}>
      <ErrorNote text={error} />
      {pick ? (
        <div className="space-y-4">
          <div className="eyebrow">Compare</div>
          <div className={`grid gap-3 ${replacing ? "sm:grid-cols-2" : ""}`}>
            {replacing && (
              <div className="rounded-xl border border-slate-200 p-4">
                <div className="text-xs text-slate-500">Current</div>
                <div className="mt-1 font-medium text-slate-900">{replacing.item_brief}</div>
                <div className="mt-1 text-xs text-slate-500"><span className="font-mono">{replacing.item_no}</span> · {replacing.cost_sheet_type}</div>
              </div>
            )}
            <div className="rounded-xl border-2 border-brand-500 bg-brand-50/40 p-4">
              <div className="text-xs text-brand-700">{replacing ? "Replacement" : "To add"}</div>
              <div className="mt-1 font-medium text-slate-900">{pick.item_brief}</div>
              <div className="mt-1 text-xs text-slate-500"><span className="font-mono">{pick.item_no}</span> · {pick.cost_sheet_type}</div>
              {pickRaw === null ? <div className="mt-3 text-xs text-slate-400">Loading details…</div> : (
                <dl className="mt-3 space-y-1 text-xs">{facts(pickRaw).map(([k, v]) => <div key={k} className="flex gap-2"><dt className="w-20 shrink-0 text-slate-500">{k}</dt><dd className="text-slate-800">{v}</dd></div>)}</dl>
              )}
            </div>
          </div>
          <p className="text-xs text-slate-500">The Item No., brief and Cost Sheet Type are taken from the Item Master record. Your choice is approved and remembered for this line.</p>
        </div>
      ) : (
        <>
          <div className="relative">
            <Search size={15} className="absolute left-3 top-2.5 text-slate-400" />
            <input autoFocus className="input pl-9" placeholder="Search the Item Master by brief or Item No." aria-label="Search Item Master" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
          <div className="eyebrow mb-2 mt-5">{items ? `Search results (${items.length})` : "Suggested by the engine"}</div>
          {list.length === 0 ? <p className="text-sm text-slate-500">{items ? `No ${types.join(" or ")} record matches.` : "Type to search the Item Master."}</p> : (
            <ul className="space-y-2">
              {list.map((i) => (
                <li key={i.item_no}>
                  <button onClick={() => setPick(i)} className="flex w-full items-center gap-3 rounded-xl border border-slate-200 px-4 py-3 text-left transition hover:border-brand-300 hover:bg-brand-50/30">
                    <div className="min-w-0 flex-1">
                      <div className="text-sm font-medium text-slate-900">{i.item_brief}</div>
                      <div className="mt-0.5 text-xs text-slate-500"><span className="font-mono">{i.item_no}</span> · {i.cost_sheet_type}{i.uom ? ` · ${i.uom}` : ""}</div>
                    </div>
                    <ChevronRight size={16} className="text-slate-400" />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </Drawer>
  );
}

function EditSpecs({ d, onSave, onClose }: { d: Description; onSave: (spec: string, note: string) => void; onClose: () => void }) {
  const [spec, setSpec] = useState(String(d.requirements.specification ?? ""));
  const [note, setNote] = useState(String(d.requirements.engineer_note ?? ""));
  return (
    <Modal title="Correct the specification" subtitle="Your values take priority when suggestions are regenerated. The BOQ text itself is not changed." onClose={onClose}
      footer={<><button className="btn btn-secondary" onClick={onClose}>Cancel</button><button className="btn btn-primary" onClick={() => onSave(spec, note)}>Save and regenerate</button></>}>
      <label className="mb-4 block"><span className="label">Specification</span><input className="input" value={spec} placeholder="e.g. 8 TR, 40 mm, FXKQ63ARV16" onChange={(e) => setSpec(e.target.value)} /></label>
      <label className="block"><span className="label">Engineer note</span><input className="input" value={note} onChange={(e) => setNote(e.target.value)} /></label>
    </Modal>
  );
}

/* ---------- one BOQ line ---------- */

function LineSection({ d, open, onToggle, act, onDetails, onLine, onPick, onEdit }: {
  d: Description; open: boolean; onToggle: () => void;
  act: (fn: () => Promise<unknown>, done: string) => void; onDetails: (m: Mapping) => void; onLine: () => void;
  onPick: (replacing: Mapping | null) => void; onEdit: () => void;
}) {
  const { confirm } = useFeedback();
  const ms = live(d);
  const rejected = d.mappings.filter((m) => m.status === "incorrect");
  const pending = ms.filter((m) => m.status === "pending").length;
  const attention = d.unmatched.length + d.notes.length;
  const status = lineStatus(d);
  return (
    <div id={`line-${d.id}`} className={`card overflow-hidden transition ${open ? "ring-1 ring-brand-200" : ""}`}>
      <button className="flex w-full items-center gap-4 px-5 py-4 text-left hover:bg-slate-50/60" onClick={onToggle} aria-expanded={open}>
        <ChevronRight size={17} className={`shrink-0 text-slate-400 transition-transform ${open ? "rotate-90" : ""}`} />
        <div className="min-w-0 flex-1">
          <div className="truncate font-medium text-slate-900" title={d.description}>{d.description}</div>
          <div className="truncate text-xs text-slate-400">{d.context[0] ? `${d.context[0]}` : `Row ${d.row_number}`}</div>
        </div>
        <div className="hidden w-28 text-right text-[13px] text-slate-500 sm:block">{ms.length} component{ms.length === 1 ? "" : "s"}</div>
        <div className="hidden w-32 text-right text-[13px] text-slate-500 md:block">{approvalText(d)}</div>
        <div className="shrink-0 sm:w-36"><Status status={status} /></div>
      </button>

      {open && (
        <div className="border-t border-slate-100 bg-slate-50/40 px-5 pb-4 pt-3 animate-fade-in">
          {attention > 0 && (
            <button onClick={onLine} className="mb-3 flex items-center gap-2 text-[13px] text-amber-800 hover:underline">
              <AlertTriangle size={14} /> {attention} note{attention > 1 ? "s" : ""} need{attention > 1 ? "" : "s"} attention
            </button>
          )}
          {ms.length === 0 ? (
            <p className="py-3 text-sm text-slate-500">No component selected. Add one from the Item Master, or regenerate after correcting the specification.</p>
          ) : (
            <div className="overflow-hidden rounded-lg border border-slate-200 bg-white">
              <table className="w-full">
                <thead className="table-head border-b border-slate-100"><tr>
                  <th>Item brief</th><th className="hidden sm:table-cell">Item No.</th>
                  <th className="hidden md:table-cell"><span className="inline-flex items-center gap-1">Cost Sheet Type <InfoTip text={COST_SHEET_TIP} /></span></th>
                  <th>Status</th><th className="!text-right">Actions</th>
                </tr></thead>
                <tbody className="divide-y divide-slate-100">
                  {ms.map((m) => (
                    <tr key={m.id} className="table-row">
                      <td>
                        <button className="text-left font-medium text-slate-900 hover:text-brand-700" onClick={() => onDetails(m)}>{m.item_brief}</button>
                        <div className="text-xs text-slate-500 sm:hidden"><span className="font-mono">{m.item_no}</span> · {m.cost_sheet_type}</div>
                      </td>
                      <td className="hidden whitespace-nowrap font-mono text-xs text-slate-500 sm:table-cell">{m.item_no}</td>
                      <td className="hidden whitespace-nowrap text-slate-600 md:table-cell">{m.cost_sheet_type}</td>
                      <td><Status status={componentStatus(m)} /></td>
                      <td>
                        <div className="flex items-center justify-end gap-1">
                          {m.status !== "approved" && (
                            <button className="btn btn-secondary btn-sm" onClick={() => act(() => api.approve(m.id), `Approved ${m.item_brief}`)}><Check size={14} /> Approve</button>
                          )}
                          <button className="btn btn-ghost btn-sm" onClick={() => onPick(m)}><Replace size={14} /> Replace</button>
                          <Menu items={[
                            { label: "View details", icon: <Eye size={15} />, onClick: () => onDetails(m) },
                            { label: "Mark as incorrect", icon: <ThumbsDown size={15} />, onClick: async () => {
                              if (await confirm({ title: "Mark as incorrect?", action: "Mark incorrect", danger: true,
                                text: <>“{m.item_brief}” will be excluded from the export and not suggested for this line again.</> }))
                                act(() => api.markIncorrect(m.id, ""), `${m.item_brief} marked incorrect`);
                            } },
                            { label: "Remove", icon: <Trash2 size={15} />, danger: true, onClick: async () => {
                              if (await confirm({ title: "Remove component?", action: "Remove", danger: true,
                                text: <>“{m.item_brief}” will be removed from this line. The change is recorded in the audit history.</> }))
                                act(() => api.remove(m.id), `Removed ${m.item_brief}`);
                            } },
                          ]} />
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {rejected.length > 0 && <p className="mt-2 text-xs text-slate-400">{rejected.length} rejected component{rejected.length > 1 ? "s" : ""} hidden; they are excluded from the export.</p>}

          <div className="mt-3 flex flex-wrap items-center gap-2">
            <button className="btn btn-secondary btn-sm" onClick={() => onPick(null)}><Plus size={14} /> Add component</button>
            {pending > 0 && <button className="btn btn-secondary btn-sm" onClick={() => act(() => api.approveAll(d.id), `Approved ${pending} component${pending > 1 ? "s" : ""}`)}><ClipboardCheck size={14} /> Approve all pending</button>}
            <div className="ml-auto">
              <Menu label="Line actions" items={[
                { label: "Line details", icon: <Eye size={15} />, onClick: onLine },
                { label: "Correct the specification", icon: <Pencil size={15} />, onClick: onEdit },
                { label: "Regenerate suggestions", icon: <RefreshCw size={15} />, onClick: () => act(() => api.regenerate(d.id), "Suggestions regenerated") },
                { label: d.flagged ? "Clear review flag" : "Flag for manual review", icon: <Flag size={15} />,
                  onClick: () => act(() => api.flag(d.id, !d.flagged, d.flagged ? "" : "Needs manual review"), d.flagged ? "Flag cleared" : "Line flagged for review") },
              ]} />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/* ---------- page ---------- */

export default function ReviewPage() {
  const { jobId, setJobId, refreshProgress } = useWork();
  const { notify } = useFeedback();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [jobs, setJobs] = useState<Job[]>([]);
  const [descs, setDescs] = useState<Description[] | null>(null);
  const [open, setOpen] = useState<Set<number>>(new Set());
  const [view, setView] = useState<View>("all");
  const [extra, setExtra] = useState("");
  const [q, setQ] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [details, setDetails] = useState<{ d: Description; m: Mapping } | null>(null);
  const [lineInfo, setLineInfo] = useState<Description | null>(null);
  const [picker, setPicker] = useState<{ d: Description; replacing: Mapping | null } | null>(null);
  const [editing, setEditing] = useState<Description | null>(null);
  const scrolled = useRef(false);

  useEffect(() => { api.jobs().then(setJobs).catch((e) => setError(errorText(e))); }, []);
  const job = jobs.find((j) => j.id === jobId) ?? null;
  const threshold = job?.config.confidence_threshold ?? 75;

  const load = useCallback(() => {
    if (jobId) api.descriptions(jobId).then(setDescs).catch((e) => setError(errorText(e)));
  }, [jobId]);
  useEffect(() => { setDescs(null); load(); }, [load]);

  useEffect(() => { // deep link from the mapping page: ?line=<id>
    const target = Number(params.get("line"));
    if (!target || !descs || scrolled.current) return;
    scrolled.current = true;
    setOpen((o) => new Set(o).add(target));
    setTimeout(() => document.getElementById(`line-${target}`)?.scrollIntoView({ behavior: "smooth", block: "center" }), 80);
    params.delete("line"); setParams(params, { replace: true });
  }, [descs]); // eslint-disable-line

  const act = (fn: () => Promise<unknown>, done: string) => {
    setError(null);
    fn().then(() => { notify(done); load(); refreshProgress(); }).catch((e) => notify(errorText(e), "error"));
  };

  const scoped = useMemo(() => (descs ?? []).filter(inScope), [descs]);
  const all = scoped.flatMap(live);
  const approved = all.filter((m) => m.status === "approved").length;
  const byView: Record<View, (d: Description) => boolean> = {
    all: () => true,
    attention: (d) => lineStatus(d) === "review" || lineStatus(d) === "matched",
    approved: (d) => lineStatus(d) === "approved",
    unmatched: (d) => lineStatus(d) === "unmatched",
  };
  const extraTest = EXTRA_FILTERS.find((f) => f.key === extra)?.test;
  const needle = q.trim().toLowerCase();
  const visible = scoped.filter((d) => byView[view](d) && (!extraTest || extraTest(d, threshold))
    && (!needle || d.description.toLowerCase().includes(needle) || live(d).some((m) => `${m.item_brief} ${m.item_no}`.toLowerCase().includes(needle))));
  const toggle = (id: number) => { const n = new Set(open); n.has(id) ? n.delete(id) : n.add(id); setOpen(n); };

  return (
    <>
      <PageHeader eyebrow="Step 3" title="Review & approve" subtitle="Only approved components are written to the exported BOQ."
        action={<>
          <select className="input !w-auto text-[13px]" aria-label="Processing run" value={jobId ?? ""} onChange={(e) => { setJobId(Number(e.target.value)); setOpen(new Set()); }}>
            <option value="" disabled>Select a run</option>
            {jobs.map((j) => <option key={j.id} value={j.id}>Run #{j.id}{j.status !== "completed" ? ` (${j.status})` : ""}</option>)}
          </select>
          <button className="btn btn-primary" disabled={!approved} onClick={() => navigate("/exports")}>Export BOQ <ArrowRight size={15} /></button>
        </>} />
      <ErrorNote text={error} />

      {!jobId ? <Empty icon={<ClipboardCheck size={20} />} title="No run selected" text="Start a mapping run first, then review its suggestions here."
        action={<Link className="btn btn-primary" to="/mapping">Go to mapping</Link>} />
        : !descs ? <Spinner /> : scoped.length === 0 ? (
          <Empty icon={<ClipboardCheck size={20} />} title="Nothing to review in this run" text="Every line was outside the selected scope or already mapped." />
        ) : (
          <>
            <Guide id="review">Open a line to check its components. Approve the right ones, replace or remove the rest, then export.</Guide>

            <div className="card mb-5 flex flex-wrap items-center gap-x-6 gap-y-3 px-5 py-4">
              <div className="min-w-[220px] flex-1">
                <div className="mb-1.5 flex items-baseline justify-between text-sm">
                  <span className="font-medium text-slate-900">{approved} of {all.length} components approved</span>
                  <span className="tabular-nums text-slate-400">{all.length ? Math.round((approved / all.length) * 100) : 0}%</span>
                </div>
                <Progress value={all.length ? (approved / all.length) * 100 : 0} tone="good" />
              </div>
            </div>

            <div className="mb-4 flex flex-wrap items-center gap-3">
              <Segmented value={view} onChange={setView} options={[
                { value: "all", label: "All", count: scoped.length },
                { value: "attention", label: "To review", count: scoped.filter(byView.attention).length },
                { value: "approved", label: "Approved", count: scoped.filter(byView.approved).length },
                { value: "unmatched", label: "Unmatched", count: scoped.filter(byView.unmatched).length },
              ]} />
              <select className="input !w-auto text-[13px]" aria-label="More filters" value={extra} onChange={(e) => setExtra(e.target.value)}>
                <option value="">All components</option>
                {EXTRA_FILTERS.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
              </select>
              <div className="relative ml-auto w-full sm:w-64">
                <Search size={15} className="absolute left-3 top-2.5 text-slate-400" />
                <input className="input pl-9" placeholder="Search lines or items" aria-label="Search lines" value={q} onChange={(e) => setQ(e.target.value)} />
              </div>
            </div>

            {visible.length === 0 ? <Empty title="No lines match" text="Try a different filter or search." /> : (
              <div className="space-y-2.5">
                {visible.map((d) => (
                  <LineSection key={d.id} d={d} open={open.has(d.id)} onToggle={() => toggle(d.id)} act={act}
                    onDetails={(m) => setDetails({ d, m })} onLine={() => setLineInfo(d)}
                    onPick={(replacing) => setPicker({ d, replacing })} onEdit={() => setEditing(d)} />
                ))}
              </div>
            )}
            {descs.length > scoped.length && (
              <p className="mt-4 text-xs text-slate-400">{descs.length - scoped.length} line{descs.length - scoped.length > 1 ? "s" : ""} outside this run's scope or already mapped are left unchanged.</p>
            )}
          </>
        )}

      {details && <ComponentDetails d={details.d} m={details.m} threshold={threshold} onClose={() => setDetails(null)} />}
      {lineInfo && <LineDetails d={lineInfo} onClose={() => setLineInfo(null)} />}
      {picker && job && (
        <Picker masterId={job.item_master_upload_id} types={job.config.cost_sheet_types} d={picker.d} replacing={picker.replacing}
          onClose={() => setPicker(null)}
          onDone={async (item) => {
            const p = picker;
            if (p.replacing) await api.replace(p.replacing.id, item.item_no);
            else await api.addMapping(p.d.id, item.item_no);
            setPicker(null);
            notify(p.replacing ? `Replaced with ${item.item_brief}` : `Added ${item.item_brief}`);
            load(); refreshProgress();
          }} />
      )}
      {editing && <EditSpecs d={editing} onClose={() => setEditing(null)} onSave={(s, n) => {
        const d = editing; setEditing(null);
        act(async () => { await api.editRequirements(d.id, s, n); await api.regenerate(d.id); }, "Specification saved and suggestions regenerated");
      }} />}
    </>
  );
}
