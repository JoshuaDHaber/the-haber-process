"""Tracked bets: a signed-in visitor records the bets they placed at a sportsbook, and each model run settles them from the
box scores, the same way it grades the board (app/grading.py, app/nba/grading.py). Private to their owner.

Phase 1 of docs/bet-tracker-plan.md: player props the site prices (main and alternate lines), singles and parlays, in
dollars, tracked only before the first game starts.
"""
import re
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Optional

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import Session

from . import auth, config, grading
from .db import Bet, BetLeg, NbaProjection, NbaRun, Projection, Run, User
from .market_fit import kickoff_utc
from .schemas import BetIn, BetLegOut, BetOut, BetPatch, BetPlayer, BetsOut, BetSummary

router = APIRouter(prefix="/api/me", tags=["bets"])

NBA_KINDS = {"pts", "reb", "ast", "threes", "pra", "pr", "pa", "stl", "blk", "dd", "tpl"}
MAX_LEGS = 25   # FanDuel and DraftKings take up to 25 legs
MAX_STAKE = 100_000
# a bet the bettor forgot to record can still be added this many days after its first game starts; it's marked late
LATE_DAYS = 7
MAX_TAGS, TAG_LEN = 6, 24
MAX_EARLY_WIN = 40   # an early-win token's lead, in points: FanDuel's are 10 (NBA) and 17 (NFL)
MAX_BOOST = 500   # a profit boost, in percent; books' run 10% to 100%, now and then more
# a parlay leg whose own price the book didn't show (most only show the parlay's): bet_legs.odds can't be NULL in
# databases created before this, and no American price is 0, so 0 means "not known"
NO_ODDS = 0


CUSTOM = "custom"   # a custom bet's one leg: the site can't grade it, so the bettor settles it
ML = "ml"           # a team's moneyline: player_id is the team, graded from the final score
NBA_GAME_ID = re.compile(r"^\d{4}_\d{8}_")   # NBA game ids carry the date ("2026_20261104_BOS_NYK"), NFL ones the week


def league_of(kind: str, game_id: str = "") -> Optional[str]:
    """The two leagues' player markets don't overlap, so a leg's market says which league it's from; a moneyline is in
    both, so its game id says. A custom bet's leg has none: nothing grades it or prices its close."""
    if kind == ML:
        return "nba" if NBA_GAME_ID.match(game_id or "") else "nfl"
    return None if kind == CUSTOM else "nba" if kind in NBA_KINDS else "nfl"


def to_decimal(american: int) -> float:
    return 1 + american / 100 if american > 0 else 1 + 100 / -american


def valid_odds(american: int) -> bool:
    return abs(american) >= 100 and abs(american) <= 100_000


def clean_tags(tags: list) -> list:
    """The bettor's labels, trimmed and de-duplicated ignoring case (the first spelling wins), at most MAX_TAGS."""
    out, seen = [], set()
    for t in tags or []:
        t = " ".join(str(t).split())[:TAG_LEN]
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out[:MAX_TAGS]


def months_ago(now: datetime, n: int) -> datetime:
    """The same day and time n calendar months back (the 31st of March, one month back, is the 28th or 29th of February)."""
    y, m = divmod(now.year * 12 + now.month - 1 - n, 12)
    m += 1
    days = (date(y + m // 12, m % 12 + 1, 1) - timedelta(days=1)).day
    return now.replace(year=y, month=m, day=min(now.day, days))


def cost(bet: Bet) -> float:
    """What the bet cost the bettor: its stake, or nothing for a bonus bet (the book's money, not theirs)."""
    return 0.0 if bet.bonus else bet.stake


def boosted(bet: Bet, dec: float) -> float:
    """A decimal price with the bet's profit boost on it: a 50% boost pays 1.5x the winnings, the stake unchanged."""
    return 1 + (dec - 1) * (1 + (bet.boost or 0) / 100)


def returned(bet: Bet, status: str, dec: Optional[float] = None) -> float:
    """What the book pays back at this status, stake included where the book returns it, a profit boost on the winnings.
    A bonus bet's stake never comes back: a win pays only the profit, and a loss, push or void returns nothing (a push
    hands back the bonus, not cash)."""
    if status == "won":
        dec = boosted(bet, to_decimal(bet.odds) if dec is None else dec)
        return round(bet.stake * (dec - 1 if bet.bonus else dec), 2)
    return 0.0 if status == "lost" or bet.bonus else bet.stake


def refund(bet: Bet) -> float:
    """What a lost no-sweat bet earns back: a bonus bet of up to its stake. It isn't cash, so it isn't in the bet's profit;
    it counts once the bettor tracks it as a bonus bet and it wins."""
    return round(min(bet.no_sweat, bet.stake), 2) if bet.no_sweat and bet.status == "lost" else 0.0


def set_promo(bet: Bet, bonus: Optional[bool], boost: Optional[int], no_sweat: Optional[float], early_win: Optional[int] = None) -> None:
    """Put the bettor's promo on the bet (None leaves that one as it is; a 0 boost, refund or lead takes it off). One per
    bet: a bonus bet, a profit boost, a no-sweat refund or an early-win token (on a single moneyline: check_early_win)."""
    if bonus is not None:
        bet.bonus = bonus
    if boost is not None:
        if boost and not 1 <= boost <= MAX_BOOST:
            raise HTTPException(422, f"A profit boost is 1% to {MAX_BOOST}%")
        bet.boost = boost or None
    if no_sweat is not None:
        if no_sweat < 0:
            raise HTTPException(422, "Enter the no-sweat refund in dollars")
        bet.no_sweat = round(min(no_sweat, bet.stake), 2) or None   # a book refunds at most the stake
    if early_win is not None:
        if early_win and not 1 <= early_win <= MAX_EARLY_WIN:
            raise HTTPException(422, f"An early win's lead is 1 to {MAX_EARLY_WIN} points")
        bet.early_win = early_win or None
    if sum([bool(bet.bonus), bool(bet.boost), bool(bet.no_sweat), bool(bet.early_win)]) > 1:
        raise HTTPException(422, "A bet takes one promo: a bonus bet, a profit boost, a no-sweat refund or an early win")


def check_early_win(bet: Bet) -> None:
    """An early-win token pays a moneyline once its team leads by enough: it only goes on a single moneyline."""
    if bet.early_win and not (bet.kind == "single" and len(bet.legs) == 1 and bet.legs[0].kind == ML):
        raise HTTPException(422, "An early win only goes on a single moneyline bet")


def _early(leg: BetLeg, result: Optional[str], lead: Optional[float]) -> Optional[str]:
    """A moneyline's result with its bet's early-win token: a win once the team led by the token's points, whatever the
    final score. The lead is kept on the leg so My bets can say it was won early."""
    leg.lead = lead
    return "win" if lead is not None and leg.bet.early_win and lead >= leg.bet.early_win else result


def require_member(user: User = Depends(auth.require_user)) -> User:
    """Signed in and 21+ confirmed: the account's age check comes before anything is saved to it."""
    if not user.age_confirmed_at:
        raise HTTPException(403, "Confirm you're 21 or older first")
    return user


# ---------- settling ----------
def settle(bet: Bet, now: Optional[datetime] = None) -> None:
    """A bet's status and payout from its legs. A losing leg loses the bet straight away. Pushed and void legs drop out
    and the parlay pays on the rest, as FanDuel and DraftKings settle: the recorded price is divided by those legs' prices,
    which keeps a book's same-game or boosted pricing in proportion. Every leg out: stake back. A bet settled by hand is
    left alone."""
    if bet.settled_by == "user":
        return
    results = [leg.result for leg in bet.legs]
    if "loss" in results:
        bet.status, bet.payout = "lost", 0.0
    elif None in results:
        bet.status, bet.payout, bet.settled_by, bet.settled_at = "open", None, None, None
        return
    elif "win" not in results:
        bet.status = "push" if "push" in results else "void"
        bet.payout = returned(bet, bet.status)
    else:
        dropped = [leg for leg in bet.legs if leg.result in ("push", "void")] if len(bet.legs) > 1 else []
        if any(leg.odds == NO_ODDS for leg in dropped):
            # a leg dropped out and its own price isn't known, so the reduced payout can't be worked out: the bettor marks it
            bet.status, bet.payout, bet.settled_by, bet.settled_at = "open", None, None, None
            return
        dec = to_decimal(bet.odds)
        for leg in dropped:
            dec /= to_decimal(leg.odds)
        bet.status, bet.payout = "won", returned(bet, "won", max(dec, 1.0))
    bet.settled_by, bet.settled_at = "grading", now or datetime.utcnow()


def _settle_touched(session, legs: list, now: datetime) -> None:
    for bet in {leg.bet for leg in legs}:
        fill_dropped_prices(session, bet)
        settle(bet, now)


def fill_dropped_prices(session, bet: Bet) -> None:
    """A parlay leg that pushed or voided drops out, and the payout is worked out by dividing out its price; most slips
    show only the parlay's price, so the leg's own often isn't recorded. Fill it from the book's own quote for that exact
    line (main or alternate), the last one before the game started. Left unknown when the book's price wasn't stored:
    the bettor then enters what the book paid (BetPatch.payout)."""
    if len(bet.legs) < 2:
        return
    from .db import AltLine, NbaAltLine, NbaOddsLine, OddsLine
    for leg in bet.legs:
        if leg.result not in ("push", "void") or leg.odds != NO_ODDS or leg.kind in (CUSTOM, ML):
            continue
        tables = (NbaOddsLine, NbaAltLine) if league_of(leg.kind, leg.game_id) == "nba" else (OddsLine, AltLine)
        best = None
        for model in tables:
            row = session.scalars(select(model).where(func.lower(model.book) == bet.book.lower(), model.player == leg.name,
                                                      model.market == leg.kind, model.line == leg.line, model.fetched_at <= leg.kickoff)
                                  .order_by(model.fetched_at.desc()).limit(1)).first()
            price = row and (row.over_odds if leg.side == "Over" else row.under_odds)
            if price and valid_odds(int(round(price))) and (best is None or row.fetched_at > best[0]):
                best = (row.fetched_at, int(round(price)))
        if best:
            leg.odds = best[1]


def needs_payout(bet: Bet) -> bool:
    """Every leg is in and none lost, but a leg that dropped out has no known price, so the payout can't be worked out:
    the bettor enters what the book paid."""
    results = [leg.result for leg in bet.legs]
    return (bet.status == "open" and bet.settled_by is None and len(bet.legs) > 1 and None not in results and "loss" not in results
            and "win" in results and any(leg.result in ("push", "void") and leg.odds == NO_ODDS for leg in bet.legs))


def grade_nfl(session, stats: pd.DataFrame, sched: pd.DataFrame, now: Optional[datetime] = None, leads: Optional[pd.DataFrame] = None) -> int:
    """Grade open NFL legs whose game is final and whose teams' stats are in (as grading.grade does the board): a player
    with no stats row didn't play, so the leg is void. A moneyline with an early-win token that didn't win outright waits
    for its team's largest lead (play-by-play: leads, else data.load_leads) for up to a week. Settles the bets they belong
    to. Returns how many legs were graded."""
    now = now or datetime.utcnow()
    todo = [leg for leg in session.scalars(select(BetLeg).where(BetLeg.result.is_(None))) if league_of(leg.kind, leg.game_id) == "nfl"]
    if not todo or stats.empty:
        return 0
    final = set(sched.loc[sched.home_score.notna(), "game_id"])
    scores = {r.game_id: r for r in sched[sched.home_score.notna()].itertuples()} if "away_score" in sched else {}
    have = set(zip(stats.season, stats.week, stats.team))
    cols = [c for c in {*grading.STAT.values(), "rushing_tds", "receiving_tds"} if c in stats]
    by_player = {(r["player_id"], r["season"], r["week"]): r for r in stats[["player_id", "season", "week", *cols]].to_dict("records")}
    done = []
    for leg in todo:
        if leg.game_id not in final:
            continue
        season, week, away, home = leg.game_id.split("_", 3)
        season, week = int(season), int(week)
        if leg.kind == ML:   # the final score is all a moneyline needs (a tie pushes), unless it has an early-win token
            g = scores.get(leg.game_id)
            if g is None:
                continue
            actual = float(g.home_score - g.away_score) * (1 if leg.player_id == home else -1)
            result = grading.outcome(leg.side, leg.line, actual)
            if leg.bet.early_win and result != "win":
                if leads is None:
                    leads = _nfl_leads(season)
                row = leads[leads.game_id == leg.game_id] if not leads.empty else leads
                lead = None if row.empty else float(row.iloc[0]["home_lead" if leg.player_id == home else "away_lead"])
                if lead is None and now - leg.kickoff < timedelta(days=7):
                    continue   # play-by-play not out yet: the lead decides it
                result = _early(leg, result, lead)
            leg.actual = actual
            leg.result, leg.graded_at = result, now
            done.append(leg)
            continue
        if not {(season, week, away), (season, week, home)} <= have:
            continue   # final, but nflverse hasn't published this game's stats yet
        st = by_player.get((leg.player_id, season, week))
        leg.actual = None if st is None else grading.actual_value(leg.kind, st)
        leg.result, leg.graded_at = grading.outcome(leg.side, leg.line, leg.actual), now
        done.append(leg)
    _settle_touched(session, done, now)
    session.commit()
    return len(done)


def _nfl_leads(season: int) -> pd.DataFrame:
    """Each game's largest lead for both sides this season (data.load_leads, cached by the model run); empty if it fails."""
    from .engine import data
    try:
        return data.load_leads([season], keep_raw=[season])
    except Exception:   # noqa: BLE001  (a download problem waits for the next run)
        return pd.DataFrame(columns=["game_id", "home_lead", "away_lead"])


def grade_nba(session, logs: pd.DataFrame, today: date, fetch: Optional[Callable] = None, now: Optional[datetime] = None,
              lead_of: Optional[Callable] = None, box_of: Optional[Callable] = None) -> int:
    """Grade open NBA legs from games before today (Eastern) against the ESPN game logs, with nba/grading.py's rules: a
    player missing from a finished box score is void once he has played again or two days have passed, and a box score
    that never appears (a postponement) voids the leg after a week. A moneyline with an early-win token that didn't win
    outright is settled by its team's largest lead (ESPN: lead_of(event id) -> (home, away)). A player missing from his
    game's logs is void straight away when the game's final box score (box_of(event id) -> who played) leaves him out,
    rather than after the wait. Settles their bets; returns how many legs were graded."""
    from .nba import espn, model
    from .nba.grading import STALE_AFTER_DAYS, VOID_AFTER_DAYS
    fetch = fetch or espn.gamelogs
    now = now or datetime.utcnow()
    todo = [leg for leg in session.scalars(select(BetLeg).where(BetLeg.result.is_(None), BetLeg.gameday < today.isoformat()))
            if league_of(leg.kind, leg.game_id) == "nba"]
    if not todo:
        return 0
    missing = sorted({leg.player_id for leg in todo if leg.kind != ML} - set(logs.player_id))
    if missing:
        seasons = sorted({int(leg.gameday[:4]) + (int(leg.gameday[5:7]) >= 9) for leg in todo})   # an NBA season ends in its spring
        extra = fetch(missing, seasons, max(seasons), log=lambda _: None)
        logs = pd.concat([logs, extra], ignore_index=True) if not extra.empty else logs
    have = set(zip(logs.date, logs.team))
    by_player = {(r.player_id, r.date): r for r in logs.itertuples()}
    last_game = logs.groupby("player_id").date.max().to_dict() if not logs.empty else {}
    has_scores = any(leg.kind == ML for leg in todo) and {"event_id", "home_score", "away_score"} <= set(logs.columns)
    scores = {(g.date, g.home_team, g.away_team): g for g in espn.team_games(logs).itertuples()} if has_scores else {}
    events = ({(g.date, g.home_team, g.away_team): g.event_id for g in espn.team_games(logs).itertuples()}
              if {"event_id", "home_score", "away_score"} <= set(logs.columns) else {})
    boxes: dict = {}   # event id -> who played (one box score read per game)
    done = []
    for leg in todo:
        _, _, away, home = leg.game_id.split("_", 3)
        age = (today - date.fromisoformat(leg.gameday)).days
        if leg.kind == ML:
            g = scores.get((leg.gameday, home, away))
            if g is None and age < VOID_AFTER_DAYS:
                continue   # no final score yet; a game that never shows up (postponed) voids after a week
            actual = None if g is None else float(g.home_score - g.away_score) * (1 if leg.player_id == home else -1)
            result = grading.outcome(leg.side, leg.line, actual)
            if leg.bet.early_win and actual is not None and result != "win":
                if lead_of is None:
                    from .nba import lead as nba_lead
                    lead_of = nba_lead._leads
                h, a = lead_of(str(g.event_id))
                lead = h if leg.player_id == home else a
                if lead is None and age < VOID_AFTER_DAYS:
                    continue   # ESPN hasn't the lead yet: it decides an early win
                result = _early(leg, result, lead)
            leg.actual = actual
            leg.result, leg.graded_at = result, now
            done.append(leg)
            continue
        if not {(leg.gameday, away), (leg.gameday, home)} <= have:
            if age < STALE_AFTER_DAYS:
                continue
            leg.actual = None
        else:
            st = by_player.get((leg.player_id, leg.gameday))
            if st is None and last_game.get(leg.player_id, "") <= leg.gameday and age < VOID_AFTER_DAYS:
                event = events.get((leg.gameday, home, away))
                if event not in boxes:
                    box_of = box_of or espn.played
                    boxes[event] = box_of(str(event)) if event is not None else None
                if boxes[event] is None or leg.player_id in boxes[event]:
                    continue   # no final box score to say, or he played and his log hasn't caught up: wait
            v = None if st is None or not st.min else model.actual(st, leg.kind)
            leg.actual = None if v is None or pd.isna(v) else float(v)
        leg.result, leg.graded_at = grading.outcome(leg.side, leg.line, leg.actual), now
        done.append(leg)
    _settle_touched(session, done, now)
    session.commit()
    return len(done)


# ---------- the API ----------
def _game(db: Session, league: str, game_id: str, player_id: str, kind: str = "") -> Optional[dict]:
    """The player and game as the latest run has them: who he plays for, against whom, and when it starts (UTC). For a
    moneyline, player_id is the team."""
    if kind == ML:
        return _team_game(db, league, game_id, player_id)
    run_t, proj_t = (Run, Projection) if league == "nfl" else (NbaRun, NbaProjection)
    run = db.scalar(select(run_t).order_by(run_t.id.desc()).limit(1))
    row = run and db.scalar(select(proj_t).where(proj_t.run_id == run.id, proj_t.game_id == game_id, proj_t.player_id == player_id).limit(1))
    if not row:
        # a game that has started drops off the board: the last run that had it says who played and when (a late bet)
        recent = [r.id for r in _recent_runs(db, run_t, datetime.utcnow())]
        row = recent and db.scalar(select(proj_t).where(proj_t.run_id.in_(recent), proj_t.game_id == game_id, proj_t.player_id == player_id)
                                   .order_by(proj_t.run_id.desc()).limit(1))
        if not row:
            return None
        run = db.get(run_t, row.run_id)
        if (ko := _kickoff(run, row)) and ko > datetime.utcnow():
            return None   # still to start but gone from the latest board (ruled out, say): not on the board any more
    return dict(name=row.name, team=row.team, opp=row.opp, gameday=row.gameday, kickoff=_kickoff(run, row))


def _kickoff(run, row) -> Optional[datetime]:
    """When a projection's game starts (UTC): the NBA run's own start time where it has one, else the schedule's date and time."""
    start = next((g.get("start") for g in getattr(run, "games", None) or [] if g.get("game_id") == row.game_id), None)
    return datetime.fromisoformat(start.replace("Z", "+00:00")).replace(tzinfo=None) if start else kickoff_utc(row.gameday, row.gametime)


def _recent_runs(db: Session, run_t, now: datetime) -> list:
    """The runs that could still have a game from the last LATE_DAYS days on them, newest first."""
    return db.scalars(select(run_t).where(run_t.created_at >= now - timedelta(days=LATE_DAYS + 2)).order_by(run_t.id.desc())).all()


def _team_game(db: Session, league: str, game_id: str, team: str) -> Optional[dict]:
    from .engine import odds as nfl_odds
    from .nba import odds as nba_odds
    run_t = Run if league == "nfl" else NbaRun
    run = db.scalar(select(run_t).order_by(run_t.id.desc()).limit(1))
    g = ((run.teams or {}) if run else {}).get(game_id)
    if not g:   # started and off the board: the last run that had it (a late bet)
        run = next((r for r in _recent_runs(db, run_t, datetime.utcnow()) if game_id in (r.teams or {})), None)
        g = run and run.teams[game_id]
    if not g or team not in (g["home"], g["away"]):
        return None
    start = next((x.get("start") for x in getattr(run, "games", None) or [] if x.get("game_id") == game_id), None) if league == "nba" else None
    ko = datetime.fromisoformat(start.replace("Z", "+00:00")).replace(tzinfo=None) if start else kickoff_utc(g["gameday"], g["gametime"])
    names = nfl_odds.TEAM_NAMES if league == "nfl" else nba_odds.TEAM_NAMES
    return dict(name=f"{names.get(team, team)}", team=team, opp=g["away"] if team == g["home"] else g["home"], gameday=g["gameday"], kickoff=ko)


def leg_ev_close(leg: BetLeg) -> Optional[float]:
    return None if leg.close_p is None or leg.odds == NO_ODDS else round(leg.close_p * to_decimal(leg.odds) - 1, 4)


def bet_ev_close(bet: Bet) -> Optional[float]:
    """The bet's price measured at the close: its legs' closing chances multiplied (as if independent: same-game legs
    move together, so a same-game parlay's is a rough guide), times its recorded odds, minus 1. None until every leg has one."""
    if not bet.legs or any(leg.close_p is None for leg in bet.legs):
        return None
    p = 1.0
    for leg in bet.legs:
        p *= leg.close_p
    return round(p * boosted(bet, to_decimal(bet.odds)) - 1, 4)


def _out(bet: Bet, now: datetime) -> BetOut:
    settled = bet.status != "open"
    return BetOut(
        id=bet.id, league=bet.league, book=bet.book, kind=bet.kind, stake=bet.stake, odds=bet.odds, status=bet.status,
        payout=bet.payout, profit=round(bet.payout - cost(bet), 2) if settled and bet.payout is not None else None,
        to_pay=returned(bet, "won"), settled_by=bet.settled_by, notes=bet.notes, tags=bet.tags or [],
        description=bet.description, bonus=bool(bet.bonus), boost=bet.boost, no_sweat=bet.no_sweat, refund=refund(bet), early_win=bet.early_win, needs_payout=needs_payout(bet),
        placed_at=bet.placed_at.isoformat(), settled_at=bet.settled_at.isoformat() if bet.settled_at else None,
        started=min(leg.kickoff for leg in bet.legs) <= now, ev_close=bet_ev_close(bet), late=bool(bet.late),
        legs=[BetLegOut(id=leg.id, game_id=leg.game_id, gameday=leg.gameday, kickoff=leg.kickoff.isoformat(), player_id=leg.player_id,
                        name=leg.name, team=leg.team, opp=leg.opp, kind=leg.kind, side=leg.side, line=leg.line, odds=leg.odds,
                        alt=leg.alt, prob=leg.prob, ev=leg.ev, actual=leg.actual, result=leg.result,
                        close_p=leg.close_p, ev_close=leg_ev_close(leg), lead=leg.lead) for leg in bet.legs])


def summarize(bets: list) -> BetSummary:
    won_lost = [b for b in bets if b.status in ("won", "lost")]
    # bonus bets add their winnings to the profit but nothing to what was staked: the bettor didn't put that money in
    staked = sum(cost(b) for b in won_lost)
    profit = sum(b.payout - cost(b) for b in won_lost)
    running, curve = 0.0, []
    for b in sorted((b for b in bets if b.status != "open" and b.settled_at), key=lambda b: b.settled_at):
        running += (b.payout or 0) - cost(b) if b.status in ("won", "lost") else 0
        curve.append((b.settled_at.isoformat(), round(running, 2)))
    open_ = [b for b in bets if b.status == "open"]
    clv = [e for b in bets if b.status != "void" and (e := bet_ev_close(b)) is not None]
    return BetSummary(clv_n=len(clv), beat_close=round(sum(e > 0 for e in clv) / len(clv), 4) if clv else None,
                      ev_close=round(sum(clv) / len(clv), 4) if clv else None,wins=sum(b.status == "won" for b in bets), losses=sum(b.status == "lost" for b in bets),
                      pushes=sum(b.status in ("push", "void") for b in bets), open=len(open_),
                      open_stake=round(sum(cost(b) for b in open_), 2), staked=round(staked, 2), profit=round(profit, 2),
                      bonus_staked=round(sum(b.stake for b in won_lost if b.bonus), 2), refunds=round(sum(refund(b) for b in bets), 2),
                      roi=round(profit / staked, 4) if staked else None, curve=curve)


def _mine(db: Session, user: User, bet_id: int) -> Bet:
    bet = db.get(Bet, bet_id)
    if not bet or bet.user_id != user.id:
        raise HTTPException(404, "No such bet")
    return bet


@router.get("/bets", response_model=BetsOut)
def my_bets(league: Optional[str] = Query(None, pattern="^(nfl|nba|oth)$"), book: Optional[str] = None, tag: Optional[str] = None,
            months: Optional[int] = Query(None, ge=1, le=120), user: User = Depends(require_member), db: Session = Depends(auth.get_db)):
    """This person's tracked bets, newest first, and their record, for the filters given: a league, a sportsbook, a tag
    (ignoring case) and the last `months` calendar months (by when the bet was placed)."""
    now = datetime.utcnow()
    every, bets = _view(db, user, league, book, tag, months, now)
    tags = sorted({t.lower(): t for b in every for t in b.tags or []}.values(), key=str.lower)
    return BetsOut(bets=[_out(b, now) for b in bets], summary=summarize(bets), books=sorted({b.book for b in every}), tags=tags,
                   leagues=[lg for lg in ("nfl", "nba", "oth") if any(b.league == lg for b in every)],
                   can_read_screenshots=bool(config.ANTHROPIC_API_KEY()))


def _view(db: Session, user: User, league, book, tag, months, now: datetime) -> tuple:
    """(every bet of this person's, the ones in this view), newest first."""
    every = db.scalars(select(Bet).where(Bet.user_id == user.id).order_by(Bet.placed_at.desc(), Bet.id.desc())).all()
    since = months_ago(now, months) if months else None
    want = tag.strip().lower() if tag else None
    return every, [b for b in every if (not league or b.league == league) and (not book or b.book == book)
                   and (not want or want in {t.lower() for t in b.tags or []}) and (not since or b.placed_at >= since)]


CSV_COLUMNS = ["bet_id", "placed_at", "league", "sportsbook", "type", "stake", "odds", "status", "payout", "profit", "settled_at",
               "tags", "ev_at_close", "bonus_bet", "profit_boost_pct", "no_sweat_refund", "added_late", "early_win_lead",
               "leg_player", "leg_team", "leg_opponent", "leg_game_date", "leg_market", "leg_side", "leg_line",
               "leg_odds", "leg_alt_line", "leg_result", "leg_actual", "leg_close_chance"]


@router.get("/bets.csv")
def export_csv(league: Optional[str] = Query(None, pattern="^(nfl|nba|oth)$"), book: Optional[str] = None, tag: Optional[str] = None,
               months: Optional[int] = Query(None, ge=1, le=120), user: User = Depends(require_member), db: Session = Depends(auth.get_db)):
    """The bets in this view as a spreadsheet: one row per leg, the bet's own columns repeated on each of its legs."""
    import csv
    import io
    now = datetime.utcnow()
    _, bets = _view(db, user, league, book, tag, months, now)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLUMNS)
    for b in reversed(bets):   # oldest first, as a ledger reads
        settled = b.status != "open" and b.payout is not None
        head = [b.id, b.placed_at.isoformat(timespec="seconds"), b.league.upper(), b.book, b.kind, b.stake, b.odds, b.status,
                b.payout if settled else "", round(b.payout - cost(b), 2) if settled else "",
                b.settled_at.isoformat(timespec="seconds") if b.settled_at else "", "; ".join(b.tags or []),
                "" if (e := bet_ev_close(b)) is None else e, "yes" if b.bonus else "", b.boost or "",
                refund(b) or "", "yes" if b.late else "", b.early_win or ""]
        for leg in b.legs:
            w.writerow([*head, leg.name, leg.team, leg.opp, leg.gameday, leg.kind, leg.side, leg.line, leg.odds or "", "yes" if leg.alt else "",
                        leg.result or "", "" if leg.actual is None else leg.actual, "" if leg.close_p is None else round(leg.close_p, 4)])
    name = f"my-bets-{now.date().isoformat()}.csv"
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/bet-players", response_model=list[BetPlayer])
def bet_players(league: str = Query(..., pattern="^(nfl|nba)$"), q: str = Query("", max_length=60),
                user: User = Depends(require_member), db: Session = Depends(auth.get_db)):
    """Players on the board matching q, with their game and markets, for adding a prop placed elsewhere (only players the
    site projects, so it can be graded): games still to start, then ones that started in the last LATE_DAYS days, which
    the bettor forgot to record (those bets are marked late)."""
    from .engine.search import rank_hits
    if not q.strip():
        return []
    now = datetime.utcnow()
    hits = rank_hits([dict(p, projected=True) for p in upcoming_players(db, league, now) + recent_players(db, league, now)], q, 12)
    # each player's games together, where the search ranked him: the next one first, then the ones played, latest first
    first = {}
    for i, h in enumerate(hits):
        first.setdefault(h["player_id"], i)
    hits.sort(key=lambda h: (first[h["player_id"]], h.get("started", False), (-1 if h.get("started") else 1) * datetime.fromisoformat(h["kickoff"]).timestamp()))
    return [BetPlayer(**{k: v for k, v in h.items() if k != "projected"}) for h in hits]


def upcoming_players(db: Session, league: str, now: datetime) -> list:
    """Every player on the latest board whose game hasn't started: one entry per player and game, with its markets."""
    run_t, proj_t = (Run, Projection) if league == "nfl" else (NbaRun, NbaProjection)
    run = db.scalar(select(run_t).order_by(run_t.id.desc()).limit(1))
    if not run:
        return []
    players: dict = {}
    for r in db.scalars(select(proj_t).where(proj_t.run_id == run.id)):
        ko = _kickoff(run, r)
        if not ko or ko <= now:
            continue
        p = players.setdefault((r.player_id, r.game_id), dict(player_id=r.player_id, name=r.name, pos=r.pos, team=r.team, opp=r.opp,
                                                              game_id=r.game_id, kickoff=ko.isoformat(), kinds=[]))
        if r.kind not in p["kinds"]:
            p["kinds"].append(r.kind)
    return list(players.values())


def recent_players(db: Session, league: str, now: datetime) -> list:
    """Every player in a game that started in the last LATE_DAYS days, as the last run with that game has him: one entry
    per player and game, shaped like upcoming_players' and flagged started (a bet on it is added late)."""
    run_t, proj_t = (Run, Projection) if league == "nfl" else (NbaRun, NbaProjection)
    runs = {r.id: r for r in _recent_runs(db, run_t, now)}
    if not runs:
        return []
    # the scan reads every recent run, and only a new run changes it: kept per league and newest run, filtered by time per call
    newest = runs[max(runs)]
    key = (league, newest.id, newest.created_at, len(runs))
    if _recent_cache.get(league, (None,))[0] != key:
        last = db.execute(select(proj_t.game_id, func.max(proj_t.run_id)).where(proj_t.run_id.in_(runs)).group_by(proj_t.game_id)).all()
        players: dict = {}
        for r in (db.scalars(select(proj_t).where(or_(*(and_(proj_t.run_id == rid, proj_t.game_id == gid) for gid, rid in last))))
                  if last else []):
            ko = _kickoff(runs[r.run_id], r)
            if not ko:
                continue
            p = players.setdefault((r.player_id, r.game_id), dict(player_id=r.player_id, name=r.name, pos=r.pos, team=r.team, opp=r.opp,
                                                                  game_id=r.game_id, kickoff=ko.isoformat(), kinds=[], started=True))
            if r.kind not in p["kinds"]:
                p["kinds"].append(r.kind)
        _recent_cache[league] = (key, list(players.values()))
    lo, hi = (now - timedelta(days=LATE_DAYS)).isoformat(), now.isoformat()
    return [dict(p, kinds=list(p["kinds"])) for p in _recent_cache[league][1] if lo <= p["kickoff"] <= hi]


_recent_cache: dict = {}   # league -> ((league, newest run's id and time, how many runs), every player in those runs' last games)


def recent_teams(db: Session, league: str, now: datetime) -> list:
    """Both teams of every game that started in the last LATE_DAYS days, shaped like upcoming_teams' entries and flagged started."""
    out, seen = [], set()
    for run in _recent_runs(db, Run if league == "nfl" else NbaRun, now):
        for gid in (run.teams or {}):
            if gid in seen:
                continue
            seen.add(gid)
            for team in (run.teams[gid].get("home"), run.teams[gid].get("away")):
                t = team and _team_game(db, league, gid, team)
                if t and t["kickoff"] and now - timedelta(days=LATE_DAYS) <= t["kickoff"] <= now:
                    out.append(dict(player_id=team, name=t["name"], pos="", team=team, opp=t["opp"], game_id=gid,
                                    kickoff=t["kickoff"].isoformat(), kinds=[ML], started=True))
    return out


def upcoming_teams(db: Session, league: str, now: datetime) -> list:
    """Both teams of every game on the latest board that hasn't started, shaped like upcoming_players' entries (player_id
    is the team, the market a moneyline), so a slip's moneyline legs match like its props."""
    run_t = Run if league == "nfl" else NbaRun
    run = db.scalar(select(run_t).order_by(run_t.id.desc()).limit(1))
    out = []
    for gid, g in ((run.teams or {}) if run else {}).items():
        for team in (g.get("home"), g.get("away")):
            t = team and _team_game(db, league, gid, team)
            if t and t["kickoff"] and t["kickoff"] > now:
                out.append(dict(player_id=team, name=t["name"], pos="", team=team, opp=t["opp"], game_id=gid, kickoff=t["kickoff"].isoformat(), kinds=[ML]))
    return out


def _track_custom(body: BetIn, user: User, db: Session, now: datetime) -> BetOut:
    """A bet the site can't grade (a spread, a total, another sport, a boost): what it was, when its event starts, the
    book, odds and stake. The bettor marks the result. One whose event started in the last LATE_DAYS days is added late."""
    text = " ".join((body.description or "").split())
    if not 3 <= len(text) <= 200:
        raise HTTPException(422, "Describe the bet in a few words (up to 200 characters)")
    try:
        starts = datetime.fromisoformat((body.starts_at or "").replace("Z", "+00:00"))
        starts = starts.astimezone(timezone.utc).replace(tzinfo=None) if starts.tzinfo else starts
    except ValueError:
        raise HTTPException(422, "Give the date and time the event starts")
    if starts < now - timedelta(days=LATE_DAYS):
        raise HTTPException(422, f"That started more than {LATE_DAYS} days ago, too long ago to add")
    if starts > now + timedelta(days=366):
        raise HTTPException(422, "That's more than a year away")
    if body.odds is None or not valid_odds(body.odds):
        raise HTTPException(422, "Odds look wrong: use American odds like -110 or +250")
    bet = Bet(user_id=user.id, league=body.league or "oth", book=body.book.strip()[:40] or "Other", kind=CUSTOM, stake=round(body.stake, 2),
              odds=body.odds, status="open", notes=(body.notes or "").strip()[:500] or None, tags=clean_tags(body.tags),
              description=text, late=starts <= now, placed_at=now)
    set_promo(bet, body.bonus, body.boost, body.no_sweat, body.early_win)
    check_early_win(bet)
    bet.legs.append(BetLeg(game_id=CUSTOM, gameday=starts.date().isoformat(), kickoff=starts, player_id="", name=text[:80], team="",
                           opp="", kind=CUSTOM, side="", line=0.0, odds=body.odds))
    db.add(bet)
    db.commit()
    return _out(bet, now)


@router.post("/bets", response_model=BetOut, dependencies=[Depends(auth.same_origin)])
def track(body: BetIn, user: User = Depends(require_member), db: Session = Depends(auth.get_db)):
    now = datetime.utcnow()
    if not 0 < body.stake <= MAX_STAKE:
        raise HTTPException(422, "Enter a stake above $0")
    if body.kind == CUSTOM:
        return _track_custom(body, user, db, now)
    if not 1 <= len(body.legs) <= MAX_LEGS:
        raise HTTPException(422, f"A bet has 1 to {MAX_LEGS} legs")
    if body.kind == "single" and len(body.legs) != 1:
        raise HTTPException(422, "A single has one leg")
    if body.kind == "parlay" and len(body.legs) < 2:
        raise HTTPException(422, "A parlay has two or more legs")
    graded = [leg for leg in body.legs if leg.kind != CUSTOM]
    by_hand = [leg for leg in body.legs if leg.kind == CUSTOM]
    if not graded:
        raise HTTPException(422, "A bet needs at least one player prop; for anything else, add it as an other bet")
    if by_hand and body.kind != "parlay":
        raise HTTPException(422, "Only a parlay can mix in legs you mark by hand")
    leagues = {league_of(leg.kind, leg.game_id) for leg in graded}
    if len(leagues) > 1:
        raise HTTPException(422, "A parlay's legs are all NFL or all NBA")
    league = leagues.pop()
    if len({(leg.game_id, leg.player_id, leg.kind) for leg in graded}) < len(graded):
        raise HTTPException(422, "The same prop is on this bet twice")
    mls = [leg.game_id for leg in graded if leg.kind == ML]
    if len(set(mls)) < len(mls):
        raise HTTPException(422, "Both teams' moneylines in one game can't be on the same bet")
    odds = body.legs[0].odds if body.kind == "single" else body.odds
    if odds is None or not valid_odds(odds) or not all(leg.odds is None or valid_odds(leg.odds) for leg in body.legs):
        raise HTTPException(422, "Odds look wrong: use American odds like -110 or +250")
    if any(not 3 <= len(" ".join((leg.description or "").split())) <= 80 for leg in by_hand):
        raise HTTPException(422, "Describe each leg you'll mark by hand in a few words")
    bet = Bet(user_id=user.id, league=league, book=body.book.strip()[:40], kind=body.kind, stake=round(body.stake, 2), odds=odds,
              status="open", notes=(body.notes or "").strip()[:500] or None, tags=clean_tags(body.tags), placed_at=now)
    set_promo(bet, body.bonus, body.boost, body.no_sweat, body.early_win)
    games = {}
    for leg in graded:
        g = _game(db, league, leg.game_id, leg.player_id, leg.kind)
        if not g or not g["kickoff"]:
            raise HTTPException(422, "One of these props isn't on the board any more")
        if g["kickoff"] < now - timedelta(days=LATE_DAYS):
            raise HTTPException(422, f"{g['name']}'s game was more than {LATE_DAYS} days ago, too long ago to add")
        games[leg.game_id] = g
        bet.legs.append(BetLeg(game_id=leg.game_id, gameday=g["gameday"], kickoff=g["kickoff"], player_id=leg.player_id, name=g["name"],
                               team=g["team"], opp=g["opp"], kind=leg.kind, side="Over" if leg.kind == ML else leg.side,
                               line=0.0 if leg.kind == ML else leg.line, odds=NO_ODDS if leg.odds is None else leg.odds,
                               alt=leg.alt and leg.kind != ML, prob=leg.prob, ev=leg.ev))
    # hand-marked legs ride on the parlay's first game (they're almost always the same game: a same-game parlay)
    first = min(games.values(), key=lambda g: g["kickoff"])
    bet.late = first["kickoff"] <= now   # recorded after it started: the bettor may have known how it went
    for leg in by_hand:
        text = " ".join(leg.description.split())
        bet.legs.append(BetLeg(game_id=CUSTOM, gameday=first["gameday"], kickoff=first["kickoff"], player_id="", name=text[:80], team="",
                               opp="", kind=CUSTOM, side="", line=0.0, odds=NO_ODDS if leg.odds is None else leg.odds))
    check_early_win(bet)
    db.add(bet)
    db.commit()
    return _out(bet, now)


@router.patch("/bets/{bet_id}", response_model=BetOut, dependencies=[Depends(auth.same_origin)])
def edit(bet_id: int, body: BetPatch, user: User = Depends(require_member), db: Session = Depends(auth.get_db)):
    """Fix the stake, odds, notes or promo (a bonus bet, a profit boost, a no-sweat refund, an early win), or settle it by
    hand (status "open" hands it back to grading)."""
    bet = _mine(db, user, bet_id)
    now = datetime.utcnow()
    if body.stake is not None:
        if not 0 < body.stake <= MAX_STAKE:
            raise HTTPException(422, "Enter a stake above $0")
        bet.stake = round(body.stake, 2)
    if body.odds is not None:
        if not valid_odds(body.odds):
            raise HTTPException(422, "Odds look wrong: use American odds like -110 or +250")
        bet.odds = body.odds
        if bet.kind in ("single", CUSTOM):
            bet.legs[0].odds = body.odds
    if body.notes is not None:
        bet.notes = body.notes.strip()[:500] or None
    if body.tags is not None:
        bet.tags = clean_tags(body.tags)
    had_early = bet.early_win
    set_promo(bet, body.bonus, body.boost, body.no_sweat, body.early_win)
    check_early_win(bet)
    if bet.early_win != had_early and bet.legs[0].result is not None:
        # the token was added, changed or taken off after grading: the next run grades the moneyline again with it
        bet.legs[0].result, bet.legs[0].lead, bet.legs[0].graded_at = None, None, None
    if bet.no_sweat and bet.no_sweat > bet.stake:
        bet.no_sweat = bet.stake   # the stake came down below the refund: a book refunds at most the stake
    promo = body.bonus is not None or body.boost is not None
    if body.status == "open":
        bet.settled_by = None
    elif body.status is not None:
        paid = returned(bet, body.status)
        if body.payout is not None and body.status == "won":
            if not 0 <= body.payout <= MAX_STAKE * 1000:
                raise HTTPException(422, "Enter what the book paid back, in dollars")
            paid = round(body.payout, 2)   # what the book actually paid (a voided leg repriced it, say)
        bet.status, bet.payout, bet.settled_by, bet.settled_at = body.status, paid, "user", now
    if bet.settled_by == "user" and body.status is None and (body.stake is not None or body.odds is not None or promo):
        bet.payout = returned(bet, bet.status)
    if bet.settled_by != "user":
        settle(bet, now)   # stake or odds changed (or handed back): what grading says now
    db.commit()
    return _out(bet, now)


@router.delete("/bets/{bet_id}", status_code=204, dependencies=[Depends(auth.same_origin)])
def remove(bet_id: int, user: User = Depends(require_member), db: Session = Depends(auth.get_db)):
    bet = _mine(db, user, bet_id)
    db.execute(delete(BetLeg).where(BetLeg.bet_id == bet.id))   # SQLite doesn't enforce the cascade
    db.delete(bet)
    db.commit()
    return Response(status_code=204)


def delete_all(db: Session, user_id: int) -> None:
    """Every bet of an account being deleted (app/auth.py)."""
    ids = db.scalars(select(Bet.id).where(Bet.user_id == user_id)).all()
    if ids:
        db.execute(delete(BetLeg).where(BetLeg.bet_id.in_(ids)))
        db.execute(delete(Bet).where(Bet.id.in_(ids)))
