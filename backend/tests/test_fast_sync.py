"""Fast sync reuses the saved calibration; a full run refits it. Both give the same projections for the same data."""
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from app import pipeline
from app.db import Run, SessionLocal

TEAMS = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]
ROLES = ["QB", "RB1", "RB2", "WR1", "WR2", "WR3", "TE"]
POS = {"QB": "QB", "RB1": "RB", "RB2": "RB", "WR1": "WR", "WR2": "WR", "WR3": "WR", "TE": "TE"}


def pairings(season, week):
    perm = np.random.default_rng(season * 100 + week).permutation(TEAMS)
    return [(perm[0], perm[1]), (perm[2], perm[3]), (perm[4], perm[5])]


def league():
    """Six teams, two seasons of weekly stats, and a schedule whose last week (2026 week 5) hasn't been played."""
    rng = np.random.default_rng(1)
    base = {"QB": (33, 0, 3), "RB1": (0, 4, 16), "RB2": (0, 2, 7), "WR1": (0, 8, 0), "WR2": (0, 6, 0), "WR3": (0, 4, 0), "TE": (0, 5, 0)}
    stats, games = [], []
    for season, weeks in ((2025, range(1, 19)), (2026, range(1, 6))):
        for week in weeks:
            for home, away in pairings(season, week):
                played = not (season == 2026 and week == 5)
                games.append(dict(season=season, week=week, game_type="REG", home_team=home, away_team=away,
                                  home_score=float(rng.integers(10, 35)) if played else np.nan,
                                  away_score=float(rng.integers(10, 35)) if played else np.nan,
                                  spread_line=float(rng.integers(-6, 7)), total_line=45.0, gameday=f"{season}-10-{week:02d}",
                                  gametime="13:00", game_id=f"{season}_{week:02d}_{away}_{home}"))
                if not played:
                    continue
                for team, opp in ((home, away), (away, home)):
                    for role in ROLES:
                        att, tgt, car = base[role]
                        carries, targets, attempts = rng.poisson(car), rng.poisson(tgt), (rng.poisson(att) if att else 0)
                        stats.append(dict(player_id=f"{team}_{role}", player_display_name=f"{team} {role}", position=POS[role],
                                          season=season, week=week, season_type="REG", team=team, opponent_team=opp,
                                          carries=carries, rushing_yards=float(rng.gamma(4, 1.1 * max(carries, 0.1))),
                                          rushing_tds=int(rng.random() < 0.05 * carries), targets=targets, receptions=round(0.65 * targets),
                                          receiving_yards=float(rng.gamma(4, 1.8 * max(targets, 0.1))),
                                          receiving_tds=int(rng.random() < 0.06 * targets), attempts=attempts,
                                          passing_yards=float(rng.gamma(30, 7.0 * max(attempts, 0.1) / 30)) if attempts else 0.0,
                                          passing_tds=int(rng.poisson(0.045 * attempts))))
    return pd.DataFrame(stats), pd.DataFrame(games)


def catches(stats):
    """One row per catch, its yards drawn around the player-game's yards per catch (play-by-play stand-in)."""
    rng = np.random.default_rng(3)
    out = [dict(player_id=r.player_id, season=r.season, week=r.week, yards=float(y))
           for r in stats[stats.receptions > 0].itertuples() for y in rng.exponential(r.receiving_yards / r.receptions, r.receptions)]
    return pd.DataFrame(out)


def rushes(stats):
    """One row per carry, like catches()."""
    rng = np.random.default_rng(4)
    out = [dict(player_id=r.player_id, season=r.season, week=r.week, yards=float(y))
           for r in stats[stats.carries > 0].itertuples() for y in rng.exponential(r.rushing_yards / r.carries, r.carries)]
    return pd.DataFrame(out)


def first_tds(stats):
    """Each game's first touchdown: one of its scorers, at random."""
    rng = np.random.default_rng(5)
    scored = stats[(stats.rushing_tds + stats.receiving_tds) > 0]
    games = scored.assign(game=[tuple(sorted((a, b))) for a, b in zip(scored.team, scored.opponent_team)])
    out = [g.iloc[rng.integers(len(g))] for _, g in games.groupby(["season", "week", "game"])]
    return pd.DataFrame([dict(player_id=r.player_id, season=r.season, week=r.week, game_id="") for r in out])


@pytest.fixture
def synthetic(monkeypatch):
    stats, sched = league()
    roster = {f"{t}_{r}": t for t in TEAMS for r in ROLES}
    monkeypatch.setattr(pipeline.data, "load_schedule", lambda: sched)
    monkeypatch.setattr(pipeline.data, "load_stats", lambda seasons, usecols=None: stats)
    monkeypatch.setattr(pipeline.data, "load_catches", lambda seasons: catches(stats))
    monkeypatch.setattr(pipeline.data, "load_rushes", lambda seasons: rushes(stats))
    monkeypatch.setattr(pipeline.data, "load_first_tds", lambda seasons: first_tds(stats))
    monkeypatch.setattr(pipeline.data, "load_injuries", lambda season: pd.DataFrame())
    monkeypatch.setattr(pipeline.data, "load_reserve", lambda season: pd.DataFrame())
    monkeypatch.setattr(pipeline.data, "load_espn_injuries", lambda: (pd.DataFrame(columns=pipeline.data.ESPN_COLUMNS), None))
    monkeypatch.setattr(pipeline.data, "load_roster", lambda season: roster)
    with SessionLocal() as s:
        before = s.query(Run).count()
        first_new = (s.query(Run.id).order_by(Run.id.desc()).limit(1).scalar() or 0) + 1
    yield first_new
    from app.db import Projection
    with SessionLocal() as s:                                   # leave other tests' runs alone (a bulk delete skips cascades)
        s.query(Projection).filter(Projection.run_id >= first_new).delete()
        s.query(Run).filter(Run.id >= first_new).delete()
        s.commit()


def rows(run_id):
    from app.db import Projection
    with SessionLocal() as s:
        return sorted((p.player_id, p.kind, round(p.mu, 6), round(p.sd, 6), round(p.vol, 6))
                      for p in s.query(Projection).filter(Projection.run_id == run_id))


def calibration(run_id):
    with SessionLocal() as s:
        return s.get(Run, run_id).variance["_calibration"]


def test_the_first_run_fits_the_calibration_and_records_when(synthetic):
    log = []
    rid = pipeline.run_projections(log=log.append)
    assert any("Walk-forward backtest" in l for l in log)
    cal = calibration(rid)
    assert cal["season"] == 2026 and cal["week"] == 5 and "reused_from_run" not in cal
    with SessionLocal() as s:
        v = s.get(Run, rid).variance
        assert all(isinstance(v[k], dict) for k in ("rush", "rec", "pass", "rr")) and "rho" in v["_redistribution"]


def test_a_fast_run_reuses_the_saved_calibration_and_gives_identical_projections(synthetic):
    full_id = pipeline.run_projections(log=lambda m: None)
    log = []
    fast_id = pipeline.run_projections(log=log.append)
    assert any("Using the saved calibration" in l for l in log) and not any("Walk-forward backtest" in l for l in log)
    assert rows(fast_id) == rows(full_id) and len(rows(fast_id)) > 0
    cal = calibration(fast_id)
    assert cal["reused_from_run"] == full_id and cal["at"] == calibration(full_id)["at"]   # still dated from the real fit
    with SessionLocal() as s:                                                              # the stats shown on Model check carry over
        assert s.get(Run, fast_id).backtest == s.get(Run, full_id).backtest


def test_full_refits_even_when_a_saved_calibration_exists(synthetic):
    first = pipeline.run_projections(log=lambda m: None)
    log = []
    again = pipeline.run_projections(log=log.append, full=True)
    assert any("Walk-forward backtest" in l for l in log)
    assert calibration(again)["at"] > calibration(first)["at"] and "reused_from_run" not in calibration(again)


def test_an_old_calibration_is_refit_whatever_was_asked(synthetic):
    first = pipeline.run_projections(log=lambda m: None)
    with SessionLocal() as s:
        run = s.get(Run, first)
        v = dict(run.variance)
        v["_calibration"] = dict(v["_calibration"], at=(datetime.utcnow() - timedelta(days=pipeline.CALIBRATION_MAX_AGE_DAYS + 5)).isoformat())
        run.variance = v
        s.commit()
    log = []
    pipeline.run_projections(log=log.append)
    assert any("Walk-forward backtest" in l for l in log)


def test_a_calibration_fitted_for_an_earlier_week_is_refit(synthetic):
    """A missed Tuesday refit: the first run of the new week refits rather than reusing last week's tables."""
    first = pipeline.run_projections(log=lambda m: None)
    assert pipeline.reusable_calibration(2026, week=5) is not None
    assert pipeline.reusable_calibration(2026, week=6) is None
    with SessionLocal() as s:
        run = s.get(Run, first)
        v = dict(run.variance)
        v["_calibration"] = dict(v["_calibration"], week=4)   # fitted the week before this target week
        run.variance = v
        s.commit()
    log = []
    again = pipeline.run_projections(log=log.append)
    assert any("Walk-forward backtest" in l for l in log) and calibration(again)["week"] == 5


def test_a_run_from_another_season_or_without_a_calibration_is_not_reused(synthetic):
    pipeline.run_projections(log=lambda m: None)
    assert pipeline.reusable_calibration(2026) is not None
    assert pipeline.reusable_calibration(2025) is None                       # different season
    with SessionLocal() as s:
        s.add(Run(season=2026, week=5, backtest={}, variance={}, excluded=[]))   # newest run has nothing to reuse
        s.commit()
    assert pipeline.reusable_calibration(2026) is None


def test_the_saved_redistribution_share_keeps_full_precision_so_reuse_changes_nothing():
    base = pd.DataFrame({"player_id": ["a"], "t": [1], "kind": ["rush"], "mu": [50.0], "actual": [55.0], "affected": [True]})
    out = pipeline._redistribution_summary(base, base, {"rush": 0.61234567891, "rec": 0.3, "pass": 0.3}, {"rush": 0.3980493, "rec": 0.0, "pass": 0.0})
    assert out["rho"]["rush"] == 0.3980493 and out["rho_fit"]["rush"] == 0.61234567891


WINDY = {"pass": dict(wind=-0.02, cold=0.0), "rec": dict(wind=-0.01, cold=0.0), "rush": dict(wind=0.01, cold=0.0)}


def _outdoor_schedule(monkeypatch, wind):
    """The synthetic league at outdoor stadiums, with the unplayed week kicking off today in `wind` mph."""
    from datetime import date
    _, sched = league()
    rng = np.random.default_rng(3)
    played = sched.home_score.notna()
    sched = sched.assign(roof="outdoors", stadium_id="BUF00", stadium="Highmark",
                         temp=np.where(played, rng.uniform(30, 80, len(sched)), np.nan),
                         wind=np.where(played, rng.uniform(0, 20, len(sched)), np.nan))
    sched.loc[~played, "gameday"] = date.today().isoformat()
    monkeypatch.setattr(pipeline.data, "load_schedule", lambda: sched)
    monkeypatch.setattr(pipeline.wxm, "fit", lambda bt: WINDY)
    monkeypatch.setattr(pipeline.wxm, "fetch_point", lambda lat, lon, day, gametime, **kw:
                        dict(temp=60.0, wind=wind, gust=wind * 1.5, precip_prob=0.0, precip=0.0, snow=0.0))


def _by_kind(run_id):
    from app.db import Projection
    with SessionLocal() as s:
        return {(p.player_id, p.kind): (p.mu, p.wx) for p in s.query(Projection).filter(Projection.run_id == run_id)}


def test_forecast_wind_scales_projections_and_is_stored_with_the_run(synthetic, monkeypatch):
    _outdoor_schedule(monkeypatch, wind=5.0)
    calm = _by_kind(pipeline.run_projections(log=lambda m: None, full=True))
    _outdoor_schedule(monkeypatch, wind=20.0)
    log = []
    rid = pipeline.run_projections(log=log.append)                       # fast: reuses the saved weather fit
    windy = _by_kind(rid)
    assert any("Using the saved calibration" in m for m in log)
    for (pid, kind), (mu, f) in windy.items():
        expect = {"pass": 0.8, "rec": 0.9, "rush": 1.1, "td": 1.0}.get(kind)
        if expect is not None:
            assert f == pytest.approx(expect) and mu == pytest.approx(calm[(pid, kind)][0] * expect)
        assert calm[(pid, kind)][1] == 1.0                                # under 10 mph: untouched
    assert any(k == "rr" and 0.9 < f < 1.1 for (_, k), (_, f) in windy.items())
    with SessionLocal() as s:
        run = s.get(Run, rid)
        assert run.variance["_weather"]["coef"] == WINDY
        w = next(iter(run.weather.values()))
        assert w["source"] == "forecast" and w["wind"] == 20.0 and w["factors"]["pass"] == pytest.approx(0.8)


def test_a_calibration_from_before_weather_is_refit(synthetic):
    first = pipeline.run_projections(log=lambda m: None)
    with SessionLocal() as s:
        run = s.get(Run, first)
        run.variance = {k: v for k, v in run.variance.items() if k != "_weather"}
        s.commit()
    assert pipeline.reusable_calibration(2026) is None


def test_a_rest_day_dnp_keeps_the_player_but_an_injury_dnp_drops_him(synthetic, monkeypatch):
    """A veteran's 'Not injury related - resting player' DNP says nothing about Sunday (Higbee played after three of them)."""
    def report(pid, why):
        return dict(gsis_id=pid, team=pid[:3], week=5, report_status=np.nan, practice_status="Did Not Participate In Practice",
                    practice_primary_injury=why)
    inj = pd.DataFrame([report("AAA_TE", "Not injury related - resting player"), report("BBB_TE", "Knee")])
    monkeypatch.setattr(pipeline.data, "load_injuries", lambda season: inj)
    projected = {pid for pid, *_ in rows(pipeline.run_projections(log=lambda m: None))}
    assert "AAA_TE" in projected and "BBB_TE" not in projected


def test_a_calibration_fitted_before_the_quarterback_receiver_adjustment_is_refit(synthetic):
    from app.engine import model
    pipeline.run_projections(log=lambda m: None)
    assert pipeline.reusable_calibration(2026) is not None
    with SessionLocal() as s:
        run = s.query(Run).order_by(Run.id.desc()).first()
        v = dict(run.variance)
        v["_calibration"] = {k: x for k, x in v["_calibration"].items() if k != "qb_rec_k"}
        run.variance = v
        s.commit()
    assert pipeline.reusable_calibration(2026) is None
    assert model.QB_REC_K > 0


def test_rows_without_a_qb_cut_store_an_empty_name_not_nan():
    assert pipeline._str_or_empty(float("nan")) == "" and pipeline._str_or_empty(None) == ""
    assert pipeline._str_or_empty("Joe Fagnano") == "Joe Fagnano"
    assert pipeline._float_or_none(float("nan")) is None and pipeline._float_or_none(0.84) == 0.84
