"""FastAPI app: JSON API for projections, priced picks and data refresh (+ serves the built React app)."""
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime
from functools import lru_cache
from typing import Literal, Optional

import numpy as np
import pandas as pd
import re
import socket
import subprocess
import threading

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import auth, bet_screens, bets, books, config, grading, slip_sync, market_fit, pipeline, scheduler, standings
from .db import AltLine, OddsLine, Projection, Run, SessionLocal, SyncRun, current_lines, proj_frame
from .engine import data, model, odds
from .engine import injuries as injm
from .engine import moneyline as mlm
from .engine import hot as hotm
from .engine import td as tdm
from .engine.ladder import build_ladder, ladder_curve
from .engine.market import weight_for
from .engine.picks import best_picks, build_picks
from .engine.search import rank_hits
from .nba import routes as nba_routes
from .schemas import (AdminState, Correlations, GradeReport, AltFetchResult, AutoSync, CreditStatus, KeyBalance, SlotOut, SyncRunDetail, SyncRunOut, SyncPlan, GameLogEntry, HistoryPoint, HotTeamOut, InjuryReport, LadderOut, ByeTeam, Game, JobStatus, Kind, Meta, MoneylineGame, OddsInfo, HotOut, PickOut, PlayerDetail, PlayerHit, ProjectionOut,
                      RefreshRequest, RunInfo, SiteSettings)

@asynccontextmanager
async def lifespan(_: FastAPI):
    with SessionLocal() as s:
        n = pipeline.seed_odds_from_csv(CSV_SEED, s)
        if n:
            print(f"Seeded {n} odds lines from {CSV_SEED}")
    n = scheduler.mark_interrupted()
    if n:
        print(f"Marked {n} sync run(s) a restart cut off as interrupted")
    scheduler.after_job = _release_memory
    if scheduler.start():
        print("Scheduled syncs are on (AUTO_SYNC)")
        # no schedule yet (a new volume, or a cleared cache): fetch it now rather than at the next sync
        if data.cached_schedule() is None:
            threading.Thread(target=_fetch_schedule, daemon=True).start()
    yield
    scheduler.stop()


app = FastAPI(title="NFL Prop Predictor", version="1.0", lifespan=lifespan)
LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


def client_key(request: Request) -> str:
    """Who is asking. Behind a host's proxy the real client is the last X-Forwarded-For entry (the one the proxy added)."""
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[-1].strip() if fwd else (request.client.host if request.client else "")


def is_admin(request: Request) -> bool:
    """May change data and spend API credits: a request from this machine (a laptop running the app, or a shell on the
    host: the way back in if the owner's account is ever unreachable), or a signed-in, 21+ account in ADMIN_EMAILS.
    The old admin password (ADMIN_TOKEN, a Bearer header) is retired: accounts replaced it."""
    host = request.client.host if request.client else ""
    return host in LOCAL_HOSTS or auth.is_admin_user(auth.request_user(request))


PUBLIC_WRITES = ("/api/auth/", "/api/me/")   # each route checks the signed-in user and Origin itself
# a screenshot shared to the installed app (manifest share_target) that reaches the server: it only redirects to My bets
PUBLIC_POSTS = ("/share-slip",)


@app.middleware("http")
async def admin_writes_only(request: Request, call_next):
    """The app can be served beyond this machine (LAN, Tailscale, a host). Writes are POSTs, and those
    (refreshes, alt-line fetches) spend API credits, so they need to come from here or from the owner's account.
    Signing in, preferences and tracked bets are writes anyone may make: app/auth.py and app/bets.py guard those."""
    if request.method not in ("GET", "HEAD", "OPTIONS") and not request.url.path.startswith(PUBLIC_WRITES) \
            and request.url.path not in PUBLIC_POSTS:
        if not is_admin(request):
            return JSONResponse({"detail": "Admin only"}, status_code=403)
    return await call_next(request)


def tailscale_ip(ifconfig_text: str) -> Optional[str]:
    """A Tailscale address (100.64.0.0/10) from `ifconfig` output, if this machine is on a tailnet."""
    for m in re.finditer(r"inet (100\.(\d+)\.\d+\.\d+)\b", ifconfig_text):
        if 64 <= int(m.group(2)) <= 127:
            return m.group(1)
    return None


def lan_ip() -> Optional[str]:
    """The address other devices should use to reach this machine: Tailscale if present (works from
    anywhere), else the local-network address (a UDP 'connect' sends no packets)."""
    try:
        ts = tailscale_ip(subprocess.run(["ifconfig"], capture_output=True, text=True, timeout=3).stdout)
        if ts:
            return ts
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
        return None if ip.startswith("127.") else ip
    except OSError:
        return None


app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS(), allow_methods=["*"], allow_headers=["*"])
# the boards are large JSON (the NBA projections run ~2 MB, each book's picks ~0.5 MB) and compress about tenfold
app.add_middleware(GZipMiddleware, minimum_size=1000)

CSV_SEED = os.path.join(config.BASE_DIR, ".cache", "odds_latest.csv")


def get_db():
    with SessionLocal() as s:
        yield s


# ---------- helpers ----------
def latest_run(db: Session) -> Optional[Run]:
    return db.scalars(select(Run).order_by(Run.id.desc()).limit(1)).first()


def game_label(gid: str) -> str:
    _, _, away, home = gid.split("_", 3)
    return f"{away} @ {home}"


_pick_cache: dict = {}
_priced_cache: dict = {}
_priced_lock = threading.Lock()


_dists = grading.dists


def _wkey(w: Optional[float]):
    return None if w is None else round(w, 3)


def _priced(db: Session, run: Run, version: tuple, w: Optional[float]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(projections, every book's priced quotes): one pricing of the board serves every book filter. The Parlays tab
    asks for each book at once, so the lock makes those requests wait for one pricing instead of each doing its own.
    w=None prices with the fitted market weights; the book weights are always the fitted ones (market_fit)."""
    key = (run.id, version, _wkey(w), market_fit.fit_state(db, "nfl").get("fitted_at"))
    with _priced_lock:
        if key not in _priced_cache:
            _priced_cache.clear()   # only the newest odds and run are asked for
            proj = proj_frame(db, run.id)
            priced = build_picks(proj, current_lines(db), w, dists=_dists(run), best_only=False,
                                 book_weights=market_fit.book_weights(db, "nfl"), fitted=market_fit.market_weights(db, "nfl"))
            _priced_cache[key] = proj, market_fit.add_moves(db, "nfl", priced, proj, _dists(run))
        return _priced_cache[key]


def all_picks(db: Session, run: Run, w: Optional[float], book: Optional[str] = None) -> pd.DataFrame:
    """Priced picks for (run, current odds, market weight); memoized because pricing loops over every line."""
    version = tuple(sorted(db.execute(select(OddsLine.market, func.max(OddsLine.fetched_at))
                                      .group_by(OddsLine.market)).all()))
    hide = books.hide_offshore(db)
    key = (run.id, version, _wkey(w), book, hide, market_fit.fit_state(db, "nfl").get("fitted_at"))
    if key not in _pick_cache:
        if len(_pick_cache) > 40:
            _pick_cache.clear()
        proj, priced = _priced(db, run, version, w)
        all_books = priced.book.unique() if not priced.empty else []
        picks = best_picks(priced, books.pricing_books(book, all_books, hide)) if not priced.empty else priced.copy()
        if not picks.empty:
            l5 = dict(zip(zip(proj.player_id, proj.kind), proj.last5))
            picks["last5"] = [l5.get(k, []) for k in zip(picks.player_id, picks.kind)]
        else:
            picks["last5"] = []
        _pick_cache[key] = picks
    return _pick_cache[key]


def records(df: pd.DataFrame) -> list[dict]:
    return df.replace({np.nan: None}).to_dict("records")


# ---------- job runner (app/scheduler.py runs jobs, one per league; the same state objects are shared here) ----------
_job = scheduler._job     # the NFL job
_jobs = scheduler._jobs


def _release_memory():
    """Hand a finished job's memory back to the OS and drop caches the new data outdates."""
    import gc
    _injury_report.cache_clear()
    _next_week_schedule.cache_clear()
    _stats.cache_clear()   # game logs in the player drawer come from the stats files, which a sync just refreshed
    _pick_cache.clear()
    _priced_cache.clear()
    _hot_cache.clear()
    market_fit.clear_cache()
    nba_routes.clear_caches()
    gc.collect()
    try:
        import ctypes
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        pass  # not glibc (e.g. macOS): nothing to trim


# ---------- routes ----------
@app.get("/api/meta", response_model=Meta)
def meta(request: Request, db: Session = Depends(get_db)):
    out = build_meta(db, _lan_url(request))
    out.can_write, out.can_run_projections = is_admin(request), config.PROJECTIONS_ON_SERVER()
    return out


@lru_cache(maxsize=4)
def _next_week_schedule(season: int, week: int) -> tuple[tuple, tuple]:
    """The week after `week`: its games as (game_id, gameday, gametime), and the teams on bye (every team on the season's
    schedule without a game that week). From the schedule a sync downloaded, never downloading on a page load; both
    empty after the regular season or before there's a copy."""
    try:
        s = data.cached_schedule()
    except Exception:
        return (), ()
    if s is None:
        return (), ()
    season_games = s[s.season == season]
    nxt = season_games[season_games.week == week + 1].sort_values(["gameday", "gametime", "game_id"])
    if nxt.empty:
        return (), ()
    playing = set(nxt.away_team) | set(nxt.home_team)
    byes = sorted((set(season_games.away_team) | set(season_games.home_team)) - playing)
    return tuple((r.game_id, str(r.gameday), "" if pd.isna(r.gametime) else str(r.gametime)) for r in nxt.itertuples()), tuple(byes)


def _fetch_schedule() -> None:
    """Download the schedule into the cache in the background, then let the next-week preview read it."""
    try:
        data.load_schedule()
        _next_week_schedule.cache_clear()
        print("Fetched the NFL schedule (the cache was empty)")
    except Exception as e:
        print(f"Couldn't fetch the NFL schedule; the next sync will: {e}")


def build_meta(db: Session, lan_url: Optional[str] = None) -> Meta:
    run = latest_run(db)
    games, next_games, next_byes, backtest = [], [], [], {}
    if run:
        rows = db.execute(select(Projection.game_id, Projection.gameday, Projection.gametime)
                          .where(Projection.run_id == run.id).distinct()).all()
        weather = run.weather or {}
        recs = standings.records("football/nfl", run.season, data.CACHE, data.ESPN_TEAMS)
        games = [Game(game_id=g, label=game_label(g), gameday=d, gametime=t, weather=weather.get(g),
                      records={t: recs[t] for t in g.split("_")[2:4] if t in recs})
                 for g, d, t in sorted(rows, key=lambda r: (r[1], r[2], r[0]))]
        upcoming, byes = _next_week_schedule(run.season, run.week)
        next_games = [Game(game_id=g, label=game_label(g), gameday=d, gametime=t, records={t_: recs[t_] for t_ in g.split("_")[2:4] if t_ in recs})
                      for g, d, t in upcoming]
        next_byes = [ByeTeam(team=t, record=recs[t]["overall"] if t in recs else None) for t in byes]
        backtest = run.backtest
    return Meta(redistribution=(run.variance or {}).get("_redistribution") if run else None,
                weather=(run.variance or {}).get("_weather") if run else None,
                calibration=(run.variance or {}).get("_calibration") if run else None,
                run=None if not run else RunInfo(id=run.id, season=run.season, week=run.week,
                                                  created_at=run.created_at.isoformat(), excluded=run.excluded),
                backtest=backtest, odds=_odds_info(db), games=games, next_games=next_games, next_byes=next_byes, has_odds_key=bool(config.ODDS_API_KEY()),
                job=JobStatus(**_job), lan_url=lan_url, auto_sync=_auto_sync(),
                books=books.shown((b for (b,) in db.execute(select(OddsLine.book).distinct()).all()), books.hide_offshore(db)))


def _lan_url(request: Request) -> Optional[str]:
    if os.environ.get("APP_URL"):  # e.g. a Tailscale MagicDNS name: http://my-mac.tailnet-name.ts.net:8000
        return os.environ["APP_URL"].rstrip("/")
    ip = lan_ip()
    port = request.url.port or (443 if request.url.scheme == "https" else 80)
    return f"{request.url.scheme}://{ip}:{port}" if ip else None


def _odds_info(db: Session) -> dict:
    """Per market: when its newest quotes were fetched and how many quotes are current (across all games)."""
    fetched = {m: t for m, t in db.execute(select(OddsLine.market, func.max(OddsLine.fetched_at)).group_by(OddsLine.market)).all()}
    counts = current_lines(db).groupby("market").size().to_dict()
    return {m: OddsInfo(fetched_at=t.isoformat(), lines=int(counts.get(m, 0))) for m, t in fetched.items()}


def _excluded(kind: str) -> tuple:
    """exclude_kind=td leaves out every touchdown market (anytime and first TD): the "all over/unders" views."""
    return tdm.YES_NO if kind == "td" else (kind,)


@app.get("/api/projections", response_model=list[ProjectionOut])
def projections(kind: Optional[Kind] = None, exclude_kind: Optional[Kind] = None, game_id: Optional[str] = None,
                q: Optional[str] = None, db: Session = Depends(get_db)):
    run = latest_run(db)
    if not run:
        return []
    stmt = select(Projection).where(Projection.run_id == run.id).order_by(Projection.mu.desc(), Projection.id)
    if kind:
        stmt = stmt.where(Projection.kind == kind)
    if exclude_kind:
        stmt = stmt.where(Projection.kind.not_in(_excluded(exclude_kind)))
    if game_id:
        stmt = stmt.where(Projection.game_id == game_id)
    if q:
        stmt = stmt.where(Projection.name.ilike(f"%{q}%"))
    return db.scalars(stmt).all()


@app.get("/api/picks", response_model=list[PickOut])
def picks(kind: Optional[Kind] = None, exclude_kind: Optional[Kind] = None, side: Optional[Literal["Over", "Under"]] = None,
          game_id: Optional[str] = None, q: Optional[str] = None, book: Optional[str] = None,
          min_ev: float = Query(0.03, ge=-1, le=1), market_weight: Optional[float] = Query(None, ge=0, le=1),
          include_flagged: bool = False, limit: int = Query(200, ge=1, le=1000), db: Session = Depends(get_db)):
    """Priced plays. market_weight: how much to trust the market (0-1); left out, each market's fitted weight."""
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
    if exclude_kind:
        df = df[~df.kind.isin(_excluded(exclude_kind))]
    if side:
        df = df[df.side == side]
    if game_id:
        df = df[df.game_id == game_id]
    if q:
        df = df[df.name.str.contains(q, case=False, regex=False)]
    return records(df.head(limit))


@app.get("/api/hot", response_model=list[HotOut])
def hot(kind: Optional[Kind] = None, exclude_kind: Optional[Kind] = None, side: Literal["Over", "Under"] = "Over",
        game_id: Optional[str] = None, q: Optional[str] = None, book: Optional[str] = None,
        window: int = Query(5, ge=3, le=20), limit: int = Query(200, ge=1, le=1000), db: Session = Depends(get_db)):
    """Who's hot: players whose last `window` games mostly cleared the line they're posted at now (Under: stayed below
    it), with the board's price and EV at that line. Lines come from the board at `book` (default: the best price)."""
    run = latest_run(db)
    if not run:
        return []
    df = all_picks(db, run, None, book)
    if df.empty:
        return []
    df = df[df.side == side]
    if kind:
        df = df[df.kind == kind]
    if exclude_kind:
        df = df[~df.kind.isin(_excluded(exclude_kind))]
    if game_id:
        df = df[df.game_id == game_id]
    if q:
        df = df[df.name.str.contains(q, case=False, regex=False)]
    return hotm.hot_rows(df.replace({np.nan: None}), _hot_logs(run.id, run.season, db), window)[:limit]


_hot_cache: dict = {}


def _hot_logs(run_id: int, season: int, db: Session) -> dict:
    """(player_id, kind) -> values oldest first, for every player projected in the run (the drawer's logs, all at once)."""
    if run_id not in _hot_cache:
        _hot_cache.clear()
        kinds: dict = {}
        for pid, k in db.execute(select(Projection.player_id, Projection.kind).where(Projection.run_id == run_id)).all():
            kinds.setdefault(pid, []).append(k)
        st = _stats(season)
        st = st[st.player_id.isin(kinds)].sort_values("t")
        _hot_cache[run_id] = {(pid, k): [e.yards for e in entries] for pid, g in st.groupby("player_id")
                              for k, entries in _logs_from(g.tail(60), kinds[pid]).items()}
    return _hot_cache[run_id]


@app.get("/api/correlations", response_model=Correlations)
def correlations(db: Session = Depends(get_db)):
    """The latest run's same-game correlations, for pricing parlays whose legs share a game."""
    return build_correlations(latest_run(db))


def build_correlations(run: Optional[Run]) -> Correlations:
    c = ((run.variance or {}).get("_correlation") if run else None) or {}
    return Correlations(pairs={k: v[0] for k, v in (c.get("pairs") or {}).items()}, check=c.get("check") or {})


@app.get("/api/grades", response_model=GradeReport)
def grades(request: Request, season: Optional[int] = None, db: Session = Depends(get_db)):
    """How the logged board did against the box scores and the closing line, whether the model or the line landed
    closer, and the fitted book and market weights. Model check is the owner's page, so this is admin-only."""
    if not is_admin(request):
        raise HTTPException(403, "Admin only")
    return dict(grading.report(db, season), market_fit=market_fit.summary(db, "nfl", current_lines(db).book.unique()))


@app.get("/api/moneylines", response_model=list[MoneylineGame])
def moneylines(book: Optional[str] = None, market_weight: Optional[float] = Query(None, ge=0, le=1), db: Session = Depends(get_db)):
    """Both teams' win chances for every game of the week (model, market, blend), with the best moneyline and its EV."""
    run = latest_run(db)
    return build_moneylines(db, run, market_weight, book)


def build_moneylines(db: Session, run: Optional[Run], market_weight: Optional[float] = None, book: Optional[str] = None) -> list[dict]:
    if not run or not run.teams:
        return []
    lines = current_lines(db)
    w = weight_for(market_fit.market_weights(db, "nfl"), "ml", None) if market_weight is None else market_weight
    return mlm.price(run.teams, lines, w, books.pricing_books(book, lines.get("book", []), books.hide_offshore(db)),
                     book_weights=market_fit.book_weights(db, "nfl"))


@app.get("/api/moneylines/{game_id}/{team}/lines", response_model=LadderOut)
def moneyline_lines(game_id: str, team: str, market_weight: Optional[float] = Query(None, ge=0, le=1), db: Session = Depends(get_db)):
    """One team's moneyline at every book, shaped like a player's line ladder, for shopping a betslip leg between books."""
    run = latest_run(db)
    lines = current_lines(db)
    w = weight_for(market_fit.market_weights(db, "nfl"), "ml", None) if market_weight is None else market_weight
    out = run and run.teams and mlm.side_ladder(run.teams, lines, game_id, team, w, books.pricing_books(None, lines.get("book", []), books.hide_offshore(db)),
                                                book_weights=market_fit.book_weights(db, "nfl"))
    if not out:
        raise HTTPException(404, "That team isn't playing that game this week")
    return out


@app.get("/api/hot-teams", response_model=list[HotTeamOut])
def hot_teams(book: Optional[str] = None, game_id: Optional[str] = None, window: int = Query(5, ge=3, le=20), db: Session = Depends(get_db)):
    """Who's hot, teams: those on a winning streak, with their moneyline (at `book`, default the best price) and its EV.
    Games that have kicked off are left out."""
    run = latest_run(db)
    now = datetime.utcnow()
    priced = [g for g in build_moneylines(db, run, None, book) if (not game_id or g["game_id"] == game_id)
              and (market_fit.kickoff_utc(g["gameday"], g["gametime"]) or now) > now]
    return mlm.hot_teams(priced, run.teams, window) if priced else []


@app.get("/api/injuries", response_model=InjuryReport)
def injuries(db: Session = Depends(get_db)):
    """This week's injury report for every team on the slate, both sides of the ball, plus reserve lists (IR, PUP)."""
    return build_injuries(db, latest_run(db))


def build_injuries(db: Session, run: Optional[Run]) -> InjuryReport:
    if not run:
        return InjuryReport()
    games = [g for (g,) in db.execute(select(Projection.game_id).where(Projection.run_id == run.id).distinct())]
    teams = {t for g in games for t in g.split("_")[2:4]}
    order = {"pass": 0, "rush": 1, "rec": 2, "recs": 3, "rr": 4, "td": 5, "ptd": 6, "lng": 7, "rlng": 8, "ftd": 9}
    props = {}
    for pid, kind in db.execute(select(Projection.player_id, Projection.kind).where(Projection.run_id == run.id)):
        if pid not in props or order.get(kind, 9) < order.get(props[pid], 9):
            props[pid] = kind
    # nflverse's file is re-fetched at most hourly, ESPN's feed every 15 minutes
    rep, news_fetched = _injury_report(run.season, run.week, int(time.time() // 900))
    path = os.path.join(data.CACHE, f"injuries_{run.season}.csv")
    fetched = datetime.utcfromtimestamp(os.path.getmtime(path)).isoformat() if os.path.exists(path) else None
    out = {t: [dict(p, kind=props.get(p["player_id"])) for p in ps] for t, ps in rep["teams"].items() if t in teams}
    return InjuryReport(season=run.season, week=run.week, report_week=rep["report_week"], fetched_at=fetched,
                        news_fetched_at=news_fetched, teams=out)


@lru_cache(maxsize=2)
def _injury_report(season: int, week: int, _slot: int) -> tuple[dict, Optional[str]]:
    try:
        espn, news_fetched = data.load_espn_injuries()
    except Exception:   # ESPN only adds detail: the nflverse report stands on its own
        espn, news_fetched = None, None
    rep = injm.build(data.load_injuries(season), data.load_reserve(season), week,
                     espn=espn, espn_ids=data.load_espn_ids(season) if espn is not None else None)
    return rep, news_fetched


@app.get("/api/player-search", response_model=list[PlayerHit])
def player_search(q: str = Query("", max_length=60), limit: int = Query(12, ge=1, le=50), db: Session = Depends(get_db)):
    """Active offensive players whose name matches q: this week's projected players plus everyone else on a roster
    (on bye, or not projected), names starting with q first, projected players before the rest."""
    run = latest_run(db)
    if not run or not q.strip():
        return []
    shots = data.load_headshots(run.season)
    hits = {pid: dict(player_id=pid, name=n, pos=pos, team=t, headshot=shots.get(pid), projected=True)
            for pid, n, pos, t in db.execute(select(Projection.player_id, Projection.name, Projection.pos, Projection.team)
                                             .where(Projection.run_id == run.id).distinct())}
    for r in data.load_active_players(run.season).to_dict("records"):
        hits.setdefault(r["player_id"], dict(r, projected=False))
    return rank_hits(list(hits.values()), q, limit)


@app.get("/api/players/{player_id}", response_model=PlayerDetail)
def player(player_id: str, market_weight: Optional[float] = Query(None, ge=0, le=1), db: Session = Depends(get_db)):
    detail = build_player_detail(db, latest_run(db), player_id, market_weight)
    if not detail:
        raise HTTPException(404, "Player has no projection in the current run")
    return detail


def build_player_detail(db: Session, run: Optional[Run], player_id: str, market_weight: Optional[float] = None) -> Optional[PlayerDetail]:
    rows = db.scalars(select(Projection).where(Projection.run_id == run.id, Projection.player_id == player_id).order_by(Projection.id)).all() if run else []
    if not rows:
        return None
    p = rows[0]
    df = all_picks(db, run, market_weight)
    pp = df[df.player_id == player_id] if not df.empty else df
    return PlayerDetail(player_id=player_id, name=p.name, pos=p.pos, team=p.team,
                        headshot=data.load_headshots(run.season).get(player_id),
                        projections=[ProjectionOut.model_validate(r, from_attributes=True) for r in rows],
                        picks=records(pp), logs=_game_logs(player_id, [r.kind for r in rows], run.season))


def _player_proj(db: Session, player_id: str, kind: str):
    run = latest_run(db)
    p = db.scalars(select(Projection).where(Projection.run_id == run.id, Projection.player_id == player_id,
                                            Projection.kind == kind)).first() if run else None
    if not p:
        raise HTTPException(404, "No projection for that player and market in the current run")
    return p


@app.get("/api/players/{player_id}/lines", response_model=LadderOut)
def player_lines(player_id: str, kind: Kind, odds_range: int = Query(300, ge=0, le=100000),
                 market_weight: Optional[float] = Query(None, ge=0, le=1), db: Session = Depends(get_db)):
    """Every posted quote (main and alternate lines, all books) for one player/market, priced by the model blended
    with the market's distribution read off the ladder. Prices outside -odds_range..+odds_range are hidden (default
    300; 0 shows everything)."""
    return build_ladder_out(db, _player_proj(db, player_id, kind), odds_range, market_weight)


@app.get("/api/players/{player_id}/history", response_model=list[HistoryPoint])
def player_history(player_id: str, kind: Kind, db: Session = Depends(get_db)):
    """How one player's market moved this week: per odds fetch, the consensus line and its fair over chance."""
    p = _player_proj(db, player_id, kind)
    return market_fit.line_history(db, p.game_id, p.name, kind, p.mu, p.sd, _dists(db.get(Run, p.run_id)).get(kind), "nfl")


def build_ladder_out(db: Session, p: Projection, odds_range: int = 0, market_weight: Optional[float] = None) -> LadderOut:
    key, kind = odds.norm(p.name), p.kind
    main = current_lines(db)
    main = main[(main.market == kind) & (main.player.map(odds.norm) == key) & (main.game_id == p.game_id)].assign(alt=False)
    alts = db.scalars(select(AltLine).where(AltLine.game_id == p.game_id, AltLine.market == kind).order_by(AltLine.id)).all()
    alt_df = pd.DataFrame([dict(player=r.player, market=r.market, line=r.line, over_odds=r.over_odds,
                                under_odds=r.under_odds, book=r.book, over_link=r.over_link, under_link=r.under_link,
                                event_link=r.event_link, alt=True) for r in alts if odds.norm(r.player) == key],
                          columns=list(main.columns))
    quotes = pd.concat([f for f in (main, alt_df) if not f.empty] or [main], ignore_index=True)
    dist = _dists(db.get(Run, p.run_id)).get(kind)
    # the market curve reads every book, offshore ones included, as the board's consensus does
    curve = None if p.thin else ladder_curve(quotes, p.mu, kind, dist, market_fit.book_weights(db, "nfl"))
    w = market_fit.ladder_weight(market_weight, market_fit.market_weights(db, "nfl"), p, main)
    if books.hide_offshore(db):
        quotes = quotes[~quotes.book.isin(books.OFFSHORE)]
    fetched = max((r.fetched_at for r in alts), default=None)
    return LadderOut(kind=kind, mu=p.mu, sd=p.sd, game_id=p.game_id, alt_fetched_at=fetched.isoformat() if fetched else None,
                     curve=curve, w=w if curve else None,
                     # thin-history projections aren't priced (model.THIN_GAMES), so they get no ladder
                     quotes=[] if p.thin else build_ladder(p.mu, p.sd, quotes, odds_range, kind, dist, curve, w))


@app.post("/api/players/{player_id}/alt-lines", response_model=AltFetchResult)
def fetch_alt_lines(player_id: str, db: Session = Depends(get_db)):
    """Fetch alternate lines (and betslip links) for this player's game. Costs 2 API credits per market the
    player has a projection in."""
    run = latest_run(db)
    rows = db.scalars(select(Projection).where(Projection.run_id == run.id, Projection.player_id == player_id).order_by(Projection.id)).all() if run else []
    if not rows:
        raise HTTPException(404, "Player has no projection in the current run")
    if not config.ODDS_API_KEY():
        raise HTTPException(400, "No ODDS_API_KEY configured")
    kinds = sorted({r.kind for r in rows})
    try:
        res = pipeline.fetch_game_lines(rows[0].game_id, kinds)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except Exception as e:  # upstream API failure
        raise HTTPException(502, f"Odds API error: {e}")
    _pick_cache.clear()
    _priced_cache.clear()
    return AltFetchResult(kinds=kinds, **res)


_LOG_COLUMNS = ["player_id", "season", "week", "season_type", "position", "team", "opponent_team", "rushing_yards",
                "receiving_yards", "passing_yards", "receptions", "carries", "targets", "attempts", "rushing_tds", "receiving_tds", "passing_tds"]


@lru_cache(maxsize=1)
def _stats(season: int) -> pd.DataFrame:
    st = model.prep(data.load_stats([season - 1, season], usecols=_LOG_COLUMNS))
    try:   # longest catch and carry, and the first TD, come from play-by-play; without it those logs are left out
        seasons = [season - 1, season]
        st = model.add_longest(st, data.load_catches(seasons), data.load_rushes(seasons))
        st = model.add_first_td(st, data.load_first_tds(seasons))
    except Exception:
        pass
    return st


def _game_logs(player_id: str, kinds: list[str], season: int, n: int = 60) -> dict:
    """The player's games per market this season and last (up to the last n): the drawer charts the last 12 and works
    out hit rates from the rest."""
    st = _stats(season)
    return _logs_from(st[st.player_id == player_id].sort_values("t").tail(n), kinds)


def _logs_from(g: pd.DataFrame, kinds) -> dict:
    """One player's game log rows (oldest first) as GameLogEntry lists per market."""
    out = {}
    for kind in kinds:
        if kind == "td":  # touchdowns scored per game (rushing + receiving) against touches
            yards, vol = g.rushing_tds.fillna(0) + g.receiving_tds.fillna(0), g.carries.fillna(0) + g.targets.fillna(0)
        elif kind == "rr":
            yards, vol = g.rushing_yards.fillna(0) + g.receiving_yards.fillna(0), g.carries.fillna(0) + g.targets.fillna(0)
        elif kind == "ftd":   # scored the game's first touchdown (1/0) against touches
            if "first_td" not in g:
                continue
            yards, vol = g.first_td, g.carries.fillna(0) + g.targets.fillna(0)
        elif kind in model.LONG_COLS:   # longest catch against catches, longest rush against carries
            if model.LONG_COLS[kind] not in g:
                continue
            yards, vol = g[model.LONG_COLS[kind]], (g.receptions if kind == "lng" else g.carries).fillna(0)
        else:
            c = model.CFG[kind]
            yards, vol = g[c["yds"]], g[c["vol"]]
        out[kind] = [GameLogEntry(label=f"{int(s)} W{int(w)}", opp=o, yards=float(y), volume=float(v), season=int(s))
                     for s, w, o, y, v in zip(g.season, g.week, g.opponent_team, yards, vol)]
    return out


@app.post("/api/refresh", response_model=JobStatus, status_code=202)
def refresh(req: RefreshRequest, bg: BackgroundTasks):
    if not scheduler.claim():
        raise HTTPException(409, "A refresh is already running")
    bg.add_task(scheduler.run, req.odds, req.full)
    return JobStatus(**_job)


@app.get("/api/refresh/plan", response_model=SyncPlan)
def refresh_plan(request: Request, odds: Literal["none", "missing", "thin", "today", "all"] = "missing", full: bool = False):
    """What a sync would do right now, and what it would cost: for the confirm step in the admin UI."""
    if not is_admin(request):
        raise HTTPException(403, "Admin only")
    games = pipeline.upcoming_games()
    if odds == "none":
        plan = dict(total_games=len(games), games=[], credits=0)
    else:
        with SessionLocal() as s:
            plan = pipeline.sync_plan(s, games, odds)
    if len(games):
        plan["season"], plan["week"] = int(games.iloc[0].season), int(games.iloc[0].week)
    with SessionLocal() as s:
        run = latest_run(s)
        cal = (run.variance or {}).get("_calibration") if run else None
    season = plan.get("season")
    will_recalibrate = bool(full) or season is None or pipeline.reusable_calibration(season, week=plan.get("week")) is None
    return SyncPlan(**plan, will_run_projections=config.PROJECTIONS_ON_SERVER(), has_odds_key=bool(config.ODDS_API_KEY()),
                    will_recalibrate=will_recalibrate, calibration=cal)


def _auto_sync(league: Optional[str] = "nfl") -> AutoSync:
    """The next scheduled sync of one league (None: of either)."""
    if not config.AUTO_SYNC():
        return AutoSync(enabled=False)
    at, slot = scheduler.upcoming(league=league)[0]
    return AutoSync(enabled=True, next_at=at.isoformat(), next_label=slot.label)


auth.client_key = client_key
app.include_router(auth.router)
app.include_router(bets.router)
app.include_router(bet_screens.router)
app.include_router(slip_sync.router)
nba_routes.is_admin = lambda request: is_admin(request)
nba_routes.auto_sync = lambda: _auto_sync("nba")
app.include_router(nba_routes.router)


# ---------- admin page: the schedule, the run log, the key's credits ----------
def require_admin(request: Request):
    if not is_admin(request):
        raise HTTPException(403, "Admin only")


_credits: dict = {}   # key -> (checked_at, remaining or error): the admin page polls, the balance changes slowly


def _credit_status(league: str = "nfl") -> CreditStatus:
    """The key syncs are spending (the first one over the floor, app/odds_keys.py) and the ones to rotate."""
    keys, floor = config.odds_keys(league), config.ODDS_CREDIT_FLOOR()
    if not keys:
        return CreditStatus(floor=floor, error=f"No {'ODDS_API_KEY_NBA' if league == 'nba' else 'ODDS_API_KEY'} set")
    now = time.time()
    for _, key in keys:
        if key not in _credits or now - _credits[key][0] > 60 or _jobs[league]["state"] == "running":
            try:
                _credits[key] = (now, odds.credits_remaining(key), None)
            except Exception as e:
                _credits[key] = (now, None, f"Couldn't check the balance: {e}")
    spent = [name for name, key in keys if _credits[key][1] is not None and _credits[key][1] <= floor]
    name, key = next(((n, k) for n, k in keys if n not in spent), keys[-1])
    checked, left, err = _credits[key]
    others = [KeyBalance(key_name=n, key_hint=k[-4:], remaining=_credits[k][1], error=_credits[k][2]) for n, k in keys if n != name]
    return CreditStatus(remaining=left, floor=floor, key_hint=key[-4:], key_name=name, error=err, rotate=spent, others=others,
                        low=len(spent) == len(keys), checked_at=datetime.utcfromtimestamp(checked).isoformat())


def _run_out(r: SyncRun) -> dict:
    return dict(id=r.id, slot=r.slot, label=scheduler.BY_ID[r.slot].label if r.slot in scheduler.BY_ID else "Manual sync",
                trigger=r.trigger, scheduled_for=r.scheduled_for.isoformat() if r.scheduled_for else None,
                started_at=r.started_at.isoformat(), finished_at=r.finished_at.isoformat() if r.finished_at else None,
                status=r.status, odds_mode=r.odds_mode, full=r.full, season=r.season, week=r.week,
                games_fetched=r.games_fetched, credits_before=r.credits_before, credits_after=r.credits_after,
                summary=r.summary, error=r.error)


@app.get("/api/admin", response_model=AdminState, dependencies=[Depends(require_admin)])
def admin_state(limit: int = Query(60, ge=1, le=500), db: Session = Depends(get_db)):
    runs = db.scalars(select(SyncRun).order_by(SyncRun.id.desc()).limit(limit)).all()
    last = {}
    for r in runs:
        last.setdefault(r.slot, r)
    slots = [SlotOut(id=s.id, label=s.label, league=s.league, when=scheduler.describe(s), odds_mode=s.odds, full=s.full, why=s.why,
                     next_at=at.isoformat(), last_run=SyncRunOut(**_run_out(last[s.id])) if s.id in last else None)
             for at, s in scheduler.upcoming()]
    return AdminState(auto_sync=_auto_sync(None), timezone="America/New_York", credits=_credit_status(),
                      credits_nba=_credit_status("nba"), slots=slots,
                      runs=[SyncRunOut(**_run_out(r)) for r in runs], job=JobStatus(**_job), job_nba=JobStatus(**_jobs["nba"]),
                      can_run_projections=config.PROJECTIONS_ON_SERVER(), hide_offshore=books.hide_offshore(db))


@app.post("/api/admin/settings", response_model=SiteSettings, dependencies=[Depends(require_admin)])
def admin_settings(body: SiteSettings, db: Session = Depends(get_db)):
    """Site-wide options: hide_offshore keeps books licensed in no US state off every list and price."""
    books.set_hide_offshore(db, body.hide_offshore)
    return SiteSettings(hide_offshore=books.hide_offshore(db))


@app.get("/api/admin/runs/{run_id}", response_model=SyncRunDetail, dependencies=[Depends(require_admin)])
def admin_run(run_id: int, db: Session = Depends(get_db)):
    r = db.get(SyncRun, run_id)
    if not r:
        raise HTTPException(404, "No such run")
    return SyncRunDetail(**_run_out(r), log=r.log or [])


@app.post("/api/admin/run/{slot_id}", response_model=JobStatus, status_code=202)
def admin_run_now(slot_id: str, bg: BackgroundTasks):
    """Run one schedule slot right now (logged as a manual run of that slot)."""
    slot = scheduler.BY_ID.get(slot_id)
    if not slot:
        raise HTTPException(404, "No such schedule slot")
    if not scheduler.claim(slot.league):
        raise HTTPException(409, f"An {slot.league.upper()} sync is already running")
    bg.add_task(scheduler.run, slot.odds, slot.full, slot.id, "manual", None, slot.league)
    return JobStatus(**_jobs[slot.league])


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"ok": True}


@app.get("/api/refresh/status", response_model=JobStatus)
def refresh_status():
    return JobStatus(**_job)


# ---------- serve the built React app in production ----------
if os.path.isdir(config.FRONTEND_DIST):
    app.mount("/assets", StaticFiles(directory=os.path.join(config.FRONTEND_DIST, "assets")), name="assets")

    @app.get("/add-slip.shortcut", include_in_schema=False)
    def add_slip_shortcut():
        """The iPhone Shortcut (frontend/pwa/add-slip.shortcut), as a download (Content-Disposition: attachment) so Safari
        offers it to Shortcuts instead of showing the file as text."""
        return FileResponse(os.path.join(config.FRONTEND_DIST, "add-slip.shortcut"), media_type="application/octet-stream",
                            filename="Add to The Haber Process.shortcut")

    @app.post("/share-slip", include_in_schema=False)
    def share_slip():
        """A screenshot shared to the installed app (manifest share_target) is caught by the service worker (pwa/sw.js). This
        only answers when the worker isn't running yet, such as the first launch after installing: on to My bets, where
        Add a bet can take the upload."""
        return RedirectResponse("bets", status_code=303)

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = os.path.join(config.FRONTEND_DIST, path)
        return FileResponse(f if path and os.path.isfile(f) else os.path.join(config.FRONTEND_DIST, "index.html"))
