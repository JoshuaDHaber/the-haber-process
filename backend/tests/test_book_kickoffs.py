import numpy as np
import pandas as pd

from app import pipeline


def _sched():
    return pd.DataFrame([
        dict(game_id="2026_11_PIT_PHI", away_team="PIT", home_team="PHI", gameday="2026-11-22", gametime="16:25", home_score=np.nan),
        dict(game_id="2026_11_NYJ_LAC", away_team="NYJ", home_team="LAC", gameday="2026-11-22", gametime="16:05", home_score=np.nan),
        dict(game_id="2026_10_PIT_CIN", away_team="PIT", home_team="CIN", gameday="2026-11-15", gametime="13:00", home_score=38.0),
        dict(game_id="2026_13_DEN_PIT", away_team="DEN", home_team="PIT", gameday="2026-11-29", gametime="13:00", home_score=np.nan),
    ])


def test_unplayed_games_take_the_books_kickoff():
    events = [dict(away="PIT", home="PHI", commence="2026-11-22T18:00:00Z"),      # moved from 4:25 to 1:00 ET
              dict(away="NYJ", home="LAC", commence="2026-11-22T21:05:00Z"),      # same time: untouched
              dict(away="PIT", home="CIN", commence="2026-11-15T21:00:00Z"),      # played: untouched
              dict(away="DEN", home="PIT", commence="2026-11-27T20:00:00Z"),      # moved to Black Friday
              dict(away="BUF", home="MIA", commence="2026-11-22T18:00:00Z")]      # not on the schedule
    logs = []
    out = pipeline.book_kickoffs(_sched(), logs.append, events).set_index("game_id")
    assert tuple(out.loc["2026_11_PIT_PHI", ["gameday", "gametime"]]) == ("2026-11-22", "13:00")
    assert tuple(out.loc["2026_11_NYJ_LAC", ["gameday", "gametime"]]) == ("2026-11-22", "16:05")
    assert tuple(out.loc["2026_10_PIT_CIN", ["gameday", "gametime"]]) == ("2026-11-15", "13:00")
    assert tuple(out.loc["2026_13_DEN_PIT", ["gameday", "gametime"]]) == ("2026-11-27", "15:00")
    assert len(logs) == 2


def test_far_off_rematch_is_not_matched():
    events = [dict(away="PIT", home="PHI", commence="2026-12-20T18:00:00Z")]
    out = pipeline.book_kickoffs(_sched(), lambda m: None, events)
    assert out.gametime.iloc[0] == "16:25"


def test_no_key_leaves_the_schedule(monkeypatch):
    monkeypatch.setattr(pipeline.config, "odds_keys", lambda league="nfl": [])
    out = pipeline.book_kickoffs(_sched(), lambda m: None)
    assert out.equals(_sched())


def test_feed_failure_leaves_the_schedule(monkeypatch):
    monkeypatch.setattr(pipeline.config, "odds_keys", lambda league="nfl": [("ODDS_API_KEY", "k")])
    def boom(key):
        raise RuntimeError("down")
    monkeypatch.setattr(pipeline.odds, "list_events", boom)
    logs = []
    out = pipeline.book_kickoffs(_sched(), logs.append)
    assert out.equals(_sched()) and "down" in logs[0]
