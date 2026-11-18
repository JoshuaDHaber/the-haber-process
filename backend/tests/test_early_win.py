"""NFL early win: largest leads from play-by-play, the history the lead model fits on, and the per-game paths a token is
priced from (the math itself is nba/lead's, tested in test_nba_lead.py)."""
import numpy as np
import pandas as pd

from app.engine import data, early_win
from app.nba import lead


def _plays(game_id, scores, season=2025):
    """One game's plays from (home, away) scores after each play."""
    return pd.DataFrame({"game_id": game_id, "season": season, "season_type": "REG",
                         "total_home_score": [h for h, _ in scores], "total_away_score": [a for _, a in scores]})


def test_largest_leads_come_from_the_score_after_every_play():
    p = pd.concat([_plays("g1", [(0, 0), (7, 0), (14, 3), (14, 17), (21, 17)]), _plays("g2", [(0, 0), (0, 3), (0, 10)])])
    g = data._leads(p).set_index("game_id")
    assert (g.loc["g1", "home_lead"], g.loc["g1", "away_lead"]) == (11, 3)
    assert (g.loc["g2", "home_lead"], g.loc["g2", "away_lead"]) == (0, 10)   # never led: 0


def _sched(n=400, seed=0, season=2024):
    """Played games whose largest leads come from Brownian paths drifting to the spread (13-point spread over a game)."""
    rng = np.random.default_rng(seed)
    spread = rng.choice(np.arange(-10, 10.5, 0.5), n)
    path = np.cumsum(rng.normal(spread[:, None] / 200, 13 / np.sqrt(200), (n, 200)), axis=1)
    ids = [f"{season}_01_A{i}_H{i}" for i in range(n)]
    sched = pd.DataFrame({"game_id": ids, "season": season, "gameday": "2024-09-08", "home_team": [f"H{i}" for i in range(n)],
                          "away_team": [f"A{i}" for i in range(n)], "spread_line": spread,
                          "home_score": 20 + np.round(path[:, -1]).clip(min=-20), "away_score": 20.0})
    leads = pd.DataFrame({"game_id": ids, "home_lead": np.floor(np.maximum(path.max(axis=1), 0)),
                          "away_lead": np.floor(np.maximum(-path.min(axis=1), 0)), "season": season})
    return sched, leads


def test_history_keeps_played_games_with_a_spread_and_leads():
    sched, leads = _sched(5)
    sched.loc[0, "spread_line"] = np.nan                                  # no closing line: left out
    sched.loc[1, "home_score"] = np.nan                                   # not played yet: left out
    df = early_win.history(sched, leads.iloc[:4])                        # no leads for the last game: left out
    assert list(df.game_id) == list(sched.game_id[2:4])
    assert {"spread_line", "margin", "home_lead", "away_lead", "home", "away"} <= set(df.columns)


def test_each_upcoming_game_gets_a_path_the_token_prices():
    sched, leads = _sched()
    df = early_win.history(sched, leads)
    teams = {"g_fav": dict(home="H1", away="A1", spread=7.0), "g_none": dict(home="H2", away="A2", spread=None)}
    paths, lf = early_win.project(df, teams, 2024)
    assert set(paths) == {"g_fav"} and lf["n"] == len(df)                 # no spread, no token
    p = paths["g_fav"]
    g = df[df.home == "H1"].iloc[0]                                       # H1 and A1 met once this season
    assert p["season"] == {"H1": [[g.home_lead, bool(g.margin > 0)]], "A1": [[g.away_lead, bool(g.margin < 0)]]}
    up_home, _ = lead.chances(p, early_win.THRESHOLD, p["mu"], p["sd"])
    up_away, _ = lead.chances(p, early_win.THRESHOLD, -p["mu"], p["sd"])
    assert up_home > up_away                                              # the 7-point favorite goes up 17 more often
    priced = [dict(game_id="g_fav", sides=[dict(team="A1", home=False, prob=0.3, odds=250), dict(team="H1", home=True, prob=0.7, odds=-300)])]
    away, home = lead.token(priced, {"g_fav": {"lead": p}}, early_win.THRESHOLD)[0]["sides"]
    assert away["token"]["threshold"] == early_win.THRESHOLD and away["token"]["p_cash"] > 0.3   # the token adds to a win
    assert home["token"]["games"] == away["token"]["games"] == 1


def test_walk_forward_predicts_each_season_from_the_ones_before():
    a, la = _sched(300, seed=1, season=2023)
    b, lb = _sched(300, seed=2, season=2024)
    bt = early_win.walk_forward(early_win.history(pd.concat([a, b]), pd.concat([la, lb])), 2024)
    assert set(bt.season) == {2024} and len(bt) == 600                     # both sides of every 2024 game
    assert abs(bt[f"p_lead{early_win.THRESHOLD}"].mean() - (bt.lead >= early_win.THRESHOLD).mean()) < 0.06
