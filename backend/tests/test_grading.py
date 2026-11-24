"""Grading: the board is snapshot before kickoff, graded from the box score, and summed up for the Model check tab."""
from datetime import datetime

import pandas as pd
import pytest

from app import grading
from app.db import PickLog, Run, SessionLocal

G1, G2 = "2026_06_AAA_BBB", "2026_06_CCC_DDD"


def pick(game_id, player_id, kind, side, line, odds, ev, mu, prob=0.6, flagged=False, gametime="13:00"):
    return dict(game_id=game_id, player_id=player_id, name=player_id.title(), pos="WR", team=game_id.split("_")[2],
                kind=kind, side=side, line=line, odds=odds, book="X", mu=mu, prob=prob, p_model=prob, p_mkt=0.5, ev=ev,
                flagged=flagged, gameday="2026-10-18", gametime=gametime)


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    with SessionLocal() as s:
        s.query(PickLog).delete(); s.query(Run).delete()
        s.add(Run(season=2026, week=6, backtest={}, variance={}, excluded=[]))
        s.commit()
    monkeypatch.setattr(grading, "proj_frame", lambda s, run_id: pd.DataFrame())
    monkeypatch.setattr(grading, "current_lines", lambda s: pd.DataFrame())
    yield
    with SessionLocal() as s:
        s.query(PickLog).delete(); s.query(Run).delete(); s.commit()


def board(monkeypatch, rows):
    monkeypatch.setattr(grading, "build_picks", lambda *a, **k: pd.DataFrame(rows))


MORNING = datetime(2026, 10, 18, 15, 30)    # 11:30 AM Eastern
AFTERNOON = datetime(2026, 10, 18, 18, 0)   # 2:00 PM Eastern: the 1:00 games have kicked off


def test_snapshots_keep_the_last_price_before_each_kickoff(monkeypatch):
    board(monkeypatch, [pick(G1, "worthy", "recs", "Under", 4.5, -110, 0.35, 2.8),
                        pick(G2, "late", "rec", "Over", 50.5, -110, 0.05, 55, gametime="16:25")])
    with SessionLocal() as s:
        assert grading.snapshot_board(s, now=MORNING) == 2
    # the line moves after the 1:00 kickoff: only the 4:25 game's row changes
    board(monkeypatch, [pick(G1, "worthy", "recs", "Under", 5.5, +100, 0.5, 2.8),
                        pick(G2, "late", "rec", "Over", 47.5, -120, 0.08, 55, gametime="16:25")])
    with SessionLocal() as s:
        assert grading.snapshot_board(s, now=AFTERNOON) == 1
        rows = {r.player_id: (r.line, r.odds) for r in s.query(PickLog)}
    assert rows == {"worthy": (4.5, -110), "late": (47.5, -120)}


def test_grades_finished_games_once_their_stats_are_in_and_voids_players_who_sat(monkeypatch):
    board(monkeypatch, [pick(G1, "worthy", "recs", "Under", 4.5, -110, 0.35, 2.8),
                        pick(G1, "worthy", "td", "Over", 0.5, 300, 0.1, 0.3),
                        pick(G1, "moore", "rec", "Under", 52.5, -115, 0.23, 36),
                        pick(G2, "hubbard", "rr", "Over", 80.5, -110, 0.05, 90)])
    with SessionLocal() as s:
        grading.snapshot_board(s, now=MORNING)
    sched = pd.DataFrame([dict(game_id=G1, home_score=20.0), dict(game_id=G2, home_score=None)])
    stats = pd.DataFrame([dict(player_id="worthy", season=2026, week=6, team="AAA", receptions=7, receiving_yards=73,
                               rushing_yards=0, rushing_tds=0, receiving_tds=1, passing_yards=0, passing_tds=0, receiving_long=20),
                          dict(player_id="other", season=2026, week=6, team="BBB", receptions=1)])
    with SessionLocal() as s:
        assert grading.grade(s, stats, sched) == 3                  # G2 isn't final yet
        got = {(r.player_id, r.kind): (r.actual, r.result) for r in s.query(PickLog)}
    assert got[("worthy", "recs")] == (7, "loss")
    assert got[("worthy", "td")] == (1, "win")
    assert got[("moore", "rec")] == (None, "void")
    assert got[("hubbard", "rr")] == (None, None)


def test_waits_for_stats_when_the_game_is_final_but_nflverse_is_behind(monkeypatch):
    board(monkeypatch, [pick(G1, "worthy", "recs", "Under", 4.5, -110, 0.35, 2.8)])
    with SessionLocal() as s:
        grading.snapshot_board(s, now=MORNING)
        stats = pd.DataFrame([dict(player_id="x", season=2026, week=5, team="AAA", receptions=3)])
        assert grading.grade(s, stats, pd.DataFrame([dict(game_id=G1, home_score=20.0)])) == 0


def test_the_report_sums_the_board_and_compares_the_model_with_the_line(monkeypatch):
    board(monkeypatch, [pick(G1, "a", "rec", "Under", 50.5, -110, 0.20, 40),   # line 50.5, actual 60: loses
                        pick(G1, "a", "rec", "Over", 50.5, -110, -0.2, 40),
                        pick(G1, "a", "recs", "Under", 4.5, +120, 0.10, 4),     # 3 catches: wins +1.2
                        pick(G1, "b", "rush", "Over", 30.5, -110, 0.02, 35),    # under the 3% board cutoff
                        pick(G1, "c", "rush", "Under", 60.5, -110, 0.5, 40, flagged=True)])
    with SessionLocal() as s:
        grading.snapshot_board(s, now=MORNING)
        stats = pd.DataFrame([dict(player_id=p, season=2026, week=6, team=t, receiving_yards=ry, receptions=rc, rushing_yards=ru)
                              for p, t, ry, rc, ru in (("a", "AAA", 60, 3, 0), ("b", "AAA", 0, 0, 35), ("c", "BBB", 0, 0, 70))])
        grading.grade(s, stats, pd.DataFrame([dict(game_id=G1, home_score=20.0)]))
        rep = grading.report(s)
    by = {b["label"]: b for b in rep["board"]}
    main = by["Board (Min EV 3%, flags hidden)"]
    assert (main["n"], main["wins"], main["losses"], main["units"]) == (2, 1, 1, 0.2)
    assert by["One pick per player"]["n"] == 1 and by["Flagged (hidden)"]["losses"] == 1
    checks = {c["kind"]: c for c in rep["model_vs_line"]}
    assert checks["rec"]["n"] == 1 and checks["rec"]["mae_model"] == 20 and checks["rec"]["mae_line"] == 9.5
    assert checks["rush"]["n"] == 2 and checks["rush"]["model_closer"] == 0.5   # b: model 0 off vs 4.5; c: 30 vs 9.5


def test_longest_rush_and_first_td_grade_from_their_play_by_play_columns():
    row = {"rushing_long": 23.0, "first_td": 1.0}
    assert grading.actual_value("rlng", row) == 23.0 and grading.outcome("Over", 19.5, 23.0) == "win"
    assert grading.actual_value("ftd", row) == 1.0 and grading.outcome("Over", 0.5, 1.0) == "win"
    assert grading.outcome("Over", 0.5, grading.actual_value("ftd", {"first_td": 0.0})) == "loss"


def test_the_report_groups_the_board_by_shown_win_chance_for_calibration(monkeypatch):
    board(monkeypatch, [pick(G1, "a", "rec", "Under", 50.5, -110, 0.20, 40),
                        pick(G1, "a", "recs", "Under", 4.5, +120, 0.10, 4)])
    with SessionLocal() as s:
        grading.snapshot_board(s, now=MORNING)
        stats = pd.DataFrame([dict(player_id=p, season=2026, week=6, team=t, receiving_yards=ry, receptions=rc, rushing_yards=0)
                              for p, t, ry, rc in (("a", "AAA", 60, 3), ("z", "BBB", 10, 1))])
        grading.grade(s, stats, pd.DataFrame([dict(game_id=G1, home_score=20.0)]))
        rep = grading.report(s)
    rows = [b for b in rep["by_prob"] if b["n"]]
    assert sum(b["n"] for b in rows) == 2 and sum(b["wins"] for b in rows) == 1
    assert all(b["expected_wins"] <= b["wins"] + b["losses"] for b in rows)


def test_base_rates_count_each_posted_line_once_per_market_and_position():
    def log(gid, pid, pos, kind, side, line, actual, result, season=2026):
        return PickLog(season=season, week=6, game_id=gid, gameday="2026-10-18", gametime="13:00", player_id=pid, name=pid, pos=pos,
                       team="AAA", kind=kind, side=side, line=line, odds=-110, book="X", mu=50, prob=.5, p_model=.5, p_mkt=.5,
                       ev=0, run_id=1, actual=actual, result=result)
    with SessionLocal() as s:
        s.add_all([log(G1, "a", "WR", "rec", "Over", 50.5, 60, "win"), log(G1, "a", "WR", "rec", "Under", 50.5, 60, "loss"),
                   log(G1, "b", "WR", "rec", "Over", 40.5, 30, "loss"), log(G2, "c", "TE", "rec", "Over", 30.0, 30, "push"),
                   log(G2, "d", "TE", "rec", "Over", 30.5, None, "void"),                     # didn't play: left out
                   log(G2, "e", "RB", "td", "Over", 0.5, 1, "win"),                           # yes/no: no base rate
                   log(G1, "f", "WR", "rec", "Over", 10.5, 99, "win", season=2025)])          # last season
        s.commit()
        out = grading.base_rates(s)
    assert out["season"] == 2026 and out["min_n"] == grading.BASE_MIN_N
    r = {(x["kind"], x["pos"]): x for x in out["rates"]}
    assert set(r) == {("rec", "all"), ("rec", "WR"), ("rec", "TE")}
    assert r[("rec", "all")]["n"] == 3 and r[("rec", "all")]["over"] == pytest.approx(1 / 3) and r[("rec", "all")]["push"] == pytest.approx(1 / 3)
    assert r[("rec", "WR")]["avg"] == pytest.approx(-0.5)   # +9.5 and -10.5
