from datetime import datetime

from sqlalchemy.orm import Session

from app.copy_db import copy
from app.db import AltLine, Base, OddsHistory, OddsLine, Projection, Run, Setting, make_engine


def _seed(url):
    eng = make_engine(url)
    with Session(eng) as s:
        run = Run(season=2026, week=4, backtest={"rush": {"n": 1}}, variance={"rush": [1, 0.1]}, excluded=["X"])
        run.projections.append(Projection(player_id="p", name="A", pos="RB", team="T", opp="O", home=True, kind="rush", mu=1.0, sd=2.0,
                                          vol=3.0, eff=4.0, spread=0.0, game_id="g", gameday="d", last5=[1.0]))
        s.add(run)
        s.add(OddsLine(fetched_at=datetime(2026, 10, 3), player="A", market="rush", line=1.5, over_odds=-110, under_odds=-110,
                       book="B", over_link="https://x"))
        s.add(AltLine(fetched_at=datetime(2026, 10, 3), game_id="g", player="A", market="rush", line=2.5, over_odds=300, book="B"))
        s.add(OddsHistory(fetched_at=datetime(2026, 10, 3), game_id="g", player="A", market="rush", line=1.5, over_odds=-110,
                          under_odds=-110, book="B"))
        s.add(Setting(key="hide_offshore", value={"on": True}))
        s.commit()
    return eng


def test_copy_moves_every_table_and_replaces_the_target(tmp_path):
    src, dst = f"sqlite:///{tmp_path}/src.db", f"sqlite:///{tmp_path}/dst.db"
    _seed(src)
    stale = make_engine(dst)
    with Session(stale) as s:  # something old in the target that must not survive
        s.add(OddsLine(fetched_at=datetime(2020, 1, 1), player="Old", market="rush", line=9.5, book="Z"))
        s.commit()
    counts = copy(src, dst, log=lambda m: None)
    assert "odds_history" not in counts
    assert {t: n for t, n in counts.items() if n} == {"runs": 1, "projections": 1, "odds_lines": 1, "alt_lines": 1, "settings": 1}
    assert set(counts) == set(Base.metadata.tables) - {"odds_history"}
    with Session(make_engine(dst)) as s:
        assert s.query(OddsLine).one().player == "A" and s.query(OddsLine).one().over_link == "https://x"
        assert s.query(Run).one().excluded == ["X"] and s.query(Projection).one().last5 == [1.0]
        assert s.query(AltLine).one().over_odds == 300
        assert s.query(Setting).one().value == {"on": True} and s.query(OddsHistory).count() == 0


def test_with_history_moves_odds_history_too(tmp_path):
    src, dst = f"sqlite:///{tmp_path}/src.db", f"sqlite:///{tmp_path}/dst.db"
    _seed(src)
    assert copy(src, dst, log=lambda m: None, with_history=True)["odds_history"] == 1
    with Session(make_engine(dst)) as s:
        assert s.query(OddsHistory).one().line == 1.5


def test_copy_refuses_to_copy_a_database_onto_itself(tmp_path):
    import pytest
    url = f"sqlite:///{tmp_path}/a.db"
    _seed(url)
    with pytest.raises(SystemExit):
        copy(url, url)


def test_without_accounts_leaves_visitors_data_out_and_the_targets_own_alone(tmp_path):
    from app.db import Bet, BetLeg, User
    src, dst = f"sqlite:///{tmp_path}/src.db", f"sqlite:///{tmp_path}/dst.db"
    with Session(_seed(src)) as s:   # production: a real person with a tracked bet
        s.add(User(email="real.person@example.com"))
        s.flush()
        bet = Bet(user_id=s.query(User).one().id, league="nfl", book="B", kind="single", stake=10, odds=100, status="open")
        bet.legs = [BetLeg(game_id="g", gameday="d", kickoff=datetime(2026, 10, 4), player_id="p", name="A", team="T", opp="O",
                           kind="rush", side="Over", line=1.5, odds=100)]
        s.add(bet)
        s.commit()
    with Session(make_engine(dst)) as s:   # the laptop's own test account
        s.add(User(email="laptop.tester@example.com"))
        s.commit()
    counts = copy(src, dst, log=lambda m: None, with_accounts=False)
    assert not {"users", "bets", "bet_legs", "user_sessions", "login_tokens"} & set(counts) and counts["runs"] == 1
    with Session(make_engine(dst)) as s:
        assert [u.email for u in s.query(User)] == ["laptop.tester@example.com"] and s.query(Bet).count() == 0
        assert s.query(Run).count() == 1   # the site's data still came across


def test_a_table_the_source_lacks_is_left_empty(tmp_path):
    from sqlalchemy import text
    src, dst = f"sqlite:///{tmp_path}/src.db", f"sqlite:///{tmp_path}/dst.db"
    eng = _seed(src)
    with eng.begin() as c:   # a source from before nba_run_tables existed
        c.execute(text("DROP TABLE nba_run_tables"))
    logs = []
    counts = copy(src, dst, log=logs.append)
    assert counts["nba_run_tables"] == 0 and counts["runs"] == 1
    assert any("nba_run_tables: not in the source yet" in m for m in logs)
