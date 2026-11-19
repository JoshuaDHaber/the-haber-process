"""Teammate volume redistribution: who counts as absent, how much moves, and who receives it."""
import numpy as np
import pandas as pd
import pytest

from app.engine import model, redistribute as rd

COLS = ["player_id", "player_display_name", "position", "team", "opponent_team", "season", "week", "carries", "targets",
        "attempts", "rushing_yards", "receiving_yards", "passing_yards", "rushing_tds", "receiving_tds", "receptions"]


def row(pid, pos, week, carries=0, targets=0, attempts=0, team="AAA"):
    return dict(player_id=pid, player_display_name=pid, position=pos, team=team, opponent_team="BBB", season=2026, week=week,
                carries=carries, targets=targets, receptions=round(0.65 * targets), attempts=attempts, rushing_yards=4.5 * carries, receiving_yards=7.0 * targets,
                passing_yards=7.0 * attempts, rushing_tds=0, receiving_tds=0)


def league(rb1_weeks=range(1, 7), weeks=range(1, 9)):
    """AAA: a 15-carry starter (rb1), a 6-carry backup (rb2), a QB and a WR. rb1 only plays rb1_weeks."""
    rows = []
    for w in weeks:
        if w in rb1_weeks:
            rows.append(row("rb1", "RB", w, carries=15, targets=3))
        rows += [row("rb2", "RB", w, carries=6, targets=2), row("qb", "QB", w, carries=5, attempts=33),
                 row("wr", "WR", w, targets=8)]
        rows.append(row("zz", "RB", w, carries=10, targets=1, team="CCC"))  # another team's back, never involved
    return model.prep(pd.DataFrame(rows, columns=COLS))


def sched(weeks=range(1, 10)):
    return pd.DataFrame([dict(season=2026, week=w, home_team="AAA", away_team="BBB", spread_line=0.0, total_line=45.0,
                              home_score=np.nan if w == 9 else 24.0, gameday="2026-10-04", gametime="13:00",
                              game_id=f"2026_{w:02d}_BBB_AAA") for w in weeks])


# ---------- pieces ----------
def test_how_much_of_an_absence_teammates_history_already_shows_grows_with_time_out():
    fracs = [rd.reflected_fraction(m) for m in range(0, 10)]
    assert fracs[0] == 0.0 and all(a < b for a, b in zip(fracs, fracs[1:])) and fracs[-1] < 1.0


def test_volume_competes_within_a_position_group():
    assert rd.group("rush", "RB") == rd.group("rush", "FB") == "RB" and rd.group("rush", "QB") == "QB"
    assert rd.group("rec", "WR") == "WR" and rd.group("rec", "TE") == "TE" and rd.group("rec", "RB") == "RB"


def test_absent_regulars_are_found_with_how_many_games_they_have_missed():
    df = league(rb1_weeks=range(1, 6))                       # rb1 plays weeks 1-5, then is gone
    logs = rd.Logs(df)
    active6 = set(df[df.t == 202606].player_id)
    found = {pid: missed for pid, _, missed in rd.find_absent(logs, "AAA", 202606, active6)}
    assert found == {"rb1": 0}                               # first missed game
    active8 = set(df[df.t == 202608].player_id)
    assert {pid: m for pid, _, m in rd.find_absent(logs, "AAA", 202608, active8)} == {"rb1": 2}   # missed two before this one
    assert rd.find_absent(logs, "AAA", 202606, active6 | {"rb1"}) == []                          # active players are never absent
    assert rd.find_absent(logs, "CCC", 202606, active6) == []                                    # another team's problem


def test_someone_gone_too_long_is_fully_reflected_and_ignored():
    df = league(rb1_weeks=range(1, 2), weeks=range(1, 14))   # rb1 played week 1 only
    logs = rd.Logs(df)
    assert rd.find_absent(logs, "AAA", 202613, set(df[df.t == 202613].player_id)) == []


def test_lost_volume_counts_regulars_only_net_of_what_history_shows():
    hist = pd.DataFrame({"position": ["RB"]})
    comp = lambda h: {"rush": (60.0, 15.0, 4.0), "rec": (10.0, 2.0, 5.0)}       # 15 carries; 2 targets is not a regular
    fresh = rd.lost_volume([("rb1", hist, 0)], comp)
    assert fresh == {("rush", "RB"): 15.0}
    assert rd.lost_volume([("rb1", hist, 3)], comp)[("rush", "RB")] == pytest.approx(15.0 * (1 - rd.reflected_fraction(3)))
    two = rd.lost_volume([("a", hist, 0), ("b", hist, 0)], comp)
    assert two[("rush", "RB")] == 30.0
    assert rd.lost_volume([("rb1", hist, 0)], lambda h: {"rush": (10.0, 5.0, 2.0)}) == {}   # a 5-carry guy isn't a regular


def test_the_lift_is_proportional_and_capped():
    assert rd.lift("rush", 0.6, lost=10, active_volume=20) == pytest.approx(1.3)
    assert rd.lift("rush", 1.0, lost=100, active_volume=10) == rd.LIFT_CAP["rush"]       # capped
    assert rd.lift("pass", 1.0, lost=100, active_volume=10) == 11.0                       # attempts aren't capped
    assert rd.lift("rush", 0.6, lost=0, active_volume=20) == 1.0 and rd.lift("rush", 0.6, lost=5, active_volume=0) == 1.0


def test_rho_is_fit_as_a_slope_and_only_helpful_roles_are_applied():
    rng = np.random.default_rng(0)
    z = rng.uniform(0, 5, 300)
    recs = [("rush", float(v), float(0.7 * v + rng.normal(0, 0.3)), 202601) for v in z] + [("rec", 1.0, 1.0, 202601)] * 10
    fit = rd.fit_rho(recs)
    assert fit["rush"] == pytest.approx(0.7, abs=0.1) and fit["rec"] == rd.DEFAULT_RHO["rec"]  # too little data: default
    used = rd.applied({"rush": 0.7, "rec": 0.3, "pass": 0.3})
    assert used == {"rush": pytest.approx(0.7 * rd.SHRINK), "rec": 0.0, "pass": 0.0}


# ---------- in the backtest ----------
def test_backtest_hands_an_absent_backs_carries_to_his_backup_and_not_to_other_positions():
    df = league(rb1_weeks=range(1, 6))
    s = sched()
    pri = model.league_priors(df)
    base = model.walk_forward(df, pri, s, 202606, rho=None, records=[])
    moved = model.walk_forward(df, pri, s, 202606, rho={"rush": 0.7, "rec": 0.0, "pass": 0.0})

    def mu(frame, pid, t, kind="rush"):
        return float(frame[(frame.player_id == pid) & (frame.t == t) & (frame.kind == kind)].mu.iloc[0])

    assert mu(moved, "rb2", 202606) > mu(base, "rb2", 202606) * 1.4          # the backup inherits carries
    assert mu(moved, "qb", 202606) == pytest.approx(mu(base, "qb", 202606))  # the QB is a different position group
    assert mu(moved, "zz", 202606) == pytest.approx(mu(base, "zz", 202606))  # another team is untouched
    assert bool(base[(base.player_id == "rb2") & (base.t == 202606)].affected.all())


def test_nothing_changes_when_nobody_is_absent_or_rho_is_off():
    df = league(rb1_weeks=range(1, 9))                       # rb1 never misses
    s, pri = sched(), model.league_priors(df)
    a = model.walk_forward(df, pri, s, 202606)
    b = model.walk_forward(df, pri, s, 202606, rho={"rush": 0.7, "rec": 0.0, "pass": 0.0})
    pd.testing.assert_series_equal(a.mu, b.mu)
    df2 = league(rb1_weeks=range(1, 6))
    c = model.walk_forward(df2, model.league_priors(df2), s, 202606)
    d = model.walk_forward(df2, model.league_priors(df2), s, 202606, rho={"rush": 0.0, "rec": 0.0, "pass": 0.0})
    pd.testing.assert_series_equal(c.mu, d.mu)               # rho of zero is a no-op


# ---------- live: the upcoming week ----------
def test_upcoming_projections_lift_teammates_of_players_ruled_out():
    df = league(rb1_weeks=range(1, 9))                       # rb1 has played every game through week 8
    s, pri = sched(), model.league_priors(df)
    dist = {k: dict(mu=[1.0, 200.0], sd=[10.0, 10.0], z=[float(z) for z in np.linspace(-2, 2, 41)]) for k in ("rush", "rec", "pass", "rr", "recs")}
    roster = {pid: "AAA" for pid in ("rb1", "rb2", "qb", "wr")}
    rho = {"rush": 0.7, "rec": 0.0, "pass": 0.0}

    def rb2(**kw):
        p = model.upcoming_projections(df, s, pri, dist, 202609, roster, **kw)
        return float(p[(p.player_id == "rb2") & (p.kind == "rush")].mu.iloc[0]), p

    healthy, _ = rb2(rho=rho, absent_ids=set())
    out, p_out = rb2(rho=rho, absent_ids={"rb1"})
    off, _ = rb2(rho=None, absent_ids={"rb1"})
    assert out > healthy * 1.3                                # rb1 ruled out: rb2 picks up his carries
    assert off == pytest.approx(healthy)                      # without redistribution nothing moves
    assert float(p_out[(p_out.player_id == "qb") & (p_out.kind == "rush")].mu.iloc[0]) == pytest.approx(
        float(rb2(rho=rho, absent_ids=set())[1].query("player_id == 'qb' and kind == 'rush'").mu.iloc[0]))   # QB untouched


# ---------- quarterbacks: the replacement takes over ----------
def qb_league(qb1_weeks, weeks=range(1, 9)):
    """AAA: a 33-attempt starter (qb1) who only plays qb1_weeks, a backup (qb2) who throws 2 a game and a third-stringer (qb3) 1."""
    rows = []
    for w in weeks:
        if w in qb1_weeks:
            rows.append(row("qb1", "QB", w, carries=3, attempts=33))
        rows += [row("qb2", "QB", w, attempts=2), row("qb3", "QB", w, attempts=1), row("rb", "RB", w, carries=15, targets=3),
                 row("wr", "WR", w, targets=8)]
        rows.append(row("zz", "QB", w, attempts=30, team="CCC"))
    return model.prep(pd.DataFrame(rows, columns=COLS))


def test_a_missing_starting_qbs_attempts_are_not_summed_or_discounted():
    hist = pd.DataFrame({"position": ["QB"]})
    comp = lambda h: {"pass": (230.0, 33.0, 7.0)}
    assert rd.lost_volume([("a", hist, 0)], comp) == {("pass", "QB"): 33.0}
    assert rd.lost_volume([("a", hist, 4)], comp) == {("pass", "QB"): 33.0}               # no discount for time out
    two = rd.lost_volume([("a", hist, 0), ("b", hist, 0)], lambda h: {"pass": (200.0, 30.0, 7.0)})
    assert two == {("pass", "QB"): 30.0}                                                  # one replacement plays, so max not sum


def member(pid, pos, vol, mu, kind="pass"):
    return dict(pid=pid, pos=pos, comp={kind: (mu, vol, 7.0)}, row=None)


def test_the_lead_replacement_qb_takes_over_and_the_rest_of_the_depth_chart_does_not():
    lost = {("pass", "QB"): 30.0}
    qb2, qb3 = member("qb2", "QB", 2.0, 14.0), member("qb3", "QB", 1.0, 7.0)
    model._lift_members([qb2, qb3], lost, rho={"rush": 0.5, "rec": 0.0, "pass": 0.0})
    assert qb2["comp"]["pass"] == (pytest.approx(14.0 * 15), 30.0, 7.0)                    # 2 -> 30 attempts, yards scale with them
    assert qb3["comp"]["pass"] == (7.0, 1.0, 7.0)                                         # third string unchanged
    ready = member("qb2", "QB", 40.0, 280.0)                                              # an established starter already throws more
    model._lift_members([ready], lost, rho={"rush": 0.5, "rec": 0.0, "pass": 0.0})
    assert ready["comp"]["pass"] == (280.0, 40.0, 7.0)
    off = member("qb2", "QB", 2.0, 14.0)
    model._lift_members([off], lost, rho=None)                                            # redistribution off: nothing moves
    assert off["comp"]["pass"] == (14.0, 2.0, 7.0)
    only = member("qb2", "QB", 2.0, 14.0); other = member("qb3", "QB", 1.0, 7.0)
    model._lift_members([only, other], lost, rho={"rush": 0.5, "rec": 0.0, "pass": 0.0}, only={"qb3"})
    assert only["comp"]["pass"][1] == 2.0 and other["comp"]["pass"][1] == 30.0            # a flagged-out QB can't be the replacement


def test_in_the_backtest_the_backup_throws_the_starters_attempts_when_the_starter_is_out():
    df = qb_league(qb1_weeks=range(1, 6))                          # qb1 is out from week 6
    s, pri = sched(), model.league_priors(df)
    base = model.walk_forward(df, pri, s, 202606, rho=None, records=[])
    moved = model.walk_forward(df, pri, s, 202606, rho={"rush": 0.0, "rec": 0.0, "pass": 0.0})

    def passrow(frame, pid):
        r = frame[(frame.player_id == pid) & (frame.t == 202606) & (frame.kind == "pass")]
        return None if r.empty else float(r.mu.iloc[0])

    assert passrow(base, "qb2") is None                                  # two attempts a game: not enough to project
    assert passrow(moved, "qb2") is not None and passrow(moved, "qb2") > 150          # now a starter's workload
    assert passrow(moved, "qb3") is None                                 # still a third-stringer
    assert passrow(moved, "zz") == pytest.approx(passrow(base, "zz"))    # another team untouched


def test_when_the_starter_is_healthy_the_backup_stays_a_backup():
    df = qb_league(qb1_weeks=range(1, 9))
    s, pri = sched(), model.league_priors(df)
    moved = model.walk_forward(df, pri, s, 202606, rho={"rush": 0.6, "rec": 0.0, "pass": 0.0})
    assert moved[(moved.player_id == "qb2") & (moved.kind == "pass")].empty


def test_live_a_ruled_out_starter_makes_his_backup_the_projected_starter():
    df = qb_league(qb1_weeks=range(1, 9))                          # qb1 has started every game so far
    s, pri = sched(), model.league_priors(df)
    dist = {k: dict(mu=[1.0, 400.0], sd=[40.0, 40.0], z=[float(z) for z in np.linspace(-2, 2, 41)]) for k in ("rush", "rec", "pass", "rr", "recs")}
    roster = {pid: "AAA" for pid in ("qb1", "qb2", "qb3", "rb", "wr")}
    rho = {"rush": 0.5, "rec": 0.0, "pass": 0.0}

    def passes(**kw):
        p = model.upcoming_projections(df, s, pri, dist, 202609, roster, **kw)
        return {r.player_id: r.mu for r in p[p.kind == "pass"].itertuples()}

    healthy, out = passes(rho=rho, absent_ids=set()), passes(rho=rho, absent_ids={"qb1"})
    assert "qb2" not in healthy and "qb1" in healthy
    assert out["qb2"] > 150 and "qb3" not in out                  # qb2 is projected for a starter's workload, qb3 is not
    assert passes(rho=None, absent_ids={"qb1"}).get("qb2") is None  # off means off


# ---------- quarterbacks: only the expected starter is projected ----------
def starts(*pairs):
    return pd.DataFrame(pairs, columns=["player_id", "t"])


def test_the_starter_is_whoever_made_the_teams_latest_start():
    games = [202601, 202602, 202603, 202604]
    assert rd.pick_starter(starts(("a", 202601), ("a", 202602), ("b", 202603), ("b", 202604)), games, {"a", "b"}) == "b"   # benched
    assert rd.pick_starter(starts(("a", 202601)), games, {"b"}) is None                                                  # nobody to judge by


def test_a_starter_back_from_injury_gets_his_job_back():
    """Darnold: hurt in week 1 (Lock threw 22 of 24), out week 2, back week 3."""
    games = [202517, 202518, 202601, 202602]
    log = starts(("darnold", 202517), ("darnold", 202518), ("lock", 202601), ("lock", 202602))
    ruled = {(202602, "darnold")}
    assert rd.pick_starter(log, games, {"darnold", "lock"}, ruled) == "darnold"
    assert rd.pick_starter(log, games, {"darnold", "lock"}) == "lock"          # without the injury report, Lock looks like the starter


def test_a_new_season_looks_past_a_week_18_rest_game():
    games = [202516, 202517, 202518]
    log = starts(("allen", 202516), ("allen", 202517), ("trubisky", 202518))
    assert rd.pick_starter(log, games, {"allen", "trubisky"}, season=2026) == "allen"
    assert rd.pick_starter(log, games, {"allen", "trubisky"}, season=2025) == "trubisky"   # within the season it's the latest start


def test_a_qbs_volume_comes_from_his_starts_not_the_game_he_got_hurt_in():
    rows = [row("qb1", "QB", w, attempts=a) for w, a in ((1, 34), (2, 33), (3, 2), (5, 35))]
    rows += [row("qb2", "QB", w, attempts=a) for w, a in ((3, 30), (4, 31))]
    df = model.prep(pd.DataFrame(rows, columns=COLS))
    assert df.set_index(["player_id", "week"]).qb_start.to_dict() == {
        ("qb1", 1): True, ("qb1", 2): True, ("qb1", 3): False, ("qb1", 5): True, ("qb2", 3): True, ("qb2", 4): True}
    hist = df[df.player_id == "qb1"]
    assert list(model.starts_only(hist, "QB")._w) == [1.0, 1.0, model.NONSTART_W, 1.0]
    assert "_w" not in model.starts_only(hist, "WR")                                  # only quarterbacks
    full, weighted = (model.project_player(h, "pass", 7.0)[1] for h in (hist, model.starts_only(hist, "QB")))
    assert weighted > full + 3                                                        # the 2-attempt exit drags him down less


def test_live_a_backup_is_not_projected_unless_the_starter_is_questionable():
    df = qb_league(qb1_weeks=range(1, 9), weeks=range(1, 9))
    df.loc[df.player_id == "qb2", "attempts"] = 18     # a backup with enough relief snaps to be projected on his own
    df = model.prep(df.drop(columns="qb_start"))
    s, pri = sched(), model.league_priors(df)
    dist = {k: dict(mu=[1.0, 400.0], sd=[40.0, 40.0], z=[float(z) for z in np.linspace(-2, 2, 41)]) for k in ("rush", "rec", "pass", "rr", "recs")}
    roster = {pid: "AAA" for pid in ("qb1", "qb2", "qb3", "rb", "wr")}
    rho = {"rush": 0.5, "rec": 0.0, "pass": 0.0}

    def qbs(**kw):
        p = model.upcoming_projections(df, s, pri, dist, 202609, roster, rho=rho, **kw)
        return set(p[p.pos == "QB"].player_id)

    assert qbs(absent_ids=set()) == {"qb1"}
    assert qbs(absent_ids=set(), questionable={"qb1"}) == {"qb1", "qb2"}
    out = qbs(absent_ids={"qb1"})                      # (qb1's own rows are dropped later, by the pipeline's status filter)
    assert "qb2" in out and "qb3" not in out


def test_a_starter_who_sat_out_a_questionable_week_gets_his_job_back_once_off_the_report():
    """Caleb Williams: out weeks 3-4, Questionable and inactive week 5, full practice week 6."""
    games = [202601, 202602, 202603, 202604, 202605]
    log = starts(("caleb", 202601), ("caleb", 202602), ("keenum", 202603), ("bagent", 202604), ("bagent", 202605))
    ruled, sat = {(202603, "caleb"), (202604, "caleb")}, {(202605, "caleb")}
    assert rd.pick_starter(log, games, {"caleb", "bagent"}, ruled) == "bagent"
    assert rd.pick_starter(log, games, {"caleb", "bagent"}, ruled, sat_out=sat) == "caleb"


def test_a_mid_season_arrival_who_was_starting_takes_over_from_a_fill_in():
    """Brissett: 8 starts for ARI in 2026, then on BAL's roster, where Fagnano had made BAL's latest 2 starts."""
    assert rd.arriving_starter("fagnano", {"fagnano": 2, "brissett": 8}, ["brissett"]) == "brissett"
    assert rd.arriving_starter(None, {"brissett": 8}, ["brissett"]) == "brissett"                 # nobody started here yet
    assert rd.arriving_starter("starter", {"starter": 8, "vet": 8}, ["vet"]) == "starter"         # signed as a backup
    assert rd.arriving_starter("fagnano", {"fagnano": 2, "vet": 0}, ["vet"]) == "fagnano"         # no starts to go on
    assert rd.arriving_starter("fagnano", {"fagnano": 2}, []) == "fagnano"


def test_live_a_traded_starter_is_projected_instead_of_the_fill_in():
    df = qb_league(qb1_weeks=range(1, 7), weeks=range(1, 9))
    df.loc[(df.player_id == "qb2") & df.week.isin([7, 8]), "attempts"] = 25   # qb2 filled in for weeks 7-8
    df = model.prep(df.drop(columns="qb_start"))
    s, pri = sched(), model.league_priors(df)
    dist = {k: dict(mu=[1.0, 400.0], sd=[40.0, 40.0], z=[float(z) for z in np.linspace(-2, 2, 41)]) for k in ("rush", "rec", "pass", "rr", "recs")}

    def qbs(roster):
        p = model.upcoming_projections(df, s, pri, dist, 202609, roster, rho={"rush": 0.5, "rec": 0.0, "pass": 0.0})
        return set(p[p.pos == "QB"].player_id)

    team = {pid: "AAA" for pid in ("qb2", "qb3", "rb", "wr")}
    assert qbs(team) == {"qb2"}
    assert qbs({**team, "zz": "AAA"}) == {"zz"}            # CCC's 8-game starter, traded to AAA


def test_live_a_starter_who_held_the_job_keeps_it_against_an_arriving_veteran():
    df = qb_league(qb1_weeks=range(1, 9), weeks=range(1, 9))
    s, pri = sched(), model.league_priors(df)
    dist = {k: dict(mu=[1.0, 400.0], sd=[40.0, 40.0], z=[float(z) for z in np.linspace(-2, 2, 41)]) for k in ("rush", "rec", "pass", "rr", "recs")}
    roster = {pid: "AAA" for pid in ("qb1", "qb2", "qb3", "rb", "wr", "zz")}
    p = model.upcoming_projections(df, s, pri, dist, 202609, roster, rho={"rush": 0.5, "rec": 0.0, "pass": 0.0})
    assert set(p[p.pos == "QB"].player_id) == {"qb1"}     # 8 starts each: the incumbent keeps the job


# ---------- receivers: "teammate out" ----------
def wr_league(wr2_weeks, weeks=range(1, 9)):
    """AAA: wr1 (8 targets), wr2 (6, plays only wr2_weeks), wr3 (2: a backup), a TE (5) and a back."""
    rows = []
    for w in weeks:
        rows += [row("wr1", "WR", w, targets=8), row("wr3", "WR", w, targets=2), row("te", "TE", w, targets=5),
                 row("rb", "RB", w, carries=15, targets=3), row("qb", "QB", w, attempts=33)]
        if w in wr2_weeks:
            rows.append(row("wr2", "WR", w, targets=6))
    return model.prep(pd.DataFrame(rows, columns=COLS))


def tags(df, absent_ids=(), roster_out=()):
    s, pri = sched(), model.league_priors(df)
    dist = {k: dict(mu=[1.0, 400.0], sd=[40.0, 40.0], z=[float(z) for z in np.linspace(-2, 2, 41)]) for k in ("rush", "rec", "pass", "rr", "recs")}
    roster = {pid: "AAA" for pid in ("wr1", "wr2", "wr3", "te", "rb", "qb") if pid not in roster_out}
    p = model.upcoming_projections(df, s, pri, dist, 202609, roster, rho={"rush": 0.5, "rec": 0.0, "pass": 0.0},
                                   absent_ids=set(absent_ids))
    return {(r.player_id, r.kind): r.teammate_out for r in p.itertuples()}


def test_a_rotation_receiver_ruled_out_tags_his_same_position_teammates_receiving():
    t = tags(wr_league(wr2_weeks=range(1, 9)), absent_ids={"wr2"})
    assert t[("wr1", "rec")] == "wr2"
    assert t[("te", "rec")] == ""                                         # other position groups untouched
    assert t[("wr1", "recs")] == ""                                       # receiving yards only
    assert t[("wr2", "rec")] == ""                                        # not on the absent player himself


def test_an_absence_off_the_roster_counts_the_same_as_one_on_the_injury_report():
    t = tags(wr_league(wr2_weeks=range(1, 9)), roster_out={"wr2"})       # e.g. placed on injured reserve
    assert t[("wr1", "rec")] == "wr2"


def test_only_the_first_game_out_is_tagged():
    t = tags(wr_league(wr2_weeks=range(1, 8)), roster_out={"wr2"})       # wr2 already missed week 8
    assert t[("wr1", "rec")] == ""


def test_a_backup_receiver_out_tags_nobody():
    df = wr_league(wr2_weeks=range(1, 9))
    t = tags(df, absent_ids={"wr3"})                                      # 2 targets a game: below the rotation line
    assert t[("wr1", "rec")] == ""


def test_thin_history_receivers_are_projected_flagged_and_leave_teammates_alone():
    """A rookie with one game (one target) is under the volume minimum: he is still projected, flagged thin."""
    base = league(rb1_weeks=range(1, 9))
    df = model.prep(pd.concat([base, pd.DataFrame([row("rook", "WR", 1, targets=1)], columns=COLS)], ignore_index=True))
    s, pri = sched(), model.league_priors(df)
    dist = {k: dict(mu=[1.0, 200.0], sd=[10.0, 10.0], z=[float(z) for z in np.linspace(-2, 2, 41)]) for k in ("rush", "rec", "pass", "rr", "recs")}
    roster = {pid: "AAA" for pid in ("rb1", "rb2", "qb", "wr", "rook")}
    p = model.upcoming_projections(df, s, pri, dist, 202609, roster)
    rook = p[p.player_id == "rook"]
    assert set(rook.kind) == {"rec", "recs"} and rook.thin.all() and (rook.mu > 0).all()
    assert not p[p.player_id != "rook"].thin.any()
    without = model.upcoming_projections(base, s, pri, dist, 202609, roster)
    key = ["player_id", "kind"]
    pd.testing.assert_series_equal(p[p.player_id != "rook"].set_index(key).mu.sort_index(), without.set_index(key).mu.sort_index())


# ---------- quarterbacks: a starter with one game, and his receivers ----------
def rookie_league():
    """AAA: qb1 starts weeks 1-7 at 8 yards an attempt and is hurt for week 8, when a rookie (qb3, no earlier games) starts
    and throws 21 for 91. CCC's QB starts every game."""
    rows = []
    for w in range(1, 9):
        if w < 8:
            rows.append(row("qb1", "QB", w, attempts=33))
        else:
            rows.append(row("qb3", "QB", w, attempts=21))
        rows += [row("wr", "WR", w, targets=8), row("rb", "RB", w, carries=15, targets=3),
                 row("zz", "QB", w, attempts=30, team="CCC"), row("zwr", "WR", w, targets=8, team="CCC")]
    df = pd.DataFrame(rows, columns=COLS)
    df.loc[df.player_id == "qb1", "passing_yards"] = 8.0 * 33
    df.loc[df.player_id == "qb3", "passing_yards"] = 91.0
    return model.prep(df)


DIST = {k: dict(mu=[1.0, 400.0], sd=[40.0, 40.0], z=[float(z) for z in np.linspace(-2, 2, 41)]) for k in ("rush", "rec", "pass", "rr", "recs")}


def test_the_passer_ratio_is_one_with_the_qb_unchanged_and_only_counts_a_downgrade():
    eff = {"a": 7.5, "b": 6.0, "c": 9.0}.get
    assert rd.passer_ratio(["a"] * 8, "a", eff) == 1.0
    assert rd.passer_ratio(["a"] * 8, "b", eff) == pytest.approx(6.0 / 7.5)
    assert rd.passer_ratio(["a"] * 8, "c", eff) == 1.0                         # a better QB taking over: no lift
    assert rd.passer_ratio(["a", "a"], "b", eff) == 1.0                         # too few games to measure
    assert rd.passer_ratio(["a"] * 8, None, eff) == 1.0
    mixed = rd.passer_ratio(["a"] * 6 + ["b"] * 2, "b", eff)                     # receivers' recent games already had b
    assert 6.0 / 7.5 < mixed < 1.0


def test_live_a_one_game_rookie_starts_for_the_injured_starter_and_is_flagged_thin():
    df = rookie_league()
    s, pri = sched(), model.league_priors(df)
    roster = {pid: "AAA" for pid in ("qb1", "qb3", "wr", "rb")} | {"zz": "CCC", "zwr": "CCC"}
    rho = {"rush": 0.5, "rec": 0.0, "pass": 0.0}
    out = model.upcoming_projections(df, s, pri, DIST, 202609, roster, rho=rho, absent_ids={"qb1"})
    qb3 = out[(out.player_id == "qb3") & (out.kind == "pass")]
    assert len(qb3) == 1 and qb3.vol.iloc[0] > 25                              # takes over the starter's ~33 attempts
    assert out[out.player_id == "qb3"].thin.all()                                # one game: shown, not priced
    assert not out[out.player_id == "wr"].thin.any()
    # qb1 back, with week 8's injury report: the rookie's start was a fill-in, and a one-game backup isn't projected
    healthy = model.upcoming_projections(df, s, pri, DIST, 202609, roster, rho=rho, absent_ids=set(), ruled_out={(202608, "qb1")})
    assert "qb3" not in set(healthy.player_id) and "qb1" in set(healthy.player_id)

    def mu(frame, pid, kind):
        return float(frame[(frame.player_id == pid) & (frame.kind == kind)].mu.iloc[0])

    eff = lambda q: model.passer_eff(df[(df.player_id == q)], pri)
    f = rd.passer_ratio(["qb1"] * 7 + ["qb3"], "qb3", eff) ** model.QB_REC_K
    assert f < 0.95
    assert mu(out, "wr", "rec") == pytest.approx(mu(healthy, "wr", "rec") * f / rd.passer_ratio(["qb1"] * 7 + ["qb3"], "qb1", eff) ** model.QB_REC_K)
    assert mu(out, "wr", "recs") == pytest.approx(mu(healthy, "wr", "recs"))    # catches left alone


def test_in_the_backtest_receivers_follow_a_downgrade_at_quarterback():
    df = rookie_league()
    rows = [row("qb1", "QB", 9, attempts=33), row("wr", "WR", 9, targets=8), row("rb", "RB", 9, carries=15, targets=3),
            row("zz", "QB", 9, attempts=30, team="CCC"), row("zwr", "WR", 9, targets=8, team="CCC")]
    later = pd.DataFrame(rows, columns=COLS)
    later.loc[later.player_id == "qb1", "passing_yards"] = 8.0 * 33
    df = model.prep(pd.concat([df.drop(columns=["t", "qb_start"]), later], ignore_index=True))
    s, pri = sched(range(1, 11)), model.league_priors(df)
    bt = model.walk_forward(df, pri, s, 202608)

    def mu(pid, t):
        return float(bt[(bt.player_id == pid) & (bt.t == t) & (bt.kind == "rec")].mu.iloc[0])

    wr8, zwr8 = mu("wr", 202608), mu("zwr", 202608)
    assert wr8 < zwr8 * 0.95                                    # same history, but the rookie starts week 8
    assert mu("wr", 202609) == pytest.approx(mu("zwr", 202609), rel=0.05)   # qb1 back in week 9: no cut (upgrades don't lift)


def test_a_qb_cut_is_stored_on_the_receiving_rows_it_touched():
    comp = {"rec": (50.0, 7.0, 7.9), "rush": (30.0, 6.0, 5.0)}
    assert model.qb_fields("rec", "WR", comp, 0.8, "rookie") == dict(qb_adj=0.8, qb_name="rookie")
    assert model.qb_fields("rr", "RB", comp, 0.8, "rookie")["qb_adj"] == pytest.approx(80.0 / (30.0 + 50.0 / 0.8))
    assert model.qb_fields("recs", "WR", comp, 0.8, "rookie") == dict(qb_adj=None, qb_name="")   # catches untouched
    assert model.qb_fields("rec", "WR", comp, 1.0, "") == dict(qb_adj=None, qb_name="")
    df = rookie_league()
    s, pri = sched(), model.league_priors(df)
    roster = {pid: "AAA" for pid in ("qb1", "qb3", "wr", "rb")}
    out = model.upcoming_projections(df, s, pri, DIST, 202609, roster, rho={"rush": 0.5, "rec": 0.0, "pass": 0.0}, absent_ids={"qb1"})
    wr = out[(out.player_id == "wr") & (out.kind == "rec")].iloc[0]
    assert wr.qb_name == "qb3" and wr.qb_adj < 0.95
    assert wr.mu == pytest.approx(wr.vol * wr.eff * wr.qb_adj * wr.wx, rel=0.02)   # the Why panel's steps multiply to mu


# ---------- trades: a regular traded away leaves his volume with his old teammates ----------
def trade_league(aaa_weeks, ccc_weeks=(), weeks=range(1, 9)):
    """AAA: wr1 (6 targets), wr3 (2: under the volume minimum), a TE (5), a back and a QB; wr2 (8 targets) plays for AAA in
    aaa_weeks and for CCC in ccc_weeks (traded)."""
    rows = []
    for w in weeks:
        rows += [row("wr1", "WR", w, targets=6), row("wr3", "WR", w, targets=2), row("te", "TE", w, targets=5),
                 row("rb", "RB", w, carries=15, targets=3), row("qb", "QB", w, attempts=33),
                 row("cwr", "WR", w, targets=7, team="CCC")]
        if w in aaa_weeks:
            rows.append(row("wr2", "WR", w, targets=8))
        if w in ccc_weeks:
            rows.append(row("wr2", "WR", w, targets=8, team="CCC"))
    return model.prep(pd.DataFrame(rows, columns=COLS))


def live(df, roster, monkeypatch, dep_rho):
    monkeypatch.setattr(rd, "DEPARTED_RHO", dep_rho)
    p = model.upcoming_projections(df, sched(), model.league_priors(df), DIST, 202609, roster,
                                   rho={"rush": 0.5, "rec": 0.0, "pass": 0.0})
    return {(r.player_id, r.kind): r.mu for r in p.itertuples()}


AAA = {pid: "AAA" for pid in ("wr1", "wr3", "te", "rb", "qb")}


def test_live_a_receiver_traded_away_hands_his_targets_to_his_old_position_group(monkeypatch):
    df = trade_league(aaa_weeks=range(1, 9))                    # wr2 played every AAA game, now on CCC's roster
    traded = {**AAA, "wr2": "CCC"}
    off = live(df, traded, monkeypatch, {"rush": 0.0, "rec": 0.0})
    on = live(df, traded, monkeypatch, {"rush": 0.6, "rec": 1.0})
    assert on[("wr1", "rec")] > off[("wr1", "rec")] * 1.3
    assert ("wr3", "rec") not in off and ("wr3", "rec") in on   # the backup clears the volume minimum and gets markets
    assert ("wr3", "recs") in on and on[("wr1", "recs")] > off[("wr1", "recs")] * 1.3   # receptions follow the targets
    assert on[("te", "rec")] == pytest.approx(off[("te", "rec")])   # targets stay in the WR group


def test_live_a_player_off_every_roster_is_an_absence_not_a_trade(monkeypatch):
    df = trade_league(aaa_weeks=range(1, 9))
    hurt = live(df, AAA, monkeypatch, {"rush": 0.6, "rec": 1.0})   # e.g. on injured reserve: no new team
    off = live(df, AAA, monkeypatch, {"rush": 0.0, "rec": 0.0})
    assert hurt[("wr1", "rec")] == pytest.approx(off[("wr1", "rec")])   # the injury rho for targets (0) applies


def test_find_departed_needs_a_new_team_and_a_recent_game_here():
    df = trade_league(aaa_weeks=range(1, 9))
    logs = rd.Logs(df)
    assert [p for p, _, missed in rd.find_departed(logs, "AAA", 202609, lambda pid, _r: {"wr2": "CCC"}.get(pid, "AAA"))] == ["wr2"]
    assert rd.find_departed(logs, "AAA", 202609, lambda pid, _r: None) == []                 # unknown: not a trade
    now = {**AAA, "wr2": "CCC", "cwr": "CCC"}
    assert rd.find_departed(logs, "CCC", 202609, lambda pid, _r: now.get(pid)) == []        # an arrival isn't gone from CCC
    old = trade_league(aaa_weeks=range(1, 2), weeks=range(1, 12))                           # gone 10 games: history shows it
    assert rd.find_departed(rd.Logs(old), "AAA", 202612, lambda pid, _r: {"wr2": "CCC"}.get(pid, "AAA")) == []


def test_backtest_hands_a_traded_receivers_targets_to_his_old_teammates_once(monkeypatch):
    monkeypatch.setattr(rd, "DEPARTED_RHO", {"rush": 0.6, "rec": 1.0})
    s = sched()

    def mu(frame, pid, t=202606, kind="rec"):
        return float(frame[(frame.player_id == pid) & (frame.t == t) & (frame.kind == kind)].mu.iloc[0])

    plays = trade_league(aaa_weeks=range(1, 6), ccc_weeks=range(6, 9))     # traded after week 5, plays for CCC at once
    base = model.walk_forward(plays, model.league_priors(plays), s, 202606)
    moved = model.walk_forward(plays, model.league_priors(plays), s, 202606, rho={"rush": 0.5, "rec": 0.0, "pass": 0.0})
    assert mu(moved, "wr1") > mu(base, "wr1") * 1.3
    assert mu(moved, "te") == pytest.approx(mu(base, "te"))
    assert mu(moved, "cwr") == pytest.approx(mu(base, "cwr"))            # his new team is untouched
    # sitting out his new team's first game, he is a trade and not also an absence: the same single hand-off
    sits = trade_league(aaa_weeks=range(1, 6), ccc_weeks=range(7, 9))
    moved2 = model.walk_forward(sits, model.league_priors(sits), s, 202606, rho={"rush": 0.5, "rec": 0.0, "pass": 0.0})
    assert mu(moved2, "wr1") == pytest.approx(mu(moved, "wr1"))


def test_live_a_trade_counts_after_he_has_already_played_for_his_new_team(monkeypatch):
    """IND 2026 week 11: Downs and Allen were traded before week 10 and played it for KC and SF."""
    df = trade_league(aaa_weeks=range(1, 8), ccc_weeks=range(8, 9))
    traded = {**AAA, "wr2": "CCC"}
    off = live(df, traded, monkeypatch, {"rush": 0.0, "rec": 0.0})
    on = live(df, traded, monkeypatch, {"rush": 0.6, "rec": 1.0})
    assert on[("wr1", "rec")] > off[("wr1", "rec")] * 1.2
    assert ("wr2", "rec") not in on                                    # and he is not projected for his old team
