"""NBA pipeline: ESPN data -> backtest -> projections and team win chances -> DB; and odds refresh -> DB.

The slate is the next two game dates (Eastern) with games still to tip off. A run takes about half a minute the
first time (every rostered player's game logs, three seasons) and a few seconds after that (finished seasons are
cached for good, this season's logs for a few hours).
"""
import math
import time
from datetime import datetime, timedelta
from typing import Callable, Optional

import pandas as pd
import requests

from .. import config
from ..db import NbaAltLine, NbaOddsLine, NbaProjection, NbaRun, NbaRunTables, SessionLocal
from ..engine import correlation as cm
from ..engine import model as nfl_model
from ..engine import moneyline as mlm
from ..engine import odds as nfl_odds
from ..odds_keys import KeyRing
from ..pipeline import _correlation_line, _s, archive_quotes, store_lines
from . import blowout, espn, grading, lead, model
from . import odds as nba_odds

Log = Callable[[str], None]
DROP = {"Out", "Doubtful", "Suspension"}   # statuses that keep a player off the board
PROPS_WINDOW_HOURS = 20   # props are fetched for games tipping off within this long (books post them the day of)
KEEP_RUNS = 12            # older NBA runs are deleted (three syncs a day would otherwise pile up)
BACKTEST_FROM = (10, 20)  # the walk-forward starts on this (month, day) of the previous season


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def run_projections(log: Log = print, now: Optional[datetime] = None) -> Optional[int]:
    """Project the slate and store an NbaRun. Returns its id (None when there are no games coming up)."""
    now = now or datetime.utcnow()
    today = espn.today_et(now)
    season = espn.season_for(today)
    log("NBA: schedule...")
    slate = espn.slate(now)
    if not slate:
        log("  no NBA games in the next 10 days; nothing to project")
        return None
    dates = sorted({g["gameday"] for g in slate})
    log(f"  slate: {len(slate)} games on {', '.join(dates)}")
    log("NBA: rosters and game logs...")
    ro = espn.rosters()
    logs = espn.gamelogs(list(ro.player_id), [season - 2, season - 1, season], season, log)
    logs = logs[logs.date < today.isoformat()]
    with SessionLocal() as s:
        grading.grade_logged(s, logs, today, log)
    pos = {p: espn.position_group(x) for p, x in zip(ro.player_id, ro.pos)}

    log("NBA: walk-forward backtest (error tables, accuracy)...")
    start = datetime(season - 2, *BACKTEST_FROM).date()
    end = today - timedelta(days=1)
    bt = model.walk_forward(logs, pos, start, end)
    tm_fit = model.fit_teammates(bt)
    bt = model.apply_teammates(bt, tm_fit)
    log("  teammates out: " + ", ".join(f"{k} {f['a']:.2f}x missing {f['b']:+.3f}" for k, f in tm_fit.items()))
    backtest, dists = model.calibrate(bt)
    for k, b in backtest.items():
        if b.get("metric") == "brier":
            log(f"  {k}: Brier {b['mae_model']:.4f} vs the player's last-10 rate {b['mae_naive']:.4f} over {b['n']:,} games")
        else:
            log(f"  {k}: MAE {b['mae_model']:.2f} vs last-10 average {b['mae_naive']:.2f} over {b['n']:,} games")
    corr = cm.fit(bt, dists)
    log("  " + _correlation_line(corr))
    games = espn.team_games(logs)
    ml_bt = model.ml_walk_forward(games, start, end)
    sigma = mlm.fit_sigma(ml_bt)
    backtest["ml"] = mlm.summary(ml_bt, sigma)
    teams = model.ml_project(games, slate, sigma, today)
    # each team's results this season, for Who's hot (teams on a winning streak)
    form = mlm.form(games[games.season == season], {t for g in teams.values() for t in (g["home"], g["away"])})
    for g in teams.values():
        g["form"] = {t: form[t] for t in (g["home"], g["away"])}
    log(f"  team ratings: win-probability curve sd {sigma:.2f} pts, Brier {backtest['ml']['mae_model']:.3f}"
        f" (always the home team's usual rate: {backtest['ml']['mae_naive']:.3f})")
    blow_fit = None
    try:   # context for the game pages; the run goes out without it
        hist = blowout.history(log)
        chances, blow_fit = blowout.project(hist, slate)
        for gid, b in chances.items():
            if gid in teams:
                teams[gid]["blowout"] = b
        log(f"  blowout chances for {len(chances)} games (t with {blow_fit['nu']:.0f} df over {blow_fit['n']:,} games,"
            f" recent rescale x{blow_fit['mult']:.3f})")
        try:   # the early-win token's lead chances ride on the blowout fit
            paths, lead_fit = lead.project(lead.add_leads(hist, log), slate, blow_fit, season)
            for gid, p in paths.items():
                if gid in teams:
                    teams[gid]["lead"] = p
            log(f"  lead chances for {len(paths)} games ({lead.describe(lead_fit)})")
        except Exception as e:
            log(f"  couldn't work out lead chances ({e})")
    except Exception as e:
        log(f"  couldn't work out blowout chances ({e})")

    log("NBA: injuries and projections...")
    try:
        inj, _ = espn.injuries()
    except Exception as e:  # the report is context; projections still go out without it
        log(f"  couldn't load the injury report ({e}); projecting everyone")
        inj = []
    status = {r["player_id"]: r["status"] for r in inj if r["player_id"]}
    on_slate = {t for g in slate for t in (g["home"], g["away"])}
    roster = ro[ro.team.isin(on_slate)]
    st = model.states(logs[logs.player_id.isin(set(roster.player_id))], pos, season, model.priors(logs, pos))
    opp = model.opp_factors(logs, today)
    by_team = {t: g for t, g in roster.groupby("team")}
    ros = model.Rosters(logs)
    matchups = model.dvp(logs, today, pos)   # the matchup grades: context, not in mu (engine/dvp.py)
    rows, excluded = [], []
    for g in slate:
        for team, other, home in ((g["away"], g["home"], False), (g["home"], g["away"], True)):
            margin = g.get("spread_line")
            spread = 0.0 if margin is None else (margin if home else -margin)
            active, absent = [], []
            for p in by_team.get(team, pd.DataFrame()).itertuples():
                s = st.get(p.player_id)
                stat = status.get(p.player_id, "")
                if s and stat in DROP and (n := ros.missed(p.player_id, team, today.isoformat())) is not None:
                    absent.append((p, s, n))
                if not s or s["min"] < model.MIN_MINUTES:
                    continue
                if stat in DROP:
                    excluded.append(f"{p.name} ({stat})")
                    continue
                active.append((p, s, stat, model.project_rows(s, opp, other)))
            # absent regulars' production goes to the teammates who play (model.fit_teammates)
            lost = model.missing([(s, n) for _, s, n in absent], opp, other)
            tot = {k: sum(pr[k][0] for *_, pr in active) for k in model.KINDS}
            tot["min"] = sum(s["min"] for _, s, _, _ in active)
            mult = model.tm_mults(tm_fit, lost, tot)
            out = [dict(name=p.name, missed=n) for p, s, n in absent if s["min"] >= model.TM_REG_MIN]
            fresh = ", ".join(o["name"] for o in out if o["missed"] < 3)   # tagged on the board: news the recent games don't show yet
            for p, s, stat, raw in active:
                m_min = mult.get("min", 1.0)
                vol = s["min"] * m_min
                proj = {k: raw[k][0] * mult.get(k, 1.0) for k in model.KINDS}
                for c, parts in model.COMBOS.items():
                    proj[c] = sum(proj[k] for k in parts)
                kinds = {k: (mu, nfl_model.sd_at(mu, dists[k]) if k in dists else math.sqrt(max(mu, 1.0)), mu / max(vol, 1e-9))
                         for k, mu in proj.items()}
                # double/triple-double: mu is the chance, sd its Bernoulli spread, eff the expected stats in double figures
                for k, (chance, tens) in model.doubles(proj, dists, dists.get("_doubles", {})).items():
                    kinds[k] = (chance, math.sqrt(chance * (1 - chance)), tens)
                for k, (mu, sd, eff) in kinds.items():
                    # what the projection is made of, for the "why" breakdown: minutes x per-minute rate x opponent, then
                    # the teammates-out multipliers (on minutes, and the rest on the rate)
                    base = model.COMBOS.get(k, (k,)) if k not in model.DOUBLES else ()
                    raw_mu = sum(raw[b][0] for b in base)
                    parts = dict(min=s["min"], tm_min=m_min, out=out)
                    if base:
                        parts.update(rate=sum(s[b] for b in base), opp=raw_mu / max(s["min"] * sum(s[b] for b in base), 1e-9),
                                     tm=mu / max(raw_mu, 1e-9))
                    dv = matchups.get(k, {}).get((other, pos.get(p.player_id, "G")))
                    rows.append(NbaProjection(player_id=p.player_id, name=p.name, pos=p.pos or espn.position_group(p.pos),
                                              team=team, opp=other, home=home, kind=k, mu=float(mu), sd=float(sd),
                                              vol=float(vol), eff=float(eff), spread=float(spread), status=stat,
                                              game_id=g["game_id"], gameday=g["gameday"], gametime=g["gametime"],
                                              last5=s["last5_" + k], wx=1.0, teammate_out=fresh, parts=parts,
                                              dvp=dv[0] if dv else None, dvp_rank=dv[1] if dv else None, dvp_n=dv[2] if dv else None))
    with SessionLocal() as s:
        run = NbaRun(season=season, slate=dates, backtest=backtest, created_at=now,
                     variance={**dists, "_correlation": corr, "_moneyline": dict(sigma=sigma, halflife_days=model.ML_HL_DAYS,
                                                           min_market_weight=model.ML_MIN_MARKET_WEIGHT),
                               "_blowout": blow_fit, "_teammates": tm_fit},
                     excluded=sorted(set(excluded)), games=slate, teams=teams)
        run.projections = rows
        s.add(run)
        s.commit()
        s.add(NbaRunTables(run_id=run.id, created_at=now, tables=run_tables(run.variance)))
        s.commit()
        for old in s.query(NbaRun).order_by(NbaRun.id.desc()).offset(KEEP_RUNS).all():
            s.delete(old)
        s.commit()
        log(f"Stored NBA run {run.id}: {len(rows) // len(model.ALL_KINDS)} players projected, {len(set(excluded))} ruled out")
        return run.id


def run_tables(variance: dict) -> dict:
    """The part of a run's variance that prices props: the per-kind error tables and the double-double curves (about
    7 KB a run; the correlations, moneyline and blowout fits are left out)."""
    return {k: v for k, v in (variance or {}).items() if not k.startswith("_") or k == "_doubles"}


# ---------- odds ----------
def _tipoff(g: dict) -> datetime:
    """Naive UTC tip-off time."""
    return datetime.fromisoformat(g["start"].replace("Z", "+00:00")).replace(tzinfo=None)


def sync_plan(session, slate: list[dict], mode: str = "missing", now: Optional[datetime] = None) -> dict:
    """What an odds sync would buy. Moneylines: one credit for the whole slate. Props: one credit per market per game,
    only for games tipping off within PROPS_WINDOW_HOURS ('missing': markets with no stored lines; 'all': every market).
    Alternate lines (the parlay ladders) are bought once per game and market, in either mode: re-pulling them before
    tip-off would double the pregame cost for rungs that move less than the main line."""
    now = now or datetime.utcnow()
    if mode == "none":
        return dict(total_games=len(slate), games=[], moneylines=False, credits=0)
    have, have_alt = {}, {}
    for gid, market in session.query(NbaOddsLine.game_id, NbaOddsLine.market).distinct():
        have.setdefault(gid, set()).add(market)
    for gid, market in session.query(NbaAltLine.game_id, NbaAltLine.market).distinct():
        have_alt.setdefault(gid, set()).add(market)
    out = []
    for g in slate:
        tip = _tipoff(g)
        if tip <= now or tip - now > timedelta(hours=PROPS_WINDOW_HOURS):
            continue
        need = list(nba_odds.PROP_KINDS) if mode == "all" else [k for k in nba_odds.PROP_KINDS if k not in have.get(g["game_id"], set())]
        need_alt = [k for k in nba_odds.ALT_KINDS if k not in have_alt.get(g["game_id"], set())]
        if need or need_alt:
            out.append(dict(game_id=g["game_id"], away=g["away"], home=g["home"], label=f"{g['away']} @ {g['home']}",
                            gameday=g["gameday"], need=need, need_alt=need_alt, credits=len(need) + len(need_alt)))
    return dict(total_games=len(slate), games=out, moneylines=True, credits=1 + sum(g["credits"] for g in out))


def _match_events(events: list[dict], slate: list[dict]) -> dict:
    """game_id -> Odds API event id, by teams and Eastern tip-off date."""
    out = {}
    for e in events:
        if not e.get("commence"):
            continue
        day = espn._et(e["commence"]).strftime("%Y-%m-%d")
        for g in slate:
            if (g["away"], g["home"]) == (e["away"], e["home"]) and abs((pd.Timestamp(g["gameday"]) - pd.Timestamp(day)).days) <= 1:
                out[g["game_id"]] = e["id"]
    return out


def refresh_odds(mode: str = "missing", log: Log = print, progress: Callable[[int, int], None] = None,
                 credit_floor: Optional[int] = None, now: Optional[datetime] = None) -> dict:
    """Fetch the slate's moneylines and the props of games tipping off soon. Same return shape as the NFL's
    pipeline.refresh_odds, for the sync log."""
    out = dict(games=0, fetched=0, skipped_for_credits=0, credits_before=None, credits_after=None)
    if mode == "none":
        return out
    keys = KeyRing("nba", credit_floor, log)
    if not keys:
        log("No ODDS_API_KEY_NBA set; skipping NBA odds")
        return out
    slate = espn.slate(now)
    with SessionLocal() as s:
        plan = sync_plan(s, slate, mode, now)
        out["games"] = len(plan["games"])
        if credit_floor is not None:
            keys.check()
            out["credits_before"] = out["credits_after"] = keys.total()
        ids = _match_events(nba_odds.list_events(keys.key), slate)   # free
        frames = []
        stamp = time.time()
        if keys.afford(1):
            by_event, left = nba_odds.fetch_moneylines(keys.key)
            if left is not None:
                keys.spent(left)
                out["credits_after"] = keys.total()
            got = {gid: by_event[eid] for gid, eid in ids.items() if eid in by_event and not by_event[eid].empty}
            frames += [nfl_odds.pair_with_links(df).assign(game_id=gid) for gid, df in got.items()]
            log(f"  moneylines for {len(got)} of {len(slate)} games")
        else:
            out["skipped_for_credits"] += 1
            log("  skipped moneylines: under the credit floor")
        todo = plan["games"]
        log(f"Fetching props for {len(todo)} games (about {sum(g['credits'] for g in todo)} credits)...")
        for i, g in enumerate(todo, 1):
            if progress:
                progress(i - 1, len(todo))
            eid = ids.get(g["game_id"])
            if not eid:
                log(f"  no sportsbook event yet for {g['label']}")
                continue
            if not keys.afford(g["credits"]):
                out["skipped_for_credits"] += 1
                log(f"  skipped {g['label']}: {g['credits']} credits would leave {keys.remaining - g['credits']}, under the floor of {credit_floor}")
                continue
            try:
                long, left = nba_odds.fetch_props(keys.key, eid, g["need"], g["need_alt"])
            except requests.RequestException as e:   # one game's failure shouldn't cost the rest their props
                out["failed"] = out.get("failed", 0) + 1
                log(f"  {g['label']}: failed after retries ({e}); the next sync tries again")
                continue
            out["fetched"] += 1
            if left is not None:
                keys.spent(left)
                out["credits_after"] = keys.total()
            paired = nfl_odds.pair_with_links(long)
            is_alt = paired.alt.astype(bool)
            frames.append(paired[~is_alt].assign(game_id=g["game_id"]))
            if is_alt.any():
                _store_alts(s, paired[is_alt].assign(game_id=g["game_id"]))
            log(f"  {g['label']}: {int((~is_alt).sum())} main and {int(is_alt.sum())} alternate quotes (credits left: {left})")
        if progress:
            progress(len(todo), len(todo))
        df = pd.concat([f for f in frames if not f.empty], ignore_index=True) if any(not f.empty for f in frames) else pd.DataFrame()
        if not df.empty:
            df["fetched_at"] = stamp
            store_lines(df.drop(columns="alt", errors="ignore"), s, table=NbaOddsLine)
        log(f"Stored {len(df)} NBA quotes")
    if keys.rotate:
        out["rotate"] = keys.rotate
    return out


def _store_alts(session, alts: pd.DataFrame) -> None:
    """Replace a game's alternate lines market by market (only the markets just fetched), and archive them."""
    now = datetime.utcnow()
    for (gid, market), _ in alts.groupby(["game_id", "market"]):
        session.query(NbaAltLine).filter(NbaAltLine.game_id == gid, NbaAltLine.market == market).delete()
    session.add_all(NbaAltLine(fetched_at=now, game_id=r.game_id, player=r.player, market=r.market, line=float(r.line),
                               over_odds=None if pd.isna(r.over_odds) else float(r.over_odds),
                               under_odds=None if pd.isna(r.under_odds) else float(r.under_odds), book=r.book,
                               over_link=_s(r.over_link), under_link=_s(r.under_link), event_link=_s(r.event_link))
                    for r in alts.itertuples())
    archive_quotes(alts, session, now, alt=True)
    session.commit()
