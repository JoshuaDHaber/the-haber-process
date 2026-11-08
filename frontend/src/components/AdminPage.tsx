import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import React, { Fragment, useEffect, useState } from "react";
import { api, type CreditStatus, type JobStatus, type ScheduleSlot, type SyncRun } from "../api";
import { useAuth } from "../auth";
import { BASE } from "../env";
import { timeAgo } from "../format";
import { elapsedSeconds, localWhen, progressLine, untilText } from "../sync";
import Logo from "./Logo";
import { openSignIn } from "./SignIn";
import ThemeToggle from "./ThemeToggle";
import Skeleton from "./Skeleton";

const ODDS_TEXT: Record<string, string> = { none: "Model only", missing: "Model + missing odds", thin: "Model + thin top-up", all: "Model + all odds" };

function Status({ s }: { s: SyncRun["status"] }) {
  const text = { ok: "OK", partial: "Partial", error: "Error", skipped: "Skipped", running: "Running", interrupted: "Interrupted" }[s];
  return <span className={`st st-${s}`}>{text}</span>;
}

function credits(r: SyncRun): string {
  if (r.credits_before == null || r.credits_after == null) return r.odds_mode === "none" ? "—" : "0";
  return `${r.credits_before - r.credits_after} (${r.credits_after} left)`;
}

function RunLog({ id }: { id: number }) {
  const q = useQuery({ queryKey: ["admin-run", id], queryFn: () => api.adminRun(id) });
  if (q.isLoading) return <Skeleton kind="rows" count={4} />;
  if (q.error || !q.data) return <p className="err">{(q.error as Error)?.message ?? "No log"}</p>;
  return <pre className="runlog">{q.data.log.join("\n") || "(no output)"}{q.data.error ? `\n\n${q.data.error}` : ""}</pre>;
}

/** The admin page signed out, or as an account that isn't the owner's: the owner's account is the only way in. */
function SignIn() {
  const user = useAuth()?.user;
  return (
    <div className="card admin-signin">
      <b>Admin</b>
      <p className="mut">{user ? <>The sync schedule, run log and credit balance are for the site owner. <b>{user.email}</b> isn't an admin account.</>
        : "The sync schedule, run log and credit balance are for the site owner. Sign in with the owner's account."}</p>
      {!user && <button className="primary" onClick={() => openSignIn()}>Sign in</button>}
    </div>
  );
}

/** Owner-only page: what the schedule will run and when, what each run did, and the Odds API key's balance.
 *  Standalone at /admin, or `embedded` as a tab of the main app (no page header of its own). */
export default function AdminPage({ embedded = false }: { embedded?: boolean }) {
  const qc = useQueryClient();
  const who = useAuth()?.user?.email ?? "";
  const [open, setOpen] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const state = useQuery({
    queryKey: ["admin", who], queryFn: api.admin, retry: false,
    refetchInterval: (q) => (q.state.data?.job.state === "running" || q.state.data?.job_nba?.state === "running" ? 2000 : 30_000),
  });
  const runNow = useMutation({ mutationFn: api.runSlotNow, onSuccess: () => qc.invalidateQueries({ queryKey: ["admin"] }) });
  // every list of books and every price changes with it: refetch the whole site's data, not just this page
  const hideOffshore = useMutation({ mutationFn: api.setHideOffshore, onSuccess: () => qc.resetQueries() });
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t); }, []);

  const header = embedded ? null : (
    <header>
      <div>
        <h1><Logo /> Sync <span className="grad">admin</span></h1>
        <div className="sub"><a href={BASE}>← Back to the site</a></div>
      </div>
      <div className="head-actions"><ThemeToggle /></div>
    </header>
  );
  const page = (children: React.ReactNode, cls = "") => embedded
    ? <div className={`admin-page ${cls}`}>{children}</div>
    : <div className={`wrap admin-page ${cls}`}>{header}{children}</div>;
  const status = (state.error as { status?: number } | null)?.status;
  if (status === 403) return page(<SignIn />);
  if (state.isLoading) return page(<Skeleton kind="cards" />);
  if (state.error || !state.data) return page(<p className="err">{(state.error as Error)?.message}</p>);

  const d = state.data;
  // each league has its own sync, so an NFL recalibration and an NBA sync can run side by side
  const busy = (league: "nfl" | "nba") => (league === "nba" ? d.job_nba : d.job)?.state === "running";
  const c = d.credits;
  const confirmRun = (s: ScheduleSlot) => {
    const cost = s.odds_mode === "none" ? "It spends no Odds API credits." : "It fetches odds, which spends credits (never below the floor).";
    if (window.confirm(`Run "${s.label}" now? ${ODDS_TEXT[s.odds_mode]}${s.full ? " with recalibration" : ""}. ${cost}`)) runNow.mutate(s.id);
  };

  return (
    page(<>

      <RotateBanner league="NFL" k={c} />
      {d.credits_nba && <RotateBanner league="NBA" k={d.credits_nba} />}
      {!d.auto_sync.enabled && (
        <div className="banner">Scheduled syncs are <b>off</b> on this server. Set <code>AUTO_SYNC=1</code> to turn them on (they're on by default on Railway).</div>
      )}

      <div className="admin-cards">
        <div className="card admin-card">
          <span className="mut">Next sync</span>
          {d.auto_sync.enabled && d.auto_sync.next_at ? (
            <><div className="big">{untilText(d.auto_sync.next_at, now)}</div>
              <span className="mut">{d.auto_sync.next_label} · {localWhen(d.auto_sync.next_at)}</span></>
          ) : <div className="big">Off</div>}
        </div>
        <JobCard label={d.job_nba ? "Now (NFL)" : "Now"} job={d.job} now={now} />
        {d.job_nba && <JobCard label="Now (NBA)" job={d.job_nba} now={now} />}
        <CreditCard label="Odds API credits (NFL)" k={c} now={now} />
        {d.credits_nba && <CreditCard label="Odds API credits (NBA)" k={d.credits_nba} now={now} />}
        <div className="card admin-card">
          <span className="mut">Offshore books</span>
          <label className="admin-toggle">
            <input type="checkbox" checked={d.hide_offshore} disabled={hideOffshore.isPending} onChange={(e) => hideOffshore.mutate(e.target.checked)} />
            <span className="big">{d.hide_offshore ? "Hidden" : "Shown"}</span>
          </label>
          <span className="mut">Bovada, BetOnline.ag, BetUS, MyBookie.ag, LowVig.ag. Hidden, they still count toward the market consensus.</span>
          {hideOffshore.error && <span className="err">{(hideOffshore.error as Error).message}</span>}
        </div>
      </div>
      {runNow.error && <p className="err">{(runNow.error as Error).message}</p>}

      <h2>Schedule <small className="mut">Eastern time</small></h2>
      <div className="card scroll">
        <table className="admin-table">
          <thead><tr><th>When</th><th>Sync</th><th>What it does</th><th>Next</th><th>Last run</th><th /></tr></thead>
          <tbody>
            {d.slots.map((s) => (
              <tr key={s.id}>
                <td>{s.when}</td>
                <td><b>{s.label}</b></td>
                <td className="wrap-cell">{s.why}</td>
                <td title={localWhen(s.next_at)}>{untilText(s.next_at, now)}</td>
                <td>{s.last_run ? <><Status s={s.last_run.status} /> <span className="mut">{timeAgo(s.last_run.started_at, now)}</span></> : <span className="mut">never</span>}</td>
                <td><button className="link" disabled={busy(s.league ?? "nfl") || runNow.isPending} title={busy(s.league ?? "nfl") ? `An ${(s.league ?? "nfl").toUpperCase()} sync is running` : undefined} onClick={() => confirmRun(s)}>Run now</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h2>Run log</h2>
      <div className="card scroll">
        <table className="admin-table">
          <thead><tr><th>Started</th><th>Sync</th><th>Trigger</th><th>Week</th><th>Status</th><th>Games</th><th>Credits</th><th>Result</th></tr></thead>
          <tbody>
            {d.runs.map((r) => (
              <Fragment key={r.id}>
                <tr className="clickable" tabIndex={0} aria-expanded={open === r.id}
                  onClick={() => setOpen(open === r.id ? null : r.id)} onKeyDown={(e) => e.key === "Enter" && setOpen(open === r.id ? null : r.id)}>
                  <td title={new Date(r.started_at + "Z").toLocaleString()}>{localWhen(r.started_at)}</td>
                  <td>{r.label}</td>
                  <td className="mut">{r.trigger}</td>
                  <td>{r.week ? `${r.season} W${r.week}` : "—"}</td>
                  <td><Status s={r.status} /></td>
                  <td>{r.odds_mode === "none" ? "—" : r.games_fetched}</td>
                  <td>{credits(r)}</td>
                  <td className="wrap-cell">{r.error ?? r.summary}</td>
                </tr>
                {open === r.id && <tr><td colSpan={8}><RunLog id={r.id} /></td></tr>}
              </Fragment>
            ))}
            {!d.runs.length && <tr><td colSpan={8} className="mut">No syncs yet. The first one runs at the next scheduled time.</td></tr>}
          </tbody>
        </table>
      </div>
    </>)
  );
}

function JobCard({ label, job, now }: { label: string; job: JobStatus; now: number }) {
  return (
    <div className="card admin-card">
      <span className="mut">{label}</span>
      {/* announces a start or finish only: the visible line below ticks every second */}
      <span className="sr-only" role="status" aria-live="polite">
        {`${label.replace(/^Now ?/, "").replace(/[()]/g, "")} sync ${job.state === "running" ? "running" : job.state === "error" ? "failed" : "idle"}`.trim()}
      </span>
      {job.state === "running" ? (
        <>
          <div className="big"><small>{progressLine(job, elapsedSeconds(job.started_at, now))}</small></div>
          {job.progress != null && <div className="job"><div className="bar"><i style={{ width: `${Math.round(job.progress * 100)}%` }} /></div></div>}
          {job.detail && <span className="mut">{job.detail}</span>}
        </>
      ) : <><div className="big">Idle</div><span className="mut">{job.finished_at ? `Last sync finished ${timeAgo(job.finished_at, now)}` : "No sync since the server started"}</span></>}
    </div>
  );
}

function RotateBanner({ league, k }: { league: string; k: CreditStatus }) {
  const rotate = k.rotate ?? [];
  if (!rotate.length) return null;
  const names = rotate.map((n, i) => <span key={n}>{i > 0 && " and "}<code>{n}</code></span>);
  return (
    <div className="banner">
      {k.low ? (
        <><b>Rotate the {league} Odds API {rotate.length > 1 ? "keys" : "key"}.</b> {names} {rotate.length > 1 ? "are" : "is"} at or under the floor
          of {k.floor} credits, so {league} syncs have stopped fetching odds.</>
      ) : (
        <><b>Rotate {names}.</b> It's at or under the floor of {k.floor} credits, so {league} syncs have switched
          to <code>{k.key_name}</code> (key ending {k.key_hint}, {k.remaining ?? "?"} credits left).</>
      )}
      {" "}In Railway, open the service's <b>Variables</b>, replace {rotate.length > 1 ? "them" : "it"} with a fresh key and deploy. Syncs always
      spend the first key until it reaches the floor, then its spare.
    </div>
  );
}

function CreditCard({ label, k, now }: { label: string; k: CreditStatus; now: number }) {
  return (
    <div className="card admin-card">
      <span className="mut">{label}</span>
      <div className={`big${k.low ? " under" : ""}`}>{k.remaining ?? "?"}</div>
      <span className="mut">
        {k.error ?? `${k.key_name ?? "Key"} …${k.key_hint} · stops at ${k.floor}`}{k.checked_at && !k.error ? ` · checked ${timeAgo(k.checked_at, now)}` : ""}
      </span>
      {(k.others ?? []).map((o) => (
        <span key={o.key_name} className="mut">
          {o.key_name} …{o.key_hint}: {o.error ? "couldn't check" : `${o.remaining ?? "?"} left`}{o.remaining != null && o.remaining <= k.floor ? " (at the floor)" : ""}
        </span>
      ))}
    </div>
  );
}
