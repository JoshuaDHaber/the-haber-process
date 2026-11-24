from datetime import date

import numpy as np
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


def test_depth_slots_rank_by_previous_games_not_the_game_itself():
    rows = []
    for t in range(1, 7):   # A is the WR1 by targets every week; in week 6 B out-targets him once, and stays the WR2
        rows += [dict(player_id="A", team="KC", t=202600 + t, grp="WR", vol=10 if t < 6 else 4),
                 dict(player_id="B", team="KC", t=202600 + t, grp="WR", vol=6 if t < 6 else 12),
                 dict(player_id="C", team="KC", t=202600 + t, grp="WR", vol=3),
                 dict(player_id="D", team="KC", t=202600 + t, grp="WR", vol=1),
                 dict(player_id="Q", team="KC", t=202600 + t, grp="QB", vol=35),
                 dict(player_id="Q2", team="KC", t=202600 + t, grp="QB", vol=2)]
    f = pd.DataFrame(rows)
    s = dvp.slots(f)
    wk6 = dict(zip(f[f.t == 202606].player_id, s[f.t == 202606]))
    assert wk6 == {"A": "WR1", "B": "WR2", "C": "WR3", "D": None, "Q": "QB", "Q2": None}   # a backup QB has no slot
    assert dvp.rank_slots({"x": ("RB", 3.0), "y": ("RB", 18.0), "z": ("RB", 1.0), "t": ("TE", 4.0)}) == \
        {"y": "RB1", "x": "RB2", "z": None, "t": "TE1"}


def test_nfl_slot_grades_split_a_position_and_fall_back_to_it():
    from app.engine import model as nfl
    rows = []
    for i in range(16):   # SOFT gives WR2s 60% more than usual and WR1s nothing extra; HARD is par for both
        opp = "SOFT" if i % 2 else "HARD"
        for pid, tg, yds in (("w1", 9, 90.0), ("w2", 6, 50.0), ("w4", 1, 8.0), ("w3", 3, 25.0)):
            k = 1.6 if (opp == "SOFT" and pid == "w2") else 1.0
            rows.append(dict(t=202600 + i + 1, season=2026, player_id=pid, team="KC", position="WR", opponent_team=opp,
                             targets=tg, receiving_yards=yds * k, receptions=tg // 2, carries=0, rushing_yards=0,
                             attempts=0, passing_yards=0))
    tab = nfl.dvp_table(pd.DataFrame(rows), 202620)
    assert tab["rec"][("SOFT", "WR2")][1] == 1 and tab["rec"][("SOFT", "WR2")][0] > 1.05
    assert tab["rec"][("SOFT", "WR1")][0] == pytest.approx(1.0, abs=0.02)
    f = nfl.dvp_fields(tab, "rec", "SOFT", "WR", "WR2")
    assert f["dvp_slot"] == "WR2" and f["dvp_rank"] == 1
    assert nfl.dvp_fields(tab, "rec", "SOFT", "WR", None)["dvp_slot"] == "WR"      # a WR4 gets the position's grade
    assert nfl.dvp_fields(tab, "rec", "SOFT", "WR", "WR9")["dvp_slot"] == "WR"     # a slot with no grade falls back


def test_recent_vs_lists_the_slot_against_the_defense_newest_first_with_his_usual():
    rows = []
    for t in range(1, 9):
        rows += [dict(player_id="a", name="A", team="KC", opp="DAL" if t % 2 else "NYG", t=202600 + t, season=2026, week=t,
                      slot="WR1", grp="WR", val=80.0 + t),
                 dict(player_id="b", name="B", team="KC", opp="DAL" if t % 2 else "NYG", t=202600 + t, season=2026, week=t,
                      slot="WR2", grp="WR", val=40.0)]
    out = dvp.recent_vs(pd.DataFrame(rows), "DAL", "WR1", 202608, n_games=2)
    assert out.week.tolist() == [7, 5]                 # DAL's last two games before week 8, newest first; WR1s only
    assert out.usual.iloc[0] == pytest.approx(np.mean([80 + t for t in range(1, 7)]))
    assert dvp.recent_vs(pd.DataFrame(rows), "DAL", "WR", 202608, n_games=1).player_id.tolist() == ["a", "b"]
