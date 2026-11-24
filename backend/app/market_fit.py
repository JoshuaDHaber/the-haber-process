"""The market side of grading: closing-line value for logged picks, and the two fits that read the market better
(engine/market.py): each book's weight in the consensus and how much to trust the market against the model.

Every sync appends the board's prices to pick_snaps (log_snaps). Each model run, after grading, refit() attaches the
close to finished games' logged picks (the market's fair chance of the pick's side at its line, from the last
odds_history fetch before kickoff), refits the book weights on recent odds_history and the market weights on graded
picks, and stores both in the settings table, where the board reads them.

Beating the closing line is the out-of-sample test that results can't give quickly: a few hundred graded props say
little about an edge, but whether the market moved toward the picks shows up within weeks.
"""
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from sqlalchemy import func as sa_func, select

from .db import BetLeg, NbaPickLog, NbaRun, OddsHistory, PickLog, PickSnap, Run, Setting
from .engine import market as mkt
from .engine import odds
from .engine.model import prob_over_under, prob_over_under_dist
from .engine.picks import HOLD, hours_before, stale_gap
from .engine.td import MIN_MARKET_WEIGHT, YES_NO

ET = ZoneInfo("America/New_York")
TABLES = {"nfl": (PickLog, Run), "nba": (NbaPickLog, NbaRun)}
# book weights are fitted on recent line history: six NFL weeks, two NBA weeks (a daily slate and three fetches a day
# make NBA history about ten times denser, and the server has 1 GB)
BOOK_WINDOW_DAYS = {"nfl": 42, "nba": 14}
# sports whose model chances go through a fitted calibration curve before the blend (engine/market.fit_calibration).
# The NFL's few hundred graded picks per market are too few to fit one yet
CALIBRATED = {"nba"}
_state: dict = {}         # sport -> the stored fit, read once per process until clear_cache()


def kickoff_utc(gameday: str, gametime: str) -> Optional[datetime]:
    """A game's start in naive UTC from its Eastern date and time."""
    try:
        local = datetime.fromisoformat(f"{gameday}T{gametime or '00:00'}").replace(tzinfo=ET)
    except ValueError:
        return None
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def _key(sport: str) -> str:
    return f"market_fit_{sport}"


def fit_state(db, sport: str) -> dict:
    """The stored fit for a sport: {book_weights, market_weights, fitted_at} ({} before the first refit)."""
    if sport not in _state:
        row = db.get(Setting, _key(sport))
        _state[sport] = (row.value if row and row.value else {})
    return _state[sport]


def book_weights(db, sport: str) -> Optional[dict]:
    return fit_state(db, sport).get("book_weights") or None


def market_weights(db, sport: str) -> Optional[dict]:
    return fit_state(db, sport).get("market_weights") or None


def clear_cache():
    _state.clear()


def dists(run) -> dict:
    """Per-kind error tables from a run's backtest (older runs stored only gamma parameters: those are skipped)."""
    return {k: v for k, v in ((run.variance if run else None) or {}).items() if isinstance(v, dict) and not k.startswith("_")}


# ---------- logging every price ----------
SNAP_COLS = ("line", "odds", "book", "p_model", "p_mkt")


def raw_chance(p) -> float:
    """The model's chance before its calibration curve (engine/picks.build_picks' p_raw): what pick_snaps and the
    pick logs store, so market.fit_calibration refits on the model itself and never on its own output."""
    v = getattr(p, "p_raw", None)
    return float(p.p_model if v is None or pd.isna(v) else v)


def _sig(x) -> tuple:
    return (round(float(x.line), 2), int(x.odds), x.book, round(raw_chance(x), 3), round(float(x.p_mkt), 3))


def log_snaps(session, sport: str, picks: pd.DataFrame, now: datetime) -> int:
    """Append the board's prices to pick_snaps, skipping a player/market/side whose price and chances haven't changed
    since its last row. The caller commits. Returns how many rows were added."""
    if picks.empty:
        return 0
    last = {}
    for r in session.scalars(select(PickSnap).where(PickSnap.game_id.in_(set(picks.game_id))).order_by(PickSnap.snapped_at)):
        last[(r.game_id, r.player_id, r.kind, r.side)] = _sig(r)
    n = 0
    for p in picks.itertuples():
        k = (p.game_id, p.player_id, p.kind, p.side)
        if last.get(k) == _sig(p):
            continue
        hours = getattr(p, "hours", None)
        session.add(PickSnap(sport=sport, snapped_at=now, game_id=p.game_id, player_id=p.player_id, kind=p.kind, side=p.side,
                             line=float(p.line), odds=int(p.odds), book=p.book, mu=float(p.mu), p_model=raw_chance(p),
                             p_mkt=float(p.p_mkt), ev=float(p.ev), flagged=bool(p.flagged),
                             hours=None if hours is None or pd.isna(hours) else float(hours)))
        n += 1
    return n


# ---------- the close ----------
def history(session, game_ids, since: Optional[datetime] = None) -> pd.DataFrame:
    """Main-line quotes from odds_history for these games (fetched on or after `since`, when given)."""
    cols = ["fetched_at", "game_id", "player", "market", "line", "over_odds", "under_odds", "book"]
    ids = list(set(game_ids))
    if not ids:
        return pd.DataFrame(columns=cols)
    out = []
    for i in range(0, len(ids), 400):   # SQLite caps the parameters in one IN (...)
        q = select(*(getattr(OddsHistory, c) for c in cols)).where(OddsHistory.game_id.in_(ids[i:i + 400]),
                                                                      OddsHistory.alt.is_(False))
        if since is not None:
            q = q.where(OddsHistory.fetched_at >= since)
        out.extend(session.execute(q).all())
    return pd.DataFrame(out, columns=cols)


def _model_over(mu: float, sd: float, line: float, dist: Optional[dict]) -> float:
    return (prob_over_under_dist(mu, line, dist) if dist else prob_over_under(mu, line, sd * sd))[0]


def closing_over(cons_row, line: float, kind: str, mu: float, sd: float, dist: Optional[dict]) -> float:
    """The close's fair chance of going over `line`: the consensus at its own line, moved along the model's slope."""
    p = float(cons_row.nv_o)
    if kind not in YES_NO and abs(line - cons_row.line) > 1e-9:
        p += _model_over(mu, sd, line, dist) - _model_over(mu, sd, cons_row.line, dist)
    return float(np.clip(p, 0.01, 0.99))


def _close_consensus(h: Optional[pd.DataFrame], kind: str, ko: datetime, spec: dict, runs: dict, weights) -> tuple:
    """The market at the last odds fetch before kickoff, for one game's market: (consensus by (player key, market), when),
    or (None, None) with no quotes. `spec` maps a player key to his logged pick (mu, run_id), for the model's slope."""
    h = h[(h.market == kind) & (h.fetched_at < ko)] if h is not None else None
    if h is None or h.empty:
        return None, None
    t = h.fetched_at.max()
    close = h[h.fetched_at == t].assign(key=lambda d: d.player.map(odds.norm))

    def p_over(key, market, line):
        r = spec.get(key)
        return None if r is None else _model_over(r.mu, r.mu * 0.5, line, runs[r.run_id].get(market))
    return mkt.consensus(close, p_over, weights, HOLD, stale_gap), t


def attach_bet_closing(session, sport: str, now: Optional[datetime] = None) -> int:
    """The close for tracked bet legs (app/bets.py) whose game has started: the same consensus attach_closing gives the
    board, at each leg's own line (main or alternate), from the player's logged pick (his projection and error table).
    A leg with no logged pick or no closing quote gets close_books = 0 and isn't looked at again. Returns how many got one."""
    from .bets import league_of
    now = now or datetime.utcnow()
    legs = [leg for leg in session.scalars(select(BetLeg).where(BetLeg.close_at.is_(None), BetLeg.kickoff <= now))
            if league_of(leg.kind, leg.game_id) == sport]
    # a moneyline has no logged pick to read a close from (the board logs player props): looked at once, then left alone
    for leg in [leg for leg in legs if leg.kind == "ml"]:
        leg.close_at, leg.close_books = leg.kickoff, 0
    legs = [leg for leg in legs if leg.kind != "ml"]
    if not legs:
        session.commit()
        return 0
    table, run_table = TABLES[sport]
    logged = {}
    for r in session.scalars(select(table).where(table.game_id.in_({leg.game_id for leg in legs}))):
        logged.setdefault((r.game_id, r.player_id, r.kind), r)
    runs = {rid: dists(session.get(run_table, rid)) for rid in {r.run_id for r in logged.values()}}
    weights = book_weights(session, sport)
    hist = history(session, {leg.game_id for leg in legs})
    by_game = {g: h for g, h in hist.groupby("game_id")} if not hist.empty else {}
    n = 0
    for (gid, kind), group in pd.Series(legs).groupby([[leg.game_id for leg in legs], [leg.kind for leg in legs]]):
        group = list(group)
        spec = {odds.norm(r.name): r for r in (logged.get((gid, leg.player_id, kind)) for leg in group) if r is not None}
        cons, t = _close_consensus(by_game.get(gid), kind, group[0].kickoff, spec, runs, weights)
        for leg in group:
            leg.close_at = t.to_pydatetime() if t is not None else leg.kickoff
            r = logged.get((gid, leg.player_id, kind))
            k = (odds.norm(r.name), kind) if r is not None else None
            c = cons.loc[k] if k is not None and cons is not None and k in cons.index else None
            if c is None or pd.isna(c.nv_o):
                leg.close_books = 0
                continue
            p = closing_over(c, leg.line, kind, r.mu, r.mu * 0.5, runs[r.run_id].get(kind))
            leg.close_p, leg.close_books = (p if leg.side == "Over" else 1 - p), int(c.books)
            n += 1
    session.commit()
    return n


def attach_closing(session, sport: str, now: Optional[datetime] = None) -> int:
    """Give every logged pick whose game has started its closing fair chance (close_p at its line, close_p_first at
    the line first shown). Picks with no closing quote get close_books = 0, so they aren't looked at again."""
    now = now or datetime.utcnow()
    table, run_table = TABLES[sport]
    todo = [r for r in session.scalars(select(table).where(table.close_at.is_(None)))
            if (ko := kickoff_utc(r.gameday, r.gametime)) is not None and ko <= now]
    if not todo:
        return 0
    runs = {rid: dists(session.get(run_table, rid)) for rid in {r.run_id for r in todo}}
    weights = book_weights(session, sport)
    hist = history(session, {r.game_id for r in todo})
    by_game = {g: h for g, h in hist.groupby("game_id")} if not hist.empty else {}
    n = 0
    for (gid, kind), rows in pd.Series(todo).groupby([[r.game_id for r in todo], [r.kind for r in todo]]):
        rows = list(rows)
        ko = kickoff_utc(rows[0].gameday, rows[0].gametime)
        cons, t = _close_consensus(by_game.get(gid), kind, ko, {odds.norm(r.name): r for r in rows}, runs, weights)
        for r in rows:
            k = (odds.norm(r.name), kind)
            c = cons.loc[k] if cons is not None and k in cons.index else None
            r.close_at = t.to_pydatetime() if t is not None else ko
            if c is None or pd.isna(c.nv_o):
                r.close_books = 0
                continue
            dist = runs[r.run_id].get(kind)
            sd = r.mu * 0.5   # only used without an error table (old runs): a rough spread
            side = (lambda p: p) if r.side == "Over" else (lambda p: 1 - p)
            r.close_p = side(closing_over(c, r.line, kind, r.mu, sd, dist))
            if r.first_line is not None:
                r.close_p_first = side(closing_over(c, r.first_line, kind, r.mu, sd, dist))
            r.close_line, r.close_books = float(c.line), int(c.books)
            n += 1
    session.commit()
    return n


# ---------- the fits ----------
def fit_rows(session, sport: str) -> pd.DataFrame:
    """Graded over (Yes) sides for the market-weight fit: every logged price from pick_snaps, the latest per player,
    market and time bucket, or pick_log's own row for a pick with no snaps (logged before pick_snaps existed).
    Pushes are left out."""
    table, _ = TABLES[sport]
    over = {(r.game_id, r.player_id, r.kind): r for r in session.scalars(
        select(table).where(table.actual.is_not(None), table.side == "Over", table.result != "void"))}
    if not over:
        return pd.DataFrame(columns=["kind", "hours", "p_model", "p_mkt", "y"])
    snaps = session.scalars(select(PickSnap).where(PickSnap.sport == sport, PickSnap.side == "Over",
                                                   PickSnap.game_id.in_({k[0] for k in over}))).all()
    out, have = {}, set()
    for s in sorted(snaps, key=lambda s: s.snapped_at):
        k = (s.game_id, s.player_id, s.kind)
        g = over.get(k)
        if g is None or g.actual == s.line:
            continue
        have.add(k)
        out[(*k, mkt.bucket(s.hours))] = (s.kind, s.hours, s.p_model, s.p_mkt, float(g.actual > s.line))
    for k, g in over.items():
        if k not in have and g.actual != g.line:
            out[(*k, "log")] = (g.kind, g.hours, g.p_model, g.p_mkt, float(g.actual > g.line))
    return pd.DataFrame(list(out.values()), columns=["kind", "hours", "p_model", "p_mkt", "y"])


def refit(session, sport: str, now: Optional[datetime] = None, log: Callable[[str], None] = print) -> dict:
    """Attach the close to started games, refit the book weights and market weights, and store them."""
    now = now or datetime.utcnow()
    n = attach_closing(session, sport, now)
    if n:
        log(f"Attached the closing line to {n} logged {sport.upper()} picks")
    n = attach_bet_closing(session, sport, now)
    if n:
        log(f"Attached the closing line to {n} tracked {sport.upper()} bet legs")
    table, _ = TABLES[sport]
    since = now - timedelta(days=BOOK_WINDOW_DAYS[sport])
    started = {}
    for gid, day, tm in session.execute(select(table.game_id, table.gameday, table.gametime).distinct()):
        ko = kickoff_utc(day, tm)
        if ko is not None and since <= ko <= now:
            started[gid] = ko
    hist = history(session, started, since)
    if not hist.empty:
        hist = hist[hist.fetched_at < hist.game_id.map(started)]
    bw = mkt.fit_book_weights(hist)
    rows = fit_rows(session, sport)
    mw = mkt.fit_market_weights(rows)
    if sport in CALIBRATED:
        mw["calibration"] = mkt.fit_calibration(rows)
    value = dict(book_weights=bw, market_weights=mw, fitted_at=now.isoformat())
    row = session.get(Setting, _key(sport)) or Setting(key=_key(sport))
    row.value = value
    session.add(row)
    session.commit()
    _state[sport] = value
    if mw.get("n"):
        log(f"Fitted the market weight on {mw['n']} graded {sport.upper()} picks (overall {mw['w']:.0%})")
    return value


def refit_logged(session, sport: str, log: Callable[[str], None] = print) -> None:
    """refit(), for the model run: a problem is logged and never stops the run."""
    try:
        refit(session, sport, log=log)
    except Exception as e:   # noqa: BLE001
        session.rollback()
        log(f"  couldn't refit the market weights ({type(e).__name__}: {e})")


# ---------- line movement on the board ----------
def model_p_over(proj: pd.DataFrame, dists_: dict) -> Callable:
    """p_over(key, market, line) from a run's projections: the model's chance of going over (for the consensus slope)."""
    pm = {(odds.norm(r.name), r.kind): (float(r.mu), float(r.sd)) for r in proj.itertuples()} if not proj.empty else {}
    cache = {}

    def p_over(key, market, line):
        v = pm.get((key, market))
        if v is None:
            return None
        k = (key, market, float(line))
        if k not in cache:
            cache[k] = _model_over(v[0], v[1], line, dists_.get(market))
        return cache[k]
    return p_over


def add_moves(session, sport: str, picks: pd.DataFrame, proj: pd.DataFrame, dists_: dict) -> pd.DataFrame:
    """picks with open_line (the consensus line at the first fetch), move (how far the market's fair chance of the
    pick's side has moved since that first fetch, at the pick's line: positive = toward the pick) and steam (+1 when the
    books moved together toward the pick between the last two fetches, -1 against it, 0 otherwise; market.steam)."""
    out = picks.assign(open_line=np.nan, move=np.nan, steam=0)
    if picks.empty:
        return out
    hist = history(session, picks.game_id.unique())
    if hist.empty:
        return out
    hist = hist.assign(key=hist.player.map(odds.norm))
    p_over = model_p_over(proj, dists_)
    weights = book_weights(session, sport)
    opens, steams = {}, {}
    for (gid, market), h in hist.groupby(["game_id", "market"]):
        times = sorted(h.fetched_at.unique())
        if len(times) < 2:
            continue
        c0 = mkt.consensus(h[h.fetched_at == times[0]], p_over, weights, HOLD, stale_gap)
        for (key, _), row in c0.iterrows():
            if pd.notna(row.nv_o):
                opens[(gid, key, market)] = row
        prev, cur = (mkt.book_fairs(h[h.fetched_at == t], p_over, weights, HOLD, stale_gap) for t in times[-2:])
        for (key, _), v in mkt.steam(prev, cur, p_over).items():
            if v:
                steams[(gid, key, market)] = int(v)
    keys = picks.name.map(odds.norm)
    open_line, move, st = (out[c].to_numpy(copy=True) for c in ("open_line", "move", "steam"))   # (writable under copy-on-write)
    for i, (gid, key, kind, side, line, mu, sd, p_mkt) in enumerate(zip(picks.game_id, keys, picks.kind, picks.side, picks.line,
                                                                        picks.mu, picks.sd, picks.p_mkt)):
        sign = 1 if side == "Over" else -1
        o = opens.get((gid, key, kind))
        if o is not None:
            p0 = closing_over(o, line, kind, mu, sd, dists_.get(kind))
            open_line[i] = float(o.line)
            move[i] = float(p_mkt) - (p0 if side == "Over" else 1 - p0)
        st[i] = steams.get((gid, key, kind), 0) * sign
    return out.assign(open_line=open_line, move=move, steam=st)


def line_history(session, game_id: str, name: str, kind: str, mu: float, sd: float, dist: Optional[dict],
                 sport: str) -> list[dict]:
    """One player's market through the week: per fetch, the consensus line, its fair over chance at that line, the
    fair chance of the over at the CURRENT consensus line (so a moved line and a moved price read on one scale), the
    best over and under prices, and how many books quoted it."""
    hist = history(session, [game_id])
    if hist.empty:
        return []
    key = odds.norm(name)
    h = hist[(hist.market == kind) & (hist.player.map(odds.norm) == key)].assign(key=key)
    if h.empty:
        return []
    weights = book_weights(session, sport)

    def p_over(_k, _m, line):
        return _model_over(mu, sd, line, dist)
    pts = []
    for t, g in h.groupby("fetched_at"):
        c = mkt.consensus(g, p_over, weights, HOLD, stale_gap)
        if (key, kind) not in c.index:
            continue
        row = c.loc[(key, kind)]
        near = g[(g.line - row.line).abs() <= stale_gap(kind, row.line)]
        pts.append(dict(at=pd.Timestamp(t).isoformat(), line=float(row.line), books=int(row.books),
                        p_over=None if pd.isna(row.nv_o) else float(row.nv_o), _row=row,
                        best_over=None if near.over_odds.isna().all() else int(near.over_odds.max()),
                        best_under=None if near.under_odds.isna().all() else int(near.under_odds.max())))
    if not pts:
        return []
    cur = pts[-1]["line"]
    for p in pts:
        row = p.pop("_row")
        p["p_now_line"] = None if pd.isna(row.nv_o) else closing_over(row, cur, kind, mu, sd, dist)
    return pts


def summary(session, sport: str, seen=()) -> dict:
    """The stored fit in the Model check tab's shape (schemas.MarketFit): book weights (every book fitted or in `seen`,
    the books on the current lines), and the market weight per market and per market and time bucket, with each one's
    log loss and the floor pricing holds it to (td.MIN_MARKET_WEIGHT)."""
    st = fit_state(session, sport)
    mw, bw = st.get("market_weights") or {}, st.get("book_weights") or {}
    kinds = mw.get("kinds") or {}

    def cell(label, c, kind):
        return dict(label=label, n=c.get("n", 0), w=c.get("w", mkt.DEFAULT_WEIGHT), w_fit=c.get("w_fit"),
                    ll_model=c.get("ll_model"), ll_market=c.get("ll_market"), ll_blend=c.get("ll_blend"),
                    floor=MIN_MARKET_WEIGHT.get(kind) or None)
    names = set(bw) | set(seen)
    books = sorted((dict(book=b, w=mkt.book_weight(b, bw), n=(bw.get(b) or {}).get("n", 0)) for b in names),
                   key=lambda r: (-r["w"], r["book"]))
    return dict(fitted_at=st.get("fitted_at"), n=mw.get("n", 0), w=mw.get("w"),
                kinds=[cell(k, v, k) for k, v in sorted(kinds.items())],
                buckets={k: [cell(b, c, k) for b, c in (v.get("buckets") or {}).items()] for k, v in sorted(kinds.items())},
                books=books)


def ladder_weight(market_weight: Optional[float], fitted: Optional[dict], p, main: pd.DataFrame) -> float:
    """The market weight for a ladder: the slider's, or the fitted one for the main line's fetch time; floored."""
    if market_weight is None:
        g = pd.DataFrame([dict(game_id=p.game_id, gameday=p.gameday, gametime=p.gametime)])
        h = hours_before(main, g) if not main.empty else pd.Series(dtype=float)
        hours = float(h.min()) if len(h) and pd.notna(h.min()) else None
        market_weight = mkt.weight_for(fitted, p.kind, hours)
    return max(market_weight, MIN_MARKET_WEIGHT.get(p.kind, 0.0))


# ---------- the slate's market board ----------
MOVER_PTS = 0.04       # a line whose fair over chance (at today's line) moved this far since the first fetch is a mover
FLAT_PTS = 0.015       # ... and one that moved less than this, with the same line, over 3+ fetches, is flat
OUTLIER_EV = 0.04      # a quote this much better than the consensus says it's worth stands out from the market
OUTLIER_MAX_ODDS = 300  # prices past -300/+300 aren't called out (a long shot's thin fair chance makes its EV noise)
OUTLIER_LINE = 0.10    # ... or a main line this far from the consensus line (share of it; whole units for small counts)


def _off_line(kind: str, cons: float) -> float:
    """How far a book's main line sits from the consensus before it's called out (0 for yes/no markets)."""
    return 0.0 if kind in YES_NO else max(OUTLIER_LINE * cons, 1.0)


def slate_market(session, sport: str, game_ids, proj: pd.DataFrame, dists_: dict, names: Optional[dict] = None) -> dict:
    """The slate's market in one read of odds_history (no Odds API calls): for every player and market quoted at the
    first and the latest fetch of his game, the consensus line then and now, how far the fair chance of the over at
    TODAY'S line has moved (the earlier consensus moved along the model's slope, as add_moves does), books and steam
    between the last two fetches; plus the latest fetch's main quotes that sit far from the consensus (a line off by
    OUTLIER_LINE or a price OUTLIER_EV better than the consensus says that side is worth at that line).
    names: player key -> (player_id, name, team, pos) from the run, for the drawer links."""
    hist = history(session, game_ids)
    out = dict(moves=[], outliers=[], fetches=0, first_at=None, last_at=None, movers=0, flat=0)
    if hist.empty:
        return out
    hist = hist.assign(key=hist.player.map(odds.norm))
    hist = hist[hist.market != "ml"]
    p_over = model_p_over(proj, dists_)
    weights = book_weights(session, sport)
    names = names or {}
    pm = {(odds.norm(r.name), r.kind): (float(r.mu), float(r.sd)) for r in proj.itertuples()} if not proj.empty else {}
    out.update(fetches=int(hist.fetched_at.nunique()), first_at=pd.Timestamp(hist.fetched_at.min()).isoformat(),
               last_at=pd.Timestamp(hist.fetched_at.max()).isoformat())
    for (gid, market), h in hist.groupby(["game_id", "market"]):
        times = sorted(h.fetched_at.unique())
        last = h[h.fetched_at == times[-1]]
        c1 = mkt.consensus(last, p_over, weights, HOLD, stale_gap)
        c0 = mkt.consensus(h[h.fetched_at == times[0]], p_over, weights, HOLD, stale_gap) if len(times) > 1 else None
        st = {}
        if len(times) > 1:
            prev, cur = (mkt.book_fairs(h[h.fetched_at == t], p_over, weights, HOLD, stale_gap) for t in times[-2:])
            st = {k: int(v) for k, v in mkt.steam(prev, cur, p_over).items()}
        n_fetch = h.groupby("key").fetched_at.nunique()
        for (key, _), now in c1.iterrows():
            who = names.get(key)
            if who is None:
                continue
            mu, sd = pm.get((key, market), (None, None))
            dist = dists_.get(market)
            row = dict(game_id=gid, kind=market, player_id=who[0], name=who[1], team=who[2], pos=who[3], line=float(now.line),
                       books=int(now.books), p_over=None if pd.isna(now.nv_o) else float(now.nv_o), fetches=int(n_fetch.get(key, 1)),
                       open_line=None, move=None, steam=st.get((key, market), 0))
            if c0 is not None and (key, market) in c0.index and mu is not None:
                o = c0.loc[(key, market)]
                if pd.notna(o.nv_o) and pd.notna(now.nv_o):
                    row["open_line"] = float(o.line)
                    row["move"] = float(now.nv_o) - closing_over(o, float(now.line), market, mu, sd, dist)
            out["moves"].append(row)
        # quotes at the latest fetch far from the consensus: a stale or off-market line, or a price the market disagrees with.
        # Over/under markets only: a yes/no long shot's fair chance is a point or two, so its "EV" is mostly noise
        if market in YES_NO:
            continue
        for q in mkt.main_quotes(last).itertuples():
            if (q.key, market) not in c1.index or q.key not in names or (q.key, market) not in pm:
                continue
            cons = c1.loc[(q.key, market)]
            # past the stale-line gap the quote is another line (an alternate in the main feed), and its "EV" would rest
            # on the model's curve far from where the market priced it
            if pd.isna(cons.nv_o) or abs(float(q.line) - float(cons.line)) > stale_gap(market, float(cons.line)):
                continue
            mu, sd = pm[(q.key, market)]
            p = closing_over(cons, float(q.line), market, mu, sd, dists_.get(market))   # the market's fair over at this book's line
            best = None
            for side, price, prob in (("Over", q.over_odds, p), ("Under", q.under_odds, 1 - p)):
                if price is None or pd.isna(price) or abs(price) > OUTLIER_MAX_ODDS:
                    continue
                ev = prob * float(odds.american_to_dec(price)) - 1
                if best is None or ev > best[2]:
                    best = (side, int(price), ev, prob)
            if best is None:
                continue
            gap = float(q.line) - float(cons.line)
            off = market not in YES_NO and abs(gap) >= _off_line(market, float(cons.line))
            if best[2] < OUTLIER_EV and not (off and best[2] >= 0):   # an off line that's merely juiced isn't news
                continue
            who = names[q.key]
            out["outliers"].append(dict(game_id=gid, kind=market, player_id=who[0], name=who[1], team=who[2], pos=who[3],
                                        book=q.book, line=float(q.line), consensus=float(cons.line), side=best[0], odds=best[1],
                                        fair=float(best[3]), ev=float(best[2]), books=int(cons.books)))
    moved = [r["move"] for r in out["moves"] if r["move"] is not None]
    out["movers"] = sum(abs(m) >= MOVER_PTS or False for m in moved)
    out["flat"] = sum(abs(r["move"]) < FLAT_PTS and r["open_line"] == r["line"] and r["fetches"] >= 3
                      for r in out["moves"] if r["move"] is not None)
    out["moves"].sort(key=lambda r: -abs(r["move"] or 0))
    out["outliers"].sort(key=lambda r: -r["ev"])
    return out


_market_cache: dict = {}


def cached_board(db, run_id: int, proj: pd.DataFrame, dists_: dict, sport: str, game_ids: list) -> dict:
    """slate_market, memoized per (league, run, games, newest stored fetch)."""
    last = db.scalar(select(sa_func.max(OddsHistory.fetched_at)).where(OddsHistory.game_id.in_(game_ids))) if game_ids else None
    key = (sport, run_id, tuple(sorted(game_ids)), last)
    if key not in _market_cache:
        if len(_market_cache) > 8:
            _market_cache.clear()
        p = proj[proj.game_id.isin(game_ids)] if not proj.empty else proj
        names = {odds.norm(r.name): (r.player_id, r.name, r.team, r.pos) for r in p.itertuples()} if not p.empty else {}
        _market_cache[key] = slate_market(db, sport, game_ids, p, dists_, names)
    return _market_cache[key]
