import math

import pandas as pd
import pytest

from app.engine import gamectx as g


def test_implied_points_and_the_script_read():
    assert g.implied(44.5, 2.5) == (23.5, 21.0)
    assert g.implied(None, 2.5) == (None, None)
    fav = g.script_read("LA", "GB", 2.5, 44.5, 0.012, -0.006)
    assert fav[0].startswith("LA favored by 2.5: LA usually leans on the run") and "rushing up 3%" in fav[0]
    dog = g.script_read("GB", "LA", -2.5, 50.5, 0.012, -0.006)
    assert "chases with the pass" in dog[0] and "rushing down 3%" in dog[0] and "higher ones" in dog[1]
    assert "pick'em" in g.script_read("A", "B", 0.0, 41.0, 0.012, -0.006)[0]


def test_shares_count_only_the_games_he_played_and_mark_trends():
    rows = []
    for wk in range(1, 11):   # team: 30 targets, 20 carries a game; "w" goes from 20% to 40% of the targets in weeks 6-10
        rows.append(dict(player_id="w", team="KC", season=2026, week=wk, targets=6 if wk < 6 else 12, carries=0))
        rows.append(dict(player_id="o", team="KC", season=2026, week=wk, targets=24 if wk < 6 else 18, carries=20))
        if wk >= 8:   # "r" back from injury for the last three games: half the carries
            rows.append(dict(player_id="r", team="KC", season=2026, week=wk, targets=0, carries=20))
    sh = g.shares(pd.DataFrame(rows), "KC", 2026, 11, {"w", "r", "nobody"})
    assert sh["w"]["tgt_l5"] == pytest.approx(12 / 30) and sh["w"]["tgt_prior"] == pytest.approx(6 / 30)
    assert g.trend(sh["w"]["tgt_l5"], sh["w"]["tgt_prior"]) == 1
    assert sh["r"]["played"] == 3 and sh["r"]["car_l5"] == pytest.approx(0.5) and sh["r"]["car_prior"] is None
    assert "nobody" not in sh


def test_two_plus_tds_keep_the_anytime_chance():
    lam, p2 = g.two_plus(0.43)
    assert 1 - math.exp(-lam) == pytest.approx(0.43)
    assert p2 == pytest.approx(1 - math.exp(-lam) * (1 + lam)) and 0.10 < p2 < 0.12


def test_nba_context_has_one_script_and_no_shares():
    c = g.nba_context(dict(game_id="x", home="ORL", away="DET", total=213.5, spread=-2.5))
    det, orl = c["teams"]
    assert det["implied"] == 108.0 and orl["implied"] == 105.5 and det["players"] == []
    assert det["script"][0] == "DET favored by 2.5." and "slow" in det["script"][1]
