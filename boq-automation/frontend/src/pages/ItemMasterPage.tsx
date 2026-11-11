import { Fragment, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Library, Search } from "lucide-react";
import { api, errorText } from "../services/api";
import { useWork } from "../context/WorkContext";
import { Disclosure, Empty, ErrorNote, Modal, PageHeader, Spinner } from "../components/ui";
import type { ItemMasterUpload, SearchItem } from "../types";

export default function ItemMasterPage() {
  const { masterId } = useWork();
  const [master, setMaster] = useState<ItemMasterUpload | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [cst, setCst] = useState("");
  const [items, setItems] = useState<SearchItem[] | null>(null);
  const [total, setTotal] = useState(0);
  const [detail, setDetail] = useState<{ item_no: string; raw: Record<string, string> } | null>(null);

  useEffect(() => {
    api.itemMasters().then((l) => setMaster(l.find((u) => u.id === masterId) ?? null)).catch((e) => setError(errorText(e)));
  }, [masterId]);

  useEffect(() => {
    if (master?.status !== "ready") return;
    const t = setTimeout(() => {
      api.searchItems(master.id, q, cst).then((r) => { setItems(r.items); setTotal(r.total); }).catch((e) => setError(errorText(e)));
    }, 250);
    return () => clearTimeout(t);
  }, [master?.id, master?.status, q, cst]);

  if (master === undefined) return <Spinner />;
  if (!master || master.status !== "ready") {
    return (<>
      <PageHeader title="Item Master" />
      <Empty icon={<Library size={20} />} title="No Item Master loaded" text="Upload the Item Master workbook to browse its components here."
        action={<Link className="btn btn-primary" to="/upload">Go to uploads</Link>} />
    </>);
  }

  const qa = master.quality;
  const types = Object.entries(qa.by_cost_sheet_type ?? {}).sort((a, b) => b[1] - a[1]);
  const issues: [string, number | undefined, string][] = [
    ["Blank Cost Sheet Type", qa.blank_cost_sheet_type, "never selected automatically"],
    ["Same brief under several Item Nos", qa.duplicate_brief_groups, "the best-populated record is preferred"],
    ["Duplicates that disagree on Cost Sheet Type", qa.conflicting_cost_sheet_groups, "flagged for review"],
    ["Retired items (NA prefix)", qa.deprecated, "penalised and flagged"],
    ["Records with zero cost", qa.zero_cost, "estimators price these after export"],
  ];

  return (
    <>
      <PageHeader title="Item Master" subtitle={`${master.filename} · ${master.record_count.toLocaleString()} records`} />
      <ErrorNote text={error} />

      <div className="card p-5">
        <div className="flex flex-wrap gap-2">
          <div className="relative min-w-[240px] flex-1">
            <Search size={15} className="absolute left-3 top-2.5 text-slate-400" />
            <input className="input pl-9" placeholder="Search by brief or Item No." aria-label="Search Item Master" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
          <select className="input sm:!w-60" aria-label="Filter by Cost Sheet Type" value={cst} onChange={(e) => setCst(e.target.value)}>
            <option value="">All Cost Sheet Types</option>
            {types.map(([t, n]) => <option key={t} value={t}>{t} ({n.toLocaleString()})</option>)}
          </select>
        </div>
        <div className="mt-2 text-xs text-slate-500">{total.toLocaleString()} matching{items && items.length < total ? `, showing first ${items.length}` : ""}. Click a row for the full record.</div>
        <div className="mt-4 overflow-x-auto rounded-lg border border-slate-200">
          {!items ? <Spinner /> : (
            <table className="w-full">
              <thead className="table-head border-b border-slate-200 bg-slate-50/70"><tr><th>Item No.</th><th>Item Brief</th><th>Cost Sheet Type</th><th>UOM</th><th className="!text-right">Cost</th></tr></thead>
              <tbody className="divide-y divide-slate-100">
                {items.map((i) => (
                  <tr key={i.item_no} className="table-row cursor-pointer hover:bg-slate-50" onClick={() => api.itemDetail(master.id, i.item_no).then(setDetail)}>
                    <td className="whitespace-nowrap font-mono text-xs text-slate-500">{i.item_no}</td>
                    <td className="text-slate-900">{i.item_brief}</td>
                    <td className="whitespace-nowrap text-slate-600">{i.cost_sheet_type || <span className="text-amber-700">Blank</span>}</td>
                    <td className="text-slate-600">{i.uom}</td>
                    <td className="text-right tabular-nums text-slate-600">{i.cost ? i.cost.toLocaleString() : "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      <div className="card mt-5 p-5">
        <Disclosure title="Data quality notes">
          <ul className="divide-y divide-slate-100 text-sm">
            {issues.map(([label, n, how]) => (
              <li key={label} className="flex justify-between gap-4 py-2.5">
                <span className="text-slate-700">{label} <span className="text-slate-400">· {how}</span></span>
                <span className="tabular-nums font-medium text-slate-900">{(n ?? 0).toLocaleString()}</span>
              </li>
            ))}
          </ul>
        </Disclosure>
      </div>

      {detail && (
        <Modal title={detail.raw["Item Brief"] ?? detail.item_no} subtitle={detail.item_no} onClose={() => setDetail(null)}>
          <dl className="grid grid-cols-[160px_1fr] gap-x-4 gap-y-2 text-sm">
            {Object.entries(detail.raw).map(([k, v]) => (
              <Fragment key={k}><dt className="text-slate-500">{k}</dt><dd className="break-words text-slate-900">{v}</dd></Fragment>
            ))}
          </dl>
        </Modal>
      )}
    </>
  );
}
