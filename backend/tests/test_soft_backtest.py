"""The soft-matchup backtest: every soft-market over replayed at its last price before kickoff and graded leg by leg."""
from datetime import datetime, timedelta

import pandas as pd
import pytest

from app import soft_backtest as sb
from app.db import OddsHistory, PickLog, Projection, Run, SessionLocal

G = "2026_10_AAA_BBB"
SNAP = datetime(2026, 11, 15, 17, 0)


NAMES = {"wr1": "Ann Arbor", "wr2": "Ben Baker", "rb": "Cal Carter", "out": "Dee Dunn"}


def proj(run_id, player_id, kind, mu, rank, pos="WR"):
    return Projection(run_id=run_id, player_id=player_id, name=NAMES[player_id], pos=pos, team="AAA", opp="BBB", home=True,
                      kind=kind, mu=mu, sd=20.0, vol=5.0, eff=8.0, spread=0.0, game_id=G, gameday="2026-11-15", last5=[],
                      dvp_rank=rank, dvp_n=32 if rank else None)


def logged(run_id, player_id, kind, mu, actual, result="win"):
    return PickLog(season=2026, week=10, game_id=G, gameday="2026-11-15", gametime="13:00", player_id=player_id, name=player_id.title(),
                   pos="WR", team="AAA", kind=kind, side="Over", line=50.5, odds=-110, book="X", mu=mu, prob=0.55, p_model=0.55,
                   p_mkt=0.5, ev=0.0, run_id=run_id, snapped_at=SNAP, actual=actual, result=result)


def quote(player, market, line, over, book="DraftKings", alt=True, at=SNAP - timedelta(hours=1)):
    return OddsHistory(fetched_at=at, game_id=G, player=player, market=market, line=line, over_odds=over, under_odds=None, book=book, alt=alt)


@pytest.fixture
def slate():
    with SessionLocal() as s:
        for t in (OddsHistory, PickLog, Projection, Run):
            s.query(t).delete()
        run = Run(season=2026, week=10, backtest={}, variance={}, excluded=[])
        s.add(run)
        s.flush()
        s.add_all([
            proj(run.id, "wr1", "rec", 80.0, 3), logged(run.id, "wr1", "rec", 80.0, 92.0),
            proj(run.id, "wr2", "rec", 55.0, 30), logged(run.id, "wr2", "rec", 55.0, 20.0, "loss"),
            # a back whose run predates rush + rec grades: borrows his rushing grade
            proj(run.id, "rb", "rush", 60.0, 5, pos="RB"), proj(run.id, "rb", "rr", 80.0, None, pos="RB"),
            logged(run.id, "rb", "rush", 60.0, 55.0), logged(run.id, "rb", "rr", 80.0, 75.0),
            # sat out: void, never graded as a leg
            proj(run.id, "out", "rec", 50.0, 1), logged(run.id, "out", "rec", 50.0, None, "void"),
        ])
        s.add_all([
            quote("Ann Arbor", "rec", 69.5, -180), quote("Ann Arbor", "rec", 79.5, -120), quote("Ann Arbor", "rec", 99.5, 150),
            quote("Ann Arbor", "rec", 79.5, -150, at=SNAP - timedelta(hours=30)),     # an older price for the same rung: superseded
            quote("Ann Arbor", "rec", 79.5, -900, at=SNAP + timedelta(hours=1)),      # after the last snapshot (in play): ignored
            quote("Ann Arbor", "rec", 79.5, -110, book="FanDuel"),                    # a better price at another book
            quote("Ben Baker", "rec", 39.5, -130), quote("Ben Baker", "rec", 9.5, -1000),   # -1000 is outside KEEP_ODDS
            quote("Cal Carter", "rr", 70.5, -150), quote("Cal Carter", "rush", 49.5, -160, alt=False),
            quote("Dee Dunn", "rec", 30.5, -200),
        ])
        s.commit()
    yield
    with SessionLocal() as s:
        for t in (OddsHistory, PickLog, Projection, Run):
            s.query(t).delete()
        s.commit()


def test_replays_each_rung_at_its_last_price_before_kickoff_and_grades_it(slate):
    with SessionLocal() as s:
        legs = sb.nfl_legs(s)
    key = lambda r: (r.player_id, r.kind, r.line, r.book)
    got = {key(r): r for r in legs.itertuples()}
    assert set(got) == {("wr1", "rec", 69.5, "DraftKings"), ("wr1", "rec", 79.5, "DraftKings"), ("wr1", "rec", 99.5, "DraftKings"),
                        ("wr1", "rec", 79.5, "FanDuel"), ("wr2", "rec", 39.5, "DraftKings"), ("rb", "rr", 70.5, "DraftKings"),
                        ("rb", "rush", 49.5, "DraftKings")}
    assert got[("wr1", "rec", 79.5, "DraftKings")].odds == -120
    assert [got[("wr1", "rec", l, "DraftKings")].result for l in (69.5, 79.5, 99.5)] == ["win", "win", "loss"]
    assert got[("wr2", "rec", 39.5, "DraftKings")].result == "loss"
    assert got[("rb", "rr", 70.5, "DraftKings")].grade == "A" and got[("wr2", "rec", 39.5, "DraftKings")].grade == "F"
    # longer lines are less likely, and the chance blends the model with the price
    probs = [got[("wr1", "rec", l, "DraftKings")].prob for l in (69.5, 79.5, 99.5)]
    assert probs == sorted(probs, reverse=True)
    r = got[("wr1", "rec", 79.5, "DraftKings")]
    assert r.prob == pytest.approx(0.65 * r.p_model + 0.35 / (1 + 100 / 120))


def test_role_percentiles_rank_each_market_within_its_slate():
    log = pd.DataFrame([dict(slate="W1", player_id=p, kind=k, side="Over", mu=m) for p, k, m in
                        (("a", "rec", 80), ("b", "rec", 20), ("c", "rec", 50), ("q", "pass", 250))])
    r = sb.role_percentiles(log)
    assert (r[("W1", "a", "rec")], r[("W1", "c", "rec")], r[("W1", "b", "rec")], r[("W1", "q", "pass")]) == (1.0, 0.5, 0.0, 1.0)


def test_report_compares_grades_at_the_section_price_floor(slate):
    with SessionLocal() as s:
        rows = {r["slice"]: r for r in sb.report(sb.nfl_legs(s))}
    built = rows["as built: A/B, -200 or longer, 50%+"]
    # best price per line: wr1 79.5 counts once (FanDuel's -110), and every leg left is graded A/B, -200 or longer, 50%+
    assert built["players"] == 3 and built["legs"] >= 3 and built["hit"] == pytest.approx(1.0)   # wr1 rec, rb rush, rb rr: all cleared
    assert rows["grade F (-200 or longer, 50%+)"]["hit"] == 0.0
    assert built["roi"] > 0
    assert "as built, rec" in rows and rows["no grade (-200 or longer, 50%+)"]["legs"] == 0
    text = sb.format_report(sb.report(sb.nfl_legs(SessionLocal())), "t")
    assert "as built" in text


def test_summary_counts_pushes_out_and_prices_the_return():
    legs = pd.DataFrame([dict(game_id="g", player_id=p, kind="rec", odds=o, prob=0.6, p_model=0.6, result=res)
                         for p, o, res in (("a", 100, "win"), ("b", 100, "loss"), ("c", -200, "push"))])
    s = sb.summarize(legs, "x")
    assert (s["legs"], s["hit"], s["roi"], s["breakeven"]) == (2, 0.5, pytest.approx(0.0), pytest.approx(0.5))
