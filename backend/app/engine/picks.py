"""Turn projections + sportsbook lines into priced picks (win probability, EV, Kelly)."""
import numpy as np
import pandas as pd

from . import market as mkt
from . import odds
from .model import median_dist, median_gamma, prob_over_under, prob_over_under_dist
from .td import (DD_ASSUMED_HOLD, FTD_ASSUMED_HOLD, MIN_MARKET_WEIGHT, TD_ASSUMED_HOLD, TD_MIN_MARKET_WEIGHT,  # noqa: F401
                 TPL_ASSUMED_HOLD, YES_NO)  # (re-exported for callers and tests)

# margin stripped from a lone Yes price
HOLD = {"td": TD_ASSUMED_HOLD, "ftd": FTD_ASSUMED_HOLD, "dd": DD_ASSUMED_HOLD, "tpl": TPL_ASSUMED_HOLD}



PICK_COLUMNS = [
    "player_id", "name", "pos", "team", "opp", "home", "kind", "mu", "sd", "status", "game_id", "gameday", "gametime",
    "side", "line", "odds", "book", "p_model", "p_mkt", "prob", "ev", "kelly", "books", "edge_yds", "flagged", "wx",
    "flag", "teammate_out", "hours", "w", "median", "dvp_rank", "dvp_n", "p_raw",
]
# a quote this far from the consensus line (or 30% of it) is an alternate or stale line; receptions are counted in catches,
# passing TDs in touchdowns (lines step 0.5 / 1.5 / 2.5, so a whole TD off the consensus is another line)
# NBA from pts on (threes, steals and blocks step 0.5 / 1.5 / 2.5, like passing TDs)
STALE_MIN_GAP = {"recs": 1.5, "ptd": 0.9, "pts": 4.0, "reb": 2.0, "ast": 2.0, "threes": 0.9, "pra": 6.0, "pr": 5.0, "pa": 5.0,
                 "stl": 0.9, "blk": 0.9}


# Flags beyond the model-market gap, from the NBA's first month of graded picks (Oct 20 to Nov 18, 2026):
# - double- and triple-double Yes prices longer than +400: 16 of 290 hit where the blend expected 25 (-33% ROI); the
#   market already overprices them, and the model's picks there were the ones it rated even higher. Triple-doubles went
#   0 for 41.
# - EV over MAX_EV (NBA only, the callers' max_ev): those picks lost 13% where 3-5% EV picks made 4.6%. An edge that
#   big is a model miss far more often than a market one (the calibration curve shrinks most of them now; this catches
#   what's left).
LONG_SHOT = {"dd": 400, "tpl": 400}   # yes/no market -> longest Yes price shown on the board
MAX_EV = 0.20


def _dvp(p) -> dict:
    """A projection's matchup rank, for its picks (None where the projection has none)."""
    rank, n = getattr(p, "dvp_rank", None), getattr(p, "dvp_n", None)
    ok = rank is not None and pd.notna(rank)
    return dict(dvp_rank=int(rank) if ok else None, dvp_n=int(n) if ok else None)


def book_set(only_book: str = None):
    """A book filter ("DraftKings" or "BetMGM,DraftKings,FanDuel") as a set; None keeps every book."""
    return {b.strip() for b in only_book.split(",") if b.strip()} if only_book else None


def stale_gap(market: str, consensus_line: float) -> float:
    """How far a quote may sit from the consensus line before it counts as an alternate or a stale line."""
    return max(STALE_MIN_GAP.get(market, 8.0), 0.3 * consensus_line)


def hours_before(lines: pd.DataFrame, proj: pd.DataFrame) -> pd.Series:
    """Hours from each quote's fetch to its game's kickoff (NaN when either is unknown), from proj's gameday and gametime
    (Eastern) and lines' fetched_at (UTC)."""
    if "fetched_at" not in lines or "game_id" not in lines or proj.empty:
        return pd.Series(np.nan, index=lines.index)
    games = proj.drop_duplicates("game_id").set_index("game_id")
    ko = pd.to_datetime(games.gameday.astype(str) + " " + games.gametime.fillna("").astype(str).replace("", "00:00"),
                        errors="coerce").dt.tz_localize("America/New_York", ambiguous="NaT", nonexistent="NaT")
    ko = ko.dt.tz_convert("UTC").dt.tz_localize(None)
    return (lines.game_id.map(ko) - pd.to_datetime(lines.fetched_at)).dt.total_seconds() / 3600


def build_picks(proj: pd.DataFrame, lines: pd.DataFrame, market_weight: float = 0.35, only_book: str = None,
                dists: dict = None, best_only: bool = True, book_weights: dict = None, fitted: dict = None,
                max_ev: float = None) -> pd.DataFrame:
    """All priced Over/Under candidates. flagged (hidden by default) = model and market disagree by >40-67%, usually a
    model blind spot; `flag` names the reason ("large gap"), or "long shot" (LONG_SHOT) or "edge too big" (EV over
    max_ev, when given). teammate_out is context only: a same-position rotation
    receiver in his first game out (redistribute.teammates_out). It isn't a flag: those receivers average a few yards
    over projection, but from a few big games; they beat it no more often than anyone (42.6% vs 42.7%), so their
    Unders hit as often as any.
    only_book keeps just that sportsbook's quotes in the output (or several, comma-separated: the books legal in the
    bettor's state); the market consensus still uses every book.
    dists: per-kind error tables from the backtest (model.fit_dist); without one, the older gamma curve is used., best book per player/market/side, sorted by EV (no EV filter).
    best_only=False keeps every book's quote (still sorted by EV); best_picks() then narrows it to the best one, so one
    pricing can serve every book filter.
    The market's chance is the power-devigged consensus of every book at its median line, each book weighted by
    book_weights (market.fit_book_weights; market.BOOK_PRIOR without) and moved to that line along the model's slope.
    market_weight=None uses the fitted weight for each market and for how long before kickoff the price was fetched
    (`fitted`, market.fit_market_weights); the market floors (MIN_MARKET_WEIGHT) apply either way. A market with a
    calibration curve in `fitted` (market.fit_calibration) has the model's chance put through it before the blend, with
    either weight; p_raw keeps the model's chance before it (what the pick logs store, so the curve refits on raw)."""
    if proj.empty or lines.empty:
        return pd.DataFrame(columns=PICK_COLUMNS)
    lines = lines.copy()
    lines["key"] = lines.player.map(odds.norm)
    if "thin" in proj:
        # thin-history projections are display only: the backtest's error tables don't cover them (model.THIN_GAMES)
        proj = proj[~proj.thin.fillna(False).astype(bool)]
    proj = proj.copy()
    proj["key"] = proj["name"].map(odds.norm)
    if "game_id" in lines:
        # a line belongs to one game: last week's quote for a player is never priced against this week's projection
        lines = lines[lines.game_id == lines.key.map(dict(zip(proj.key, proj.game_id)))]
        if lines.empty:
            return pd.DataFrame(columns=PICK_COLUMNS)
    pm = {(r.key, r.kind): r for r in proj.itertuples()}

    lines = lines.assign(hours=hours_before(lines, proj).to_numpy())
    keep = book_set(only_book)
    out = []
    chances = {}   # (player, market, line) -> (p_over, p_under): most books post the same lines

    def chance(p, market, line):
        k = (p.key, market, line)
        if k not in chances:
            chances[k] = (prob_over_under_dist(p.mu, line, dists[market]) if dists and market in dists
                          else prob_over_under(p.mu, line, p.sd ** 2))
        return chances[k]

    medians = {}

    def median(p, market):
        k = (p.key, market)
        if k not in medians:
            medians[k] = (median_dist(p.mu, dists[market]) if dists and market in dists
                          else median_gamma(p.mu, p.sd ** 2) if p.mu > 0 and p.sd > 0 else p.mu)
        return medians[k]

    def p_over(key, market, line):
        p = pm.get((key, market))
        return None if p is None else chance(p, market, line)[0]

    # market consensus per player/market: power devig, sharp books weighted up, every quote moved to the median line
    cons = mkt.consensus(lines, p_over, book_weights, HOLD, stale_gap)

    for r in lines.itertuples():
        p = pm.get((r.key, r.market))
        if p is None or pd.isna(r.line):
            continue
        if (r.key, r.market) not in cons.index:
            continue
        c = cons.loc[(r.key, r.market)]
        if abs(r.line - c.line) > stale_gap(r.market, c.line):
            continue  # alternate / stale line far from the market consensus
        if keep and r.book not in keep:
            continue
        wx = getattr(p, "wx", 1.0)
        wx = 1.0 if wx is None or pd.isna(wx) else float(wx)
        is_td = r.market in YES_NO
        if is_td:
            po, pu = p.mu, 1 - p.mu  # mu already is the chance (of any TD, or of the game's first)
        else:
            po, pu = chance(p, r.market, r.line)
        raw = {"Over": po, "Under": pu}
        po_cal, pu_cal = mkt.calibrate(po, pu, mkt.curve_for(fitted, r.market))
        mk_over = c.nv_o
        if not is_td and pd.notna(mk_over) and abs(c.line - r.line) > 1e-9:
            # shift the consensus probability to this book's line using the model's own slope
            po_c = chance(p, r.market, c.line)[0]
            mk_over = float(np.clip(c.nv_o + (po - po_c), 0.02, 0.98))
        # the consensus moved along the raw model's slope above: the curve flattens the model's view of this player,
        # not how a chance changes from one line to the next
        for side, odd, p_side, mk in (("Over", r.over_odds, po_cal, mk_over), ("Under", r.under_odds, pu_cal, 1 - mk_over)):
            if pd.isna(odd):
                continue
            mk = p_side if pd.isna(mk) else mk  # only one side posted: no market view to blend
            hours = None if pd.isna(r.hours) else float(r.hours)
            base = mkt.weight_for(fitted, r.market, hours) if market_weight is None else market_weight
            w = max(base, MIN_MARKET_WEIGHT.get(r.market, 0.0))
            prob = (1 - w) * p_side + w * mk
            dec = float(odds.american_to_dec(odd))
            ev = prob * (dec - 1) - (1 - prob)
            # yardage: projection minus line in yards; yes/no (TD, double-double): model minus market chance in percentage points
            edge = (p_side - mk) * 100 if is_td else (p.mu - r.line) * (1 if side == "Over" else -1)
            # passing TDs (and NBA threes, steals, blocks) sit at 0.5 / 1.5 / 2.5, where projection-over-line ratios swing wildly, so like anytime TD they
            # compare chances instead
            by_chance = is_td or r.market in ("ptd", "threes", "stl", "blk")
            gap = (not (0.6 <= p_side / max(mk, 1e-6) <= 1.67)) if by_chance else not (0.6 <= p.mu / max(c.line, 0.5) <= 1.67)
            out_ = getattr(p, "teammate_out", "") or ""
            out_ = "" if pd.isna(out_) else str(out_)
            long_shot = side == "Over" and odd > LONG_SHOT.get(r.market, float("inf"))
            flag = ("large gap" if gap else "long shot" if long_shot
                    else "edge too big" if max_ev is not None and ev > max_ev else "")
            out.append(dict(
                player_id=p.player_id, name=p.name, pos=p.pos, team=p.team, opp=p.opp, home=bool(p.home), kind=r.market,
                mu=p.mu, sd=p.sd, status=p.status, game_id=p.game_id, gameday=p.gameday, gametime=p.gametime,
                side=side, line=float(r.line), odds=int(odd), book=r.book, p_model=p_side, p_mkt=mk, prob=prob, ev=ev,
                kelly=max(0.0, ev / (dec - 1)) / 4, books=int(c.books),
                edge_yds=edge, flagged=bool(flag), wx=wx, flag=flag, teammate_out=out_, hours=hours, w=w,
                median=None if is_td else median(p, r.market), p_raw=raw[side], **_dvp(p)))
    df = pd.DataFrame(out, columns=PICK_COLUMNS)
    if df.empty:
        return df
    df = df.sort_values("ev", ascending=False, kind="stable")
    return best_picks(df) if best_only else df.reset_index(drop=True)


def best_picks(priced: pd.DataFrame, only_book: str = None) -> pd.DataFrame:
    """The best book per player/market/side from build_picks(..., best_only=False), within only_book's books."""
    keep = book_set(only_book)
    if keep:
        priced = priced[priced.book.isin(keep)]
    return priced.drop_duplicates(["name", "kind", "side"]).reset_index(drop=True)
