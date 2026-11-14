export const live = (d) => d.mappings.filter((m) => m.status !== "incorrect");

export function lineStatus(d) {
  if (d.status === "skipped") return "skipped";
  if (d.status === "incomplete") return "incomplete";
  if (d.status === "mapped") return "preserved";
  const ms = live(d);
  if (d.status === "scope_review" && !ms.length) return "scope_review";
  if (!ms.length) return "unmatched";
  if (ms.every((m) => m.status === "approved")) return d.unmatched.length || d.flagged ? "review" : "approved";
  if (d.flagged || d.unmatched.length || ms.some((m) => m.status === "review")) return "review";
  return "matched";
}

export function componentStatus(m) {
  if (m.status === "incorrect") return "rejected";
  return m.status;
}

export const inScope = (d) => d.status !== "skipped" && d.status !== "mapped" && d.status !== "incomplete";
/** Skipped because its own unit or quantity is missing or invalid: never gets components. */
export const missingUnitQty = (d) => d.status === "incomplete";

export function approvalText(d) {
  const ms = live(d);
  if (!ms.length) return "";
  const n = ms.filter((m) => m.status === "approved").length;
  return n === ms.length ? "" : `${n} of ${ms.length} approved`; // the status already says "Approved"
}
