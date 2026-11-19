"""pipeline.book_starters: the one QB per team the books post a passing line for."""
import pandas as pd

from app import pipeline


def test_the_only_qb_with_a_passing_line_is_his_teams_starter():
    games = pd.DataFrame([dict(game_id="g1", home_team="PHI", away_team="PIT"), dict(game_id="g2", home_team="AAA", away_team="BBB")])
    qbs = pd.DataFrame([dict(player_id="milton", team="PHI", norm="joe milton"), dict(player_id="mckee", team="PHI", norm="tanner mckee"),
                        dict(player_id="rodgers", team="PIT", norm="aaron rodgers"),
                        dict(player_id="a1", team="AAA", norm="a one"), dict(player_id="a2", team="AAA", norm="a two")])
    passers = {"g1": {"joe milton", "aaron rodgers"}, "g2": {"a one", "a two"}}
    assert pipeline.book_starters(passers, games, qbs) == {"PHI": "milton", "PIT": "rodgers"}   # two lines for AAA: no call
    assert pipeline.book_starters({}, games, qbs) == {}
