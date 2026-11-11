import { ReactNode, useEffect, useRef, useState } from "react";
import { AlertCircle, CheckCircle2, ChevronDown, Info, Lightbulb, Loader2, MoreHorizontal, UploadCloud, X } from "lucide-react";

/* ---------- layout ---------- */

export function PageHeader({ title, subtitle, action, eyebrow }: {
  title: string; subtitle?: ReactNode; action?: ReactNode; eyebrow?: string;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        {eyebrow && <div className="eyebrow mb-1.5 text-brand-600">{eyebrow}</div>}
        <h1 className="text-[22px] font-semibold tracking-tight">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-slate-500">{subtitle}</p>}
      </div>
      {action && <div className="flex flex-wrap items-center gap-2">{action}</div>}
    </div>
  );
}

/** Several figures in one quiet strip, instead of a row of separate cards. */
export function MetricStrip({ items }: { items: { label: string; value: ReactNode; tone?: "good" | "warn" | "bad"; hint?: string }[] }) {
  const tone = { good: "text-emerald-700", warn: "text-amber-700", bad: "text-rose-700" };
  return (
    <div className="card grid grid-cols-2 divide-slate-100 sm:flex sm:divide-x">
      {items.map((m) => (
        <div key={m.label} className="flex-1 px-5 py-4">
          <div className="flex items-center gap-1 text-[13px] text-slate-500">{m.label}{m.hint && <InfoTip text={m.hint} />}</div>
          <div className={`mt-1 text-2xl font-semibold tabular-nums ${m.tone ? tone[m.tone] : "text-slate-900"}`}>{m.value}</div>
        </div>
      ))}
    </div>
  );
}

export function Empty({ icon, title, text, action }: { icon?: ReactNode; title: string; text?: string; action?: ReactNode }) {
  return (
    <div className="card flex flex-col items-center px-6 py-14 text-center">
      {icon && <div className="mb-3 rounded-full bg-brand-50 p-3 text-brand-600">{icon}</div>}
      <div className="text-[15px] font-semibold text-slate-900">{title}</div>
      {text && <p className="mt-1 max-w-md text-sm text-slate-500">{text}</p>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}

export function Spinner({ text = "Loading" }: { text?: string }) {
  return <div className="flex items-center gap-2 p-8 text-sm text-slate-500"><Loader2 size={16} className="animate-spin" /> {text}</div>;
}

export function ErrorNote({ text }: { text: string | null }) {
  if (!text) return null;
  return (
    <div className="mb-5 flex items-start gap-2.5 rounded-lg border border-rose-200 bg-rose-50 px-3.5 py-2.5 text-sm text-rose-800">
      <AlertCircle size={16} className="mt-0.5 shrink-0" /> <span>{text}</span>
    </div>
  );
}

/* ---------- status ---------- */

const STATUS: Record<string, [string, string]> = {
  // label, dot colour
  matched: ["Matched", "bg-brand-500"], approved: ["Approved", "bg-emerald-500"], verified: ["Verified", "bg-emerald-500"],
  completed: ["Completed", "bg-emerald-500"], ready: ["Ready", "bg-emerald-500"],
  review: ["Review required", "bg-amber-500"], pending: ["Pending", "bg-slate-400"], queued: ["Queued", "bg-slate-400"],
  running: ["Running", "bg-brand-500"], processing: ["Processing", "bg-brand-500"],
  unmatched: ["Unmatched", "bg-rose-500"], rejected: ["Rejected", "bg-rose-500"], failed: ["Failed", "bg-rose-500"],
  skipped: ["Out of scope", "bg-slate-300"], preserved: ["Already mapped", "bg-slate-300"],
};

export function Status({ status }: { status: string }) {
  const [label, dot] = STATUS[status] ?? [status, "bg-slate-400"];
  const pulse = status === "running" || status === "processing";
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap text-[13px] font-medium text-slate-700">
      <span className={`h-1.5 w-1.5 rounded-full ${dot} ${pulse ? "animate-pulse" : ""}`} />{label}
    </span>
  );
}

export function Progress({ value, tone = "brand" }: { value: number; tone?: "brand" | "good" }) {
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-100">
      <div className={`h-full rounded-full transition-all duration-500 ${tone === "good" ? "bg-emerald-500" : "bg-brand-500"}`}
        style={{ width: `${Math.max(0, Math.min(100, value))}%` }} />
    </div>
  );
}

/* ---------- guidance ---------- */

export function InfoTip({ text }: { text: string }) {
  return (
    <span className="group relative inline-flex">
      <Info size={13} className="cursor-help text-slate-400" tabIndex={0} aria-label={text} />
      <span role="tooltip" className="pointer-events-none absolute bottom-full left-1/2 z-50 mb-2 w-60 -translate-x-1/2 rounded-lg bg-slate-900
        px-3 py-2 text-xs font-normal normal-case leading-relaxed tracking-normal text-white opacity-0 shadow-pop transition-opacity
        group-hover:opacity-100 group-focus-within:opacity-100">{text}</span>
    </span>
  );
}

/** A small, dismissible hint. Dismissal is remembered in this browser. */
export function Guide({ id, children }: { id: string; children: ReactNode }) {
  const key = `guide.${id}`;
  const [open, setOpen] = useState(() => { try { return localStorage.getItem(key) !== "hidden"; } catch { return true; } });
  if (!open) return null;
  return (
    <div className="mb-6 flex items-start gap-3 rounded-xl border border-brand-100 bg-brand-50/60 px-4 py-3 text-sm text-brand-900 animate-fade-in">
      <Lightbulb size={16} className="mt-0.5 shrink-0 text-brand-600" />
      <div className="flex-1">{children}</div>
      <button className="text-brand-700/60 hover:text-brand-900" aria-label="Dismiss tip"
        onClick={() => { setOpen(false); try { localStorage.setItem(key, "hidden"); } catch { /* convenience only */ } }}>
        <X size={15} />
      </button>
    </div>
  );
}

/* ---------- overlays ---------- */

function useEscape(onClose: () => void) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);
}

export function Modal({ title, subtitle, onClose, children, footer, size = "md" }: {
  title: string; subtitle?: ReactNode; onClose: () => void; children: ReactNode; footer?: ReactNode; size?: "sm" | "md" | "lg";
}) {
  useEscape(onClose);
  const width = { sm: "max-w-md", md: "max-w-xl", lg: "max-w-3xl" }[size];
  return (
    <div className="fixed inset-0 z-40 flex items-start justify-center overflow-y-auto bg-slate-900/30 p-4 pt-[10vh] backdrop-blur-[2px] animate-fade-in" onMouseDown={onClose}>
      <div className={`w-full ${width} rounded-2xl bg-white shadow-pop animate-rise-in`} onMouseDown={(e) => e.stopPropagation()} role="dialog" aria-label={title}>
        <div className="flex items-start justify-between gap-4 px-6 pb-2 pt-5">
          <div className="min-w-0"><h2 className="text-base font-semibold">{title}</h2>
            {subtitle && <div className="mt-0.5 text-sm text-slate-500">{subtitle}</div>}</div>
          <button onClick={onClose} aria-label="Close" className="btn-icon"><X size={18} /></button>
        </div>
        <div className="px-6 pb-6 pt-2">{children}</div>
        {footer && <div className="flex justify-end gap-2 rounded-b-2xl border-t border-slate-100 bg-slate-50/60 px-6 py-3.5">{footer}</div>}
      </div>
    </div>
  );
}

export function Drawer({ title, subtitle, onClose, children, footer }: {
  title: string; subtitle?: ReactNode; onClose: () => void; children: ReactNode; footer?: ReactNode;
}) {
  useEscape(onClose);
  return (
    <div className="fixed inset-0 z-40 bg-slate-900/25 backdrop-blur-[1px] animate-fade-in" onMouseDown={onClose}>
      <aside className="absolute inset-y-0 right-0 flex w-full max-w-xl flex-col bg-white shadow-pop animate-slide-in"
        onMouseDown={(e) => e.stopPropagation()} role="dialog" aria-label={title}>
        <div className="flex items-start justify-between gap-4 border-b border-slate-100 px-6 py-4">
          <div className="min-w-0"><h2 className="text-base font-semibold">{title}</h2>
            {subtitle && <div className="mt-0.5 text-sm text-slate-500">{subtitle}</div>}</div>
          <button onClick={onClose} aria-label="Close" className="btn-icon"><X size={18} /></button>
        </div>
        <div className="flex-1 overflow-y-auto px-6 py-5">{children}</div>
        {footer && <div className="flex justify-end gap-2 border-t border-slate-100 px-6 py-3.5">{footer}</div>}
      </aside>
    </div>
  );
}

/** A compact menu for secondary actions, so rows show only the main ones. */
export function Menu({ items, label = "More actions" }: {
  items: { label: string; icon?: ReactNode; onClick: () => void; danger?: boolean; hidden?: boolean }[]; label?: string;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const h = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, [open]);
  return (
    <div className="relative" ref={ref}>
      <button className="btn-icon" aria-label={label} aria-expanded={open} onClick={() => setOpen(!open)}><MoreHorizontal size={17} /></button>
      {open && (
        <div className="absolute right-0 z-30 mt-1 min-w-[200px] rounded-xl border border-slate-200 bg-white p-1 shadow-pop animate-rise-in" role="menu">
          {items.filter((i) => !i.hidden).map((i) => (
            <button key={i.label} role="menuitem" onClick={() => { setOpen(false); i.onClick(); }}
              className={`flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-left text-sm ${i.danger ? "text-rose-700 hover:bg-rose-50" : "text-slate-700 hover:bg-slate-50"}`}>
              {i.icon}{i.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export function Disclosure({ title, children, defaultOpen = false }: { title: ReactNode; children: ReactNode; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div>
      <button className="flex items-center gap-1.5 text-sm font-medium text-slate-600 hover:text-slate-900" onClick={() => setOpen(!open)} aria-expanded={open}>
        <ChevronDown size={15} className={`transition-transform ${open ? "" : "-rotate-90"}`} />{title}
      </button>
      {open && <div className="mt-3 animate-fade-in">{children}</div>}
    </div>
  );
}

/* ---------- inputs ---------- */

export function Segmented<T extends string>({ value, onChange, options }: {
  value: T; onChange: (v: T) => void; options: { value: T; label: string; count?: number }[];
}) {
  return (
    <div className="inline-flex flex-wrap rounded-lg bg-slate-100 p-1" role="tablist">
      {options.map((o) => (
        <button key={o.value} role="tab" aria-selected={value === o.value} onClick={() => onChange(o.value)}
          className={`rounded-md px-3 py-1.5 text-[13px] font-medium transition ${value === o.value ? "bg-white text-slate-900 shadow-sm" : "text-slate-500 hover:text-slate-800"}`}>
          {o.label}{o.count !== undefined && <span className="ml-1.5 tabular-nums text-slate-400">{o.count}</span>}
        </button>
      ))}
    </div>
  );
}

export function FileDrop({ accept, label, hint, onFile, busy, progress, compact }: {
  accept: string; label: string; hint?: string; onFile: (f: File) => void; busy: boolean; progress: number; compact?: boolean;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  return (
    <button type="button" disabled={busy} onClick={() => input.current?.click()}
      onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
      onDrop={(e) => { e.preventDefault(); setOver(false); const f = e.dataTransfer.files[0]; if (f) onFile(f); }}
      className={`flex w-full flex-col items-center gap-2 rounded-xl border-2 border-dashed text-sm transition
        ${compact ? "p-5" : "p-9"} ${over ? "border-brand-400 bg-brand-50" : "border-slate-200 bg-slate-50/50 hover:border-brand-300 hover:bg-brand-50/30"}`}>
      <div className="rounded-full bg-white p-2.5 text-brand-600 shadow-sm">
        {busy ? <Loader2 size={18} className="animate-spin" /> : <UploadCloud size={18} />}
      </div>
      <span className="font-medium text-slate-800">{busy ? `Uploading ${progress}%` : label}</span>
      <span className="text-xs text-slate-500">{hint ?? `${accept} files`}</span>
      <input ref={input} type="file" accept={accept} hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) onFile(f); e.target.value = ""; }} />
    </button>
  );
}

export function FileSummary({ name, meta, status, action }: { name: string; meta: string; status: string; action?: ReactNode }) {
  return (
    <div className="flex items-center gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3">
      <div className="rounded-lg bg-emerald-50 p-2 text-emerald-600"><CheckCircle2 size={18} /></div>
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium text-slate-900">{name}</div>
        <div className="text-xs text-slate-500">{meta}</div>
      </div>
      <Status status={status} />
      {action}
    </div>
  );
}

export function PreviewTable({ rows, maxCols = 14 }: { rows: string[][]; maxCols?: number }) {
  return (
    <div className="max-h-[60vh] overflow-auto rounded-lg border border-slate-200">
      <table className="w-full text-xs">
        <tbody className="divide-y divide-slate-100">
          {rows.map((r, i) => (
            <tr key={i} className={i < 2 ? "sticky top-0 bg-slate-50 font-medium text-slate-700" : "text-slate-600"}>
              {r.slice(0, maxCols).map((c, j) => <td key={j} className="max-w-[220px] truncate px-2.5 py-1.5" title={c}>{c}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Confidence({ value, threshold }: { value: number; threshold: number }) {
  const low = value < threshold;
  return (
    <div className="flex items-center gap-2.5">
      <div className="w-24"><Progress value={value} tone={low ? "brand" : "good"} /></div>
      <span className={`text-sm tabular-nums ${low ? "font-semibold text-amber-700" : "text-slate-700"}`}>{value}%</span>
      {low && <span className="text-xs text-amber-700">below the {threshold}% threshold</span>}
    </div>
  );
}

export const dateText = (s: string) => new Date(s).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });

export const engineName = (mode: string) =>
  ({ claude: "Claude", openai: "OpenAI", gemini: "Gemini", manual: "Manual", mock: "Rule-based", rule: "Rule-based" } as Record<string, string>)[mode] ?? "Rule-based";

export const COST_SHEET_TIP = "Taken directly from this component's Item Master record. It is never guessed and is written to column H on export.";
