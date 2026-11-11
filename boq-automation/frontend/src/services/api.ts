import axios, { AxiosError } from "axios";
import type {
  BoqUpload, Dashboard, Description, ExportRecord, ItemMasterUpload, Job, Mapping, Options, SearchItem,
} from "../types";

const http = axios.create({ baseURL: "/api" });

export function errorText(e: unknown): string {
  const err = e as AxiosError<{ detail?: string }>;
  return err.response?.data?.detail ?? err.message ?? "Request failed";
}

const get = <T,>(url: string, params?: object) => http.get<T>(url, { params }).then((r) => r.data);
const post = <T,>(url: string, body?: object) => http.post<T>(url, body ?? {}).then((r) => r.data);

function upload<T>(url: string, file: File, onProgress: (pct: number) => void) {
  const form = new FormData();
  form.append("file", file);
  return http.post<T>(url, form, {
    onUploadProgress: (ev) => onProgress(Math.round((ev.loaded / (ev.total ?? ev.loaded)) * 100)),
  }).then((r) => r.data);
}

export const api = {
  options: () => get<Options>("/config/options"),
  dashboard: () => get<Dashboard>("/dashboard"),

  boqUploads: () => get<BoqUpload[]>("/uploads/boq"),
  uploadBoq: (f: File, p: (n: number) => void) => upload<BoqUpload>("/uploads/boq", f, p),
  boqPreview: (id: number, sheet?: string) => get<{ sheet: string; rows: string[][] }>(`/uploads/boq/${id}/preview`, { sheet }),

  itemMasters: () => get<ItemMasterUpload[]>("/uploads/item-master"),
  uploadItemMaster: (f: File, p: (n: number) => void) => upload<ItemMasterUpload>("/uploads/item-master", f, p),
  searchItems: (id: number, q: string, cost_sheet_type = "", limit = 50, offset = 0, types: string[] = []) =>
    get<{ total: number; items: SearchItem[] }>(`/item-master/${id}/search`, { q, cost_sheet_type, limit, offset, types: types.join(",") }),
  itemDetail: (id: number, itemNo: string) =>
    get<{ item_no: string; raw: Record<string, string> }>(`/item-master/${id}/items/${encodeURIComponent(itemNo)}`),

  jobs: () => get<Job[]>("/jobs"),
  job: (id: number) => get<Job>(`/jobs/${id}`),
  createJob: (body: object) => post<Job>("/jobs", body),
  descriptions: (id: number) => get<Description[]>(`/jobs/${id}/descriptions`),

  approve: (id: number) => post<Mapping>(`/mappings/${id}/approve`),
  markIncorrect: (id: number, note: string) => post<Mapping>(`/mappings/${id}/incorrect`, { note }),
  remove: (id: number) => http.delete(`/mappings/${id}`),
  replace: (id: number, item_no: string, note = "") => post<Mapping>(`/mappings/${id}/replace`, { item_no, note }),
  addMapping: (descId: number, item_no: string) => post<Mapping>(`/descriptions/${descId}/mappings`, { item_no }),
  approveAll: (descId: number) => post<Description>(`/descriptions/${descId}/approve-all`),
  regenerate: (descId: number) => post<Description>(`/descriptions/${descId}/regenerate`),
  flag: (descId: number, flagged: boolean, note: string) => post<Description>(`/descriptions/${descId}/flag`, { flagged, note }),
  editRequirements: (descId: number, specification: string, engineer_note: string) =>
    http.patch<Description>(`/descriptions/${descId}/requirements`, { specification, engineer_note }).then((r) => r.data),

  exportJob: (id: number) => post<ExportRecord>(`/jobs/${id}/export`),
  exports: () => get<ExportRecord[]>("/exports"),
  downloadUrl: (id: number, kind: "boq" | "mapping" | "unmatched") => `/api/exports/${id}/download/${kind}`,
};
