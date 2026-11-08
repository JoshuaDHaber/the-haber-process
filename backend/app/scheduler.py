"""Sync jobs and the weekly schedule that runs them, so nobody has to press a button.

One job per league runs at a time (model run and/or odds fetch), whether the schedule or the admin page started it,
so an NFL recalibration and the NBA morning sync can run side by side. Every job is logged as a SyncRun. Odds fetches stop before a key drops under config.ODDS_CREDIT_FLOOR credits, switching to the spare key first.
The schedule runs in a background thread of the web process: fine on a single always-on instance (Railway Hobby). The
job's work runs in a child process (config.SYNC_SUBPROCESS), so its memory goes back to the OS when it finishes.
"""
import multiprocessing
import queue
import threading
import traceback
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import select

from . import config, grading, pipeline
from .db import NbaRun, Run, SessionLocal, SyncRun
from .engine import odds as odds_api
from .nba import grading as nba_grading
from .nba import pipeline as nba_pipeline

ET = ZoneInfo("America/New_York")
CATCH_UP = timedelta(hours=3)   # a slot missed by less than this (a redeploy at the wrong moment) runs on startup
LOG_LINES = 200                 # lines of job output kept per run
MAX_ATTEMPTS = 2                # a slot whose runs keep getting cut off (out of memory?) stops retrying on startup


@dataclass(frozen=True)
class Slot:
    id: str
    label: str
    days: tuple[int, ...]   # weekdays, 0 = Monday
    hour: int               # Eastern time
    minute: int
    odds: str               # none | missing | thin | all
    full: bool = False      # refit the calibration
    why: str = ""
    league: str = "nfl"     # nfl | nba


EVERY_DAY = (0, 1, 2, 3, 4, 5, 6)
SLOTS = [
    Slot("daily-topup", "Daily top-up", EVERY_DAY, 9, 0, "thin",
         why="Model run, plus odds for games with no lines or only a few old ones"),
    Slot("new-week", "New week", (1,), 10, 0, "none", full=True,
         why="After Monday night's stats land: recalibrate and project the new week. No credits"),
    Slot("wed-odds", "Wednesday odds", (2,), 10, 0, "missing", why="Model run, plus odds for games that have none yet"),
    Slot("thu-odds", "Thursday odds", (3,), 10, 0, "missing", why="Model run, plus odds for games that have none yet"),
    Slot("thu-injuries", "Thursday injuries + lines", (3,), 16, 0, "today",
         why="Model run after the practice reports, plus fresh main lines for Thursday night's game (about 11 credits)"),
    Slot("sat-injuries", "Saturday statuses", (5,), 10, 0, "today",
         why="Model run after Friday's game statuses, plus fresh main lines for any Saturday games (none most weeks: no credits)"),
    Slot("sun-pregame", "Sunday pregame", (6,), 11, 0, "none", why="Model run with the latest statuses. No credits"),
    Slot("sun-lines", "Sunday lines", (6,), 11, 30, "today",
         why="Fresh main lines for every game still to kick off today (9 credits per game), plus anything missing"),
    Slot("mon-lines", "Monday lines", (0,), 16, 0, "today",
         why="Model run, plus fresh main lines for Monday night's games (about 11 credits per game)"),
    # NBA (its own key, ODDS_API_KEY_NBA): games every day, props posted the day of
    Slot("nba-morning", "NBA morning", EVERY_DAY, 10, 0, "missing", league="nba",
         why="NBA model run after last night's box scores, moneylines (1 credit), props already posted for today (4 per game, plus up to 4 for alternate lines the first time)"),
    Slot("nba-afternoon", "NBA afternoon", EVERY_DAY, 14, 0, "missing", league="nba",
         why="NBA model run, moneylines, and props for today's games that had none in the morning"),
    Slot("nba-pregame", "NBA pregame", EVERY_DAY, 18, 0, "all", league="nba",
         why="NBA model run with the late injury news, and a fresh pull of tonight's props and moneylines (4 per game)"),
]
BY_ID = {s.id: s for s in SLOTS}


def _utc(local: datetime) -> datetime:
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def _local_times(slot: Slot, now: datetime, step: int):
    """Slot times from now's Eastern date, walking forward (step 1) or back (-1) day by day; naive UTC."""
    day = now.replace(tzinfo=timezone.utc).astimezone(ET).date()
    for d in range(0, 9):
        date = day + timedelta(days=d * step)
        if date.weekday() in slot.days:
            yield _utc(datetime(date.year, date.month, date.day, slot.hour, slot.minute, tzinfo=ET))


def next_fire(slot: Slot, now: datetime) -> datetime:
    """The first time after now (naive UTC) that the slot runs."""
    return next(t for t in _local_times(slot, now, 1) if t > now)


def last_fire(slot: Slot, now: datetime) -> datetime:
    """The latest time at or before now (naive UTC) that the slot was due."""
    return next(t for t in _local_times(slot, now, -1) if t <= now)


def describe(slot: Slot) -> str:
    """'Tue 10:00 AM ET', 'Daily 9:00 AM ET'."""
    days = "Daily" if slot.days == EVERY_DAY else "/".join("Mon Tue Wed Thu Fri Sat Sun".split()[d] for d in slot.days)
    t = datetime(2000, 1, 1, slot.hour, slot.minute).strftime("%I:%M %p").lstrip("0")
    return f"{days} {t} ET"


# ---------- the jobs: one per league ----------
# Each league's job runs in its own child process, so they don't share memory, and each spends its own Odds API key.
# Two jobs of one league never overlap: both would write that league's runs and odds.
LEAGUES = ("nfl", "nba")


def _idle() -> dict:
    return {"state": "idle", "started_at": None, "finished_at": None, "log": [], "error": None,
            "stage": None, "step": 0, "steps": 0, "detail": None, "progress": None}


_jobs = {lg: _idle() for lg in LEAGUES}
_job = _jobs["nfl"]   # the NFL job: what /api/meta and the NFL refresh endpoints report
_lock = threading.Lock()
after_job: Callable[[], None] = lambda: None   # main.py hooks in cache clearing and memory release


def claim(league: str = "nfl") -> bool:
    """Mark the league's job as running; False if one already is."""
    job = _jobs[league]
    with _lock:
        if job["state"] == "running":
            return False
        job.update(state="running", started_at=datetime.utcnow().isoformat(), finished_at=None, log=[], error=None,
                   stage="Starting", step=0, steps=0, detail=None, progress=None)
        return True


def run(odds_mode: str, full: bool = False, slot: str = "manual", trigger: str = "manual",
        scheduled_for: Optional[datetime] = None, league: str = "nfl") -> None:
    """Run a job claimed for its league: the model (unless this host only fetches odds), then odds. Logs it as a SyncRun."""
    nba, job = league == "nba", _jobs[league]
    run_model = nba or config.PROJECTIONS_ON_SERVER()   # the NBA model is small enough for any host
    steps = int(run_model) + int(odds_mode != "none")
    job.update(steps=steps, step=0, stage=None, detail=None, progress=None)
    with SessionLocal() as s:
        rec = SyncRun(slot=slot, trigger=trigger, scheduled_for=scheduled_for, odds_mode=odds_mode, full=full,
                      started_at=datetime.utcnow(), log=[])
        s.add(rec)
        s.commit()
        rec_id = rec.id

    def log(msg):
        msg = odds_api.redact(msg)   # tracebacks and request errors can carry the API key in a URL
        job["log"].append(msg)
        job["detail"] = msg.strip()

    res, status, parts, error = {}, "error", [], None
    try:
        args = (odds_mode, full, nba, run_model, steps)
        res, parts, status, error = _in_child(args, log, job=job) if config.SYNC_SUBPROCESS() else _work(*args, job.update, log)
    except Exception as e:  # the child couldn't even start
        error = odds_api.redact(f"{type(e).__name__}: {e}")
        log(traceback.format_exc(limit=3))
    finally:
        # the log row is written before the job reads as finished, so the admin page never sees one without the other
        try:
            with SessionLocal() as s:
                rec = s.get(SyncRun, rec_id)
                latest = s.scalars(select(NbaRun if nba else Run).order_by((NbaRun if nba else Run).id.desc()).limit(1)).first()
                rec.finished_at, rec.status, rec.error = datetime.utcnow(), status, error
                rec.season, rec.week = (latest.season, None if nba else latest.week) if latest else (None, None)
                rec.games_fetched = int(res.get("fetched", 0))
                rec.credits_before, rec.credits_after = res.get("credits_before"), res.get("credits_after")
                rec.summary = (" · ".join(p for p in parts if p) or (error or ""))[:500]
                rec.log = job["log"][-LOG_LINES:]
                s.commit()
        except Exception:  # a failed log write must never leave the job looking stuck
            traceback.print_exc()
        if error:
            job.update(state="error", error=error)
        else:
            job.update(state="done", stage="Done", detail=None, progress=1.0)
        job["finished_at"] = datetime.utcnow().isoformat()
        after_job()


def _work(odds_mode: str, full: bool, nba: bool, run_model: bool, steps: int, update: Callable, log: Callable):
    """The job itself: the model, then odds, then the grading snapshot. Returns (odds result, summary parts, status, error)."""
    def odds_progress(done: int, total: int):
        update(progress=(done / total) if total else 1.0, detail=f"game {min(done + 1, total)} of {total}")

    res, status, parts, error = {}, "ok", [], None
    try:
        if nba:
            update(step=1, stage="Running the NBA model", progress=None)
            parts.append("NBA model run" if nba_pipeline.run_projections(log=log) else "No NBA games coming up")
        elif run_model:
            update(step=1, stage="Running the model" + (" (full recalibration)" if full else ""), progress=None)
            pipeline.run_projections(log=log, full=full)
            parts.append("Model recalibrated" if full else "Model run")
        else:
            log("Projections run off-server on this host; fetching odds only")
        if odds_mode != "none":
            update(step=steps, stage="Fetching odds", detail=None, progress=0.0)
            refresh = nba_pipeline.refresh_odds if nba else pipeline.refresh_odds
            res = refresh(odds_mode, log=log, progress=odds_progress, credit_floor=config.ODDS_CREDIT_FLOOR()) or {}
            parts.append(_odds_summary(res))
            if res.get("skipped_for_credits") or res.get("failed"):
                status = "partial"
        _snapshot(log, nba)
    except Exception as e:  # surfaced to the UI and the run log
        status, error = "error", odds_api.redact(f"{type(e).__name__}: {e}")
        log(traceback.format_exc(limit=3))
    return res, parts, status, error


# ---------- the job's own process ----------
# Python rarely hands freed memory back to the OS, so every sync run in the web process left it bigger, until one
# pushed it past the 1 GB host limit and the whole server was killed. A child process gives it all back when it exits,
# and if it is the one killed for memory, the web process lives on to log the run as failed.
def _child(q, args: tuple) -> None:
    """Runs in the spawned process: the job, reporting log lines, progress and the result back over q."""
    q.put(("done", _work(*args, lambda **kw: q.put(("update", kw)), lambda msg: q.put(("log", msg)))))


def _in_child(args: tuple, log: Callable, target: Callable = _child, job: Optional[dict] = None):
    """Run _work in a fresh process, relaying what it reports to the job (the NFL one by default); the same result tuple as _work."""
    job = _job if job is None else job
    ctx = multiprocessing.get_context("spawn")   # not fork: this process has threads and open database connections
    q = ctx.Queue()
    p = ctx.Process(target=target, args=(q, args), name="sync-job", daemon=True)
    p.start()
    while True:
        try:
            kind, payload = q.get(timeout=1)
        except queue.Empty:
            if p.is_alive():
                continue
            try:   # its last messages can land just after it exits
                kind, payload = q.get(timeout=1)
            except queue.Empty:
                break
        if kind == "done":
            p.join()
            return payload
        if kind == "log":
            log(payload)
        else:
            job.update(**payload)
    p.join()
    error = f"The sync process died (exit code {p.exitcode}" + (", most likely out of memory)" if p.exitcode == -9 else ")")
    log(error)
    return {}, [], "error", error


def _snapshot(log, nba: bool = False) -> None:
    """Log the board for games still to kick off (app/grading.py, app/nba/grading.py). Never fails the sync."""
    try:
        with SessionLocal() as s:
            n = (nba_grading if nba else grading).snapshot_board(s)
        if n:
            log(f"Logged {n} {'NBA ' if nba else ''}picks for grading")
    except Exception as e:   # noqa: BLE001
        log(f"  couldn't log picks for grading ({type(e).__name__}: {e})")


def _odds_summary(res: dict) -> str:
    before, after = res.get("credits_before"), res.get("credits_after")
    if not res.get("games"):
        return "odds already complete"
    bits = [f"odds for {res.get('fetched', 0)} of {res['games']} games"]
    if before is not None and after is not None:
        bits.append(f"{before - after} credits used, {after} left")
    if res.get("failed"):
        bits.append(f"{res['failed']} failed (the Odds API errored; the next sync retries)")
    if res.get("skipped_for_credits"):
        bits.append(f"{res['skipped_for_credits']} skipped at the {config.ODDS_CREDIT_FLOOR()}-credit floor: rotate the key")
    if res.get("rotate"):
        bits.append(f"switched to the spare key at the floor: rotate {' and '.join(res['rotate'])}")
    return ", ".join(bits)


# ---------- the schedule ----------
def mark_interrupted() -> int:
    """Close out runs a restart cut off (out of memory, a redeploy): on a fresh process nothing is running."""
    with SessionLocal() as s:
        rows = s.scalars(select(SyncRun).where(SyncRun.status == "running")).all()
        for r in rows:
            r.status, r.finished_at = "interrupted", datetime.utcnow()
            r.summary = "Cut off by a server restart (out of memory or a redeploy)"
        s.commit()
        return len(rows)


def _already_ran(slot: Slot, at: datetime) -> bool:
    """True once the slot finished for that time, or was cut off MAX_ATTEMPTS times (retrying would just crash again)."""
    with SessionLocal() as s:
        statuses = s.scalars(select(SyncRun.status).where(SyncRun.slot == slot.id, SyncRun.scheduled_for == at)).all()
    return any(st != "interrupted" for st in statuses) or len(statuses) >= MAX_ATTEMPTS


def _fire(slot: Slot, at: datetime, trigger: str, stop: threading.Event) -> None:
    """Run a due slot, waiting out a job the admin started (up to half an hour) rather than dropping the slot."""
    if _already_ran(slot, at):
        return
    deadline = datetime.utcnow() + timedelta(minutes=30)
    while not claim(slot.league):
        if datetime.utcnow() > deadline or stop.wait(20):
            with SessionLocal() as s:
                s.add(SyncRun(slot=slot.id, trigger=trigger, scheduled_for=at, odds_mode=slot.odds, full=slot.full,
                              finished_at=datetime.utcnow(), status="skipped", summary="Another sync was still running"))
                s.commit()
            return
    run(slot.odds, slot.full, slot.id, trigger, at, slot.league)


def _worker(league: str, q: "queue.Queue", stop: threading.Event) -> None:
    """Run one league's due slots in the order they came due, one at a time."""
    while not stop.is_set():
        try:
            s, at, trigger = q.get(timeout=1)
        except queue.Empty:
            continue
        try:
            _fire(s, at, trigger, stop)
        except Exception:  # never let one bad run stop the schedule
            traceback.print_exc()


def _loop(stop: threading.Event) -> None:
    # each league's slots go to its own worker thread, so a long NFL run never holds up an NBA slot (or the clock below)
    queues = {lg: queue.Queue() for lg in LEAGUES}
    for lg, q in queues.items():
        threading.Thread(target=_worker, args=(lg, q, stop), name=f"sync-{lg}", daemon=True).start()
    now = datetime.utcnow()
    # a deploy or restart that straddled a slot: run each league's most recent one missed within the catch-up window
    missed = [(last_fire(s, now), s) for s in SLOTS]
    missed = [(t, s) for t, s in missed if now - t < CATCH_UP and not _already_ran(s, t)]
    for lg in LEAGUES:
        theirs = [m for m in missed if m[1].league == lg]
        if theirs:
            t, s = max(theirs, key=lambda m: m[0])
            queues[lg].put((s, t, "catch-up"))
    due = {s.id: next_fire(s, now) for s in SLOTS}
    while not stop.wait(30):
        now = datetime.utcnow()
        for s in sorted(SLOTS, key=lambda s: due[s.id]):
            if due[s.id] <= now:
                queues[s.league].put((s, due[s.id], "schedule"))
                due[s.id] = next_fire(s, now)


_thread: Optional[threading.Thread] = None
_stop = threading.Event()


def start() -> bool:
    """Start the schedule (once per process) if AUTO_SYNC is on."""
    global _thread
    if not config.AUTO_SYNC() or (_thread and _thread.is_alive()):
        return False
    _stop.clear()
    _thread = threading.Thread(target=_loop, args=(_stop,), name="sync-schedule", daemon=True)
    _thread.start()
    return True


def stop() -> None:
    _stop.set()


def upcoming(now: Optional[datetime] = None, league: Optional[str] = None) -> list[tuple[datetime, Slot]]:
    """Every slot (of one league, if given) with its next run time, soonest first."""
    now = now or datetime.utcnow()
    return sorted(((next_fire(s, now), s) for s in SLOTS if league in (None, s.league)), key=lambda x: x[0])
