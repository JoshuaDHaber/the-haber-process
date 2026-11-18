import numpy as np
import pandas as pd

from app.engine.model import Z_PROBS
from app.nba import king
from scipy.stats import norm

# a PRA error table shaped like the run's: a normal miss whose spread grows with the projection
DIST = {"mu": [10.0, 50.0], "sd": [4.0, 9.0], "z": list(norm.ppf(np.clip(Z_PROBS, 1e-4, 1 - 1e-4)))}


def _proj(mus, ranks=None, n=30):
    ranks = ranks or [15] * len(mus)
    return pd.DataFrame({"player_id": [f"p{i}" for i in range(len(mus))], "name": [f"P{i}" for i in range(len(mus))], "pos": "G",
                         "team": "BOS", "opp": "NY", "home": True, "game_id": "g", "gameday": "2026-11-18", "gametime": "19:00",
                         "status": "", "mu": mus, "sd": 7.0, "dvp_rank": ranks, "dvp_n": n})


def test_chances_add_up_and_follow_the_projection():
    top, top3 = king.chances([45.0, 40.0, 30.0, 20.0], 7.0, DIST, sims=20000)
    assert top[0] > top[1] > top[2] > top[3] and 1.0 <= top.sum() < 1.1   # ties count for everyone tied
    assert (top3 >= top).all() and abs(top3.sum() - 3) < 0.3


def test_two_equal_players_split_the_crown():
    top, _ = king.chances([40.0, 40.0], 7.0, DIST, sims=40000)
    assert abs(top[0] - top[1]) < 0.02


def test_no_table_falls_back_to_a_normal_curve():
    x = king.draws([30.0], [5.0], None, 20000, 1)
    assert abs(x.mean() - 30) < 0.2 and abs(x.std() - 5) < 0.2 and (x >= 0).all()


def test_board_ranks_by_chance_and_marks_sleepers():
    mus = [50.0, 48.0, 46.0, 45.0, 44.0, 43.0, 42.0, 25.0]
    ranks = [1, 1, 1, 1, 1, 2, 28, 1]   # the top five are soft too but aren't sleepers; 6th soft, 7th tough, 8th too far back
    rows = king.board(_proj(mus, ranks), DIST, sims=20000)
    assert [r["proj_rank"] for r in rows[:2]] == [1, 2] and rows == sorted(rows, key=lambda r: -r["p_top"])
    sleepers = {r["player_id"] for r in rows if r["sleeper"]}
    assert sleepers == {"p5"}
    assert "p7" not in {r["player_id"] for r in rows}                         # under 0.1%: off the board
    assert king.board(_proj([]).iloc[0:0], DIST) == []


def test_the_tables_worst_miss_doesnt_become_a_fat_tail():
    ztab = list(DIST["z"])
    ztab[-1] = 5.9                                       # one freak game at the top of the backtest
    g = np.array([0.0, 1.96, 2.5, 3.5])
    z = king.tail_z(g, ztab)
    assert abs(z[0]) < 0.01 and abs(z[1] - 1.96) < 0.02 and 2.4 < z[2] < 2.7 and z[3] < 3.8
    x = king.draws([20.0], [8.0], dict(DIST, z=ztab), 50000, 2)
    assert (x >= 20 + 8 * 4).mean() < 0.001             # a 4-sd night stays rare


def test_sleepers_are_capped_at_the_likeliest():
    mus = [50.0, 48.0, 46.0, 45.0, 44.0] + [40.0 - i * 0.5 for i in range(10)]
    rows = king.board(_proj(mus, [1] * len(mus)), DIST, sims=20000)
    assert sum(r["sleeper"] for r in rows) == king.MAX_SLEEPERS
