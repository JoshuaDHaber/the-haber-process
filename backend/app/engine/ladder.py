"""Price every quote for one player's market (main + alternate lines): the model's chance blended with the market's
distribution read off the ladder itself (market.fit_market_curve)."""
import pandas as pd

from . import market as mkt
from .market import calibrated_mu  # noqa: F401  (the ladder routes price from it)
from . import odds
from .model import prob_over_under, prob_over_under_dist
from .picks import HOLD
from .td import MIN_MARKET_WEIGHT, YES_NO


def price_quote(mu: float, sd: float, line: float, side: str, american: float, kind: str = "", dist: dict = None):
    """(model win probability, EV per $1 staked) for one side of one quote. No market blending: alt lines
    are usually one-sided so there is no no-vig price to blend with. For anytime and first TD, mu is already the chance."""
    if kind in YES_NO:
        po, pu = mu, 1 - mu
    elif dist:
        po, pu = prob_over_under_dist(mu, line, dist)
    else:
        po, pu = prob_over_under(mu, line, sd * sd)
    p = po if side == "over" else pu
    dec = float(odds.american_to_dec(american))
    return p, p * (dec - 1) - (1 - p)


def ladder_curve(quotes: pd.DataFrame, mu: float, kind: str, dist: dict = None, book_weights: dict = None):
    """The market's distribution for this player and market, fitted to every rung of every book (None for yes/no
    markets, Poisson tables, or a ladder with nothing two-sided or priced to read)."""
    if kind in YES_NO or not dist or quotes.empty:
        return None
    return mkt.fit_market_curve(mkt.ladder_fair(quotes), mu, dist, book_weights)


# Alternate overs priced longer than this are flagged "long shot" (the NBA ladder's long_shot). Backtested on every
# NBA ladder rung from Oct 26 to Nov 18, 2026 (107k lines, graded against the box score): the 4,790 alternate overs
# at +250 or longer with EV 3-20% hit 11.7% where the blend said 14.3%, -19% ROI at the best price, while shorter
# alternate overs were near break-even (-3%). Past two typical misses above the line the model's error table says
# 2.4% and players got there 1.4% of the time, and the books' own one-sided prices lean the same way.
ALT_OVER_LONG_SHOT = 250


def build_ladder(mu: float, sd: float, quotes: pd.DataFrame, max_abs_odds: float = 0, kind: str = "", dist: dict = None,
                 curve: dict = None, w: float = 0.0, long_shot: float = None) -> list[dict]:
    """quotes columns: line, book, alt, over_odds, under_odds, over_link, under_link, event_link.

    max_abs_odds > 0 hides prices outside -max..+max (American), e.g. 300 hides -400 locks and +900 longshots.
    It doesn't apply to anytime or first TD, where +400 to +2000 are ordinary prices.
    With a market curve (ladder_curve), each rung's chance is (1 - w) * model + w * curve, and off_curve is the rung's EV
    against the curve alone: a price better than the book's own ladder implies, whatever the model says.
    long_shot: an alternate rung's over priced longer than this gets flag "long shot" (ALT_OVER_LONG_SHOT; none without)."""
    out = []
    # anytime TD: lean on the market's chance (the lone Yes prices, margin stripped), as the Best props board does
    td_market, floor = None, MIN_MARKET_WEIGHT.get(kind, 0.0)
    if floor and quotes.over_odds.notna().any():
        td_market = float((odds.implied(quotes.over_odds.dropna()) / (1 + HOLD[kind])).mean())
    for q in quotes.itertuples():
        row = dict(line=float(q.line), book=q.book, alt=bool(q.alt), event_link=_none(q.event_link), over=None, under=None)
        for side in ("over", "under"):
            odd = getattr(q, f"{side}_odds")
            if pd.notna(odd) and (kind in YES_NO or not max_abs_odds or -max_abs_odds <= odd <= max_abs_odds):
                p, ev = price_quote(mu, sd, q.line, side, odd, kind, dist)
                dec = float(odds.american_to_dec(odd))
                p_model, p_curve, off = p, None, None
                if td_market is not None and side == "over":
                    p = (1 - floor) * mu + floor * td_market
                    ev = p * (dec - 1) - (1 - p)
                elif curve is not None:
                    po = mkt.curve_over(curve, float(q.line), dist)
                    p_curve = po if side == "over" else 1 - po
                    p = (1 - w) * p_model + w * p_curve
                    ev = p * (dec - 1) - (1 - p)
                    off = p_curve * dec - 1
                flag = "long shot" if long_shot is not None and side == "over" and bool(q.alt) and odd > long_shot else ""
                row[side] = dict(odds=int(odd), prob=p, ev=ev, link=_none(getattr(q, f"{side}_link")), p_model=p_model,
                                 p_curve=p_curve, off_curve=off, flag=flag)
        if row["over"] or row["under"]:
            out.append(row)
    return sorted(out, key=lambda r: (r["line"], r["book"]))


def _none(v):
    return None if v is None or (isinstance(v, float) and pd.isna(v)) or v == "" else v
