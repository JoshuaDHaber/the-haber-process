"""King of the Court: the chance each player has the most points + rebounds + assists (PRA) on a day's slate.

DraftKings' King of the Court (and promos like it) pays when the player you bet on leads every player that night in PRA,
ties included. Each player's PRA is drawn from his projection and the run's backtested PRA error table (the one the
props board prices overs and unders with: a spread that grows with the projection and the shape of past misses), and
the board counts how often each one comes out on top. Players are drawn independently: teammates share possessions,
which would shave a little off two stars on one team, but the top of a slate is usually spread across games.

Sleepers: the (up to five) likeliest players outside the slate's top projections whose matchup grade is A or B (engine/dvp.py: the softest two
fifths of defenses against their position for PRA) and who still lead often enough to matter. The projection already
counts the defense, so a sleeper's chance is the model's own; the grade says where the upside comes from.
"""
from typing import Optional

import numpy as np
import pandas as pd
from scipy.stats import norm

from ..engine.model import Z_PROBS

SIMS = 20_000
TOP_PROJECTED = 5          # the slate's top projections aren't sleepers, however soft their matchup
SLEEPER_GRADE = 0.4        # dvp_rank within the softest 40% (grades A and B)
SLEEPER_MIN = 0.01         # and at least a 1% chance to lead
MAX_SLEEPERS = 5           # the likeliest of them
MIN_CHANCE = 0.001         # players below this chance to lead are left off the board
MAX_ROWS = 40


def draws(mu, sd, dist: Optional[dict], sims: int, seed: int) -> np.ndarray:
    """(sims, players) PRA outcomes, whole numbers: mu + spread x a standardized miss from the error table (a normal
    curve with the projection's own sd when the run has no table)."""
    mu, sd = np.asarray(mu, float), np.asarray(sd, float)
    rng = np.random.default_rng(seed)
    g = norm.ppf(np.clip(rng.random((sims, len(mu))), 1e-9, 1 - 1e-9))   # normal scores
    if dist and "z" in dist:
        z = tail_z(g, dist["z"])
        sd = np.interp(mu, dist["mu"], dist["sd"])
    else:
        z = g
    return np.maximum(np.round(mu + sd * z), 0)


def tail_z(g: np.ndarray, ztab) -> np.ndarray:
    """The error table's standardized miss at each normal score g. The table's end points are the backtest's single
    worst misses (5.9 sd above at the top, against 2.1 at the 97.5th percentile), so read straight off it the top 2.5%
    of draws would spread evenly up to 5.9 and hand every bench player a monster night now and then. Instead the
    table is read between its 2.5th and 97.5th percentiles, piecewise linear in normal scores, and carried past them
    at the slope of its last segment (heavier than a normal tail where the table is, never a cliff)."""
    q, z = norm.ppf(Z_PROBS[1:-1]), np.asarray(ztab, float)[1:-1]
    lo, hi = (z[1] - z[0]) / (q[1] - q[0]), (z[-1] - z[-2]) / (q[-1] - q[-2])
    return np.where(g < q[0], z[0] + (g - q[0]) * lo, np.where(g > q[-1], z[-1] + (g - q[-1]) * hi, np.interp(g, q, z)))


def chances(mu, sd, dist: Optional[dict], sims: int = SIMS, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """(P(most PRA on the slate, ties included), P(top three)) per player."""
    x = draws(mu, sd, dist, sims, seed)
    top = x == x.max(axis=1, keepdims=True)
    third = -np.sort(-x, axis=1)[:, min(2, x.shape[1] - 1)][:, None]
    return top.mean(axis=0), (x >= third).mean(axis=0)


def board(proj: pd.DataFrame, dist: Optional[dict], seed: int = 0, sims: int = SIMS) -> list[dict]:
    """The slate's King of the Court board from its PRA projections (one row per player), best chance first."""
    if proj.empty:
        return []
    proj = proj.sort_values("mu", ascending=False).reset_index(drop=True)
    p_top, p_top3 = chances(proj.mu, proj.sd, dist, sims, seed)
    rows = []
    for i, r in proj.iterrows():
        soft = r.dvp_rank is not None and r.dvp_n and not pd.isna(r.dvp_rank) and r.dvp_rank <= r.dvp_n * SLEEPER_GRADE
        rows.append(dict(player_id=r.player_id, name=r["name"], pos=r.pos, team=r.team, opp=r.opp, home=bool(r.home),
                         game_id=r.game_id, gameday=r.gameday, gametime=r.gametime, status=r.status or "",
                         mu=float(r.mu), sd=float(r.sd), proj_rank=i + 1, p_top=float(p_top[i]), p_top3=float(p_top3[i]),
                         dvp_rank=None if pd.isna(r.dvp_rank) else int(r.dvp_rank), dvp_n=None if pd.isna(r.dvp_n) else int(r.dvp_n),
                         sleeper=bool(soft and i >= TOP_PROJECTED and p_top[i] >= SLEEPER_MIN)))
    rows = sorted((r for r in rows if r["p_top"] >= MIN_CHANCE), key=lambda r: -r["p_top"])[:MAX_ROWS]
    for r in [r for r in rows if r["sleeper"]][MAX_SLEEPERS:]:
        r["sleeper"] = False
    return rows
