"""/api/nba/*: the NBA side of the app, in the same response shapes as the NFL routes so the UI can share components."""
import threading
import time
from datetime import datetime
from functools import lru_cache
from typing import Callable, Literal, Optional

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import books, config, market_fit, standings
from ..db import NbaAltLine, NbaOddsLine, NbaProjection, NbaRun, SessionLocal, current_lines
from ..engine import hot as hotm
from ..engine import moneyline as mlm
from ..engine import odds
from ..engine.ladder import ALT_OVER_LONG_SHOT, build_ladder, calibrated_mu, ladder_curve
from ..engine.market import curve_for, weight_for
from ..engine.picks import MAX_EV, best_picks, build_picks
from ..engine.search import rank_hits
from ..schemas import (AutoSync, BacktestStat, Correlations, Game, GameLogEntry, GradeReport, HistoryPoint, HotOut, HotTeamOut, InjuredPlayer, InjuryReport, LadderOut,
                       MoneylineGame, NbaMeta, NbaRunInfo, OddsInfo, PickOut, PlayerDetail, PlayerHit, ProjectionOut)
from . import espn, grading, model
from . import odds as nba_odds

NbaKind = Literal["pts", "reb", "ast", "threes", "pra", "pr", "pa", "stl", "blk", "dd", "tpl"]
router = APIRouter(prefix="/api/nba", tags=["nba"])
# main.py sets these: who may see owner-only bits, and the next scheduled NBA sync
is_admin: Callable[[Request], bool] = lambda request: False
auto_sync: Callable[[], AutoSync] = lambda: AutoSync(enabled=False)


def get_db():
    with SessionLocal() as s:
        yield s


def latest_run(db: Session) -> Optional[NbaRun]:
    return db.scalars(select(NbaRun).order_by(NbaRun.id.desc()).limit(1)).first()


def _records(df: pd.DataFrame) -> list[dict]:
    return df.replace({np.nan: None}).to_dict("records")


def _label(gid: str) -> str:
    _, _, away, home = gid.split("_", 3)
    return f"{away} @ {home}"


def _upcoming(run: NbaRun, now: Optional[datetime] = None) -> list[dict]:
    """The run's games that haven't tipped off yet (a game in progress has no pregame prices worth showing)."""
    now = now or datetime.utcnow()
    return [g for g in run.games or [] if datetime.fromisoformat(g["start"].replace("Z", "+00:00")).replace(tzinfo=None) > now]


@router.get("/meta", response_model=NbaMeta)
def meta(request: Request, db: Session = Depends(get_db)):
    run = latest_run(db)
    upcoming = _upcoming(run) if run else []
    recs = standings.records("basketball/nba", run.season, config.NBA_CACHE) if upcoming else {}
    games = [Game(game_id=g["game_id"], label=_label(g["game_id"]), gameday=g["gameday"], gametime=g["gametime"],
                  records={t: recs[t] for t in g["game_id"].split("_")[2:4] if t in recs})
             for g in upcoming]
    fetched = dict(db.execute(select(NbaOddsLine.market, func.max(NbaOddsLine.fetched_at)).group_by(NbaOddsLine.market)).all())
    counts = current_lines(db, NbaOddsLine).groupby("market").size().to_dict()
    return NbaMeta(
        run=None if not run else NbaRunInfo(id=run.id, season=run.season, slate=run.slate or [],
                                            created_at=run.created_at.isoformat(), excluded=run.excluded or []),
        backtest={k: BacktestStat(**v) for k, v in (run.backtest or {}).items()} if run else {},
        odds={m: OddsInfo(fetched_at=t.isoformat(), lines=int(counts.get(m, 0))) for m, t in fetched.items()},
        games=games, has_odds_key=bool(config.ODDS_API_KEY_NBA()), auto_sync=auto_sync(), can_write=is_admin(request),
        books=books.shown((b for (b,) in db.execute(select(NbaOddsLine.book).distinct()).all()), books.hide_offshore(db)))


def _proj_frame(db: Session, run_id: int) -> pd.DataFrame:
    rows = db.scalars(select(NbaProjection).where(NbaProjection.run_id == run_id).order_by(NbaProjection.id)).all()
    return pd.DataFrame([{c.name: getattr(r, c.name) for c in NbaProjection.__table__.columns} for r in rows])


def _dists(run: NbaRun) -> dict:
    return {k: v for k, v in (run.variance or {}).items() if isinstance(v, dict) and not k.startswith("_")}


_pick_cache: dict = {}
_priced_cache: dict = {}
_priced_lock = threading.Lock()


def _wkey(w: Optional[float]):
    return None if w is None else round(w, 3)


def _priced(db: Session, run: NbaRun, version: tuple, w: Optional[float]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(projections, every book's priced quotes): one pricing of the board serves every book filter. The Parlays tab
    asks for each book at once, so the lock makes those requests wait for one pricing instead of each doing its own.
    w=None prices with the fitted market weights (app/market_fit.py)."""
    key = (run.id, version, _wkey(w), market_fit.fit_state(db, "nba").get("fitted_at"))
    with _priced_lock:
        if key not in _priced_cache:
            _priced_cache.clear()   # only the newest odds and run are asked for
            proj = _proj_frame(db, run.id)
            priced = build_picks(proj, current_lines(db, NbaOddsLine), w, dists=_dists(run), best_only=False,
                                 book_weights=market_fit.book_weights(db, "nba"), fitted=market_fit.market_weights(db, "nba"),
                                 max_ev=MAX_EV)
            _priced_cache[key] = proj, market_fit.add_moves(db, "nba", priced, proj, _dists(run))
        return _priced_cache[key]


def all_picks(db: Session, run: NbaRun, w: Optional[float], book: Optional[str] = None) -> pd.DataFrame:
    version = tuple(sorted(db.execute(select(NbaOddsLine.market, func.max(NbaOddsLine.fetched_at)).group_by(NbaOddsLine.market)).all()))
    live = frozenset(g["game_id"] for g in _upcoming(run))   # a game that tips off leaves the board
    hide = books.hide_offshore(db)
    key = (run.id, version, _wkey(w), book, live, hide, market_fit.fit_state(db, "nba").get("fitted_at"))
    if key not in _pick_cache:
        if len(_pick_cache) > 40:
            _pick_cache.clear()
        proj, priced = _priced(db, run, version, w)
        all_books = priced.book.unique() if not priced.empty else []
        picks = best_picks(priced, books.pricing_books(book, all_books, hide)) if not priced.empty else priced.copy()
        if not picks.empty:
            l5 = dict(zip(zip(proj.player_id, proj.kind), proj.last5))
            picks["last5"] = [l5.get(k, []) for k in zip(picks.player_id, picks.kind)]
            picks = picks[picks.game_id.isin(live)]
        else:
            picks["last5"] = []
        _pick_cache[key] = picks
    return _pick_cache[key]


@router.get("/projections", response_model=list[ProjectionOut])
def projections(kind: Optional[NbaKind] = None, game_id: Optional[str] = None, gameday: Optional[str] = None, q: Optional[str] = None,
                db: Session = Depends(get_db)):
    run = latest_run(db)
    if not run:
        return []
    live = [g["game_id"] for g in _upcoming(run)]
    stmt = select(NbaProjection).where(NbaProjection.run_id == run.id, NbaProjection.game_id.in_(live)).order_by(NbaProjection.mu.desc(), NbaProjection.id)
    if kind:
        stmt = stmt.where(NbaProjection.kind == kind)
    if game_id:
        stmt = stmt.where(NbaProjection.game_id == game_id)
    if gameday:
        stmt = stmt.where(NbaProjection.gameday == gameday)
    if q:
        stmt = stmt.where(NbaProjection.name.ilike(f"%{q}%"))
    return db.scalars(stmt).all()


@router.get("/picks", response_model=list[PickOut])
def picks(kind: Optional[NbaKind] = None, side: Optional[Literal["Over", "Under"]] = None, game_id: Optional[str] = None,
          gameday: Optional[str] = None, q: Optional[str] = None, book: Optional[str] = None, min_ev: float = Query(0.03, ge=-1, le=1),
          market_weight: Optional[float] = Query(None, ge=0, le=1), include_flagged: bool = False,
          limit: int = Query(200, ge=1, le=1000), db: Session = Depends(get_db)):
    run = latest_run(db)
    if not run:
        return []
    df = all_picks(db, run, market_weight, book)
    if df.empty:
        return []
    df = df[df.ev >= min_ev]
    if not include_flagged:
        df = df[~df.flagged]
    if kind:
        df = df[df.kind == kind]
    if side:
        df = df[df.side == side]
    if game_id:
        df = df[df.game_id == game_id]
    if gameday:
        df = df[df.gameday == gameday]
    if q:
        df = df[df.name.str.contains(q, case=False, regex=False)]
    return _records(df.head(limit))


@router.get("/hot", response_model=list[HotOut])
def hot(kind: Optional[NbaKind] = None, side: Literal["Over", "Under"] = "Over", game_id: Optional[str] = None,
        gameday: Optional[str] = None, q: Optional[str] = None, book: Optional[str] = None,
        window: int = Query(10, ge=3, le=30), limit: int = Query(200, ge=1, le=1000), db: Session = Depends(get_db)):
    """Who's hot: players whose last `window` games mostly cleared the line they're posted at now (Under: stayed below it)."""
    run = latest_run(db)
    if not run:
        return []
    df = all_picks(db, run, None, book)
    if df.empty:
        return []
    df = df[df.side == side]
    if kind:
        df = df[df.kind == kind]
    if game_id:
        df = df[df.game_id == game_id]
    if gameday:
        df = df[df.gameday == gameday]
    if q:
        df = df[df.name.str.contains(q, case=False, regex=False)]
    return hotm.hot_rows(df.replace({np.nan: None}), _hot_logs(run.id, run.season, sorted(df.player_id.unique()), int(time.time() // 10800)), window)[:limit]


_hot_cache: dict = {}


def _hot_logs(run_id: int, season: int, player_ids: list, slot: int) -> dict:
    """(player_id, kind) -> values oldest first, the drawer's logs for every player asked about. Kept per run and
    three-hour slot (ESPN's current-season logs are re-read after three hours), so a page view reads the disk cache once."""
    key = (run_id, slot)
    if _hot_cache.get("key") != key:
        _hot_cache.clear()
        _hot_cache["key"], _hot_cache["logs"] = key, {}
    have = _hot_cache["logs"]
    todo = [pid for pid in player_ids if pid not in have]
    if todo:
        logs = espn.gamelogs(todo, [season - 1, season], season, log=lambda _: None)
        logs = logs[logs["min"] > 0]
        by = {pid: g for pid, g in logs.groupby("player_id")}
        for pid in todo:
            g = by.get(pid)
            # the drawer's double- and triple-double logs count the stats in double figures; the line asks yes or no
            have[pid] = {} if g is None else {k: [float(e.yards >= model.DOUBLES[k]) if k in model.DOUBLES else e.yards for e in v]
                                               for k, v in _logs_from(g.tail(120)).items()}
    return {(pid, k): v for pid in player_ids for k, v in have[pid].items()}


@router.get("/correlations", response_model=Correlations)
def correlations(db: Session = Depends(get_db)):
    """The latest NBA run's same-game correlations (same shape as /api/correlations), for pricing parlays."""
    return build_correlations(latest_run(db))


def build_correlations(run: Optional[NbaRun]) -> Correlations:
    c = ((run.variance or {}).get("_correlation") if run else None) or {}
    return Correlations(pairs={k: v[0] for k, v in (c.get("pairs") or {}).items()}, check=c.get("check") or {})


@router.get("/grades", response_model=GradeReport)
def grades(request: Request, season: Optional[int] = None, db: Session = Depends(get_db)):
    """How the NBA board did once graded (app/nba/grading.py), against the close too, and the fitted weights.
    Model check is the owner's page, so this is admin-only."""
    if not is_admin(request):
        raise HTTPException(403, "Admin only")
    return dict(grading.report(db, season), market_fit=market_fit.summary(db, "nba", current_lines(db, NbaOddsLine).book.unique()))


@router.get("/moneylines", response_model=list[MoneylineGame])
def moneylines(book: Optional[str] = None, market_weight: Optional[float] = Query(None, ge=0, le=1), db: Session = Depends(get_db)):
    """Both teams' win chances for every slate game still to tip off (model, market, blend) and the best moneyline."""
    return build_moneylines(db, latest_run(db), market_weight, book)


def build_moneylines(db: Session, run: Optional[NbaRun], market_weight: Optional[float] = None, book: Optional[str] = None) -> list[dict]:
    if not run or not run.teams:
        return []
    live = {g["game_id"] for g in _upcoming(run)}
    games = {gid: g for gid, g in run.teams.items() if gid in live}
    lines = current_lines(db, NbaOddsLine)
    w = weight_for(market_fit.market_weights(db, "nba"), "ml", None) if market_weight is None else market_weight
    return mlm.price(games, lines, w, books.pricing_books(book, lines.get("book", []), books.hide_offshore(db)), names=nba_odds.TEAM_NAMES,
                     min_market_weight=model.ML_MIN_MARKET_WEIGHT, book_weights=market_fit.book_weights(db, "nba"))


@router.get("/moneylines/{game_id}/{team}/lines", response_model=LadderOut)
def moneyline_lines(game_id: str, team: str, market_weight: Optional[float] = Query(None, ge=0, le=1), db: Session = Depends(get_db)):
    """One team's moneyline at every book, shaped like a player's line ladder, for shopping a betslip leg between books."""
    run = latest_run(db)
    lines = current_lines(db, NbaOddsLine)
    w = weight_for(market_fit.market_weights(db, "nba"), "ml", None) if market_weight is None else market_weight
    out = run and run.teams and mlm.side_ladder(run.teams, lines, game_id, team, w, books.pricing_books(None, lines.get("book", []), books.hide_offshore(db)),
                                                names=nba_odds.TEAM_NAMES, min_market_weight=model.ML_MIN_MARKET_WEIGHT,
                                                book_weights=market_fit.book_weights(db, "nba"))
    if not out:
        raise HTTPException(404, "That team isn't playing that game")
    return out


@router.get("/hot-teams", response_model=list[HotTeamOut])
def hot_teams(book: Optional[str] = None, game_id: Optional[str] = None, gameday: Optional[str] = None,
              window: int = Query(10, ge=3, le=30), db: Session = Depends(get_db)):
    """Who's hot, teams: those on a winning streak, with their moneyline and its EV (games still to tip off)."""
    run = latest_run(db)
    priced = [g for g in build_moneylines(db, run, None, book)
              if (not game_id or g["game_id"] == game_id) and (not gameday or g["gameday"] == gameday)]
    return mlm.hot_teams(priced, run.teams, window) if priced else []


@router.get("/injuries", response_model=InjuryReport)
def injuries(db: Session = Depends(get_db)):
    """ESPN's injury report for every team on the slate (re-read at most every 15 minutes)."""
    run = latest_run(db)
    if not run:
        return InjuryReport()
    teams = {t for g in run.games or [] for t in (g["home"], g["away"])}
    props = {pid: kind for pid, kind in db.execute(select(NbaProjection.player_id, NbaProjection.kind)
                                                   .where(NbaProjection.run_id == run.id, NbaProjection.kind == "pts"))}
    rows, fetched = _injuries(int(time.time() // 900))
    out: dict = {}
    for r in rows:
        if r["team"] not in teams:
            continue
        out.setdefault(r["team"], []).append(InjuredPlayer(
            player_id=r["player_id"] or r["name"], name=r["name"], pos=r["pos"], team=r["team"], side="off",
            status=r["status"], injury=r["injury"], practice="", rest=False, kind=props.get(r["player_id"]),
            comment=r["comment"], return_date=r["return_date"], updated=r["updated"]))
    order = {"Out": 0, "Suspension": 0, "Doubtful": 1, "Questionable": 2, "Day-To-Day": 2, "Probable": 3}
    for players in out.values():
        players.sort(key=lambda p: (order.get(p.status, 4), p.name))
    return InjuryReport(season=run.season, fetched_at=fetched, teams=out)


@lru_cache(maxsize=2)
def _injuries(_slot: int):
    return espn.injuries()


HEADSHOT = "https://a.espncdn.com/i/headshots/nba/players/full/{}.png"


@lru_cache(maxsize=1)
def _rosters(_slot: int) -> pd.DataFrame:
    """Every rostered player, re-read every 3 hours (ESPN's roster pages are cached on disk as long)."""
    try:
        return espn.rosters()
    except Exception:   # search still covers the slate's projected players
        return pd.DataFrame(columns=["player_id", "name", "pos", "team", "headshot"])


@router.get("/player-search", response_model=list[PlayerHit])
def player_search(q: str = Query("", max_length=60), limit: int = Query(12, ge=1, le=50), db: Session = Depends(get_db)):
    """Rostered players whose name matches q: the slate's projected players first, then everyone else (not playing on the slate)."""
    if not q.strip():
        return []
    run = latest_run(db)
    live = [g["game_id"] for g in _upcoming(run)] if run else []
    hits = {pid: dict(player_id=pid, name=n, pos=pos, team=t, headshot=HEADSHOT.format(pid), projected=True)
            for pid, n, pos, t in db.execute(select(NbaProjection.player_id, NbaProjection.name, NbaProjection.pos, NbaProjection.team)
                                             .where(NbaProjection.run_id == run.id, NbaProjection.game_id.in_(live)).distinct())} if run else {}
    for r in _rosters(int(time.time() // 10800)).to_dict("records"):
        shot = r["headshot"] if isinstance(r["headshot"], str) and r["headshot"] else HEADSHOT.format(r["player_id"])  # NaN when ESPN has none
        hits.setdefault(r["player_id"], dict(r, headshot=shot, projected=False))
    return rank_hits(list(hits.values()), q, limit)


@router.get("/players/{player_id}", response_model=PlayerDetail)
def player(player_id: str, market_weight: Optional[float] = Query(None, ge=0, le=1), db: Session = Depends(get_db)):
    run = latest_run(db)
    rows = db.scalars(select(NbaProjection).where(NbaProjection.run_id == run.id, NbaProjection.player_id == player_id).order_by(NbaProjection.id)).all() if run else []
    if not rows:
        raise HTTPException(404, "Player has no projection in the current run")
    p = rows[0]
    df = all_picks(db, run, market_weight)
    pp = df[df.player_id == player_id] if not df.empty else df
    return PlayerDetail(player_id=player_id, name=p.name, pos=p.pos, team=p.team,
                        headshot=HEADSHOT.format(player_id),
                        projections=[ProjectionOut.model_validate(r, from_attributes=True) for r in rows],
                        picks=_records(pp), logs=_game_logs(player_id, run.season))


def _game_logs(player_id: str, season: int, n: int = 120) -> dict:
    """The player's games per stat this season and last (value, with minutes as the volume, up to the last n), from the
    cached ESPN logs: the drawer charts the last 12 and works out hit rates from the rest. For double- and
    triple-doubles the value is how many stats reached double figures."""
    logs = espn.gamelogs([player_id], [season - 1, season], season, log=lambda _: None)
    return _logs_from(logs[logs["min"] > 0].tail(n))


def _logs_from(logs: pd.DataFrame) -> dict:
    """One player's game log rows (oldest first) as GameLogEntry lists per stat."""
    label = lambda d: datetime.fromisoformat(d).strftime("%b %-d")  # noqa: E731
    return {k: [GameLogEntry(label=label(r.date), opp=r.opp or "", yards=float(model.tens(r) if k in model.DOUBLES else model.actual(r, k)),
                             volume=float(r.min), season=int(r.season), home=bool(r.home))
                for r in logs.itertuples()] for k in model.ALL_KINDS}


@router.get("/players/{player_id}/lines", response_model=LadderOut)
def player_lines(player_id: str, kind: NbaKind, odds_range: int = Query(300, ge=0, le=100000),
                 market_weight: Optional[float] = Query(None, ge=0, le=1), db: Session = Depends(get_db)):
    """Every posted quote for one player and stat, main and alternate lines, all books, priced by the model blended
    with the market's distribution read off the ladder."""
    run = latest_run(db)
    p = _proj(db, run, player_id, kind)
    main = current_lines(db, NbaOddsLine)
    key = odds.norm(p.name)
    main = main[(main.market == kind) & (main.player.map(odds.norm) == key) & (main.game_id == p.game_id)].assign(alt=False)
    alts = [r for r in db.scalars(select(NbaAltLine).where(NbaAltLine.game_id == p.game_id, NbaAltLine.market == kind).order_by(NbaAltLine.id)).all()
            if odds.norm(r.player) == key]
    alt_df = pd.DataFrame([dict(player=r.player, market=r.market, line=r.line, over_odds=r.over_odds, under_odds=r.under_odds,
                                book=r.book, over_link=r.over_link, under_link=r.under_link, event_link=r.event_link,
                                game_id=r.game_id, alt=True) for r in alts], columns=list(main.columns))
    quotes = pd.concat([f for f in (main, alt_df) if not f.empty] or [main], ignore_index=True)
    dist = _dists(run).get(kind)
    curve = ladder_curve(quotes, p.mu, kind, dist, market_fit.book_weights(db, "nba"))
    w = market_fit.ladder_weight(market_weight, market_fit.market_weights(db, "nba"), p, main)
    # the market's calibration curve, through the projection at the main line (the board's), not on each rung
    mu = calibrated_mu(p.mu, p.sd, main.line.median() if not main.empty else None, kind, dist,
                       curve_for(market_fit.market_weights(db, "nba"), kind))
    if books.hide_offshore(db):
        quotes = quotes[~quotes.book.isin(books.OFFSHORE)]
    fetched = max((r.fetched_at for r in alts), default=None)
    return LadderOut(kind=kind, mu=p.mu, sd=p.sd, game_id=p.game_id, alt_fetched_at=fetched.isoformat() if fetched else None,
                     curve=curve, w=w if curve else None,
                     quotes=build_ladder(mu, p.sd, quotes, odds_range, kind, dist, curve, w, long_shot=ALT_OVER_LONG_SHOT))


def _proj(db: Session, run: Optional[NbaRun], player_id: str, kind: str) -> NbaProjection:
    p = db.scalars(select(NbaProjection).where(NbaProjection.run_id == run.id, NbaProjection.player_id == player_id,
                                               NbaProjection.kind == kind)).first() if run else None
    if not p:
        raise HTTPException(404, "No projection for that player and stat in the current run")
    return p


@router.get("/players/{player_id}/history", response_model=list[HistoryPoint])
def player_history(player_id: str, kind: NbaKind, db: Session = Depends(get_db)):
    """How one player's market moved: per odds fetch, the consensus line and its fair over chance."""
    run = latest_run(db)
    p = _proj(db, run, player_id, kind)
    return market_fit.line_history(db, p.game_id, p.name, kind, p.mu, p.sd, _dists(run).get(kind), "nba")


def clear_caches():
    _pick_cache.clear()
    _hot_cache.clear()
    _priced_cache.clear()
    _injuries.cache_clear()
