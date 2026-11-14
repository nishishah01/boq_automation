import { createContext, useCallback, useContext, useRef, useState } from "react";
import { AlertCircle, CheckCircle2, X } from "lucide-react";
import { Modal } from "./ui";

const Ctx = createContext(null);

export function FeedbackProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const [pending, setPending] = useState(null);
  const resolver = useRef();
  const next = useRef(0);

  const notify = useCallback((text, kind = "success") => {
    const id = ++next.current;
    setToasts((t) => [...t, { id, kind, text }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === "error" ? 7000 : 3500);
  }, []);

  const confirm = useCallback((c) => {
    setPending(c);
    return new Promise((resolve) => { resolver.current = resolve; });
  }, []);

  const close = (ok) => { resolver.current?.(ok); setPending(null); };

  return (
    <Ctx.Provider value={{ notify, confirm }}>
      {children}
      <div className="pointer-events-none fixed bottom-5 right-5 z-50 flex w-[360px] max-w-[calc(100vw-2.5rem)] flex-col gap-2" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className="pointer-events-auto flex items-start gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm shadow-pop animate-rise-in">
            {t.kind === "success" ? <CheckCircle2 size={17} className="mt-0.5 shrink-0 text-emerald-600" />
              : <AlertCircle size={17} className="mt-0.5 shrink-0 text-rose-600" />}
            <span className="flex-1 text-slate-700">{t.text}</span>
            <button aria-label="Dismiss" className="text-slate-400 hover:text-slate-700" onClick={() => setToasts((x) => x.filter((y) => y.id !== t.id))}><X size={14} /></button>
          </div>
        ))}
      </div>
      {pending && (
        <Modal title={pending.title} size="sm" onClose={() => close(false)}
          footer={<>
            <button className="btn btn-secondary" onClick={() => close(false)}>Cancel</button>
            <button className={`btn ${pending.danger ? "bg-rose-600 text-white hover:bg-rose-700" : "btn-primary"}`} onClick={() => close(true)} autoFocus>{pending.action}</button>
          </>}>
          <div className="text-sm text-slate-600">{pending.text}</div>
        </Modal>
      )}
    </Ctx.Provider>
  );
}

export function useFeedback() {
  const c = useContext(Ctx);
  if (!c) throw new Error("useFeedback outside FeedbackProvider");
  return c;
}
