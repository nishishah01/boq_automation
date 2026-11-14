import { useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, CornerDownRight } from "lucide-react";
import { api, errorText } from "../services/api";
import { ErrorNote, InfoTip, Modal, Spinner } from "./ui";

const ROLES = [
  { key: "description", label: "Description", required: true, hint: "The text describing each BOQ item. The AI reads this to find components." },
  { key: "serial", label: "Serial / Item No.", hint: "Numbers like 1, 1.1, 1.2 help group items under their headings." },
  { key: "quantity", label: "Quantity", required: true, hint: "Only BOQ items with both a quantity and a unit are mapped. Items missing either are listed as skipped." },
  { key: "unit", label: "Unit", required: true, hint: "Nos, Rmt, Sqm and so on. Only items with both a unit and a quantity are mapped." },
  { key: "rate", label: "Rate", hint: "Optional." },
  { key: "amount", label: "Amount", hint: "Optional." },
  { key: "cost_sheet_type", label: "Cost Sheet Type", hint: "Only if the BOQ already classifies its items. Otherwise the engine chooses." },
];

const letter = (i) => {
  let s = "", n = i + 1;
  while (n > 0) { const m = (n - 1) % 26; s = String.fromCharCode(65 + m) + s; n = Math.floor((n - 1) / 26); }
  return s;
};

export function layoutOf(u) {
  return u?.sheets.find((s) => s.name === u.selected_sheet)?.layout;
}

export function LayoutStatus({ upload, onEdit }) {
  const lay = layoutOf(upload);
  if (!lay) return null;
  if (lay.format === "sales_bom") {
    return (
      <div className="flex items-center justify-between gap-3 text-[13px] text-slate-600">
        <span className="flex items-center gap-1.5"><CheckCircle2 size={14} className="text-emerald-600" /> Sales BOM template recognised</span>
        <button className="btn btn-ghost btn-sm" onClick={onEdit}>Worksheet</button>
      </div>
    );
  }
  if (lay.needs_mapping) {
    return (
      <div className="rounded-lg border border-amber-200 bg-amber-50 px-3.5 py-3 text-sm text-amber-900">
        <div className="flex items-start gap-2">
          <AlertTriangle size={16} className="mt-0.5 shrink-0" />
          <div className="flex-1">
            <div className="font-medium">Confirm the columns of this BOQ</div>
            <div className="mt-0.5 text-[13px]">
              {lay.columns.description === undefined ? "No description column was found. A description column is mandatory for AI matching."
                : lay.columns.unit === undefined || lay.columns.quantity === undefined
                  ? "The unit or quantity column was not recognised. Both are needed: only items with a unit and a quantity are mapped."
                  : "The description column could not be identified with confidence. Check it before mapping."}
            </div>
          </div>
          <button className="btn btn-primary btn-sm" onClick={onEdit}>Map columns</button>
        </div>
      </div>
    );
  }
  const desc = lay.headers[lay.columns.description] || `Column ${letter(lay.columns.description)}`;
  return (
    <div className="flex items-center justify-between gap-3 text-[13px] text-slate-600">
      <span className="flex min-w-0 items-center gap-1.5"><CheckCircle2 size={14} className="shrink-0 text-emerald-600" />
        <span className="truncate">Descriptions from “{desc}”, headings on row {lay.header_row}</span></span>
      <button className="btn btn-ghost btn-sm" onClick={onEdit}>Columns</button>
    </div>
  );
}

export default function ColumnMapping({ upload, onSaved, onClose }) {
  const sheets = upload.sheets.filter((s) => (s.rows ?? 2) > 1);
  const [sheet, setSheet] = useState(upload.selected_sheet);
  const [layout, setLayout] = useState(layoutOf(upload) ?? null);
  const [headerRow, setHeaderRow] = useState(layout?.header_row ?? null);
  const [columns, setColumns] = useState(layout?.columns ?? {});
  const [preview, setPreview] = useState([]);
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);
  const [summary, setSummary] = useState(null);

  useEffect(() => { api.boqPreview(upload.id, sheet).then((p) => setPreview(p.rows)).catch(() => setPreview([])); }, [sheet, upload.id]);

  async function detect(nextSheet, row) {
    setError(null);
    try {
      const l = await api.boqLayout(upload.id, nextSheet, row);
      setLayout(l); setHeaderRow(l.header_row); setColumns(l.columns);
    } catch (e) { setError(errorText(e)); }
  }

  async function save() {
    if (!layout) return;
    setSaving(true); setError(null);
    try {
      const r = await api.saveBoqLayout(upload.id, layout.format === "sales_bom"
        ? { sheet, header_row: null, columns: {} } : { sheet, header_row: headerRow, columns });
      setSummary(r.summary);
      onSaved(r.upload, r.summary);
    } catch (e) { setError(errorText(e)); } finally { setSaving(false); }
  }

  if (summary) {
    return (
      <Modal title="BOQ ready for mapping" size="lg" onClose={onClose}
        footer={<button className="btn btn-primary" onClick={onClose}>Done</button>}>
        <p className="text-sm text-slate-700">Found <b>{summary.lines}</b> BOQ items{summary.headings ? <> under <b>{summary.headings}</b> headings and notes</> : null}. The first few:</p>
        <ul className="mt-4 divide-y divide-slate-100 rounded-lg border border-slate-200">
          {summary.sample.map((x) => (
            <li key={x.row} className="px-4 py-2.5 text-sm">
              {x.context.length > 0 && <div className="truncate text-xs text-slate-400">{[...x.context].reverse().join(" › ")}</div>}
              <div className="flex items-start gap-1.5"><CornerDownRight size={14} className="mt-0.5 shrink-0 text-slate-300" />
                <span className="flex-1 text-slate-900">{x.text}</span>
                <span className="whitespace-nowrap text-xs text-slate-500">Row {x.row}{x.quantity ? ` · ${x.quantity} ${x.unit}` : ""}</span></div>
            </li>
          ))}
        </ul>
        {summary.warnings.map((w) => <p key={w} className="mt-3 text-xs text-amber-700">{w}</p>)}
      </Modal>
    );
  }

  const headers = layout?.headers ?? [];
  const taken = (key) => new Set(Object.entries(columns).filter(([k, v]) => k !== key && v !== null && v !== undefined).map(([, v]) => v));
  const shown = headerRow ? preview.slice(Math.max(0, headerRow - 2), headerRow + 6) : preview.slice(0, 8);
  const firstShown = headerRow ? Math.max(1, headerRow - 1) : 1;

  return (
    <Modal title="BOQ columns" subtitle="Tell the app where each piece of information is. Your file is not changed." size="lg" onClose={onClose}
      footer={<>
        <button className="btn btn-secondary" onClick={onClose}>Cancel</button>
        <button className="btn btn-primary" disabled={saving || !layout || (layout.format === "generic" && (ROLES.some((r) => r.required && (columns[r.key] === undefined || columns[r.key] === null)) || !headerRow))} onClick={save}>
          {saving ? "Checking…" : "Save and check"}
        </button>
      </>}>
      <ErrorNote text={error} />
      <div className="grid gap-4 sm:grid-cols-2">
        <label><span className="label">Worksheet</span>
          <select className="input" value={sheet} onChange={(e) => { setSheet(e.target.value); detect(e.target.value); }}>
            {sheets.map((s) => <option key={s.name} value={s.name}>{s.name}{s.state !== "visible" ? " (hidden)" : ""}</option>)}
          </select>
        </label>
        {layout?.format === "generic" && (
          <label><span className="label flex items-center gap-1">Heading row <InfoTip text="The row that holds the column names, such as Description, Unit and Qty." /></span>
            <input type="number" min={1} max={60} className="input" value={headerRow ?? ""}
              onChange={(e) => { const n = Number(e.target.value) || null; setHeaderRow(n); if (n) detect(sheet, n); }} />
          </label>
        )}
      </div>

      {!layout ? <Spinner /> : layout.format === "sales_bom" ? (
        <p className="mt-5 flex items-center gap-2 text-sm text-slate-700"><CheckCircle2 size={16} className="text-emerald-600" /> This worksheet uses the Sales BOM template; its columns are recognised automatically.</p>
      ) : (
        <>
          {layout.notes.map((n) => <p key={n} className="mt-4 text-[13px] text-amber-700">{n}</p>)}
          <div className="mt-5 grid gap-x-5 gap-y-3 sm:grid-cols-2">
            {ROLES.map((r) => (
              <label key={r.key}>
                <span className="label flex items-center gap-1">{r.label}{r.required && <span className="text-rose-600">*</span>} <InfoTip text={r.hint} /></span>
                <select className={`input ${r.required && (columns[r.key] === undefined || columns[r.key] === null) ? "border-rose-300" : ""}`}
                  value={columns[r.key] ?? ""} onChange={(e) => setColumns({ ...columns, [r.key]: e.target.value === "" ? null : Number(e.target.value) })}>
                  <option value="">{r.required ? "Choose a column" : "Not in this BOQ"}</option>
                  {headers.map((h, i) => !taken(r.key).has(i) && <option key={i} value={i}>{letter(i)} · {h || "(no heading)"}</option>)}
                </select>
              </label>
            ))}
          </div>
          {shown.length > 0 && (
            <div className="mt-5">
              <div className="eyebrow mb-2">Sheet preview</div>
              <div className="max-h-56 overflow-auto rounded-lg border border-slate-200">
                <table className="w-full text-xs">
                  <tbody className="divide-y divide-slate-100">
                    {shown.map((row, i) => {
                      const n = firstShown + i;
                      return (
                        <tr key={n} className={n === headerRow ? "bg-brand-50 font-semibold text-brand-900" : "text-slate-600"}>
                          <td className="w-10 px-2 py-1.5 text-right text-slate-400">{n}</td>
                          {row.slice(0, 10).map((c, j) => (
                            <td key={j} className={`max-w-[200px] truncate px-2 py-1.5 ${j === columns.description ? "bg-emerald-50/70" : ""}`} title={c}>{c}</td>
                          ))}
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}
    </Modal>
  );
}
