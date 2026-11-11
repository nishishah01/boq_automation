import { Check, LayoutGrid, Library, Menu, Settings, X } from "lucide-react";
import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { useWork } from "../context/WorkContext";

export default function AppLayout() {
  const { options, progress, refreshProgress } = useWork();
  const [open, setOpen] = useState(false);
  const location = useLocation();
  useEffect(() => { refreshProgress(); setOpen(false); }, [location.pathname]); // eslint-disable-line

  const p = progress;
  const steps = [
    { to: "/upload", label: "Upload files", done: p.boqReady && p.masterReady, hint: "BOQ and Item Master" },
    { to: "/mapping", label: "Run mapping", done: p.runStatus === "completed", hint: "Choose scope and match" },
    { to: "/review", label: "Review & approve", done: p.hasApprovals, hint: "Check each component" },
    { to: "/exports", label: "Export BOQ", done: p.exported, hint: "Download the workbook" },
  ];
  const current = steps.findIndex((s) => !s.done);

  const nav = (
    <nav className="flex h-full flex-col">
      <div className="flex items-center gap-2.5 px-5 pb-6 pt-5">
        <div className="grid h-8 w-8 place-items-center rounded-lg bg-gradient-to-br from-brand-500 to-brand-700 text-[13px] font-bold text-white shadow-sm">BQ</div>
        <div className="leading-tight">
          <div className="text-[14px] font-semibold text-slate-900">BOQ Mapping</div>
          <div className="text-xs text-slate-500">Component automation</div>
        </div>
      </div>

      <div className="eyebrow px-5 pb-2">Workflow</div>
      <ol className="space-y-0.5 px-3">
        {steps.map((s, i) => (
          <li key={s.to}>
            <NavLink to={s.to} className={({ isActive }) => `group flex items-center gap-3 rounded-lg px-2.5 py-2 transition
              ${isActive ? "bg-white shadow-card ring-1 ring-slate-200/70" : "hover:bg-white/70"}`}>
              {({ isActive }) => (<>
                <span className={`grid h-6 w-6 shrink-0 place-items-center rounded-full text-[11px] font-semibold transition
                  ${s.done ? "bg-emerald-500 text-white" : i === current ? "bg-brand-600 text-white" : "bg-slate-200 text-slate-500"}`}>
                  {s.done ? <Check size={13} strokeWidth={3} /> : i + 1}
                </span>
                <span className="min-w-0">
                  <span className={`block text-[13.5px] ${isActive ? "font-semibold text-slate-900" : "font-medium text-slate-700"}`}>{s.label}</span>
                  <span className="block truncate text-xs text-slate-400">{i === current && !isActive ? "Next step" : s.hint}</span>
                </span>
              </>)}
            </NavLink>
          </li>
        ))}
      </ol>

      <div className="eyebrow px-5 pb-2 pt-7">Workspace</div>
      <div className="space-y-0.5 px-3">
        {[{ to: "/", label: "Overview", icon: LayoutGrid }, { to: "/item-master", label: "Item Master", icon: Library },
          { to: "/settings", label: "Settings", icon: Settings }].map(({ to, label, icon: Icon }) => (
          <NavLink key={to} to={to} end={to === "/"} className={({ isActive }) => `flex items-center gap-3 rounded-lg px-2.5 py-2 text-[13.5px] transition
            ${isActive ? "bg-white font-semibold text-slate-900 shadow-card ring-1 ring-slate-200/70" : "font-medium text-slate-600 hover:bg-white/70"}`}>
            <Icon size={16} className="text-slate-400" /> {label}
          </NavLink>
        ))}
      </div>

     
    </nav>
  );

  return (
    <div className="min-h-screen lg:flex">
      <header className="sticky top-0 z-30 flex items-center gap-3 border-b border-slate-200 bg-white/90 px-4 py-3 backdrop-blur lg:hidden">
        <button className="btn-icon" aria-label="Open menu" onClick={() => setOpen(true)}><Menu size={20} /></button>
        <span className="text-sm font-semibold text-slate-900">BOQ Mapping</span>
      </header>
      {open && (
        <div className="fixed inset-0 z-40 bg-slate-900/30 lg:hidden animate-fade-in" onClick={() => setOpen(false)}>
          <aside className="relative h-full w-72 bg-slate-50 shadow-pop" onClick={(e) => e.stopPropagation()}>
            <button className="btn-icon absolute right-3 top-3" aria-label="Close menu" onClick={() => setOpen(false)}><X size={18} /></button>
            {nav}
          </aside>
        </div>
      )}
      <aside className="hidden border-r border-slate-200/80 bg-slate-50/80 lg:sticky lg:top-0 lg:block lg:h-screen lg:w-64 lg:shrink-0">{nav}</aside>
      <main className="min-w-0 flex-1 px-4 py-6 sm:px-8 lg:py-9">
        <div className="mx-auto max-w-6xl"><Outlet /></div>
      </main>
    </div>
  );
}
