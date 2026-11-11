import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from "react";
import { api } from "../services/api";
import type { Options } from "../types";

/** Where the user is in the workflow, derived from real backend data. */
export interface WorkflowProgress {
  boqReady: boolean;
  masterReady: boolean;
  runStatus: string | null; // status of the selected run
  hasApprovals: boolean;
  exported: boolean; // the selected run has a verified export
}

interface Work {
  options: Options | null;
  boqId: number | null; setBoqId: (n: number | null) => void;
  masterId: number | null; setMasterId: (n: number | null) => void;
  jobId: number | null; setJobId: (n: number | null) => void;
  progress: WorkflowProgress;
  refreshProgress: () => void;
}

const Ctx = createContext<Work | null>(null);

function remembered(key: string): number | null {
  try { const v = localStorage.getItem(key); return v ? Number(v) : null; } catch { return null; }
}

function useRemembered(key: string) {
  const [value, set] = useState<number | null>(() => remembered(key));
  const update = (n: number | null) => {
    set(n);
    try { n === null ? localStorage.removeItem(key) : localStorage.setItem(key, String(n)); } catch { /* conveniences only */ }
  };
  return [value, update] as const;
}

const EMPTY: WorkflowProgress = { boqReady: false, masterReady: false, runStatus: null, hasApprovals: false, exported: false };

export function WorkProvider({ children }: { children: ReactNode }) {
  const [options, setOptions] = useState<Options | null>(null);
  const [boqId, setBoqId] = useRemembered("boq.upload");
  const [masterId, setMasterId] = useRemembered("boq.master");
  const [jobId, setJobId] = useRemembered("boq.job");
  const [progress, setProgress] = useState<WorkflowProgress>(EMPTY);

  useEffect(() => { api.options().then(setOptions).catch(() => setOptions(null)); }, []);

  const refreshProgress = useCallback(() => {
    Promise.all([
      api.boqUploads().catch(() => []), api.itemMasters().catch(() => []),
      jobId ? api.job(jobId).catch(() => null) : Promise.resolve(null),
      jobId ? api.descriptions(jobId).catch(() => []) : Promise.resolve([]),
      api.exports().catch(() => []),
    ]).then(([boqs, masters, job, descs, exports]) => {
      setProgress({
        boqReady: boqs.some((b) => b.id === boqId),
        masterReady: masters.some((m) => m.id === masterId && m.status === "ready"),
        runStatus: job?.status ?? null,
        hasApprovals: descs.some((d) => d.mappings.some((m) => m.status === "approved")),
        exported: exports.some((e) => e.job_id === jobId && e.status === "verified"),
      });
    });
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
