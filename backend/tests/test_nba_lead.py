import numpy as np
import pandas as pd

from app.nba import blowout, lead

# a blowout fit with no bias and a flat 13-point scale (nu large: a normal curve)
FLAT = dict(bias=0.0, coef=[float(np.log(13.0)), 0.0, 0.0], nu=200.0, n=0)


def _paths(mu, sd, n=20000, steps=400, seed=0):
    """Simulated home margins: Brownian paths drifting to mu with spread sd over the game, on a fine grid."""
    rng = np.random.default_rng(seed)
    inc = rng.normal(mu / steps, sd / np.sqrt(steps), (n, steps))
    return np.cumsum(inc, axis=1)


def _games(n=3000, seed=1, k=1.0, steps=400):
    """Synthetic games whose largest leads come from paths with spread k times FLAT's scale (leads floored to whole points)."""
    rng = np.random.default_rng(seed)
    spread = rng.choice(np.arange(-12, 12.5, 0.5), n)
    inc = rng.normal(spread[:, None] / steps, 13.0 * k / np.sqrt(steps), (n, steps))
    path = np.cumsum(inc, axis=1)
    return pd.DataFrame({"event_id": [str(i) for i in range(n)], "season": 2025, "date": "2025-01-01", "home": "BOS", "away": "NY",
                         "spread_line": spread, "margin": np.round(path[:, -1]),
                         "home_lead": np.floor(np.maximum(path.max(axis=1), 0)), "away_lead": np.floor(np.maximum(-path.min(axis=1), 0))})


# a path checked at 400 points misses the peaks between them: about 0.583 sd/sqrt(400) points (Broadie and Glasserman),
# the same kind of gap c allows for, so the simulations are compared at that allowance
GRID = -0.5826 * 13.0 / np.sqrt(400)


def test_lead_chances_match_simulated_paths():
    for mu in (-8.0, 0.0, 6.0):
        x = _paths(mu, 13.0)
        assert abs(lead.p_lead(10, mu, 13.0, GRID) - (x.max(axis=1) >= 10).mean()) < 0.01
        assert abs(lead.p_lead_then_lose(10, mu, 13.0, GRID) - ((x.max(axis=1) >= 10) & (x[:, -1] < 0)).mean()) < 0.01
    # the away team's chance is the home formula with the drift flipped
    assert abs(lead.p_lead(10, -5.0, 13.0, GRID) - ((-_paths(5.0, 13.0)).max(axis=1) >= 10).mean()) < 0.01


def test_lead_chances_behave_at_the_edges():
    p = lead.p_lead([5, 10, 20], 4.0, 13.0, 0)
    assert p[0] > p[1] > p[2]                                          # a bigger lead is less likely
    assert lead.p_lead(10, 30.0, 13.0, 0) > 0.95 and lead.p_lead(10, -30.0, 13.0, 0) < 0.05
    assert np.isfinite(lead.p_lead(40, 25.0, 6.0, 0))                  # no overflow on a huge spread
    assert lead.p_lead(10, 0.0, 13.0, 1.0) > lead.p_lead(10, 0.0, 13.0, 0)   # the overshoot allowance lowers the bar
    up, lost = lead.p_lead(10, 5.0, 13.0, 0), lead.p_lead_then_lose(10, 5.0, 13.0, 0)
    assert 0 < lost < up


def test_fit_recovers_the_path_spread_and_drift():
    df = _games(k=1.1)
    f = lead.fit(df, FLAT, np.ones(len(df)))
    assert abs(np.exp(f["a"]) - 14.3) < 0.8 and abs(f["c"] - GRID * 1.1) < 0.6
    drift, _ = lead.path(f, [-10.0, -5.0, 5.0, 10.0], 0.0, 1.0)   # m and q trade off; the drift they give is what's identified
    np.testing.assert_allclose(drift, [-10.0, -5.0, 5.0, 10.0], atol=1.0)
    # these paths don't come back any more than a Brownian path does, so going up and losing needs no adjustment
    up, lost = lead.chances(f, 10, -4.0, 14.3)
    assert abs(lost - lead.p_lead_then_lose(10, -4.0, 14.3, f["c"])) < 0.03 and f["n"] == len(df)


def test_walk_forward_scores_later_seasons():
    df = pd.concat([_games(n=1500, seed=s).assign(season=2023 + s, date=f"{2022 + s}-12-01",
                                                  event_id=lambda d, s=s: f"{s}-" + d.event_id) for s in range(3)])
    bt = lead.walk_forward(df, 2025, thresholds=(10,))
    assert set(bt.season) == {2025} and len(bt) == 3000   # both teams of each game
    s = lead.summary(bt, thresholds=(10,))
    assert abs(s["lead10"]["pred"] - s["lead10"]["actual"]) < 0.03 and abs(s["cash10"]["pred"] - s["cash10"]["actual"]) < 0.03
    assert s["cash10"]["actual"] > s["lead10"]["actual"]


def test_project_and_token_price_both_sides():
    hist = _games(n=1500).assign(season=2026)
    hist.loc[:2, ["home_lead", "margin"]] = [[12.0, -3.0], [4.0, 6.0], [15.0, 9.0]]   # BOS: up 12 and lost, up 4 and won, up 15 and won
    slate = [dict(game_id="2027_20261120_NY_BOS", home="BOS", away="NY", spread_line=6.0),
             dict(game_id="2027_20261120_X_Y", home="Y", away="X", spread_line=None)]
    paths, fit = lead.project(hist, slate, dict(FLAT, mult=1.0), 2026)
    assert list(paths) == ["2027_20261120_NY_BOS"] and fit["n"] == 1500
    teams = {gid: {"lead": p} for gid, p in paths.items()}
    priced = [dict(game_id="2027_20261120_NY_BOS", sides=[dict(team="NY", home=False, prob=0.3, odds=250),
                                                          dict(team="BOS", home=True, prob=0.7, odds=None)]),
              dict(game_id="2027_20261120_X_Y", sides=[dict(team="X", home=False, prob=0.5, odds=100)])]
    out = lead.token(priced, teams, 10)
    ny, bos = out[0]["sides"]
    assert bos["token"]["p_lead"] > ny["token"]["p_lead"] and ny["token"]["p_cash"] > 0.3   # the dog's token adds to its win chance
    assert ny["token"]["ev"] == ny["token"]["p_cash"] * 3.5 - 1 and bos["token"]["ev"] is None
    assert out[1]["sides"][0]["token"] is None                     # no spread, no path
    rec = bos["token"]
    assert rec["games"] == 1500 and rec["up"] >= 2 and rec["up_lost"] >= 1


def test_add_leads_fills_only_missing_games(monkeypatch, tmp_path):
    df = _games(n=6).drop(columns=["home_lead", "away_lead"]).assign(season=2026, date=[f"2026-01-0{i + 1}" for i in range(6)])
    asked = []
    monkeypatch.setattr(lead, "_leads", lambda eid: (asked.append(eid), (10.0, 2.0))[1])
    path = tmp_path / "h.csv"
    out = lead.add_leads(df, log=lambda m: None, path=str(path), limit=4)
    assert asked == ["5", "4", "3", "2"] and out.home_lead.notna().sum() == 4   # newest first, capped
    out = lead.add_leads(out, log=lambda m: None, path=str(path))
    assert asked[4:] == ["1", "0"] and len(pd.read_csv(path).dropna(subset=["home_lead"])) == 6


def test_leads_read_both_competitors(monkeypatch):
    def fake(url):
        if url.endswith("/competitors"):
            return {"items": [{"id": "8", "homeAway": "home"}, {"id": "17", "homeAway": "away"}]}
        v = 13.0 if "/8/" in url else 4.0
        return {"splits": {"categories": [{"stats": [{"name": "points", "value": 100}, {"name": "largestLead", "value": v}]}]}}
    monkeypatch.setattr(blowout, "_get", fake)
    assert lead._leads("1") == (13.0, 4.0)
    monkeypatch.setattr(blowout, "_get", lambda url: {"items": []})
    assert lead._leads("1") == (None, None)


def test_add_leads_leaves_games_espn_has_no_stats_for_to_ask_again(monkeypatch, tmp_path):
    df = _games(n=4).drop(columns=["home_lead", "away_lead"]).assign(season=2026, date=[f"2026-01-0{i + 1}" for i in range(4)])
    monkeypatch.setattr(lead, "_leads", lambda eid: (None, None) if eid in ("2", "3") else (9.0, 4.0))
    out = lead.add_leads(df, log=lambda m: None, path=str(tmp_path / "h.csv"))
    # NaN in a number column, not None (pandas 3 refuses None in a float column)
    assert out.home_lead.dtype == "float64" and out.home_lead.isna().sum() == 2 and out.away_lead.notna().sum() == 2
