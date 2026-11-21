import type { JobStatus } from "./api";

/** 83 -> "1:23" */
export function mmss(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** Seconds since the job started (the server's timestamps are UTC without a zone suffix). */
export function elapsedSeconds(startedAt: string | null | undefined, now = Date.now()): number {
  if (!startedAt) return 0;
  const t = Date.parse(/[zZ]|[+-]\d\d:?\d\d$/.test(startedAt) ? startedAt : startedAt + "Z");
  return Number.isNaN(t) ? 0 : Math.max(0, (now - t) / 1000);
}

/** One line describing where a running sync is: "Step 1 of 2 · Running the model · 3:12". */
export function progressLine(job: Pick<JobStatus, "stage" | "step" | "steps">, elapsed: number): string {
  const parts: string[] = [];
  if (job.steps && job.steps > 1 && job.step) parts.push(`Step ${job.step} of ${job.steps}`);
  parts.push(job.stage ?? "Working");
  parts.push(mmss(elapsed));
  return parts.join(" · ");
}

/** A hint for the slow step, shown once it has been running a while. */
export function slowHint(job: Pick<JobStatus, "stage" | "detail">, elapsed: number): string | null {
  const model = job.stage?.startsWith("Running the model");
  if (!model || elapsed < 45) return null;
  return job.stage?.includes("recalibration") || /Walk-forward/.test(job.detail ?? "")
    ? "Refitting the calibration is the slow part on a small server (about 8 minutes on the free one). The page keeps updating."
    : "Still working: the model is running on a small server.";
}

/** "in 30 min", "in 3 h", "in 2 d" until a UTC time from the server; "now" once it has passed. */
export function untilText(iso: string, now = Date.now()): string {
  const mins = Math.round((Date.parse(iso + "Z") - now) / 60000);
  if (mins < 1) return "now";
  if (mins < 60) return `in ${mins} min`;
  if (mins < 60 * 48) return `in ${Math.round(mins / 60)} h`;
  return `in ${Math.round(mins / 1440)} d`;
}

/** A server UTC time as the viewer's local weekday and clock: "Wed 10:00 AM". */
export function localWhen(iso: string): string {
  return new Date(iso + "Z").toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" });
}

/** For an empty board: "Check back after the next update, Sat 10:00 AM.", without the time when no sync is scheduled. */
export const checkBack = (nextUpdate?: string | null) => `Check back after the next update${nextUpdate ? `, ${localWhen(nextUpdate)}` : ""}.`;
