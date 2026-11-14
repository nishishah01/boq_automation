import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api } from "../services/api";

const Ctx = createContext(null);

function remembered(key) {
  try { const v = localStorage.getItem(key); return v ? Number(v) : null; } catch { return null; }
}

function useRemembered(key) {
  const [value, set] = useState(() => remembered(key));
  const update = (n) => {
    set(n);
    try { n === null ? localStorage.removeItem(key) : localStorage.setItem(key, String(n)); } catch { /* conveniences only */ }
  };
  return [value, update];
}

const EMPTY = { boqReady: false, masterReady: false, runStatus: null, hasApprovals: false, exported: false };

export function WorkProvider({ children }) {
  const [options, setOptions] = useState(null);
  const [boqId, setBoqId] = useRemembered("boq.upload");
  const [masterId, setMasterId] = useRemembered("boq.master");
  const [jobId, setJobId] = useRemembered("boq.job");
  const [progress, setProgress] = useState(EMPTY);

  useEffect(() => { api.options().then(setOptions).catch(() => setOptions(null)); }, []);

  const refreshProgress = useCallback(() => {
    api.progress(boqId, masterId, jobId).then(setProgress).catch(() => setProgress(EMPTY));
  }, [boqId, masterId, jobId]);

  useEffect(() => { refreshProgress(); }, [refreshProgress]);

  return (
    <Ctx.Provider value={{ options, boqId, setBoqId, masterId, setMasterId, jobId, setJobId, progress, refreshProgress }}>
      {children}
    </Ctx.Provider>
  );
}

export function useWork() {
  const c = useContext(Ctx);
  if (!c) throw new Error("useWork outside WorkProvider");
  return c;
}
