"""The market side of grading (app/market_fit.py): every price logged, the close attached to finished games, CLV in
the report, the weights refit, and line movement on the board."""
from datetime import datetime

import pandas as pd
import pytest

from app import grading, market_fit
from app.db import OddsHistory, PickLog, PickSnap, Run, SessionLocal, Setting

G = "2026_06_AAA_BBB"
EARLY = datetime(2026, 10, 15, 14)       # Thursday
MORNING = datetime(2026, 10, 18, 15, 30)  # Sunday 11:30 AM Eastern; kickoff 1:00 PM Eastern is 17:00 UTC
AFTER = datetime(2026, 10, 19, 12)
DIST = dict(mu=[20.0, 80.0], sd=[15.0, 15.0], z=[-3.0 + 6.0 * i / 40 for i in range(41)])


def pick(side, line, odds, ev, p_mkt=0.5, hours=48.0, kind="rec", player="worthy"):
    return dict(game_id=G, player_id=player, name=player.title(), pos="WR", team="AAA", kind=kind, side=side, line=line,
                odds=odds, book="X", mu=50.0, prob=0.6, p_model=0.6 if side == "Over" else 0.4, p_mkt=p_mkt, ev=ev,
                flagged=False, gameday="2026-10-18", gametime="13:00", hours=hours)


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    def wipe(s):
        for t in (PickLog, PickSnap, OddsHistory, Run, Setting):
            s.query(t).delete()
    with SessionLocal() as s:
        wipe(s)
        s.add(Run(season=2026, week=6, backtest={}, variance={"rec": DIST}, excluded=[]))
        s.commit()
    market_fit.clear_cache()
    monkeypatch.setattr(grading, "proj_frame", lambda s, run_id: pd.DataFrame())
    monkeypatch.setattr(grading, "current_lines", lambda s: pd.DataFrame())
    yield
    with SessionLocal() as s:
        wipe(s)
        s.commit()
    market_fit.clear_cache()


def board(monkeypatch, rows):
    monkeypatch.setattr(grading, "build_picks", lambda *a, **k: pd.DataFrame(rows))


def history(t, rows):
    with SessionLocal() as s:
        s.add_all(OddsHistory(fetched_at=t, game_id=G, player=p, market="rec", line=l, over_odds=o, under_odds=u, book=b, alt=False)
                  for p, l, o, u, b in rows)
        s.commit()


def test_prices_are_logged_once_each_and_the_first_board_price_is_kept(monkeypatch):
    board(monkeypatch, [pick("Over", 45.5, -110, 0.06), pick("Under", 45.5, -110, -0.1)])
    with SessionLocal() as s:
        grading.snapshot_board(s, now=EARLY)
        grading.snapshot_board(s, now=EARLY.replace(hour=15))     # nothing changed: no new snaps
    board(monkeypatch, [pick("Over", 48.5, -105, 0.02, hours=1.5), pick("Under", 48.5, -115, -0.08, hours=1.5)])
    with SessionLocal() as s:
        grading.snapshot_board(s, now=MORNING)
        assert s.query(PickSnap).count() == 4
        over = s.query(PickLog).filter_by(side="Over").one()
        assert (over.line, over.hours) == (48.5, 1.5)                                   # the last price
        assert (over.first_line, over.first_odds, over.first_at) == (45.5, -110, EARLY)  # as first shown (EV 3%+)
        assert s.query(PickLog).filter_by(side="Under").one().first_at is None         # never on the board


def test_the_close_is_attached_and_the_report_shows_clv(monkeypatch):
    board(monkeypatch, [pick("Over", 45.5, -110, 0.06), pick("Under", 45.5, -110, -0.1)])
    with SessionLocal() as s:
        grading.snapshot_board(s, now=EARLY)
    history(EARLY, [("Worthy", 45.5, -110, -110, "A"), ("Worthy", 45.5, -110, -110, "B")])
    # the market moved toward the over: the close is 48.5, even money there
    history(datetime(2026, 10, 18, 16), [("Worthy", 48.5, -110, -110, "A"), ("Worthy", 48.5, -110, -110, "B")])
    history(datetime(2026, 10, 18, 18), [("Worthy", 52.5, -110, -110, "A")])   # after kickoff: not the close
    with SessionLocal() as s:
        assert market_fit.attach_closing(s, "nfl", now=AFTER) == 2
        over = s.query(PickLog).filter_by(side="Over").one()
        under = s.query(PickLog).filter_by(side="Under").one()
        assert over.close_line == 48.5 and over.close_books == 2
        # 45.5 is 3 yards under the closing 48.5: 0.2 of a 15-yard spread, on an error table spread evenly over +-3
        assert over.close_p == pytest.approx(0.5 + 0.2 / 6) and over.close_p + under.close_p == pytest.approx(1.0)
        assert over.close_p_first == pytest.approx(over.close_p)
        assert market_fit.attach_closing(s, "nfl", now=AFTER) == 0    # done once
        stats = pd.DataFrame([dict(player_id="worthy", season=2026, week=6, team="AAA", receiving_yards=60),
                              dict(player_id="x", season=2026, week=6, team="BBB", receiving_yards=1)])
        grading.grade(s, stats, pd.DataFrame([dict(game_id=G, home_score=20.0)]))
        rep = grading.report(s, 2026)
    first = next(c for c in rep["clv"] if c["label"] == "As first shown on the board")
    # -110 needs 52.4%; the close made the over 53.3%
    assert first["n"] == 1 and first["beat"] == 1.0 and first["ev_close"] == pytest.approx(0.5333 * 1.909 - 1, abs=1e-3)
    assert first["move"] == pytest.approx(0.0333, abs=1e-3)
    assert rep["clv_by_market"][0]["label"] == "rec"


def test_a_pick_with_no_closing_quote_is_marked_and_skipped(monkeypatch):
    board(monkeypatch, [pick("Over", 45.5, -110, 0.06)])
    with SessionLocal() as s:
        grading.snapshot_board(s, now=EARLY)
        assert market_fit.attach_closing(s, "nfl", now=MORNING) == 0      # not kicked off yet
        assert market_fit.attach_closing(s, "nfl", now=AFTER) == 0
        r = s.query(PickLog).one()
        assert r.close_books == 0 and r.close_p is None and r.close_at is not None


def test_refit_stores_weights_the_board_reads(monkeypatch):
    rows = []
    for i in range(30):
        name = f"p{chr(97 + i % 26)}{chr(97 + i // 26)}"
        rows += [pick("Over", 45.5, -110, 0.06, p_mkt=0.7 if i % 2 else 0.3, player=name),
                 pick("Under", 45.5, -110, -0.1, p_mkt=0.3 if i % 2 else 0.7, player=name)]
    board(monkeypatch, rows)
    with SessionLocal() as s:
        grading.snapshot_board(s, now=EARLY)
        # the market was right every time, the model's 60% never moved
        stats = pd.DataFrame([dict(player_id=f"p{chr(97 + i % 26)}{chr(97 + i // 26)}", season=2026, week=6, team="AAA",
                                   receiving_yards=60 if i % 2 else 30) for i in range(30)]
                             + [dict(player_id="x", season=2026, week=6, team="BBB", receiving_yards=1)])
        grading.grade(s, stats, pd.DataFrame([dict(game_id=G, home_score=20.0)]))
        fr = market_fit.fit_rows(s, "nfl")
        assert len(fr) == 30 and set(fr.y) == {0.0, 1.0}
        market_fit.refit(s, "nfl", now=AFTER, log=lambda _: None)
    market_fit.clear_cache()
    with SessionLocal() as s:
        mw = market_fit.market_weights(s, "nfl")
        assert mw["n"] == 30 and mw["kinds"]["rec"]["w_fit"] == 1.0 and mw["w"] > 0.35
        summary = market_fit.summary(s, "nfl")
        assert summary["kinds"][0]["label"] == "rec" and summary["books"] == []   # no history, no current lines
        assert market_fit.summary(s, "nfl", ["FanDuel", "Pinnacle"])["books"][0] == dict(book="Pinnacle", w=3.0, n=0)


def test_line_history_and_moves():
    history(EARLY, [("Worthy", 45.5, -110, -110, "A"), ("Worthy", 45.5, -110, -110, "B"), ("Worthy", 45.5, -110, -110, "C")])
    history(MORNING, [("Worthy", 45.5, -150, 120, "A"), ("Worthy", 45.5, -150, 120, "B"), ("Worthy", 45.5, -145, 115, "C")])
    with SessionLocal() as s:
        pts = market_fit.line_history(s, G, "Worthy", "rec", 50.0, 15.0, DIST, "nfl")
        assert [p["line"] for p in pts] == [45.5, 45.5] and pts[0]["p_over"] == pytest.approx(0.5)
        assert pts[1]["p_over"] > 0.55 and pts[1]["best_over"] == -145 and pts[1]["books"] == 3
        proj = pd.DataFrame([dict(name="Worthy", kind="rec", mu=50.0, sd=15.0)])
        picks = pd.DataFrame([dict(game_id=G, name="Worthy", kind="rec", side=side, line=45.5, mu=50.0, sd=15.0,
                                   p_mkt=pts[1]["p_over"] if side == "Over" else 1 - pts[1]["p_over"]) for side in ("Over", "Under")])
        moved = market_fit.add_moves(s, "nfl", picks, proj, {"rec": DIST})
    over, under = moved.iloc[0], moved.iloc[1]
    assert over.open_line == 45.5 and over.move > 0.05 and under.move == pytest.approx(-over.move)
    assert over.steam == 1 and under.steam == -1


def test_tracked_bet_legs_get_the_close_at_their_own_line(monkeypatch):
    """A tracked parlay's legs get the board's close moved to each leg's line, the alternate one too (app/bets.py)."""
    from app import bets
    from app.db import Bet, BetLeg, User
    board(monkeypatch, [pick("Over", 45.5, -110, 0.06), pick("Under", 45.5, -110, -0.1)])
    with SessionLocal() as s:
        grading.snapshot_board(s, now=EARLY)
    history(EARLY, [("Worthy", 45.5, -110, -110, "A"), ("Worthy", 45.5, -110, -110, "B")])
    history(datetime(2026, 10, 18, 16), [("Worthy", 48.5, -110, -110, "A"), ("Worthy", 48.5, -110, -110, "B")])
    kickoff = datetime(2026, 10, 18, 17)
    leg = lambda line, odds: BetLeg(game_id=G, gameday="2026-10-18", kickoff=kickoff, player_id="worthy", name="Worthy", team="AAA",
                                    opp="BBB", kind="rec", side="Over", line=line, odds=odds, alt=line != 45.5)
    with SessionLocal() as s:
        user = User(email="clv@example.com")
        s.add(user)
        s.flush()
        bet = Bet(user_id=user.id, league="nfl", book="X", kind="parlay", stake=10, odds=400, status="open", placed_at=EARLY)
        bet.legs = [leg(45.5, -110), leg(50.5, 150)]
        s.add(bet)
        s.commit()
        assert market_fit.attach_bet_closing(s, "nfl", now=MORNING) == 0   # not kicked off yet
        assert market_fit.attach_bet_closing(s, "nfl", now=AFTER) == 2
        main, alt = s.get(Bet, bet.id).legs
        # the close is 48.5 at even money: 3 yards under it is +0.2/6, 2 yards over it is -0.133/6 (as for the board)
        assert main.close_p == pytest.approx(0.5 + 0.2 / 6) and alt.close_p == pytest.approx(0.5 - (2 / 15) / 6)
        assert main.close_books == 2 and main.close_at == datetime(2026, 10, 18, 16)
        assert bets.leg_ev_close(main) == pytest.approx(main.close_p * (1 + 100 / 110) - 1, abs=1e-4)
        assert bets.bet_ev_close(s.get(Bet, bet.id)) == pytest.approx(main.close_p * alt.close_p * 5 - 1, abs=1e-4)
        assert market_fit.attach_bet_closing(s, "nfl", now=AFTER) == 0     # done once
        s.query(BetLeg).delete()
        s.query(Bet).delete()
        s.delete(s.get(User, user.id))
        s.commit()


def test_nba_refits_a_calibration_curve_and_the_logs_keep_the_raw_chance(monkeypatch):
    import numpy as np
    rng = np.random.default_rng(5)
    n = 2000
    pm = rng.uniform(0.1, 0.9, n)
    y = (rng.uniform(size=n) < 0.5 + 0.3 * (pm - 0.5)).astype(float)   # the model is far surer than it should be
    rows = pd.DataFrame(dict(kind="pts", hours=2.0, p_model=pm, p_mkt=0.5, y=y))
    monkeypatch.setattr(market_fit, "fit_rows", lambda s, sport: rows)
    with SessionLocal() as s:
        nba = market_fit.refit(s, "nba", now=AFTER, log=lambda _: None)["market_weights"]
        nfl = market_fit.refit(s, "nfl", now=AFTER, log=lambda _: None)["market_weights"]
    assert nba["calibration"]["pts"]["b"] < 0.6 and "calibration" not in nfl
    # the board shows the calibrated chance, but the pick log and pick_snaps store the model's own
    board(monkeypatch, [dict(pick("Over", 45.5, -110, 0.06), p_model=0.55, p_raw=0.7)])
    with SessionLocal() as s:
        grading.snapshot_board(s, now=EARLY)
        assert s.query(PickLog).one().p_model == 0.7 and s.query(PickSnap).one().p_model == 0.7
