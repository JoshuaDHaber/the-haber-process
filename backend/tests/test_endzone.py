"""King of the Endzone: touchdown lengths from play-by-play and the simulated chance of a game's longest TD."""
import numpy as np
import pandas as pd

from app.engine import data, endzone


def test_touchdowns_are_cut_with_their_kind_and_length():
    p = pd.DataFrame({"game_id": "g", "season": 2025, "week": 3, "touchdown": [1, 1, 1, 0, 1],
                      "td_player_id": ["r", "c", "d", None, "x"], "td_team": ["H", "H", "A", None, None],
                      "rush_touchdown": [1, 0, 0, 0, 0], "pass_touchdown": [0, 1, 0, 0, 0],
                      "yards_gained": [3, 45, 0, 10, 5], "return_yards": [0, 0, 88, 0, 0]})
    t = data._tds(p)
    assert list(zip(t.player_id, t.kind, t.yards)) == [("r", "rush", 3), ("c", "rec", 45), ("d", "ret", 88)]   # no team: dropped


def _tds():
    rows = [("wr", "rec", y) for y in (40, 55, 62, 70, 35, 48)] + [("rb", "rush", y) for y in (1, 1, 2, 2, 3, 1)] \
        + [("o1", "rec", y) for y in range(5, 60, 5)] + [("o2", "rush", y) for y in range(1, 30, 3)] + [("d", "ret", 90), ("d", "ret", 60)]
    return pd.DataFrame([dict(player_id=p, kind=k, yards=y, game_id=f"g{i % 4}", season=2025, week=1, team="H") for i, (p, k, y) in enumerate(rows)])


POS = {"wr": "WR", "rb": "RB", "o1": "WR", "o2": "RB"}
PLAYED = pd.DataFrame({"game_id": ["g0", "g1", "g2", "g3"], "home_score": 24.0, "away_score": 20.0})


def test_summary_holds_length_histograms_and_league_rates():
    ez = endzone.summarize(_tds(), POS, PLAYED)
    assert len(ez["pools"]["WR_rec"]) == endzone.MAX_YARDS and sum(ez["pools"]["WR_rec"]) == 17
    assert sum(ez["pools"]["ret"]) == 2 and sum(ez["pools"]["other"]) == 33
    assert ez["rush_share"]["RB"] == 1.0 and ez["rush_share"]["WR"] == 0.0
    assert ez["avg_total"] == 44.0 and abs(ez["td_per_point"] - 33 / 176) < 1e-9 and ez["ret_per_game"] == 2 / 8
    own = endzone.own_lengths(_tds(), ["wr", "rb", "nobody"])
    assert own["wr"]["rec"] == [40, 55, 62, 70, 35, 48] and own["rb"] == {"rush": [1, 1, 2, 2, 3, 1], "rec": []} and "nobody" not in own


def test_a_deep_threat_beats_a_goal_line_back_at_the_same_scoring_chance():
    tds = _tds()
    ez = endzone.summarize(tds, POS, PLAYED)
    own = endzone.own_lengths(tds, ["wr", "rb"])
    players = [dict(player_id="wr", name="Deep", pos="WR", team="H", prob=0.4, own=own["wr"]),
               dict(player_id="rb", name="Short", pos="RB", team="A", prob=0.4, own=own["rb"])]
    b = endzone.board(players, dict(home="H", away="A", spread=0.0, total=44.0), ez, sims=20000)
    rows = {r["name"]: r for r in b["rows"]}
    assert rows["Deep"]["p_long"] > 2 * rows["Short"]["p_long"]
    assert rows["Deep"]["p_long_if_td"] > rows["Short"]["p_long_if_td"]
    assert rows["Deep"]["median_td"] == 51.5 and rows["Short"]["n_tds"] == 6
    # every game with a TD has exactly one winner's worth of equity, ties split
    assert abs(sum(r["p_long"] for r in b["rows"]) - (1 - b["p_no_td"])) < 1e-9
    assert {r["kind"] for r in b["rows"]} == {"player", "other", "ret"} and len(b["rows"]) == 6
    assert not b["total_assumed"] and b["median_longest"] > 0


def test_listed_scorers_are_scaled_to_the_team_total_and_a_missing_total_uses_the_average():
    ez = endzone.summarize(_tds(), POS, PLAYED)
    many = [dict(player_id=f"p{i}", name=f"P{i}", pos="RB", team="H", prob=0.6, own=None) for i in range(8)]
    b = endzone.board(many, dict(home="H", away="A", spread=3.0, total=None), ez, sims=2000)
    assert b["total_assumed"] and b["total"] == 44.0
    t = b["teams"]["H"]
    assert t["scale"] < 1 and abs(t["points"] - 23.5) < 1e-9
    listed = sum(-np.log1p(-r["p_td"]) for r in b["rows"] if r["kind"] == "player")
    assert abs(listed - (1 - endzone.OTHER_FLOOR) * t["tds"]) < 1e-6   # the rest of the offense keeps its floor
