import axios from "axios";

const http = axios.create({ baseURL: "/api" });

export function errorText(e) {
  return e?.response?.data?.detail ?? e?.message ?? "Request failed";
}

const get = (url, params) => http.get(url, { params }).then((r) => r.data);
const post = (url, body) => http.post(url, body ?? {}).then((r) => r.data);

function upload(url, file, onProgress = () => {}, method = "post") {
  const form = new FormData();
  form.append("file", file);
  return http[method](url, form, {
    onUploadProgress: (ev) => onProgress(Math.round((ev.loaded / (ev.total ?? ev.loaded)) * 100)),
  }).then((r) => r.data);
}

export const api = {
  options: () => get("/config/options"),
  dashboard: () => get("/dashboard"),
  progress: (boq, master, job) => get("/progress", { boq: boq ?? undefined, master: master ?? undefined, job: job ?? undefined }),

  boqUploads: () => get("/uploads/boq"),
  uploadBoq: (f, p) => upload("/uploads/boq", f, p),
  boqPreview: (id, sheet) => get(`/uploads/boq/${id}/preview`, { sheet }),

  boqLayout: (id, sheet, header_row) =>
    get(`/uploads/boq/${id}/layout`, { sheet, header_row }),
  saveBoqLayout: (id, body) =>
    http.put(`/uploads/boq/${id}/layout`, body).then((r) => r.data),

  itemMasters: () => get("/uploads/item-master"),
  uploadItemMaster: (f, p) => upload("/uploads/item-master", f, p),
  searchItems: (id, q, cost_sheet_type = "", limit = 50, offset = 0, types = []) =>
    get(`/item-master/${id}/search`, { q, cost_sheet_type, limit, offset, types: types.join(",") }),
  itemDetail: (id, itemNo) =>
    get(`/item-master/${id}/items/${encodeURIComponent(itemNo)}`),

  jobs: () => get("/jobs"),
  job: (id) => get(`/jobs/${id}`),
  createJob: (body) => post("/jobs", body),
  descriptions: (id) => get(`/jobs/${id}/descriptions`),

  approve: (id) => post(`/mappings/${id}/approve`),
  markIncorrect: (id, note) => post(`/mappings/${id}/incorrect`, { note }),
  remove: (id) => http.delete(`/mappings/${id}`),
  replace: (id, item_no, note = "") => post(`/mappings/${id}/replace`, { item_no, note }),
  addMapping: (descId, item_no) => post(`/descriptions/${descId}/mappings`, { item_no }),
  approveAll: (descId) => post(`/descriptions/${descId}/approve-all`),
  regenerate: (descId) => post(`/descriptions/${descId}/regenerate`),
  flag: (descId, flagged, note) => post(`/descriptions/${descId}/flag`, { flagged, note }),
  editRequirements: (descId, specification, engineer_note) =>
    http.patch(`/descriptions/${descId}/requirements`, { specification, engineer_note }).then((r) => r.data),

  reclassify: (id, cost_sheet_type, note = "") => post(`/mappings/${id}/cost-sheet-type`, { cost_sheet_type, note }),


  historical: (q = "", status = "") => get("/historical", { q, status }),
  historicalOne: (id) => get(`/historical/${id}`),
  uploadHistorical: (f, p) => upload("/historical", f, p),
  replaceHistorical: (id, f, p) => upload(`/historical/${id}/file`, f, p, "put"),
  reprocessHistorical: (id) => post(`/historical/${id}/reprocess`),
  reprocessAllHistorical: () => post("/historical/reprocess-all"),
  deleteHistorical: (id) => http.delete(`/historical/${id}`).then((r) => r.data),
  historicalMappings: (id, q = "", limit = 50, offset = 0, scope = "highside") =>
    get(`/historical/${id}/mappings`, { q, limit, offset, scope }),
  retryFailedHistorical: () => post("/historical/retry-failed"),
  startScan: (root_path, reprocess_changed = true) => post("/historical/scans", { root_path, reprocess_changed }),
  scans: () => get("/historical/scans"),
  scan: (id, status = "") => get(`/historical/scans/${id}`, { status }),
  knowledgeMappings: (params) => get("/knowledge/mappings", params),
  knowledgeParent: (id) => get(`/knowledge/parents/${id}`),
  rebuildIndex: () => post("/knowledge/index/rebuild"),
  knowledgeStats: () => get("/knowledge/stats"),
  knowledgeSearch: (q) => get("/knowledge/search", { q }),

  exportJob: (id) => post(`/jobs/${id}/export`),
  exports: () => get("/exports"),
  downloadUrl: (id, kind) => `/api/exports/${id}/download/${kind}`,
};
