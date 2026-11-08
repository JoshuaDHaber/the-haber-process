"""The sync schedule: slot times in Eastern time, the run log, catch-up and the admin endpoints."""
import os
import signal
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app import main, scheduler
from app.db import SessionLocal, SyncRun


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    with SessionLocal() as s:
        s.query(SyncRun).delete(); s.commit()
    for job in scheduler._jobs.values():
        job.update(state="idle")
    monkeypatch.setattr(scheduler.pipeline, "run_projections", lambda log=print, **kw: log("fake run"))
    monkeypatch.setattr(scheduler.nba_pipeline, "run_projections", lambda log=print, **kw: log("fake NBA run") or True)
    yield
    for job in scheduler._jobs.values():
        job.update(state="idle")


def test_slot_times_follow_eastern_time_across_daylight_saving():
    wed = scheduler.BY_ID["wed-odds"]
    # October is EDT (UTC-4), December is EST (UTC-5)
    assert scheduler.next_fire(wed, datetime(2026, 10, 13, 12)) == datetime(2026, 10, 14, 14)
    assert scheduler.next_fire(wed, datetime(2026, 12, 1, 12)) == datetime(2026, 12, 2, 15)
    # exactly at the slot time it is the last fire, and the next one is a week out
    assert scheduler.last_fire(wed, datetime(2026, 10, 14, 14)) == datetime(2026, 10, 14, 14)
    assert scheduler.next_fire(wed, datetime(2026, 10, 14, 14)) == datetime(2026, 10, 21, 14)


def test_daily_slot_runs_every_day_and_describes_itself():
    daily = scheduler.BY_ID["daily-topup"]
    assert scheduler.next_fire(daily, datetime(2026, 10, 13, 14)) == datetime(2026, 10, 14, 13)
    assert scheduler.describe(daily) == "Daily 9:00 AM ET"
    assert scheduler.describe(scheduler.BY_ID["sun-lines"]) == "Sun 11:30 AM ET"


def test_monday_and_thursday_night_games_get_a_game_day_price_pull():
    for slot_id, day in (("mon-lines", "Mon"), ("thu-injuries", "Thu"), ("sun-lines", "Sun")):
        slot = scheduler.BY_ID[slot_id]
        assert slot.odds == "today" and scheduler.describe(slot).startswith(day)
    assert scheduler.next_fire(scheduler.BY_ID["mon-lines"], datetime(2026, 10, 26, 13)) == datetime(2026, 10, 26, 20)   # 4 PM EDT


def test_a_run_is_logged_with_its_odds_result(monkeypatch):
    monkeypatch.setattr(scheduler.pipeline, "refresh_odds", lambda mode, log=print, **kw: dict(
        games=3, fetched=2, skipped_for_credits=1, credits_before=100, credits_after=88))
    assert scheduler.claim()
    scheduler.run("missing", slot="wed-odds", trigger="schedule", scheduled_for=datetime(2026, 10, 14, 14))
    with SessionLocal() as s:
        r = s.query(SyncRun).one()
    assert r.status == "partial" and r.games_fetched == 2 and (r.credits_before, r.credits_after) == (100, 88)
    assert "12 credits used, 88 left" in r.summary and "rotate the key" in r.summary
    assert r.log == ["fake run"] and r.finished_at


def test_a_failed_run_is_logged_as_an_error(monkeypatch):
    def boom(**kw):
        raise RuntimeError("nflverse down")
    monkeypatch.setattr(scheduler.pipeline, "run_projections", boom)
    assert scheduler.claim()
    scheduler.run("none", slot="new-week", trigger="schedule")
    with SessionLocal() as s:
        r = s.query(SyncRun).one()
    assert r.status == "error" and "nflverse down" in r.error
    assert scheduler._job["state"] == "error"


# targets for a real spawned child (module level, so the child can import them)
def _child_reports_and_finishes(q, args):
    q.put(("update", dict(stage="Fetching odds", progress=0.5)))
    q.put(("log", "hello from the child"))
    q.put(("done", (dict(games=1, fetched=1), ["odds for 1 of 1 games"], "ok", None)))


def _child_killed_for_memory(q, args):
    q.put(("log", "about to run out of memory"))
    q.close(); q.join_thread()   # a real job's earlier lines are long sent by then; a kill drops only unsent ones
    os.kill(os.getpid(), signal.SIGKILL)


def test_a_child_process_relays_its_log_progress_and_result():
    lines = []
    out = scheduler._in_child(("all", False, True, True, 2), lines.append, target=_child_reports_and_finishes)
    assert out == (dict(games=1, fetched=1), ["odds for 1 of 1 games"], "ok", None)
    assert lines == ["hello from the child"] and scheduler._job["stage"] == "Fetching odds"


def test_a_child_killed_for_memory_is_logged_as_an_error_and_the_server_lives_on(monkeypatch):
    real = scheduler._in_child
    monkeypatch.setattr(scheduler.config, "SYNC_SUBPROCESS", lambda: True)
    monkeypatch.setattr(scheduler, "_in_child", lambda args, log, job=None: real(args, log, target=_child_killed_for_memory, job=job))
    assert scheduler.claim("nba")
    scheduler.run("all", slot="nba-pregame", trigger="schedule", league="nba")
    with SessionLocal() as s:
        r = s.query(SyncRun).one()
    assert r.status == "error" and "out of memory" in r.error
    assert r.log[0] == "about to run out of memory"
    assert scheduler._jobs["nba"]["state"] == "error" and scheduler._job["state"] == "idle"


def test_a_slot_never_runs_twice_for_the_same_time(monkeypatch):
    monkeypatch.setattr(scheduler.pipeline, "refresh_odds", lambda mode, log=print, **kw: {})
    slot, at = scheduler.BY_ID["sun-pregame"], datetime(2026, 10, 18, 15)
    stop = scheduler.threading.Event()
    scheduler._fire(slot, at, "schedule", stop)
    scheduler._fire(slot, at, "catch-up", stop)
    with SessionLocal() as s:
        assert s.query(SyncRun).count() == 1


def test_a_restart_marks_cut_off_runs_interrupted_and_catch_up_retries_them_once(monkeypatch):
    monkeypatch.setattr(scheduler.pipeline, "refresh_odds", lambda mode, log=print, **kw: {})
    slot, at = scheduler.BY_ID["sun-pregame"], datetime(2026, 10, 18, 15)
    with SessionLocal() as s:   # the process died mid-run: the row never finished
        s.add(SyncRun(slot=slot.id, trigger="schedule", scheduled_for=at, odds_mode=slot.odds)); s.commit()
    assert scheduler._already_ran(slot, at)
    assert scheduler.mark_interrupted() == 1
    assert not scheduler._already_ran(slot, at)
    scheduler._fire(slot, at, "catch-up", scheduler.threading.Event())
    with SessionLocal() as s:
        assert sorted(r.status for r in s.query(SyncRun)) == ["interrupted", "ok"]
        s.query(SyncRun).filter(SyncRun.status == "ok").update({"status": "running"}); s.commit()
    scheduler.mark_interrupted()   # cut off a second time: a third try would likely crash again
    assert scheduler._already_ran(slot, at)


def test_admin_page_needs_the_owners_account(monkeypatch):
    from conftest import sign_in_admin
    with TestClient(main.app, client=("203.0.113.9", 4000)) as remote:
        assert remote.get("/api/admin").status_code == 403
        assert remote.post("/api/admin/run/wed-odds").status_code == 403
        r = sign_in_admin(remote, monkeypatch).get("/api/admin")
    assert r.status_code == 200
    body = r.json()
    assert {s["id"] for s in body["slots"]} == {s.id for s in scheduler.SLOTS}
    assert body["credits"] == {**body["credits"], "error": "No ODDS_API_KEY set", "floor": 10}


def test_admin_can_run_a_slot_now_and_see_it_in_the_log(monkeypatch):
    monkeypatch.setattr(scheduler.pipeline, "refresh_odds", lambda mode, log=print, **kw: {})
    with TestClient(main.app) as local:
        assert local.post("/api/admin/run/nope").status_code == 404
        assert local.post("/api/admin/run/thu-injuries").status_code == 202
        body = local.get("/api/admin").json()
        run = body["runs"][0]
        assert (run["slot"], run["trigger"], run["status"], run["label"]) == ("thu-injuries", "manual", "ok", "Thursday injuries + lines")
        assert next(s for s in body["slots"] if s["id"] == "thu-injuries")["last_run"]["id"] == run["id"]
        assert local.get(f"/api/admin/runs/{run['id']}").json()["log"] == ["fake run"]


def test_meta_shows_the_next_sync_only_when_the_schedule_is_on(monkeypatch):
    with TestClient(main.app) as c:
        assert c.get("/api/meta").json()["auto_sync"] == {"enabled": False, "next_at": None, "next_label": None}
        monkeypatch.setenv("AUTO_SYNC", "1")
        monkeypatch.setattr(scheduler, "start", lambda: False)
        auto = c.get("/api/meta").json()["auto_sync"]
    assert auto["enabled"] and auto["next_label"] in {s.label for s in scheduler.SLOTS}


def test_admin_credits_show_the_spare_key_too(monkeypatch):
    monkeypatch.setenv("ODDS_API_KEY", "main-key-aaaa")
    monkeypatch.setenv("ODDS_API_KEY_NFL_SECONDARY", "spare-key-bbbb")
    monkeypatch.setattr(main.odds, "credits_remaining", lambda key: {"main-key-aaaa": 300, "spare-key-bbbb": 480}[key])
    main._credits.clear()
    with TestClient(main.app) as local:
        c = local.get("/api/admin").json()["credits"]
    assert c["key_name"] == "ODDS_API_KEY" and c["remaining"] == 300 and c["rotate"] == []
    assert c["others"] == [dict(key_name="ODDS_API_KEY_NFL_SECONDARY", key_hint="bbbb", remaining=480, error=None)]


def test_each_league_has_its_own_job_so_nfl_and_nba_syncs_can_overlap():
    assert scheduler.claim("nfl")
    assert not scheduler.claim("nfl")        # never two syncs of one league
    assert scheduler.claim("nba")            # but the NBA one starts while the NFL one runs
    scheduler.run("none", slot="nba-morning", trigger="schedule", league="nba")
    assert scheduler._jobs["nba"]["state"] == "done" and scheduler._jobs["nfl"]["state"] == "running"
    assert scheduler._job is scheduler._jobs["nfl"]


def test_a_long_nfl_sync_does_not_hold_up_a_due_nba_slot(monkeypatch):
    """The schedule's clock keeps ticking during a run: each league's slots go to their own worker.
    (run itself is faked: the tests' in-memory SQLite shares one connection, which two writing threads would trip over.)"""
    import threading
    release, nfl_started, nba_done = threading.Event(), threading.Event(), threading.Event()
    ran = []

    def fake_run(odds, full, slot, trigger, at, league):
        ran.append((slot, trigger))
        if league == "nfl":
            nfl_started.set(); release.wait(10)
        else:
            nba_done.set()
        scheduler._jobs[league].update(state="done")
    monkeypatch.setattr(scheduler, "run", fake_run)
    monkeypatch.setattr(scheduler, "_already_ran", lambda slot, at: False)
    monkeypatch.setattr(scheduler, "SLOTS", [scheduler.BY_ID["new-week"], scheduler.BY_ID["nba-morning"]])
    at = datetime(2026, 11, 10, 15)          # Tue 10:00 AM ET: both slots due at once
    monkeypatch.setattr(scheduler, "last_fire", lambda slot, now: at)
    monkeypatch.setattr(scheduler, "datetime", type("D", (datetime,), {"utcnow": staticmethod(lambda: datetime(2026, 11, 10, 15, 5))}))
    stop = threading.Event()
    t = threading.Thread(target=scheduler._loop, args=(stop,), daemon=True)
    t.start()
    try:
        assert nfl_started.wait(10) and nba_done.wait(10), "the NBA slot waited on the NFL recalibration"
        assert scheduler._jobs["nfl"]["state"] == "running"
    finally:
        release.set(); stop.set(); t.join(5)
    assert sorted(ran) == [("nba-morning", "catch-up"), ("new-week", "catch-up")]


def test_admin_shows_both_leagues_jobs_and_runs_one_league_while_the_other_is_busy(monkeypatch):
    monkeypatch.setattr(scheduler.nba_pipeline, "refresh_odds", lambda mode, log=print, **kw: {})
    with TestClient(main.app) as local:
        assert scheduler.claim("nfl")
        assert local.post("/api/admin/run/wed-odds").status_code == 409
        assert local.post("/api/admin/run/nba-morning").status_code == 202
        body = local.get("/api/admin").json()
    assert body["job"]["state"] == "running" and body["job_nba"]["state"] == "done"
