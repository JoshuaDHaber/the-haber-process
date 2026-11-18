from datetime import date

import numpy as np
import pandas as pd

from app.nba import blowout


def _games(n=4000, seed=0, sd=13.0, start="2023-10-20"):
    """Synthetic seasons: margins around the spread with a normal miss of sd points."""
    rng = np.random.default_rng(seed)
    spread = rng.choice(np.arange(-15, 15.5, 0.5), n)
    days = pd.date_range(start, periods=n // 8, freq="D").repeat(8)[:n]
    return pd.DataFrame({"event_id": [str(i) for i in range(n)], "season": 2024 + np.arange(n) // 1300,
                         "date": days.strftime("%Y-%m-%d"), "spread_line": spread,
                         "margin": np.round(spread + rng.normal(0, sd, n))})


def test_odds_take_the_pregame_median_and_flip_to_a_home_margin():
    js = {"items": [{"provider": {"name": "DraftKings"}, "spread": -10.5, "overUnder": 241.5},
                    {"provider": {"name": "ESPN BET"}, "spread": -11.5, "overUnder": 240.5},
                    {"provider": {"name": "MGM"}, "spread": -10.5, "overUnder": 242.5},
                    {"provider": {"name": "ESPN Bet - Live Odds"}, "spread": 3.5, "overUnder": 250.5}]}
    assert blowout.parse_odds(js) == (10.5, 241.5, 3)   # home favored by 10.5; the live line left out
    assert blowout.parse_odds({"items": []}) == (None, None, 0)


def test_finished_keeps_scored_nba_games_only():
    def ev(i, state="post", home=110, away=100, typ=2, abbr="BOS"):
        return {"id": i, "date": "2026-01-10T00:30Z", "season": {"year": 2026, "type": typ},
                "competitions": [{"status": {"type": {"state": state}}, "competitors": [
                    {"homeAway": "home", "team": {"abbreviation": "NY"}, "score": str(home)},
                    {"homeAway": "away", "team": {"abbreviation": abbr}, "score": str(away)}]}]}
    js = {"events": [ev("1"), ev("2", state="pre"), ev("3", home=0, away=0), ev("4", typ=1), ev("5", abbr="STARS")]}
    [g] = blowout._finished(js)
    assert (g["event_id"], g["date"], g["home"], g["away"], g["home_score"]) == ("1", "2026-01-09", "NY", "BOS", 110.0)


def test_fit_recovers_the_miss_and_probabilities_add_up():
    df = _games()
    p = blowout.fit(df)
    assert abs(blowout.scale(p, [0])[0] * np.sqrt(p["nu"] / (p["nu"] - 2)) - 13.0) < 0.6
    pr = blowout.probs(p, [0.0, 10.0, -10.0], 20)
    np.testing.assert_allclose(pr.p_blowout, pr.p_home + pr.p_away)
    assert pr.p_blowout[1] > pr.p_blowout[0] and abs(pr.p_blowout[1] - pr.p_blowout[2]) < 0.02   # symmetric in side
    assert pr.p_home[1] > pr.p_away[1]   # the favorite does the blowing out
    assert (blowout.probs(p, [0.0], 25).p_blowout < pr.p_blowout[:1].to_numpy()).all()


def test_recent_scale_follows_a_wilder_stretch():
    calm, wild = _games(sd=12.0), _games(seed=1, sd=15.0, start="2026-01-01")
    p = blowout.fit(calm)
    assert abs(blowout.recent_scale(p, calm) - 1) < 0.05
    assert blowout.recent_scale(p, pd.concat([calm, wild])) > 1.1
    assert blowout.recent_scale(p, calm.head(100)) == 1.0   # too few games to say


def test_walk_forward_and_summary_score_each_later_season():
    df = _games(n=5200)
    bt = blowout.walk_forward(df, 2025, thresholds=(20,))
    assert set(bt.season) == {2025, 2026, 2027} and bt.p20.between(0, 1).all()
    s = blowout.summary(bt, thresholds=(20,))[20]
    assert s["n"] == len(bt) and abs(s["pred"] - s["actual"]) < 0.03 and len(s["calibration"]) == 5


def test_history_only_fetches_dates_after_the_stored_games(monkeypatch, tmp_path):
    path = tmp_path / "h.csv"
    _games(n=16).assign(season_type=2, home="NY", away="BOS", home_score=100.0, away_score=90.0, neutral=False,
                        total=225.0, books=3).to_csv(path, index=False)
    asked = []

    def fake_fetch(days, log=print):
        asked.extend(days)
        return pd.DataFrame([dict(event_id="new", season=2024, season_type=2, date="2023-10-23", home="NY", away="BOS",
                                  home_score=120.0, away_score=90.0, neutral=False, spread_line=4.0, total=220.0, books=2,
                                  margin=30.0)])
    monkeypatch.setattr(blowout, "fetch", fake_fetch)
    df = blowout.history(log=lambda m: None, path=str(path), today=date(2023, 10, 25))
    assert asked[0] == date(2023, 10, 19) and asked[-1] == date(2023, 10, 24)   # two days' overlap, through yesterday
    assert len(df) == 17 and "new" in set(df.event_id) and len(pd.read_csv(path)) == 17


def test_project_prices_slate_games_with_a_spread():
    slate = [dict(game_id="a", spread_line=12.0), dict(game_id="b", spread_line=-1.0), dict(game_id="c", spread_line=None)]
    out, fit = blowout.project(_games(), slate)
    assert set(out) == {"a", "b"} and fit["n"] == 4000 and 0.85 <= fit["mult"] <= 1.15
    a = {c["margin"]: c for c in out["a"]["chances"]}
    assert list(a) == list(blowout.THRESHOLDS) and a[20]["p"] > {c["margin"]: c for c in out["b"]["chances"]}[20]["p"]
    assert a[20]["p_home"] > a[20]["p_away"] and abs(a[20]["p"] - a[20]["p_home"] - a[20]["p_away"]) < 1e-9
    assert blowout.project(_games(), [dict(game_id="c", spread_line=None)])[0] == {}


def test_history_keeps_other_columns_on_games_it_fetches_again(monkeypatch, tmp_path):
    path = tmp_path / "h.csv"
    _games(n=16).assign(season_type=2, home="NY", away="BOS", home_score=100.0, away_score=90.0, neutral=False, total=225.0, books=3,
                        home_lead=12.0, away_lead=3.0).to_csv(path, index=False)
    last = pd.read_csv(path, dtype={"event_id": str}).iloc[-1]

    def fake_fetch(days, log=print):   # the overlap brings the last stored game back, without the leads nba/lead.py added
        return pd.DataFrame([dict(last.drop(["home_lead", "away_lead"]), home_score=101.0)])
    monkeypatch.setattr(blowout, "fetch", fake_fetch)
    df = blowout.history(log=lambda m: None, path=str(path), today=date(2023, 10, 25))
    row = df[df.event_id == last.event_id].iloc[0]
    assert len(df) == 16 and row.home_score == 101.0 and (row.home_lead, row.away_lead) == (12.0, 3.0)
