"""Pipeline: nflverse data -> backtest -> projections -> DB; and odds refresh -> DB."""
import copy
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from . import config, grading
from .db import AltLine, EmptyMarket, OddsHistory, OddsLine, Projection, Run, SessionLocal, clear_lines_cache
from .engine import data, model, odds
from .odds_keys import KeyRing
from .engine import correlation as cm
from .engine import injuries as injm
from .engine import moneyline as mlm
from .engine import redistribute as rd
from .engine import td as tdm
from .engine import weather as wxm

Log = Callable[[str], None]
ET = ZoneInfo("America/New_York")
ALL_MARKETS = set(odds.MARKETS.values())
ALT_KINDS = set(odds.ALT_MARKETS.values())  # anytime TD has no alternate market


CALIBRATION_MAX_AGE_DAYS = 21   # a saved calibration older than this is refit, whatever the caller asked


def reusable_calibration(season: int, now: datetime = None, week: Optional[int] = None):
    """The latest run, if it carries a calibration (error tables, TD scale, redistribution shares) that is still
    fresh enough to reuse for this season (and, given a week, was fitted for that week). Reusing it skips the backtest,
    which is almost all of a run's time.

    The backtest only scores weeks before the target week, so a refit within one week sees the same games and gives
    the same calibration. It changes once a week, when the target week moves on (Tuesday's new-week slot refits then).
    If that refit was missed (cut off, a failed sync), the first run of the new week refits instead of carrying last
    week's calibration for up to CALIBRATION_MAX_AGE_DAYS."""
    now = now or datetime.utcnow()
    with SessionLocal() as s:
        run = s.query(Run).order_by(Run.id.desc()).first()
        if not run or not run.variance:
            return None
        v = run.variance
        cal, red = v.get("_calibration"), v.get("_redistribution") or {}
        if not cal or cal.get("season") != season or "rho" not in red or not isinstance(v.get("td"), list):
            return None
        if not all(isinstance(v.get(k), dict) for k in ("rush", "rec", "pass", "rr", "recs", "ptd", "lng", "rlng")) \
                or not isinstance(v.get("ftd"), list):
            return None  # fitted before a market existed (e.g. receptions, passing TDs)
        if not isinstance((v.get("_weather") or {}).get("coef"), dict):
            return None  # fitted before weather was modeled
        if not isinstance((v.get("_correlation") or {}).get("pairs"), dict):
            return None  # fitted before same-game correlations
        if cal.get("qb_rec_k") != model.QB_REC_K:
            return None  # error tables fitted with another receiver adjustment for a downgrade at quarterback
        if (now - datetime.fromisoformat(cal["at"])).days > CALIBRATION_MAX_AGE_DAYS:
            return None
        if week is not None and cal.get("week") != week:
            return None  # fitted for an earlier week: this week's backtest has a week more of games
        return dict(variance=copy.deepcopy(v), backtest=copy.deepcopy(run.backtest), run_id=run.id)


def _float_or_none(x):
    return float(x) if x is not None and pd.notna(x) else None


def _str_or_empty(x) -> str:
    """A text column from a projection row: "" for the NaN pandas fills in on rows that never set it (TD rows)."""
    return x if isinstance(x, str) else ""


def _dvp_cols(r) -> dict:
    """A projection row's matchup-grade columns (model.dvp_fields), None where it has none."""
    f, rank, n = (getattr(r, c, None) for c in ("dvp", "dvp_rank", "dvp_n"))
    ok = rank is not None and pd.notna(rank)
    return dict(dvp=float(f) if ok else None, dvp_rank=int(rank) if ok else None, dvp_n=int(n) if ok else None)


def week_status(cur_inj: pd.DataFrame) -> dict:
    """gsis_id -> this week's game status, or "DNP" for a player who missed practice and has no status yet.

    A DNP only counts until his team's final report is out (the first game statuses, usually Friday): on that report
    a player with no status is cleared to play, however his practice week went (DK Metcalf, 2026 week 6)."""
    # a veteran's rest day ("Not injury related - resting player") is a DNP that says nothing about Sunday
    resting = cur_inj.practice_primary_injury.fillna("").str.contains("resting", case=False)
    final = cur_inj.team.isin(cur_inj.loc[cur_inj.report_status.notna(), "team"])
    dnp = (cur_inj.practice_status.fillna("").str.startswith("Did Not") & cur_inj.report_status.isna()
           & ~resting & ~final)
    return dict(zip(cur_inj.gsis_id, cur_inj.report_status.where(~dnp, "DNP")))


def last_kickoffs(played: pd.DataFrame) -> dict:
    """team -> UTC kickoff of its most recent played game."""
    kick = pd.to_datetime(played.gameday.astype(str) + " " + played.gametime.fillna("13:00").astype(str), errors="coerce")
    kick = kick.dt.tz_localize(ET, ambiguous="NaT", nonexistent="NaT").dt.tz_convert("UTC")
    both = pd.concat([pd.DataFrame(dict(team=played.home_team, kick=kick)), pd.DataFrame(dict(team=played.away_team, kick=kick))])
    return both.dropna().groupby("team").kick.max().to_dict()


def early_outs(cur_inj: pd.DataFrame, reserve: pd.DataFrame, espn: pd.DataFrame, espn_ids: dict,
               last_kick: dict, game_day: dict, names: dict) -> dict:
    """gsis_id -> "IR"/"PUP"/"NFI"/"Reserve", "Out" or "Suspension" for players ruled out before the week's report says so.

    The report only starts on Wednesday, so early in the week a player lost for the season still looks healthy to
    week_status (Lamar Jackson, 2026 week 9). This adds the roster file's reserve lists (unless this week's report
    gives him a game status) and ESPN's Out, Suspension and IR, the same sources the injury panel shows. ESPN only counts
    for a team playing this week (game_day: team -> this week's gameday) whose final report isn't out yet, and its Out
    only when posted after the team's last kickoff (an Out from before then was about that game) and not expected back
    by this game: ESPN's Tuesday refresh marks players "Out" with a return date that can be a bye week away.
    names maps (normalized name, team) -> gsis_id for ESPN rows without an id."""
    reported = cur_inj.report_status.notna() if not cur_inj.empty else pd.Series(dtype=bool)
    on_report = set(cur_inj.gsis_id[reported]) if len(reported) else set()
    final = set(cur_inj.team[reported]) if len(reported) else set()
    out = {}
    for r in reserve.itertuples():
        if r.gsis_id not in on_report:
            abbr = r.status_description_abbr
            out[r.gsis_id] = injm.RESERVE.get("" if pd.isna(abbr) else str(abbr), "Reserve")
    for e in espn.itertuples():
        status = injm.ESPN_STATUS.get(e.status)
        if status not in ("Out", "Suspension", "IR") or e.team in final or e.team not in game_day:
            continue
        pid = (espn_ids.get(str(e.espn_id)) if e.espn_id else None) or names.get((odds.norm(e.name), e.team))
        if not pid or pid in out:
            continue
        if status != "IR":
            posted, kick = pd.to_datetime(e.updated, utc=True, errors="coerce"), last_kick.get(e.team)
            if pd.isna(posted) or (kick is not None and posted < kick):
                continue
            if isinstance(e.return_date, str) and e.return_date[:10] <= str(game_day[e.team])[:10]:
                continue
        out[pid] = status
    return out


def run_projections(exclude=(), keep_dnp=False, season=None, week=None, log: Log = print, full: bool = False) -> int:
    """Compute projections for the next unplayed week and store them. Returns the run id.

    By default the previous run's calibration is reused when it is recent (seconds instead of minutes on a small
    server); full=True, or no usable calibration, refits it with the walk-forward backtest."""
    sched = data.load_schedule()
    played = sched[sched.home_score.notna()]
    cur = sched[sched.home_score.isna()].sort_values(["season", "week"]).iloc[0]
    season, week = season or int(cur.season), week or int(cur.week)
    week_t = season * 100 + week
    log(f"Target: {season} week {week}")

    seasons = [season - 2, season - 1, season]
    stats = model.prep(data.load_stats(seasons, usecols=data.PIPELINE_COLUMNS))
    # play-by-play: catches and carries for the longest reception and rush, each game's first touchdown scorer
    catches, rushes = data.load_catches(seasons), data.load_rushes(seasons)
    stats = model.add_first_td(model.add_longest(stats, catches, rushes), data.load_first_tds(seasons))
    with SessionLocal() as s:   # last week's logged picks, now that their box scores are in
        grading.grade_logged(s, stats, sched, log)
    priors = model.league_priors(stats, catches, rushes)
    td_pri = tdm.td_priors(stats)
    # who is out this week (injury report, reserve lists, ESPN before the report is out, your --exclude list): their volume
    # goes to teammates, and they are dropped below
    inj = data.load_injuries(season)
    cur_inj = inj[inj.week == week] if not inj.empty else pd.DataFrame(columns=["gsis_id", "team", "report_status"])
    status = week_status(cur_inj) if not cur_inj.empty else {}
    espn, _ = data.load_espn_injuries()
    latest = stats.sort_values("t").drop_duplicates("player_id", keep="last")
    games = sched[(sched.season == season) & (sched.week == week)]
    game_day = {**dict(zip(games.home_team, games.gameday)), **dict(zip(games.away_team, games.gameday))}
    early = early_outs(cur_inj, data.load_reserve(season), espn, data.load_espn_ids(season) if len(espn) else {},
                       last_kickoffs(played), game_day,
                       dict(zip(zip(latest.player_display_name.map(odds.norm), latest.team), latest.player_id)))
    status.update(early)
    if early:
        log(f"Ruled out before this week's report: {len(early)} players (reserve lists, ESPN)")
    drop = {"Out", "Doubtful", "Suspension"} | injm.RESERVED | (set() if keep_dnp else {"DNP"})
    # past weeks' Out/Doubtful: a start made while the starter was out is a fill-in (model.upcoming_projections)
    # and Questionable then didn't play
    ruled_out, sat_out, played_ids = set(), set(), set(zip(stats.t, stats.player_id))
    for yr, f in ((season - 1, data.load_injuries(season - 1)), (season, inj)):
        if not f.empty:
            o = f[f.report_status.isin(["Out", "Doubtful"])]
            ruled_out |= set(zip(yr * 100 + o.week.astype(int), o.gsis_id))
            q = f[f.report_status == "Questionable"]
            sat_out |= set(zip(yr * 100 + q.week.astype(int), q.gsis_id)) - played_ids
    names = {odds.norm(n) for n in exclude}
    manual_ids = set(stats[stats.player_display_name.map(odds.norm).isin(names)].player_id) if names else set()
    absent_ids = {pid for pid, s_ in status.items() if s_ in drop} | manual_ids

    saved = None if full else reusable_calibration(season, week=week)
    if saved:
        var_params, bt_sum = saved["variance"], saved["backtest"]
        cal = dict(var_params.get("_calibration"), reused_from_run=saved["run_id"])
        var_params["_calibration"] = cal
        td_scale, ftd_scale = var_params["td"][0], var_params["ftd"][0]
        rho = {k: float(v) for k, v in var_params["_redistribution"]["rho"].items()}
        wx_coef = var_params["_weather"]["coef"]
        log(f"Using the saved calibration from week {cal['week']} (run {saved['run_id']}): skipping the backtest")
    else:
        log("Walk-forward backtest (fits variance, checks accuracy)...")
        start_t = (season - 1) * 100 + 6
        records = []
        base = model.walk_forward(stats, priors, played, start_t=start_t, td_priors=td_pri, records=records)  # also gathers data to fit rho
        rho_fit = rd.fit_rho([x for x in records if x[3] < week_t])
        rho = rd.applied(rho_fit)
        wx_coef = wxm.fit(base[(base.t < week_t) & ~base.kind.isin(tdm.YES_NO)])  # base has no weather adjustment yet
        bt = model.walk_forward(stats, priors, played, start_t=start_t, td_priors=td_pri, rho=rho, wx_coef=wx_coef)
        bt, base = bt[bt.t < week_t], base[base.t < week_t]
        bt_td, bt_ftd, bt = bt[bt.kind == "td"], bt[bt.kind == "ftd"], bt[~bt.kind.isin(tdm.YES_NO)]
        bt_sum, var_params = model.backtest_summary(bt), model.fit_dist(bt)
        lam = -np.log1p(-np.clip(bt_td.mu.to_numpy(), 0, 1 - 1e-9))
        td_scale = tdm.fit_scale(lam, bt_td.actual.to_numpy()) if len(bt_td) else 1.0
        bt_sum["td"] = tdm.summary(bt_td, td_scale)
        var_params["td"] = [td_scale, 0.0]  # anytime TD: (scale on expected TDs, unused); yardage kinds hold their error tables
        # first TD: the rows' expected TDs are unscaled, like anytime TD's, so the anytime scale goes on first
        ftd_p = bt_ftd.mu.to_numpy() * td_scale
        ftd_scale = tdm.fit_ftd_scale(ftd_p, bt_ftd.actual.to_numpy())
        bt_sum["ftd"] = tdm.brier_summary(np.clip(ftd_p * ftd_scale, 0, 0.95), bt_ftd.actual.to_numpy(float))
        var_params["ftd"] = [ftd_scale, 0.0]
        log(f"First TD: scale {ftd_scale:.2f}, Brier {bt_sum['ftd']['mae_model']:.4f} (base rate {bt_sum['ftd']['mae_naive']:.4f})")
        var_params["_redistribution"] = _redistribution_summary(base, bt, rho_fit, rho)
        var_params["_weather"] = wxm.summary(bt, wx_coef)
        var_params["_correlation"] = cm.fit(pd.concat([bt, bt_td]), var_params)
        log(_correlation_line(var_params["_correlation"]))
        var_params["_calibration"] = dict(at=datetime.utcnow().isoformat(), season=season, week=week, qb_rec_k=model.QB_REC_K)
        log(f"Redistribution: {var_params['_redistribution']['summary']}")
        log("Weather: " + _weather_line(wx_coef))

    log("Fetching stadium weather forecasts...")
    week_games = sched[(sched.season == season) & (sched.week == week) & sched.home_score.isna()]
    game_weather = wxm.forecast(week_games, wx_coef, log=log)
    outdoor = [w for w in game_weather.values() if w["source"] == "forecast"]
    log(f"  {len(outdoor)} outdoor games forecast, {sum(w['indoor'] for w in game_weather.values())} indoors")
    log("Projecting upcoming games...")
    proj = model.upcoming_projections(stats, sched, priors, var_params, week_t, data.load_roster(season),
                                      td_priors=td_pri, td_scale=td_scale, rho=rho, absent_ids=absent_ids,
                                      game_wx={g: w["factors"] for g, w in game_weather.items()}, ruled_out=ruled_out, sat_out=sat_out,
                                      questionable={pid for pid, s_ in status.items() if s_ == "Questionable"}, ftd_scale=ftd_scale)

    # team win chances: cheap (seconds), so fitted fresh every run, fast or full
    log("Team ratings and win probabilities...")
    ml_bt = mlm.walk_forward(sched, season, week)
    sigma = mlm.fit_sigma(ml_bt)
    bt_sum = {**bt_sum, "ml": mlm.summary(ml_bt, sigma)}
    var_params["_moneyline"] = dict(sigma=sigma, halflife=mlm.HALFLIFE, min_market_weight=mlm.ML_MIN_MARKET_WEIGHT)
    teams = mlm.project(sched, season, week, sigma)
    # each team's results this season, for Who's hot (teams on a winning streak)
    form = mlm.form(sched[(sched.season == season) & sched.home_score.notna()].rename(columns={"gameday": "date"}),
                    {t for g in teams.values() for t in (g["home"], g["away"])})
    for g in teams.values():
        g["form"] = {t: form[t] for t in (g["home"], g["away"])}
    log(f"  {len(teams)} games; win-probability curve sd {sigma:.2f} pts, Brier {bt_sum['ml']['mae_model']:.3f}"
        + (f" (closing moneyline {bt_sum['ml']['mae_market']:.3f})" if bt_sum["ml"].get("mae_market") is not None else ""))

    proj["status"] = proj.player_id.map(status).fillna("")
    proj.loc[proj.player_id.isin(manual_ids), "status"] = "Out"
    gone = proj[proj.status.isin(drop)]
    excluded = sorted({f"{n} ({s_})" for n, s_ in zip(gone["name"], gone.status)})
    proj = proj[~proj.status.isin(drop)]

    with SessionLocal() as s:
        run = Run(season=season, week=week, backtest=bt_sum, variance=var_params,
                  excluded=excluded, weather=game_weather, teams=teams)
        run.projections = [
            Projection(player_id=r.player_id, name=r.name, pos=r.pos, team=r.team, opp=r.opp, home=bool(r.home),
                       kind=r.kind, mu=float(r.mu), sd=float(r.sd), vol=float(r.vol), eff=float(r.eff),
                       spread=float(r.spread), status=r.status, game_id=r.game_id, gameday=str(r.gameday),
                       gametime="" if pd.isna(r.gametime) else str(r.gametime), last5=[float(x) for x in r.last5],
                       wx=float(r.wx), teammate_out=getattr(r, "teammate_out", "") or "", thin=bool(pd.notna(getattr(r, "thin", False)) and getattr(r, "thin", False)),
                       qb_adj=_float_or_none(getattr(r, "qb_adj", None)), qb_name=_str_or_empty(getattr(r, "qb_name", "")),
                       **_dvp_cols(r))
            for r in proj.itertuples()]
        s.add(run)
        s.commit()
        log(f"Stored run {run.id}: {len(run.projections)} projections, {len(excluded)} players excluded")
        return run.id


def _correlation_line(c: dict) -> str:
    """e.g. 'Same-game correlations: 363 pairs; held out, correlated pairs hit together 23.1% (copula 23.7%, independent 17.0%)'"""
    p = (c.get("check") or {}).get("positive") or {}
    tail = (f"; held out, correlated pairs hit together {p['actual']:.1%} (copula {p['copula']:.1%}, independent {p['independent']:.1%})"
            if p.get("n") else "")
    return f"Same-game correlations: {len(c.get('pairs', {}))} pairs{tail}"


def _weather_line(coef: dict) -> str:
    """e.g. 'per mph over 10: pass -2.3%, rec -2.5%, rush +1.0%; per degree under 45: ...'"""
    def part(key):
        return ", ".join(f"{k} {coef[k][key] * 100:+.1f}%" for k in wxm.KINDS if k in coef)
    return f"per mph over {wxm.WIND_FROM:.0f}: {part('wind')}; per degree under {wxm.COLD_FROM:.0f}F: {part('cold')}"


def _redistribution_summary(base: pd.DataFrame, bt: pd.DataFrame, rho_fit: dict, rho: dict) -> dict:
    """What redistribution did in the backtest (in-sample): bias on teammates of absent regulars, before and after."""
    # full precision: a later sync reuses `rho` as-is, and rounding it would shift projections slightly
    out = dict(rho_fit={k: float(v) for k, v in rho_fit.items()}, rho={k: float(v) for k, v in rho.items()},
               active_roles=list(rd.ACTIVE_ROLES), replace_roles=list(rd.REPLACE_ROLES), by_kind={})
    key = ["player_id", "t", "kind"]
    b = base[~base.kind.isin(tdm.YES_NO)].set_index(key)
    n = bt[~bt.kind.isin(tdm.YES_NO)].set_index(key)
    idx = b.index.intersection(n.index)
    for kind in rd.ROLES:
        sel = (idx.get_level_values("kind") == kind) & b.loc[idx, "affected"].to_numpy()
        if sel.sum() < 10:
            continue
        eb, en = (b.loc[idx, "actual"] - b.loc[idx, "mu"])[sel], (n.loc[idx, "actual"] - n.loc[idx, "mu"])[sel]
        out["by_kind"][kind] = dict(n=int(sel.sum()), bias_before=float(eb.mean()), bias_after=float(en.mean()),
                                    mae_before=float(eb.abs().mean()), mae_after=float(en.abs().mean()))
    r = out["by_kind"].get("rush")
    out["summary"] = ("rushing teammates of an absent regular: bias %+.1f -> %+.1f yds over %d games" % (r["bias_before"], r["bias_after"], r["n"])
                      if r else "no affected games")
    return out


def _s(v):
    return None if v is None or (isinstance(v, float) and pd.isna(v)) or str(v) == "" else str(v)


def archive_quotes(df: pd.DataFrame, session, fetched_at: datetime, alt: bool) -> None:
    """Append quotes to odds_history (see db.OddsHistory); the caller commits."""
    gid = df["game_id"] if "game_id" in df else pd.Series([None] * len(df), index=df.index)
    session.add_all(OddsHistory(fetched_at=fetched_at, game_id=None if pd.isna(g) else g, player=r.player, market=r.market,
                                line=float(r.line), over_odds=None if pd.isna(r.over_odds) else float(r.over_odds),
                                under_odds=None if pd.isna(r.under_odds) else float(r.under_odds), book=r.book, alt=alt)
                    for r, g in zip(df.itertuples(), gid))


def store_lines(df: pd.DataFrame, session, table=OddsLine) -> int:
    """Store main-market quotes. Each (market, game) is one snapshot: its older rows are replaced, so the table
    holds only current lines and a later fetch for other games never hides the ones already stored.
    table: OddsLine, or NbaOddsLine for the NBA (both are archived to odds_history; NBA game ids can't collide)."""
    fetched = df["fetched_at"] if "fetched_at" in df else pd.Series(time.time(), index=df.index)
    gid = df["game_id"] if "game_id" in df else pd.Series([None] * len(df), index=df.index)
    df = df.assign(_gid=gid.where(gid.notna(), None))
    for (market, game_id), g in df.groupby(["market", df["_gid"].fillna("")]):
        game_id = game_id or None
        t = datetime.utcfromtimestamp(float(fetched[g.index].max()))
        session.add_all(table(fetched_at=t, player=r.player, market=market, line=float(r.line),
                                 over_odds=None if pd.isna(r.over_odds) else float(r.over_odds),
                                 under_odds=None if pd.isna(r.under_odds) else float(r.under_odds), book=r.book,
                                 over_link=_s(getattr(r, "over_link", None)), under_link=_s(getattr(r, "under_link", None)),
                                 event_link=_s(getattr(r, "event_link", None)), game_id=game_id)
                        for r in g.itertuples())
        archive_quotes(g, session, t, alt=False)
        session.flush()
        if game_id:
            session.query(table).filter(table.market == market, table.game_id == game_id,
                                        table.fetched_at < t).delete()
    session.commit()
    clear_lines_cache()
    return len(df)


def upcoming_games() -> pd.DataFrame:
    """Unplayed games of the next week to be played (the week the projections are for)."""
    sched = data.load_schedule()
    todo = sched[sched.home_score.isna()]
    first = todo.sort_values(["season", "week"]).iloc[0]
    return todo[(todo.season == first.season) & (todo.week == first.week)]


def _kicks_off_later_today(g, now_et: datetime) -> bool:
    """The game is on now's Eastern date and hasn't kicked off (nflverse's gameday/gametime are Eastern)."""
    day, at = str(getattr(g, "gameday", "") or ""), getattr(g, "gametime", None)
    if day != now_et.date().isoformat() or not isinstance(at, str) or ":" not in at:
        return False
    hh, mm = (int(x) for x in at.split(":")[:2])
    return (now_et.hour, now_et.minute) < (hh, mm)


THIN_MIN_QUOTES = 100       # a game with fewer stored quotes than this had its lines fetched early (books post gradually)
THIN_MIN_AGE_HOURS = 3      # ...and "thin" games are only topped up if their lines are at least this old
EMPTY_RETRY_HOURS = 24      # a market no book had posted for a game is asked for again after this long (once a day)


def sync_plan(session, games: pd.DataFrame, mode: str = "missing", now: datetime = None) -> dict:
    """What a sync would fetch, game by game. Costs one credit per market.

    'missing': the main markets with no stored lines for a game, and the alternate markets it has none of.
    'thin':    'missing', plus a full re-fetch of games whose stored lines are few and old (lines post gradually
               through the week, so an early fetch leaves some games sparse).
    'today':   'missing', plus a fresh pull of the main markets for games kicking off later today (Eastern), so a
               game day's picks are priced against that morning's lines rather than Thursday's. Alternates aren't
               re-bought.
    'all':     everything for every game.
    'thin' and 'today' add to 'missing' rather than replace it, and a market that came back empty for a game (no book
    posts it) isn't asked for again until its EmptyMarket row is EMPTY_RETRY_HOURS old: one market books never offer
    once kept every game 'missing', so game-day refreshes never ran."""
    from sqlalchemy import func
    now = now or datetime.utcnow()
    now_et = now.replace(tzinfo=timezone.utc).astimezone(ET)
    have_main, stored, last = {}, {}, {}
    for gid, market in session.query(OddsLine.game_id, OddsLine.market).distinct():
        have_main.setdefault(gid, set()).add(market)
    for model_, in ((OddsLine,), (AltLine,)):
        for gid, n, t in session.query(model_.game_id, func.count(), func.max(model_.fetched_at)).group_by(model_.game_id):
            stored[gid] = stored.get(gid, 0) + n
            last[gid] = max(last[gid], t) if gid in last else t
    have_alt = {}
    for gid, market in session.query(AltLine.game_id, AltLine.market).distinct():
        have_alt.setdefault(gid, set()).add(market)
    empty_main, empty_alt = {}, {}
    retry_after = now - timedelta(hours=EMPTY_RETRY_HOURS)
    for gid, market, alt in session.query(EmptyMarket.game_id, EmptyMarket.market, EmptyMarket.alt) \
            .filter(EmptyMarket.checked_at > retry_after):
        (empty_alt if alt else empty_main).setdefault(gid, set()).add(market)
    out = []
    for g in games.itertuples():
        gid = g.game_id
        reason = None
        if mode == "all":
            need_main, need_alt, reason = sorted(ALL_MARKETS), sorted(ALT_KINDS), "all"
        else:
            need_main = sorted(ALL_MARKETS - have_main.get(gid, set()) - empty_main.get(gid, set()))
            # a game with no alternates gets all of them; one that has some gets only markets added since (e.g. receptions)
            need_alt = sorted(((ALT_KINDS - have_alt[gid]) if gid in have_alt else ALT_KINDS) - empty_alt.get(gid, set()))
            if need_main or need_alt:
                reason = "missing"
            if mode == "thin" and stored.get(gid, 0) < THIN_MIN_QUOTES and gid in last \
                    and (now - last[gid]).total_seconds() >= THIN_MIN_AGE_HOURS * 3600:
                need_main, need_alt, reason = sorted(ALL_MARKETS), sorted(ALT_KINDS), "thin"
            elif mode == "today" and _kicks_off_later_today(g, now_et):
                need_main, reason = sorted(ALL_MARKETS), "today"
        if reason:
            out.append(dict(game_id=gid, away=g.away_team, home=g.home_team, label=f"{g.away_team} @ {g.home_team}",
                            gameday=str(getattr(g, "gameday", "")), need_main=need_main, need_alt=bool(need_alt),
                            alt_kinds=need_alt, reason=reason, stored_quotes=int(stored.get(gid, 0)),
                            credits=len(need_main) + len(need_alt)))
    first = games.iloc[0] if len(games) else None
    has = first is not None and "season" in games.columns and "week" in games.columns
    return dict(season=int(first.season) if has else None, week=int(first.week) if has else None,
                total_games=len(games), games=out, credits=sum(g["credits"] for g in out))


def note_empty_markets(session, game_id: str, main_kinds, alt_kinds, paired: pd.DataFrame) -> list[str]:
    """Record the markets just asked for that came back with no quotes (and forget those that came back with some), so
    sync_plan doesn't ask for them again until EMPTY_RETRY_HOURS have passed. Returns the empty ones, alternates
    suffixed " alt", for the log."""
    got = set(zip(paired.market, paired.alt.astype(bool))) if not paired.empty else set()
    asked = [(m, False) for m in main_kinds] + [(m, True) for m in alt_kinds]
    if not asked:
        return []
    now = datetime.utcnow()
    for market, alt in asked:
        session.query(EmptyMarket).filter(EmptyMarket.game_id == game_id, EmptyMarket.market == market,
                                          EmptyMarket.alt.is_(alt)).delete()
    empty = [(m, a) for m, a in asked if (m, a) not in got]
    session.add_all(EmptyMarket(game_id=game_id, market=m, alt=a, checked_at=now) for m, a in empty)
    return [m + (" alt" if a else "") for m, a in empty]


def refresh_odds(mode: str = "missing", log: Log = print, progress: Callable[[int, int], None] = None,
                 credit_floor: Optional[int] = None) -> dict:
    """Fetch prop lines, main and alternate, for the upcoming week's games (one API call per game).

    'missing' fetches only what each game lacks (so a new week is fetched in full and a repeat costs nothing);
    'thin' also tops up games whose stored lines are few and old; 'today' re-pulls the main lines of games kicking off
    later today; 'all' refetches everything for the week; 'none'
    skips. Costs one credit per market per game. progress(done, total) is called after each game.

    With credit_floor set, the keys' balances are checked first (free) and a game is only fetched if its cost leaves at
    least that many credits, so a free key is never run dry; once ODDS_API_KEY can't afford a game, the sync switches to
    ODDS_API_KEY_NFL_SECONDARY and out["rotate"] names the key to replace (app/odds_keys.py). Returns what happened, for the sync log.
    """
    out = dict(games=0, fetched=0, skipped_for_credits=0, credits_before=None, credits_after=None)
    if mode == "none":
        return out
    keys = KeyRing("nfl", credit_floor, log)
    games = upcoming_games()
    with SessionLocal() as s:
        plan = sync_plan(s, games, mode)
        out["games"] = len(plan["games"])
        if not plan["games"]:
            log("Odds already cover every game")
            return out
        if not keys:
            log("No ODDS_API_KEY set; skipping odds fetch")
            return out
        if credit_floor is not None:
            keys.check()
            out["credits_before"] = out["credits_after"] = keys.total()
        events = {(e["away"], e["home"]): e["id"] for e in odds.list_events(keys.key)}
        todo = [(g, events.get((g["away"], g["home"]))) for g in plan["games"]]
        cost = sum(g["credits"] for g, e in todo if e)
        log(f"Fetching odds for {len(todo)} games (about {cost} API credits)...")
        mains, alts = [], []
        for i, (g, event_id) in enumerate(todo, 1):
            if progress:
                progress(i - 1, len(todo))
            if not event_id:
                log(f"  no sportsbook event yet for {g['label']}")
                continue
            if not keys.afford(g["credits"]):
                out["skipped_for_credits"] += 1
                log(f"  skipped {g['label']}: {g['credits']} credits would leave {keys.remaining - g['credits']}, under the floor of {credit_floor}")
                continue
            long, left = odds.fetch_game_odds(keys.key, event_id, g["need_main"], g["alt_kinds"])
            out["fetched"] += 1
            if left is not None:
                keys.spent(left)
                out["credits_after"] = keys.total()
            paired = odds.pair_with_links(long)
            is_alt = paired.alt.astype(bool)
            mains.append(paired[~is_alt].assign(game_id=g["game_id"]))
            alts.append(paired[is_alt].assign(game_id=g["game_id"]))
            empty = note_empty_markets(s, g["game_id"], g["need_main"], g["alt_kinds"], paired)
            log(f"  {g['label']}: {len(paired)} quotes (credits left: {left})"
                + (f"; none posted for {', '.join(empty)}" if empty else ""))
        if progress:
            progress(len(todo), len(todo))
        now = datetime.utcnow()
        main_df = pd.concat([m for m in mains if not m.empty], ignore_index=True) if any(not m.empty for m in mains) else pd.DataFrame()
        if not main_df.empty:
            main_df["fetched_at"] = time.time()
            store_lines(main_df.drop(columns="alt"), s)
        alt_df = pd.concat([a for a in alts if not a.empty], ignore_index=True) if any(not a.empty for a in alts) else pd.DataFrame()
        if not alt_df.empty:
            for (gid, market), _ in alt_df.groupby(["game_id", "market"]):   # only the markets just fetched are replaced
                s.query(AltLine).filter(AltLine.game_id == gid, AltLine.market == market).delete()
            s.add_all(AltLine(fetched_at=now, game_id=r.game_id, player=r.player, market=r.market, line=float(r.line),
                              over_odds=None if pd.isna(r.over_odds) else float(r.over_odds),
                              under_odds=None if pd.isna(r.under_odds) else float(r.under_odds), book=r.book,
                              over_link=_s(r.over_link), under_link=_s(r.under_link), event_link=_s(r.event_link))
                      for r in alt_df.itertuples())
            archive_quotes(alt_df, s, now, alt=True)
            s.commit()
        s.commit()   # the EmptyMarket rows, when nothing else was stored
        log(f"Stored {len(main_df)} main and {len(alt_df)} alternate quotes")
    if keys.rotate:
        out["rotate"] = keys.rotate
    return out


def seed_odds_from_csv(path: str, session) -> int:
    """One-time import of the legacy .cache/odds_latest.csv so existing credits aren't re-spent."""
    if not os.path.exists(path) or session.query(OddsLine).count():
        return 0
    df = pd.read_csv(path)
    if "fetched_at" not in df:
        df["fetched_at"] = os.path.getmtime(path)
    df["fetched_at"] = df["fetched_at"].fillna(os.path.getmtime(path))
    return store_lines(df, session)


def fetch_game_lines(game_id: str, kinds: list[str], log: Log = print) -> dict:
    """Fetch main + alternate lines (with betslip links) for one game and store them.

    Alternate lines go to alt_lines (replacing that game's previous alts). Main-line quotes already in
    odds_lines get their links filled in; prices there are left alone so the weekly snapshot stays consistent.
    """
    key = config.ODDS_API_KEY()
    if not key:
        raise ValueError("No ODDS_API_KEY set")
    _, _, away, home = game_id.split("_", 3)
    event_id = odds.find_event_id(key, away, home)
    if not event_id:
        raise LookupError(f"No sportsbook event found for {away} @ {home}")
    long, remaining = odds.fetch_game_odds(key, event_id, kinds, kinds)
    paired = odds.pair_with_links(long)
    now = datetime.utcnow()
    alts, mains = paired[paired.alt.astype(bool)], paired[~paired.alt.astype(bool)]
    with SessionLocal() as s:
        for m in kinds:
            s.query(AltLine).filter(AltLine.game_id == game_id, AltLine.market == m).delete()
        s.add_all(AltLine(fetched_at=now, game_id=game_id, player=r.player, market=r.market, line=float(r.line),
                          over_odds=None if pd.isna(r.over_odds) else float(r.over_odds),
                          under_odds=None if pd.isna(r.under_odds) else float(r.under_odds), book=r.book,
                          over_link=_s(r.over_link), under_link=_s(r.under_link), event_link=_s(r.event_link))
                  for r in alts.itertuples())
        archive_quotes(alts.assign(game_id=game_id), s, now, alt=True)
        patched = 0
        for r in mains.itertuples():
            patched += s.query(OddsLine).filter(
                OddsLine.player == r.player, OddsLine.market == r.market, OddsLine.book == r.book,
                OddsLine.line == float(r.line)).update(
                {"over_link": _s(r.over_link), "under_link": _s(r.under_link), "event_link": _s(r.event_link)})
        s.commit()
    log(f"{game_id}: stored {len(alts)} alternate quotes, linked {patched} main quotes")
    return {"alt_quotes": len(alts), "linked": patched, "credits_remaining": remaining}
