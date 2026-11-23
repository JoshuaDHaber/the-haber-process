from datetime import date

import pandas as pd
import pytest

from app.engine import dvp
from app.nba import model


def test_factors_compare_with_the_players_usual_and_shrink_small_samples():
    rows = []
    for i in range(10):   # guards beat their usual 20 by 50% against SOFT, fall 25% short against HARD; one game against ODD
        rows.append(dict(opp="SOFT", grp="G", w=1.0, act_pts=30.0, exp_pts=20.0))
        rows.append(dict(opp="HARD", grp="G", w=1.0, act_pts=15.0, exp_pts=20.0))
        rows.append(dict(opp="SOFT", grp="C", w=1.0, act_pts=10.0, exp_pts=10.0))
    rows.append(dict(opp="ODD", grp="G", w=1.0, act_pts=40.0, exp_pts=20.0))
    fac = dvp.factors(pd.DataFrame(rows), ["pts"], k_rows=10.0)["pts"]
    assert fac[("SOFT", "G")] > 1.15 and fac[("HARD", "G")] < 0.9 and fac[("SOFT", "C")] == pytest.approx(1.0, abs=0.05)
    assert 1.0 < fac[("ODD", "G")] < fac[("SOFT", "G")]   # one big night is mostly shrunk away
    r = dvp.ranks({"pts": fac})["pts"]
    assert r[("SOFT", "G")][1:] == (1, 3) and r[("HARD", "G")][1] == 3 and r[("SOFT", "C")][1:] == (1, 1)
    assert [dvp.grade(k, 30) for k in (1, 6, 7, 15, 24, 25, 30)] == ["A", "A", "B", "C", "D", "F", "F"]


def test_nba_matchups_rank_each_position_group_against_each_defense():
    rows = []
    for i in range(40):
        d = date(2025, 11, 1 + i % 28).replace(month=11 + i // 28).isoformat()
        opp = "SOFT" if i % 2 else "HARD"
        rows.append(dict(player_id="g", event_id=str(i), season=2026, date=d, team="BOS", opp=opp, home=True, min=34.0,
                         pts=30.0 if opp == "SOFT" else 20.0, reb=4.0, ast=6.0, threes=3.0, stl=1.0, blk=0.0))
    m = model.dvp(pd.DataFrame(rows), date(2026, 1, 1), {"g": "G"})
    assert m["pts"][("SOFT", "G")][1] == 1 and m["pts"][("HARD", "G")][1] == 2
    assert m["pra"][("SOFT", "G")][0] > 1 > m["pra"][("HARD", "G")][0]


def test_nfl_rush_rec_grade_combines_both_yardages():
    from app.engine import model as nfl
    rows = []
    for i in range(16):   # an RB who runs for 60 and catches for 20 most weeks, and does 50% better against SOFT
        opp = "SOFT" if i % 2 else "HARD"
        k = 1.5 if opp == "SOFT" else 1.0
        rows.append(dict(t=202600 + i + 1, season=2026, player_id="rb", position="RB", opponent_team=opp,
                         carries=15, rushing_yards=60 * k, targets=3, receiving_yards=20 * k,
                         attempts=0, passing_yards=0, receptions=2))
    tab = nfl.dvp_table(pd.DataFrame(rows), 202620)
    assert tab["rr"][("SOFT", "RB")][1] == 1 and tab["rr"][("HARD", "RB")][1] == 2
    assert tab["rr"][("SOFT", "RB")][0] > 1 > tab["rr"][("HARD", "RB")][0]
    assert nfl.dvp_fields(tab, "rr", "SOFT", "RB")["dvp_rank"] == 1
