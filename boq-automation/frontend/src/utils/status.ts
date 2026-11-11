import type { Description, Mapping } from "../types";

/** The five statuses users see, used the same way on every page. */
export type LineStatus = "matched" | "review" | "approved" | "unmatched" | "skipped" | "preserved";

export const live = (d: Description) => d.mappings.filter((m) => m.status !== "incorrect");

export function lineStatus(d: Description): LineStatus {
  if (d.status === "skipped") return "skipped";
  if (d.status === "mapped") return "preserved";
  const ms = live(d);
  if (!ms.length) return "unmatched";
  if (ms.every((m) => m.status === "approved")) return d.unmatched.length || d.flagged ? "review" : "approved";
  if (d.flagged || d.unmatched.length || ms.some((m) => m.status === "review")) return "review";
  return "matched";
}

export function componentStatus(m: Mapping): "approved" | "review" | "pending" | "rejected" {
  if (m.status === "incorrect") return "rejected";
  return m.status;
}

export const inScope = (d: Description) => d.status !== "skipped" && d.status !== "mapped";

export function approvalText(d: Description): string {
  const ms = live(d);
  if (!ms.length) return "";
  const n = ms.filter((m) => m.status === "approved").length;
  return n === ms.length ? "" : `${n} of ${ms.length} approved`; // the status already says "Approved"
}
