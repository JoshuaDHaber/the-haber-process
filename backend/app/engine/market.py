"""Reading the market: the margin stripped with the power method, a consensus that leans on the books that lead the
close, the market's whole distribution read off an alternate-line ladder, and how much to trust the market against
the model, fitted on graded picks.

Power devig. Splitting the margin in proportion to each side's price (p / (p + q)) leaves the favorite-longshot bias
in: books shade longshots more than favorites, so the proportional split overrates the longshot. The power method
finds k with p**k + q**k = 1 and takes p**k: the same answer at even prices, a smaller longshot chance otherwise.

Book weights. Some books post their own numbers and others follow. A book whose early price already sits where the
market closes is one the others move toward, so each book is scored on how far its quotes 6+ hours before the close
were from the close of every other book at the same line (fit_book_weights), and the consensus averages books by that
score. Books owned by one company post the same lines, so they share one voice (TWINS).
"""
from datetime import datetime
from typing import Callable, Optional

import numpy as np
import pandas as pd
from scipy.optimize import brentq, minimize

from . import odds
from .model import prob_over_under, prob_over_under_dist
from .td import YES_NO

# before there's history to fit them: books that post their own numbers count for more, the slowest offshore ones less
BOOK_PRIOR = {"Pinnacle": 3.0, "Circa Sports": 2.0, "BetOnline.ag": 1.5, "LowVig.ag": 1.5, "Novig": 1.5, "ProphetX": 1.5,
              "Kalshi": 1.5, "Polymarket": 1.5, "FanDuel": 1.25, "DraftKings": 1.25, "Bovada": 0.75, "BetUS": 0.75,
              "MyBookie.ag": 0.75}
TWINS = {"LowVig.ag": "BetOnline.ag"}   # same owner, same lines: one voice between them
WEIGHT_PRIOR_N = 200     # quotes a book's fitted score needs before it outweighs the prior
WEIGHT_RANGE = (0.4, 3.0)
EARLY_HOURS = 6          # a quote this long before the close counts toward its book's score
ALT_HOLD = 0.05          # margin assumed on a lone alternate price when its book posts no two-sided line to measure


def power_devig(p_over, p_under, iters: int = 30):
    """The over side's fair chance from two implied probabilities (with margin): p_over**k where
    p_over**k + p_under**k = 1. Works elementwise; NaN where either side is missing."""
    p = np.asarray(p_over, float)
    q = np.asarray(p_under, float)
    ok = np.isfinite(p) & np.isfinite(q) & (p > 0) & (q > 0) & (p < 1) & (q < 1)
    pp, qq = np.where(ok, p, 0.5), np.where(ok, q, 0.5)
    lp, lq = np.log(pp), np.log(qq)
    k = np.ones_like(pp)
    for _ in range(iters):    # Newton on f(k) = p^k + q^k - 1; f is convex and decreasing, so it converges from k = 1
        a, b = pp ** k, qq ** k
        k = k - (a + b - 1) / (a * lp + b * lq)
    return np.where(ok, pp ** k, np.nan)


def power_k(p_over, p_under) -> np.ndarray:
    """The exponent the power method found for each two-sided quote (1 = no margin)."""
    fair = power_devig(p_over, p_under)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.log(fair) / np.log(np.asarray(p_over, float))


def fair_over(lines: pd.DataFrame, hold: dict = None) -> pd.Series:
    """Each quote's fair over (or Yes) chance: power devig where both sides are posted, the assumed margin stripped from
    a lone Yes price in the markets `hold` names (market -> margin), NaN otherwise."""
    io, iu = odds.implied(lines.over_odds), odds.implied(lines.under_odds)
    nv = pd.Series(power_devig(io, iu), index=lines.index)
    for kind, h in (hold or {}).items():
        yes_only = (lines.market == kind) & lines.under_odds.isna() & lines.over_odds.notna()
        nv[yes_only] = odds.implied(lines.loc[yes_only, "over_odds"]) / (1 + h)
    return nv


def book_weight(book: str, weights: Optional[dict] = None) -> float:
    w = (weights or {}).get(book)
    w = w["w"] if isinstance(w, dict) else w
    return float(w if w is not None else BOOK_PRIOR.get(book, 1.0))


def consensus(lines: pd.DataFrame, p_over: Optional[Callable] = None, weights: Optional[dict] = None,
              hold: dict = None, gap: Optional[Callable] = None) -> pd.DataFrame:
    """The market's view per (key, market): its median line, the weighted fair over chance AT that line, and how many
    books quote it. lines needs key, market, line, over_odds, under_odds, book.

    p_over(key, market, line) is the model's chance of going over, used only for its slope: a book at 48.5 when the
    consensus is 50.5 has its fair chance moved by the model's difference between the two lines before it's averaged.
    Without it (or where it returns None), each quote counts as if posted at the consensus line.
    gap(market, consensus line) is how far a quote may sit from the consensus line before it's dropped as an alternate
    or a stale line (picks.STALE_MIN_GAP); without it every quote counts."""
    cols = ["line", "nv_o", "books"]
    d = book_fairs(lines, p_over, weights, hold, gap)
    if d.empty:
        return pd.DataFrame(columns=cols, index=pd.MultiIndex.from_tuples([], names=["key", "market"]))
    d["wnv"] = (d.w * d.nv_c).fillna(0.0)
    g = d.groupby(["key", "market"])
    wsum = g.w.sum()
    # a player quoted only one-sided (no fair chance to read) keeps his line and book count, with no market chance
    return pd.DataFrame({"line": g.cline.first(), "nv_o": (g.wnv.sum() / wsum).where(wsum > 0), "books": g.book.nunique()})[cols]


def book_fairs(lines: pd.DataFrame, p_over: Optional[Callable] = None, weights: Optional[dict] = None,
               hold: dict = None, gap: Optional[Callable] = None) -> pd.DataFrame:
    """consensus() per quote: each book's fair over chance moved to its (key, market)'s median line (nv_c, NaN for a
    one-sided quote), that line (cline) and the quote's weight (w)."""
    if lines.empty:
        return lines.assign(nv=[], nv_c=[], cline=[], w=[])
    d = lines.assign(nv=fair_over(lines, hold).to_numpy())
    med = d.groupby(["key", "market"]).line.transform("median")
    if gap is not None:
        keep = np.array([abs(l - m) <= gap(mk, m) for l, m, mk in zip(d.line, med, d.market)], bool)
        d, med = d[keep], med[keep]
    shift = np.zeros(len(d))
    if p_over is not None:
        for i, (k, mk, l, m, nv) in enumerate(zip(d.key, d.market, d.line, med, d.nv)):
            if mk in YES_NO or abs(l - m) < 1e-9 or pd.isna(nv):
                continue
            a, b = p_over(k, mk, m), p_over(k, mk, l)
            if a is not None and b is not None:
                shift[i] = a - b
    d = d.assign(nv_c=np.clip(d.nv.to_numpy() + shift, 0.01, 0.99), cline=med.to_numpy())
    d["w"] = np.where(d.nv_c.notna(), quote_weights(d, weights), 0.0) if len(d) else []
    return d


STEAM_BOOKS = 3      # books that must move the same way between two fetches
STEAM_MOVE = 0.02    # how far one book's fair chance must move to count
STEAM_TOTAL = 0.03   # how far the consensus must move


def steam(prev: pd.DataFrame, cur: pd.DataFrame, p_over: Optional[Callable] = None) -> pd.Series:
    """Per (key, market): +1 when the market moved together toward the over between two fetches (STEAM_BOOKS or more
    books moved its fair chance up by STEAM_MOVE, none moved it down that far, and the consensus rose STEAM_TOTAL),
    -1 for the same toward the under, 0 otherwise. prev and cur are book_fairs() of the two fetches; each book's chance
    is compared at the current consensus line (the model's slope moves the earlier one there)."""
    if prev.empty or cur.empty:
        return pd.Series(dtype=int)
    a = prev.dropna(subset=["nv_c"]).groupby(["key", "market", "book"]).agg(p0=("nv_c", "mean"), l0=("cline", "first"))
    b = cur.dropna(subset=["nv_c"]).groupby(["key", "market", "book"]).agg(p1=("nv_c", "mean"), l1=("cline", "first"), w=("w", "first"))
    m = a.join(b, how="inner")
    if m.empty:
        return pd.Series(dtype=int)
    if p_over is not None:   # the earlier chance moved to the current line along the model's slope
        for i, (k, mk, _), l0, l1 in zip(range(len(m)), m.index, m.l0, m.l1):
            if mk not in YES_NO and abs(l1 - l0) > 1e-9:
                a, b = p_over(k, mk, l1), p_over(k, mk, l0)
                if a is not None and b is not None:
                    m.iloc[i, m.columns.get_loc("p0")] += a - b
    m["d"] = m.p1 - m.p0
    out = {}
    for k, g in m.groupby(level=["key", "market"]):
        up, down = int((g.d >= STEAM_MOVE).sum()), int((g.d <= -STEAM_MOVE).sum())
        total = float(np.average(g.d, weights=np.maximum(g.w, 1e-6)))
        out[k] = 1 if (up >= STEAM_BOOKS and down == 0 and total >= STEAM_TOTAL) else \
            -1 if (down >= STEAM_BOOKS and up == 0 and total <= -STEAM_TOTAL) else 0
    return pd.Series(out, dtype=int)


def quote_weights(d: pd.DataFrame, weights: Optional[dict] = None) -> np.ndarray:
    """Each quote's weight in its (key, market) consensus: the book's weight, with twin books (same owner, same lines)
    sharing one book's weight between them."""
    w = d.book.map(lambda b: book_weight(b, weights)).to_numpy(float)
    owner = d.book.map(lambda b: TWINS.get(b, b))
    keys = [d[c] for c in ("key", "market") if c in d]
    twins = d.groupby([*keys, owner]).book.transform("nunique").to_numpy(float)
    return w / np.maximum(twins, 1.0)


# ---------- fitting the book weights ----------
def fit_book_weights(hist: pd.DataFrame, prior: dict = None, n0: float = WEIGHT_PRIOR_N,
                     early_hours: float = EARLY_HOURS) -> dict:
    """book -> {w, n, mse}: how close each book's early quotes came to the close of every OTHER book at the same line.

    hist: main-line quotes from odds_history, already cut to those fetched before kickoff, with game_id, player,
    market, line, over_odds, under_odds, book, fetched_at. The close is each (game, market)'s last fetch. A book's score
    is the average squared miss of all quotes against its own; the weight is that ratio (bigger = closer to the close),
    pulled toward the prior by n0 quotes on the log scale and kept within WEIGHT_RANGE."""
    prior = BOOK_PRIOR if prior is None else prior
    if hist.empty:
        return {}
    d = hist[hist.over_odds.notna() & hist.under_odds.notna()].copy()
    if d.empty:
        return {}
    d["key"] = d.player.map(odds.norm)
    d["nv"] = power_devig(odds.implied(d.over_odds), odds.implied(d.under_odds))
    d["close_t"] = d.groupby(["game_id", "market"]).fetched_at.transform("max")
    close = d[d.fetched_at == d.close_t]
    early = d[d.fetched_at <= d.close_t - pd.Timedelta(hours=early_hours)]
    if early.empty or close.empty:
        return {}
    close = close.assign(owner=close.book.map(lambda b: TWINS.get(b, b)))
    # per (game, key, market, line): sum and count of closing fair chances by owner, so one owner can be left out
    agg = close.groupby(["game_id", "key", "market", "line", "owner"]).nv.agg(["sum", "count"]).reset_index()
    tot = agg.groupby(["game_id", "key", "market", "line"])[["sum", "count"]].sum().rename(columns={"sum": "S", "count": "N"})
    e = early.assign(owner=early.book.map(lambda b: TWINS.get(b, b)))
    e = e.merge(tot, on=["game_id", "key", "market", "line"], how="inner")
    e = e.merge(agg, on=["game_id", "key", "market", "line", "owner"], how="left").fillna({"sum": 0.0, "count": 0})
    e["others"] = e.N - e["count"]
    e = e[e.others > 0]
    if e.empty:
        return {}
    e["err2"] = (e.nv - (e.S - e["sum"]) / e.others) ** 2
    overall = float(e.err2.mean())
    out = {}
    for book, g in e.groupby("book"):
        n, mse = len(g), float(g.err2.mean())
        ratio = overall / max(mse, 1e-6)
        p = prior.get(book, 1.0)
        w = float(np.exp((n0 * np.log(p) + n * np.log(ratio)) / (n0 + n)))
        out[book] = dict(w=round(float(np.clip(w, *WEIGHT_RANGE)), 3), n=n, mse=round(mse, 5))
    return out


# ---------- the ladder's market curve ----------
def ladder_fair(quotes: pd.DataFrame) -> pd.DataFrame:
    """Every rung's fair over chance. Two-sided quotes: power devig. A lone over (or under) price: the power exponent
    of the same book's two-sided quotes in this ladder (its median), or of every book's when it has none, or failing
    that ALT_HOLD stripped proportionally. Returns line, book, nv (fair over chance), two_sided."""
    q = quotes.copy()
    io, iu = odds.implied(q.over_odds), odds.implied(q.under_odds)
    both = q.over_odds.notna() & q.under_odds.notna()
    q["nv"] = power_devig(io, iu)
    k = pd.Series(power_k(io, iu), index=q.index).where(both)
    by_book = k.groupby(q.book).median()
    k_all = float(k.median()) if k.notna().any() else np.nan
    for i in q.index[~both]:
        kk = by_book.get(q.at[i, "book"], np.nan)
        kk = k_all if pd.isna(kk) else kk
        if pd.notna(q.at[i, "over_odds"]):
            p = float(odds.implied(q.at[i, "over_odds"]))
            q.at[i, "nv"] = p ** kk if pd.notna(kk) else p / (1 + ALT_HOLD)
        elif pd.notna(q.at[i, "under_odds"]):
            p = float(odds.implied(q.at[i, "under_odds"]))
            q.at[i, "nv"] = 1 - (p ** kk if pd.notna(kk) else p / (1 + ALT_HOLD))
    q["two_sided"] = both
    return q[q.nv.notna()][["line", "book", "nv", "two_sided"]]


def _cond_over(cdf: Callable, line: float) -> float:
    """P(over | no push) at a line, from a CDF of the stat: books refund a push, so a fair two-way price prices this."""
    if float(line) == int(line):
        po, pu = 1 - cdf(line + 0.5), cdf(line - 0.5)
    else:
        po, pu = 1 - cdf(line), cdf(line)
    return po / max(po + pu, 1e-9)


def fit_market_curve(points: pd.DataFrame, mu: float, dist: dict, weights: Optional[dict] = None) -> Optional[dict]:
    """The market's distribution of the stat, read off its ladder: the model's own error shape (dist, model.fit_dist)
    moved to the market's center m and stretched by exp(c) (c within +-0.5), fitted to the rungs' fair chances on the
    log-odds scale, each rung weighted by its book. The stretch is fitted only when the ladder has three or more lines
    spanning at least one typical miss (closer rungs can't tell a wide curve from a narrow one); otherwise only the center.
    Returns {m, s, median, n_lines}: m is where the model's projection would sit, median the stat's market median.
    None for Poisson tables and ladders with no usable rung."""
    from .model import Z_PROBS, sd_at
    if dist is None or dist.get("poisson") or points.empty:
        return None
    pts = points[(points.nv > 0.005) & (points.nv < 0.995)]
    if pts.empty:
        return None
    L = pts.line.to_numpy(float)
    y = np.log(pts.nv / (1 - pts.nv)).to_numpy(float)
    w = pts.book.map(lambda b: book_weight(b, weights)).to_numpy(float) * np.where(pts.get("two_sided", True), 1.0, 0.6)
    sd0 = sd_at(mu, dist)
    z = np.asarray(dist["z"], float)
    n_lines = len(np.unique(L))

    def curve(m, s):
        return np.array([_cond_over(lambda v: float(np.interp((v - m) / s, z, Z_PROBS)), l) for l in L])

    def loss(x):
        m, c = x if len(x) == 2 else (x[0], 0.0)
        p = np.clip(curve(m, sd0 * np.exp(c)), 1e-4, 1 - 1e-4)
        return float(np.sum(w * (np.log(p / (1 - p)) - y) ** 2))

    stretch = n_lines >= 3 and float(L.max() - L.min()) >= sd0
    z_med = float(np.interp(0.5, Z_PROBS, z))
    x0 = [float(np.median(L)) - z_med * sd0] + ([0.0] if stretch else [])
    bounds = [(x0[0] - 4 * sd0, x0[0] + 4 * sd0)] + ([(-0.5, 0.5)] if stretch else [])
    res = minimize(loss, x0, bounds=bounds, method="L-BFGS-B")
    m, c = (res.x if stretch else (res.x[0], 0.0))
    s = float(sd0 * np.exp(c))
    return dict(m=float(m), s=s, median=float(m + z_med * s), n_lines=int(n_lines))


def curve_over(curve: dict, line: float, dist: dict) -> float:
    """The market curve's fair chance of going over a line (push excluded, as books settle it)."""
    from .model import Z_PROBS
    z = np.asarray(dist["z"], float)
    p = _cond_over(lambda v: float(np.interp((v - curve["m"]) / curve["s"], z, Z_PROBS)), line)
    return float(np.clip(p, 0.01, 0.99))


# ---------- how much to trust the market, fitted ----------
BUCKETS = ((6.0, "game day"), (48.0, "6–48 h"), (float("inf"), "48 h+"))  # hours from the quote's fetch to kickoff
FIT_PRIOR_N = 150        # graded picks a level needs before its own fit outweighs its parent's
DEFAULT_WEIGHT = 0.35
W_GRID = np.linspace(0, 1, 41)


def bucket(hours: Optional[float]) -> str:
    if hours is None or not np.isfinite(hours):
        return BUCKETS[-1][1]
    return next(label for top, label in BUCKETS if hours < top)


def _logloss(p: np.ndarray, y: np.ndarray) -> float:
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def _best_w(pm: np.ndarray, pk: np.ndarray, y: np.ndarray) -> float:
    return float(W_GRID[int(np.argmin([_logloss((1 - w) * pm + w * pk, y) for w in W_GRID]))])


def fit_market_weights(rows: pd.DataFrame, default: float = DEFAULT_WEIGHT, n0: float = FIT_PRIOR_N) -> dict:
    """How much to lean on the market, by market and by how long before kickoff the price was posted.

    rows: graded over (or Yes) sides with kind, hours (fetch to kickoff), p_model, p_mkt and y (1 = went over). The
    weight w in (1 - w) * model + w * market that scores best (log loss) is found for every level of
    all markets -> market -> market and time bucket, each pulled toward its parent by n0 picks (the top toward
    `default`). Log loss of the model, the market and the blend is kept for the Model check tab."""
    if rows.empty:
        return dict(fitted_at=datetime.utcnow().isoformat(), n=0, w=default, kinds={})
    pm, pk, y = (rows[c].to_numpy(float) for c in ("p_model", "p_mkt", "y"))

    def level(mask, parent):
        n = int(mask.sum())
        if n == 0:
            return dict(n=0, w=parent, w_fit=None)
        wf = _best_w(pm[mask], pk[mask], y[mask])
        w = (n * wf + n0 * parent) / (n + n0)
        return dict(n=n, w=round(w, 3), w_fit=wf,
                    ll_model=round(_logloss(pm[mask], y[mask]), 4), ll_market=round(_logloss(pk[mask], y[mask]), 4),
                    ll_blend=round(_logloss((1 - w) * pm[mask] + w * pk[mask], y[mask]), 4))

    top = level(np.ones(len(rows), bool), default)
    kinds = {}
    # a price with no known fetch time (picks logged before it was kept) counts toward its market, not a bucket
    buckets = np.array([bucket(h) if h is not None and np.isfinite(h) else "" for h in rows.hours.astype(float)])
    for kind in sorted(rows.kind.unique()):
        km = (rows.kind == kind).to_numpy()
        k = level(km, top["w"])
        k["buckets"] = {label: level(km & (buckets == label), k["w"]) for _, label in BUCKETS}
        kinds[kind] = k
    return dict(fitted_at=datetime.utcnow().isoformat(), **top, kinds=kinds)


def weight_for(table: Optional[dict], kind: str, hours: Optional[float], default: float = DEFAULT_WEIGHT) -> float:
    """The fitted market weight for a market and a price posted `hours` before kickoff (default without a fit)."""
    if not table:
        return default
    k = (table.get("kinds") or {}).get(kind)
    if not k:
        return float(table.get("w", default))
    b = (k.get("buckets") or {}).get(bucket(hours))
    return float(b["w"] if b else k["w"])


# ---------- recalibrating the model's own chance, fitted ----------
# The market weight shrinks the model toward the market by the same share at every chance, so it can't fix a model that
# is too sure of itself or that leans one way. In the first month of NBA grading the combo markets (PA, PRA, PR) had
# almost no signal past the line: whether the model said 26% or 60%, overs went 47-49%. Points were too sure too
# (21% said, 30% hit). A logistic curve on the logit of the model's chance, per market, fixes both before the blend:
# the curve goes through the consensus line and every other line is priced from the projection that puts it there
# (calibrated_mu). Fitted on graded picks before Nov 1, 4 and 8 and tested after each, the blend's log loss beat both
# today's and the curve put on each book's own line, and the main-prop board (EV 3-20%) went from -1.0%..+0.8% ROI to
# +1.2%..+2.9%, on a board about 40% smaller: a book's shifted line no longer gets the consensus line's chance. The
# market weight stays fitted on the raw chance (the weights refitted on the curve leaned on a market bias that didn't
# hold, and doubled the board).
# Double- and triple-doubles keep their own curve (model.fit_doubles): another one on top made them worse.
CAL_PRIOR_N = 150        # graded picks a market needs before its own curve outweighs no change (a = 0, b = 1)
CAL_MIN_N = 100          # fewer graded picks than this and the market isn't recalibrated at all
CAL_B_RANGE = (0.0, 1.5)  # the slope: 0 = the model knows nothing here, 1 = as sure as it says, above 1 = not sure enough


def _logit(p):
    p = np.clip(np.asarray(p, float), 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


def fit_calibration(rows: pd.DataFrame, n0: float = CAL_PRIOR_N, min_n: int = CAL_MIN_N) -> dict:
    """kind -> {a, b, n}: logit(p) = a + b * logit(model's chance), fitted (log loss) on graded over (or Yes) sides
    with kind, p_model and y, pulled toward no change (a = 0, b = 1) by n0 picks. Markets with fewer than min_n rows
    are left out, and so are the yes/no markets (YES_NO)."""
    out = {}
    if rows.empty:
        return out
    for kind, g in rows.groupby("kind"):
        n = len(g)
        if kind in YES_NO:
            continue
        y = g.y.to_numpy(float)
        if n < min_n or y.min() == y.max():
            continue
        x = _logit(g.p_model.to_numpy(float))
        nll = lambda ab: float(np.mean(np.logaddexp(0, ab[0] + ab[1] * x) - y * (ab[0] + ab[1] * x)))  # noqa: E731
        a, b = minimize(nll, [0.0, 1.0]).x
        s = n / (n + n0)
        out[str(kind)] = dict(a=round(float(s * a), 4), b=round(float(np.clip(s * b + (1 - s), *CAL_B_RANGE)), 4), n=int(n))
    return out


def calibrate(p_over: float, p_under: float, curve: Optional[dict]) -> tuple[float, float]:
    """The model's (over, under) chances through a market's curve (unchanged without one). The under goes through the
    curve as 1 - under, so a whole-number line keeps its push chance between the two."""
    if not curve:
        return p_over, p_under
    f = lambda p: float(1 / (1 + np.exp(-(curve["a"] + curve["b"] * _logit(p)))))  # noqa: E731
    return f(p_over), 1 - f(1 - p_under)


def curve_for(table: Optional[dict], kind: str) -> Optional[dict]:
    """A market's calibration curve from the fitted market weights (fit_market_weights' table), if it has one."""
    return ((table or {}).get("calibration") or {}).get(kind)


def calibrated_mu(mu: float, sd: float, line: Optional[float], kind: str, dist: Optional[dict], curve: Optional[dict]) -> float:
    """The projection every line of a market is priced from once it has a calibration curve (fit_calibration): the one
    whose raw chance of going over `line` (the consensus line) is the curve's chance at the real projection. The Best
    props board (picks.build_picks) and the ladder (ladder.build_ladder) both price from it.

    The curve corrects how sure the model is about this player, not how a chance changes from one line to the next, so
    it can't go on each line: with PA's near-flat curve every ladder rung would read about 50%, ten points below the
    projection too, and a book posting a line two points under the consensus would get the same chance as the
    consensus. Moving the projection keeps the consensus line on the curve and lets other lines fall off along the
    model's own error table. Unchanged without a curve or a line, and for yes/no."""
    if not curve or line is None or pd.isna(line) or kind in YES_NO:
        return mu
    chance = (lambda m: prob_over_under_dist(m, line, dist)[0]) if dist else (lambda m: prob_over_under(m, line, sd * sd)[0])
    target = calibrate(chance(mu), 1 - chance(mu), curve)[0]
    lo, hi = 1e-3, max(3.0 * mu, 3.0 * float(line) + 5.0)
    # the raw chances are held to 1-99%: a target outside what the range can reach stays at its nearest end
    if target <= chance(lo):
        return lo
    if target >= chance(hi):
        return hi
    return float(brentq(lambda m: chance(m) - target, lo, hi, xtol=1e-4))
