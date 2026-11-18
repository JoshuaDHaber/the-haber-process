"""NBA grading: the board is snapshot before tip-off, graded from ESPN's game logs, and summed up at /api/nba/grades."""
from datetime import date, datetime, timedelta

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import main
from app.db import NbaOddsLine, NbaPickLog, NbaProjection, NbaRun, SessionLocal
from app.nba import grading

GID = "2027_20261020_BOS_DET"
TIP = datetime(2026, 10, 20, 23, 30)   # 7:30 PM Eastern


@pytest.fixture(autouse=True)
def clean():
    def wipe(s):
        for t in (NbaPickLog, NbaOddsLine, NbaProjection, NbaRun):
            s.query(t).delete()
    with SessionLocal() as s:
        wipe(s)
        game = dict(event_id="1", game_id=GID, season=2027, gameday="2026-10-20", gametime="19:30",
                    start=TIP.strftime("%Y-%m-%dT%H:%MZ"), home="DET", away="BOS", state="pre", spread_line=1.5, total=221.5)
        run = NbaRun(season=2027, slate=["2026-10-20"], backtest={}, variance={}, excluded=[], games=[game], teams={})
        run.projections = [NbaProjection(player_id=pid, name=name, pos="PG", team="DET", opp="BOS", home=True, kind="pts", mu=mu,
                                         sd=6.0, vol=34.0, eff=mu / 34, spread=1.5, status="", game_id=GID, gameday="2026-10-20",
                                         gametime="19:30", last5=[mu] * 5, wx=1.0, teammate_out="")
                           for pid, name, mu in (("p1", "Star Guard", 28.0), ("p2", "Bench Wing", 9.0))]
        s.add(run)
        t = datetime.utcnow()   # one fetch: the board keeps each game's newest fetch per market
        s.add_all([NbaOddsLine(fetched_at=t, player=name, market="pts", line=line, over_odds=-110, under_odds=-110,
                               book="FanDuel", game_id=GID) for name, line in (("Star Guard", 24.5), ("Bench Wing", 10.5))])
        s.commit()
    yield
    with SessionLocal() as s:
        wipe(s)
        s.commit()


def logs(*rows):
    cols = ["player_id", "event_id", "season", "date", "team", "opp", "home", "min", "pts", "reb", "ast", "threes", "home_score", "away_score"]
    return pd.DataFrame([dict(zip(cols, r)) for r in rows], columns=cols)


def box(pid, team, day="2026-10-20", pts=30.0, mins=35.0):
    return (pid, "e1", 2027, day, team, "BOS" if team == "DET" else "DET", team == "DET", mins, pts, 5.0, 6.0, 2.0, 112.0, 108.0)


def snap(now):
    with SessionLocal() as s:
        return grading.snapshot_board(s, now=now)


def test_snapshots_keep_the_last_price_before_tip_off():
    assert snap(TIP - timedelta(hours=9)) == 4   # two players, Over and Under
    with SessionLocal() as s:
        s.query(NbaOddsLine).filter(NbaOddsLine.player == "Star Guard").update({"line": 26.5})
        s.commit()
    snap(TIP - timedelta(hours=1))
    with SessionLocal() as s:
        assert {r.line for r in s.query(NbaPickLog).filter(NbaPickLog.player_id == "p1")} == {26.5}
        s.query(NbaOddsLine).filter(NbaOddsLine.player == "Star Guard").update({"line": 30.5})
        s.commit()
    # after tip-off the game is off the board and its logged picks keep the pregame line
    assert snap(TIP + timedelta(minutes=5)) == 0
    with SessionLocal() as s:
        assert {r.line for r in s.query(NbaPickLog).filter(NbaPickLog.player_id == "p1")} == {26.5}
        assert s.query(NbaPickLog).count() == 4


def test_grades_once_both_box_scores_are_in_and_voids_players_who_sat_only_when_sure():
    snap(TIP - timedelta(hours=1))
    no_fetch = lambda *a, **k: pd.DataFrame(columns=["player_id"])   # noqa: E731
    with SessionLocal() as s:
        # the game day itself: nothing is graded until the next morning
        assert grading.grade(s, logs(box("p1", "DET"), box("x", "BOS")), date(2026, 10, 20), fetch=no_fetch) == 0
        # next morning, only one team's box score is in: wait
        assert grading.grade(s, logs(box("p1", "DET")), date(2026, 10, 21), fetch=no_fetch) == 0
        # both in: p1 is graded; p2 has no row and nothing newer, so he waits (a stale log would look like a DNP)
        assert grading.grade(s, logs(box("p1", "DET", pts=30.0), box("x", "BOS")), date(2026, 10, 21), fetch=no_fetch) == 2
        res = {(r.player_id, r.side): (r.result, r.actual) for r in s.query(NbaPickLog)}
        assert res[("p1", "Over")] == ("win", 30.0) and res[("p1", "Under")] == ("loss", 30.0)
        assert res[("p2", "Over")] == (None, None)
        # p2 plays the next game, so the missing row means he sat: void
        later = logs(box("p1", "DET"), box("x", "BOS"), box("p2", "DET", day="2026-10-22"))
        assert grading.grade(s, later, date(2026, 10, 23), fetch=no_fetch) == 2
        assert {r.result for r in s.query(NbaPickLog).filter(NbaPickLog.player_id == "p2")} == {"void"}


def test_players_off_every_roster_are_fetched_for_grading():
    snap(TIP - timedelta(hours=1))
    asked = []
    def fetch(ids, seasons, current, log=None):
        asked.append(ids)
        return logs(box("p2", "DET", pts=8.0))
    with SessionLocal() as s:
        assert grading.grade(s, logs(box("p1", "DET"), box("x", "BOS")), date(2026, 10, 21), fetch=fetch) == 4
        assert asked == [["p2"]]
        assert {r.result for r in s.query(NbaPickLog).filter(NbaPickLog.player_id == "p2", NbaPickLog.side == "Under")} == {"win"}


def test_grades_endpoint_reports_the_nba_record_by_week():
    snap(TIP - timedelta(hours=1))
    with SessionLocal() as s:
        grading.grade(s, logs(box("p1", "DET", pts=30.0), box("p2", "DET", pts=8.0), box("x", "BOS")), date(2026, 10, 21),
                      fetch=lambda *a, **k: pd.DataFrame())
        s.query(NbaPickLog).update({"ev": 0.05, "flagged": False})   # every pick on the visible board
        s.commit()
    with TestClient(main.app) as c:
        g = c.get("/api/nba/grades").json()
    assert g["season"] == 2027 and g["graded"] == 4 and g["pending"] == 0 and g["dates"] == ["2026-10-20", "2026-10-20"]
    assert [(l["label"], l["wins"], l["losses"]) for l in g["by_week"]] == [("Week of Oct 19", 2, 2)]
    [check] = g["model_vs_line"]
    assert check["kind"] == "pts" and check["n"] == 2
    # the NFL report never sees NBA picks
    with TestClient(main.app) as c:
        assert c.get("/api/grades").json()["graded"] == 0
