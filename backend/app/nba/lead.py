"""Lead chances: the chance an NBA team leads by `threshold` points or more at any point in a game.

FanDuel's early-win tokens pay a moneyline as a winner once the team goes up by a set number of points (10 in the NBA),
so the token's bet cashes if the team goes up by that much OR wins. This module prices both chances.

History: each game in the blowout history (blowout.HISTORY_CSV) gets both teams' largest lead from ESPN's core API (the
competitors list, then each competitor's statistics: three small requests a game). They are stored in the same CSV as
home_lead and away_lead, and later runs fetch them only for the games that don't have them yet.

Model: the home margin moves through the game like a Brownian motion, and a team "goes up N" when that path reaches
N - c (c makes up for the score moving in 2s and 3s: a jump overshoots the line a continuous path would just touch).
- Its spread over the game is one number, exp(a), times the blowout model's recent rescale. Tying it to the blowout
  scale (which grows with |spread|, since big spreads miss their final margin by more) made big underdogs go up 10 far
  more often than they do (31% against 23% for 10-point dogs).
- It drifts to the market's spread (plus the blowout fit's bias) times m + q |spread| / 10: lopsided games tilt harder
  than their spread says (about x1.1 at 10 points, x1.25 at 15), likely because garbage time shrinks final margins.
- Going up N and still losing (the games the token saves) gets its own settings: the same reflected-path formula with
  the spread and drift scaled and its own allowance, fitted on that outcome at the board's leads. Leads come back more
  often than a Brownian path lets them, so the lead path's own settings undercounted these by a few points.
a, m, q and c are fitted by maximum likelihood on both teams' largest leads, then the went-up-and-lost settings on top.
Walk-forward over 2021-22 to 2025-26 (main()): going up 10 lands within about 2 points in every spread band.
"""
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import numpy as np
import pandas as pd
import requests
from scipy.optimize import minimize
from scipy.stats import norm

from ..engine.odds import american_to_dec
from . import blowout, espn

THRESHOLD = 10            # FanDuel's NBA early win: up by 10
LEADS_FIRST_SEASON = 2019
MAX_LEAD = 60             # largest leads above this are lumped together in the fit
TOPUP_LIMIT = 1500        # games whose leads one run fetches at most (a missing CSV fills over a few runs)


# ---------- history ----------
def _leads(event_id: str) -> tuple[Optional[float], Optional[float]]:
    """(home team's largest lead, away team's) in one finished game, from ESPN's core API (None, None if missing)."""
    base = f"{blowout.CORE}/events/{event_id}/competitions/{event_id}/competitors"
    try:
        out = {}
        for c in blowout._get(base).get("items", []):
            js = blowout._get(f"{base}/{c['id']}/statistics")
            stats = {s["name"]: s.get("value") for cat in js.get("splits", {}).get("categories", []) for s in cat.get("stats", [])}
            out[c.get("homeAway")] = stats.get("largestLead")
        home, away = out.get("home"), out.get("away")
        return (float(home), float(away)) if home is not None and away is not None else (None, None)
    except (requests.RequestException, ValueError, KeyError):
        return None, None


def add_leads(df: pd.DataFrame, log=print, path: str = blowout.HISTORY_CSV, limit: int = TOPUP_LIMIT,
              workers: int = 12) -> pd.DataFrame:
    """Fill home_lead/away_lead for the games that lack them (newest first, at most `limit`), and save to path."""
    df = df.copy()
    for col in ("home_lead", "away_lead"):
        if col not in df:
            df[col] = np.nan
    todo = df[df.home_lead.isna() & (df.season >= LEADS_FIRST_SEASON)].sort_values("date", ascending=False).head(limit)
    if todo.empty:
        return df
    log(f"  largest leads for {len(todo)} games...")
    with ThreadPoolExecutor(workers) as ex:
        leads = list(ex.map(_leads, todo.event_id))
    df.loc[todo.index, "home_lead"] = [h for h, _ in leads]
    df.loc[todo.index, "away_lead"] = [a for _, a in leads]
    df.to_csv(path, index=False)
    log(f"  {int(df.home_lead.notna().sum())} games with largest leads")
    return df


# ---------- model ----------
HIT_LOSE_LEADS = (5, 8, 10, 12, 15)   # leads the went-up-and-lost settings are fitted on (the board's choices)


def _log_reflect(a, mu, sd, shift):
    """log(exp(2 a mu / sd^2) * Phi((-a - mu + shift) / sd)): the reflected path's term, kept finite for big spreads."""
    return 2 * a * mu / sd ** 2 + norm.logcdf((-a - mu + shift) / sd)


def p_lead(threshold, mu, sd, c: float) -> np.ndarray:
    """P(the home margin reaches +threshold at some point): the first-passage chance of a Brownian path drifting to mu
    with spread sd over the game, at the line threshold - c (the overshoot allowance). Use -mu for the away team."""
    a = np.maximum(np.asarray(threshold, float) - c, 1e-6)
    mu, sd = np.asarray(mu, float), np.asarray(sd, float)
    return np.clip(norm.cdf((mu - a) / sd) + np.exp(_log_reflect(a, mu, sd, 0)), 0, 1)


def p_lead_then_lose(threshold, mu, sd, c: float) -> np.ndarray:
    """P(the home margin reaches +threshold and the home team still loses): the token's extra cash on top of a win."""
    a = np.maximum(np.asarray(threshold, float) - c, 1e-6)
    mu, sd = np.asarray(mu, float), np.asarray(sd, float)
    # reflection: paths that touch a and end below 0 mirror the ones that end below -2a
    return np.clip(np.exp(_log_reflect(a, mu, sd, -a)), 0, 1)


def path(fit_: dict, spread_line, bias: float, mult) -> tuple[np.ndarray, np.ndarray]:
    """Each game's drift (the home margin's) and path spread: (spread + bias) * (m + q |spread| / 10) and exp(a) * mult."""
    s = np.asarray(spread_line, float)
    return (s + bias) * (fit_["m"] + fit_["q"] * np.abs(s) / 10), np.exp(fit_["a"]) * np.asarray(mult, float) * np.ones_like(s)


def chances(fit_: dict, threshold, drift, sd) -> tuple[np.ndarray, np.ndarray]:
    """(P(up by threshold), P(up by threshold and still lose)) for a side whose margin drifts to `drift` (path())."""
    return (p_lead(threshold, drift, sd, fit_["c"]),
            p_lead_then_lose(threshold, np.asarray(drift) * fit_["hl_m"], np.asarray(sd) * np.exp(fit_["hl_k"]), fit_["hl_c"]))


def _lead_nll(th, s, bias, mult, home_lead, away_lead) -> float:
    drift, sd = path(dict(a=th[0], m=th[1], q=th[2]), s, bias, mult)
    total = 0.0
    for d, lead in ((drift, home_lead), (-drift, away_lead)):
        n = np.minimum(lead, MAX_LEAD)
        hi = np.where(n >= MAX_LEAD, 0.0, p_lead(n + 1, d, sd, th[3]))
        lo = np.where(n <= 0, 1.0, p_lead(n, d, sd, th[3]))
        total -= np.log(np.clip(lo - hi, 1e-9, 1)).sum()
    return total / len(s)


def _hit_lose_nll(th, drift, sd, home_lead, away_lead, home_won) -> float:
    total = 0.0
    for d, lead, lost in ((drift, home_lead, ~home_won), (-drift, away_lead, home_won)):
        for t in HIT_LOSE_LEADS:
            q = np.clip(p_lead_then_lose(t, d * th[1], sd * np.exp(th[0]), th[2]), 1e-6, 1 - 1e-6)
            y = (lead >= t) & lost
            total -= (y * np.log(q) + (1 - y) * np.log(1 - q)).sum()
    return total / len(drift)


def fit(df: pd.DataFrame, params: dict, mult) -> dict:
    """The lead path (a, m, q, c) by maximum likelihood on both teams' largest leads, then the went-up-and-lost settings
    (hl_k, hl_m, hl_c) on that outcome. df: games with a spread and leads; params: the blowout fit; mult: each game's
    recent rescale."""
    s, bias = df.spread_line.to_numpy(float), params["bias"]
    home_lead, away_lead = df.home_lead.to_numpy(float), df.away_lead.to_numpy(float)
    th = minimize(_lead_nll, np.array([np.log(14.0), 1.0, 0.0, 1.0]), args=(s, bias, mult, home_lead, away_lead),
                  method="L-BFGS-B", bounds=[(np.log(5), np.log(30)), (0.5, 2), (-1, 1), (-3, 5)]).x
    f = dict(a=float(th[0]), m=float(th[1]), q=float(th[2]), c=float(th[3]))
    drift, sd = path(f, s, bias, mult)
    h = minimize(_hit_lose_nll, np.array([0.0, 1.0, 0.0]), args=(drift, sd, home_lead, away_lead, df.margin.to_numpy() > 0),
                 method="L-BFGS-B", bounds=[(-1, 1), (0.3, 3), (-5, 5)]).x
    return dict(f, hl_k=float(h[0]), hl_m=float(h[1]), hl_c=float(h[2]), n=int(len(df)))


def _usable(df: pd.DataFrame) -> pd.DataFrame:
    return df.dropna(subset=["spread_line"]).sort_values(["date", "event_id"]).reset_index(drop=True)


def walk_forward(df: pd.DataFrame, first_test: int, thresholds=(5, THRESHOLD, 15, 20)) -> pd.DataFrame:
    """Each season from first_test on, predicted from fits (blowout and lead) on the seasons before it. One row per
    team-game: season, spread (the team's), lead, won, and per threshold p_lead<t> and p_cash<t> (up by t or wins; the
    win chance here is the blowout model's, from the spread)."""
    df = _usable(df)
    out = []
    for season in sorted(s for s in df.season.unique() if s >= first_test):
        params = blowout.fit(df[df.season < season])
        mult = blowout.rolling_mult(params, df)
        tr = df[(df.season < season) & df.home_lead.notna()]
        lf = fit(tr, params, mult.loc[tr.index].to_numpy())
        te = df[(df.season == season) & df.home_lead.notna()]
        drift, sd = path(lf, te.spread_line, params["bias"], mult.loc[te.index])
        p_home_win = blowout.probs(params, te.spread_line, 0.5, mult.loc[te.index].to_numpy()).p_home.to_numpy()
        s = te.spread_line.to_numpy()
        for d, spread, lead, won, p_win in ((drift, s, te.home_lead, te.margin > 0, p_home_win),
                                            (-drift, -s, te.away_lead, te.margin < 0, 1 - p_home_win)):
            o = pd.DataFrame({"season": season, "spread": spread, "lead": lead.to_numpy(), "won": won.to_numpy()})
            for t in thresholds:
                up, lost = chances(lf, t, d, sd)
                o[f"p_lead{t}"] = up
                o[f"p_cash{t}"] = np.clip(p_win + lost, 0, 1)
            out.append(o)
    return pd.concat(out, ignore_index=True)


def summary(bt: pd.DataFrame, thresholds=(5, THRESHOLD, 15, 20)) -> dict:
    """Per threshold and outcome (lead: up by t; cash: up by t or won): team-games, predicted and actual rates, Brier
    score vs. the season-before base rate, and a five-bin calibration table."""
    out = {}
    for t in thresholds:
        for what in ("lead", "cash"):
            y = (bt.lead >= t).to_numpy(float)
            if what == "cash":
                y = np.maximum(y, bt.won.to_numpy(float))
            p = bt[f"p_{what}{t}"].to_numpy()
            rate = pd.Series(y).groupby(bt.season.to_numpy()).mean()
            base = bt.season.map(lambda s: rate.get(s - 1, y.mean())).to_numpy()
            brier, brier0 = float(np.mean((p - y) ** 2)), float(np.mean((base - y) ** 2))
            cal = pd.DataFrame({"p": p, "y": y, "bin": pd.qcut(p, 5, duplicates="drop")}).groupby("bin", observed=True) \
                .agg(p=("p", "mean"), y=("y", "mean"), n=("y", "size"))
            out[f"{what}{t}"] = dict(n=int(len(y)), pred=float(p.mean()), actual=float(y.mean()), brier=brier,
                                     brier_base=brier0, skill=float(1 - brier / brier0),
                                     calibration=[dict(p=float(r.p), y=float(r.y), n=int(r.n)) for r in cal.itertuples()])
    return out


# ---------- the slate ----------
FIT_KEYS = ("c", "hl_k", "hl_m", "hl_c")   # the fit's settings each game carries, to price any lead at request time


def project(df: pd.DataFrame, slate: list[dict], blow_fit: dict, season: int) -> tuple[dict, dict]:
    """Each slate game's path from a lead fit on all of history, plus both teams' largest leads this season. Returns
    (game_id -> dict(mu, sd, c, hl_k, hl_m, hl_c, games, season={team: [[lead, won], ...]}), the fit); mu is the home
    margin's drift."""
    df = _usable(df)
    tr = df[df.home_lead.notna()]
    lf = fit(tr, blow_fit, blowout.rolling_mult(blow_fit, df).loc[tr.index].to_numpy())
    now = df[(df.season == season) & df.home_lead.notna()]
    rec: dict = {}
    for r in now.itertuples():
        rec.setdefault(r.home, []).append([float(r.home_lead), bool(r.margin > 0)])
        rec.setdefault(r.away, []).append([float(r.away_lead), bool(r.margin < 0)])
    out = {}
    for g in slate:
        if g.get("spread_line") is None:
            continue
        drift, sd = path(lf, [float(g["spread_line"])], blow_fit["bias"], blow_fit.get("mult", 1.0))
        out[g["game_id"]] = dict(mu=float(drift[0]), sd=float(sd[0]), **{k: lf[k] for k in FIT_KEYS}, games=lf["n"],
                                 season={t: rec.get(t, []) for t in (g["home"], g["away"])})
    return out, lf


def token(priced: list[dict], teams: dict, threshold: float = THRESHOLD) -> list[dict]:
    """Add each side's early-win token to priced moneylines (engine/moneyline.price): the chance it goes up by threshold,
    the chance the token's bet cashes (its blended win chance plus going up and still losing), the EV at its best price,
    and this season's record (games, games it went up by threshold, of those how many it lost)."""
    for g in priced:
        game = (teams.get(g["game_id"]) or {}).get("lead")
        for s in g["sides"]:
            s["token"] = None
            if not game:
                continue
            up, lost = chances(game, threshold, game["mu"] if s["home"] else -game["mu"], game["sd"])
            cash = min(1.0, s["prob"] + float(lost))
            ev = None if s.get("odds") is None else cash * float(american_to_dec(s["odds"])) - 1
            games = game["season"].get(s["team"], [])
            s["token"] = dict(threshold=threshold, p_lead=float(up), p_cash=cash, ev=ev, games=len(games),
                              up=sum(lead >= threshold for lead, _ in games),
                              up_lost=sum(lead >= threshold and not won for lead, won in games))
    return priced


def describe(lf: dict) -> str:
    """The fit in a line, for the sync log."""
    return (f"path spread {np.exp(lf['a']):.1f} pts, drift x{lf['m'] + lf['q'] * 0.5:.2f} at a 5-point spread and"
            f" x{lf['m'] + lf['q']:.2f} at 10, overshoot {lf['c']:.2f} pts; over {lf['n']:,} games")


def main():
    """python -m app.nba.lead: refresh history and leads, print the walk-forward backtest and the next slate's chances."""
    df = add_leads(blowout.history(), limit=100_000)
    bt = walk_forward(df, espn.season_for(espn.today_et()) - 5)
    s = summary(bt)
    print("\nWalk-forward backtest, per team-game (each season fitted on the ones before it):")
    print(f"{'outcome':>8} {'n':>7} {'pred':>7} {'actual':>7} {'Brier':>7} {'base':>7} {'skill':>7}")
    for k, v in s.items():
        print(f"{k:>8} {v['n']:>7,} {v['pred']:>7.1%} {v['actual']:>7.1%} {v['brier']:>7.4f} {v['brier_base']:>7.4f} {v['skill']:>7.1%}")
    for k in (f"lead{THRESHOLD}", f"cash{THRESHOLD}"):
        print(f"{k} calibration: " + "  ".join(f"{c['p']:.0%}->{c['y']:.0%}" for c in s[k]["calibration"]))
    band = pd.cut(bt.spread, [-30, -14, -10, -6, -3, 0, 3, 6, 10, 14, 30])
    by = bt.assign(up=bt.lead >= THRESHOLD).groupby(band, observed=True)
    print(f"\nUp {THRESHOLD} by the team's spread:")
    for b, d in by:
        print(f"  {str(b):>11} {len(d):>6,}  pred {d[f'p_lead{THRESHOLD}'].mean():.1%}  actual {d.up.mean():.1%}")
    params = blowout.fit(df)
    params["mult"] = blowout.recent_scale(params, df)
    out, lf = project(df, espn.slate(), params, espn.season_for(espn.today_et()))
    print(f"\nFit: {describe(lf)}")
    for gid, p in out.items():
        h, a = gid.split("_")[-1], gid.split("_")[-2]
        up_h, _ = chances(p, THRESHOLD, p["mu"], p["sd"])
        up_a, _ = chances(p, THRESHOLD, -p["mu"], p["sd"])
        print(f"  {a + ' @ ' + h:<12} up {THRESHOLD}: {a} {float(up_a):.0%}  {h} {float(up_h):.0%}")


if __name__ == "__main__":
    main()
