import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ArrowRight, Sparkles } from "lucide-react";
import { api, errorText } from "../services/api";
import { useWork } from "../context/WorkContext";
import { dateText, Empty, engineName, ErrorNote, MetricStrip, PageHeader, Spinner, Status } from "../components/ui";
import type { Dashboard } from "../types";

export default function DashboardPage() {
  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { setJobId, progress } = useWork();
  const navigate = useNavigate();
  useEffect(() => { api.dashboard().then(setData).catch((e) => setError(errorText(e))); }, []);

  const next = !progress.boqReady || !progress.masterReady
    ? { title: "Upload your BOQ and Item Master", text: "Both workbooks are needed before mapping can start.", to: "/upload", cta: "Upload files" }
    : progress.runStatus !== "completed"
      ? { title: "Run AI component mapping", text: "Choose the Cost Sheet Types and let the engine match each BOQ line.", to: "/mapping", cta: "Start mapping" }
      : !progress.exported
        ? { title: "Review and approve the suggestions", text: "Only approved components are written to the exported BOQ.", to: "/review", cta: "Open review" }
        : { title: "Your BOQ is exported", text: "Download it from Export, or start a new run.", to: "/exports", cta: "View exports" };

  return (
    <>
      <PageHeader title="Overview" subtitle="All BOQ processing in this workspace" />
      <ErrorNote text={error} />

      <div className="card mb-6 flex flex-wrap items-center gap-5 bg-gradient-to-br from-white to-brand-50/50 p-6">
        <div className="rounded-xl bg-brand-600 p-3 text-white shadow-sm"><Sparkles size={20} /></div>
        <div className="min-w-0 flex-1">
          <div className="eyebrow text-brand-600">Next step</div>
          <div className="mt-0.5 text-[17px] font-semibold text-slate-900">{next.title}</div>
          <div className="text-sm text-slate-500">{next.text}</div>
        </div>
        <button className="btn btn-primary" onClick={() => navigate(next.to)}>{next.cta} <ArrowRight size={15} /></button>
      </div>

      {!data ? !error && <Spinner /> : (
        <>
          <MetricStrip items={[
            { label: "BOQ files", value: data.boq_files },
            { label: "Descriptions mapped", value: data.parent_descriptions },
            { label: "Components suggested", value: data.components_mapped },
            { label: "Approved", value: data.approved, tone: "good" },
            { label: "Awaiting review", value: data.pending, tone: data.pending ? "warn" : undefined },
          ]} />

          <h2 className="mb-3 mt-8 text-[15px] font-semibold">Recent runs</h2>
          {data.recent.length === 0 ? <Empty title="No runs yet" text="Runs appear here once mapping has started." action={<Link className="btn btn-primary" to="/upload">Get started</Link>} /> : (
            <div className="card overflow-hidden">
              <table className="w-full">
                <thead className="table-head border-b border-slate-200 bg-slate-50/70"><tr><th>Run</th><th>BOQ</th><th className="hidden sm:table-cell">Engine</th><th>Status</th><th className="hidden md:table-cell">Started</th><th /></tr></thead>
                <tbody className="divide-y divide-slate-100">
                  {data.recent.map((j) => (
                    <tr key={j.id} className="table-row hover:bg-slate-50/70">
                      <td className="font-medium text-slate-900">#{j.id}</td>
                      <td className="max-w-[280px] truncate text-slate-600" title={j.filename}>{j.filename}</td>
                      <td className="hidden text-slate-600 sm:table-cell">{engineName(j.mode)}</td>
                      <td><Status status={j.status} /></td>
                      <td className="hidden whitespace-nowrap text-slate-500 md:table-cell">{dateText(j.created_at)}</td>
                      <td className="text-right"><button className="btn btn-ghost btn-sm" onClick={() => { setJobId(j.id); navigate("/review"); }}>Open</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </>
  );
}
