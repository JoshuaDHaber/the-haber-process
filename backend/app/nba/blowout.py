"""Blowout probability: the chance an NBA game ends with a final margin of `threshold` points or more, either way.

History comes from ESPN: each date's scoreboard (finished games and scores) and each game's odds (the pregame spread
and total, the median across the books ESPN lists, in-game lines left out). The raw responses run to hundreds of MB, so
they aren't cached; the parsed games are kept in one small CSV (HISTORY_CSV) and later runs fetch only the dates after
its last game. A first run over eight seasons makes about 12,000 requests (two to three minutes).

Model (walk-forward over 2021-22 to 2025-26, each season fitted on the ones before it):
- The home margin is Student-t around the market's spread: the margin's tails are fatter than a normal curve's
  (2.5-sigma misses happen 1.8% of the time, not 1.2%), which matters most at 25+ and 30+.
- Its scale depends on |spread|, quadratically: pick'ems and big spreads miss by more than 3-6 point lines do.
  The total added nothing once the spread was in.
- The scale is then rescaled by how far the last RECENT_GAMES games missed their lines: blowouts have grown (games
  decided by 20+: 16-19% a season from 2019 to 2023, 22.8% in 2025-26), and the rescale follows that drift. Weighting
  recent seasons more did not.
P(|margin| >= t) is the sum of the two tails.
"""
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from typing import Optional

import numpy as np
import pandas as pd
import requests
from scipy.optimize import minimize
from scipy.stats import t as tdist

from .. import config
from . import espn

CORE = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba"
HISTORY_CSV = os.path.join(os.path.dirname(config.NBA_CACHE), "nba_blowout_history.csv")
FIRST_SEASON = 2019       # 2018-19: ESPN has closing lines from many books back to at least here
THRESHOLDS = (15, 20, 25, 30)
RECENT_GAMES = 600        # about half a season; 300 was noisier, 1,000-2,000 slower to follow the drift
SCALE_BOUNDS = (0.85, 1.15)


# ---------- history ----------
def _get(url: str, tries: int = 3) -> dict:
    for i in range(tries):
        try:
            return espn._download(url)
        except (requests.RequestException, ValueError):
            if i == tries - 1:
                raise
            time.sleep(1 + i)


def _season_days(season: int) -> list[date]:
    """Every date from October 1 to June 30 of a season (ESPN names a season by the year it ends)."""
    d, end = date(season - 1, 10, 1), date(season, 6, 30)
    return [d + timedelta(days=i) for i in range((end - d).days + 1)]


def _finished(js: dict) -> list[dict]:
    """Finished regular-season, play-in and playoff games from one scoreboard response."""
    out = []
    for e in js.get("events", []):
        if (e.get("season") or {}).get("type") not in espn.GAME_TYPES:
            continue
        c = e["competitions"][0]
        if c["status"]["type"].get("state") != "post":
            continue
        side = {t["homeAway"]: t for t in c["competitors"]}
        if "home" not in side or "away" not in side:
            continue
        home, away = side["home"]["team"]["abbreviation"], side["away"]["team"]["abbreviation"]
        if home not in espn.NBA_TEAMS or away not in espn.NBA_TEAMS:
            continue
        if not float(side["home"].get("score") or 0) or not float(side["away"].get("score") or 0):
            continue   # postponed games can show as final with no score
        out.append(dict(event_id=str(e["id"]), season=int(e["season"]["year"]), season_type=int(e["season"]["type"]),
                        date=espn._et(e["date"]).strftime("%Y-%m-%d"), home=home, away=away,
                        home_score=float(side["home"]["score"]), away_score=float(side["away"]["score"]),
                        neutral=bool(c.get("neutralSite"))))
    return out


def parse_odds(js: dict) -> tuple[Optional[float], Optional[float], int]:
    """(home spread line as a margin, total, books) from ESPN's odds for one game: medians across the pregame books."""
    spreads, totals = [], []
    for it in js.get("items", []):
        if "live" in ((it.get("provider") or {}).get("name") or "").lower():
            continue
        if it.get("spread") is not None:
            spreads.append(float(it["spread"]))
        if it.get("overUnder") is not None:
            totals.append(float(it["overUnder"]))
    # ESPN's spread is the home team's line (-4.5 = home favored by 4.5); spread_line is the expected home margin
    return (-float(np.median(spreads)) if spreads else None, float(np.median(totals)) if totals else None, len(spreads))


def _odds(event_id: str):
    try:
        return parse_odds(_get(f"{CORE}/events/{event_id}/competitions/{event_id}/odds"))
    except (requests.RequestException, ValueError):
        return None, None, 0


def fetch(days: list[date], log=print, workers: int = 8) -> pd.DataFrame:
    """Finished games on the given dates, with scores and the pregame spread and total."""
    log(f"  {len(days)} scoreboards...")
    with ThreadPoolExecutor(workers) as ex:
        games = [g for day in ex.map(lambda d: _finished(_get(f"{espn.SITE}/scoreboard?dates={d:%Y%m%d}")), days) for g in day]
    games = list({g["event_id"]: g for g in games}.values())
    log(f"  {len(games)} games; odds...")
    with ThreadPoolExecutor(workers) as ex:
        odds = list(ex.map(_odds, [g["event_id"] for g in games]))
    df = pd.DataFrame(games, columns=["event_id", "season", "season_type", "date", "home", "away", "home_score",
                                      "away_score", "neutral"])
    df["spread_line"] = [o[0] for o in odds]
    df["total"] = [o[1] for o in odds]
    df["books"] = [o[2] for o in odds]
    df["margin"] = df.home_score - df.away_score
    return df


def history(log=print, path: str = HISTORY_CSV, today: Optional[date] = None) -> pd.DataFrame:
    """Every finished game since FIRST_SEASON, read from path and topped up from ESPN (dates from two days before the
    last stored game through yesterday; the overlap catches late finals and lines). Saves the result back to path."""
    today = today or espn.today_et()
    old = pd.read_csv(path, dtype={"event_id": str}) if os.path.exists(path) else pd.DataFrame()
    start = date(FIRST_SEASON - 1, 10, 1) if old.empty else date.fromisoformat(old.date.max()) - timedelta(days=2)
    days = [d for s in range(FIRST_SEASON, espn.season_for(today) + 1) for d in _season_days(s) if start <= d < today]
    log(f"blowout history: {len(old)} stored games; fetching {days[0] if days else '-'} to {days[-1] if days else '-'}")
    new = fetch(days, log) if days else pd.DataFrame()
    if not new.empty and not old.empty:
        # columns other modules keep in this file (nba/lead.py's largest leads) carry over to the games fetched again
        extra = [c for c in old.columns if c not in new.columns]
        if extra:
            new = new.merge(old[["event_id", *extra]], on="event_id", how="left")
    df = pd.concat([old, new], ignore_index=True) if not new.empty else old
    df = df.drop_duplicates("event_id", keep="last").sort_values(["date", "event_id"]).reset_index(drop=True)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    df.to_csv(path, index=False)
    log(f"  {len(df)} games, {int(df.spread_line.notna().sum())} with a spread")
    return df


# ---------- model ----------
def _design(spread_line) -> np.ndarray:
    """Columns of the log-scale regression: intercept, |spread| and |spread|^2 (spread in tens of points)."""
    s = np.abs(np.asarray(spread_line, float)) / 10
    return np.column_stack([np.ones_like(s), s, s ** 2])


def fit(df: pd.DataFrame) -> dict:
    """Maximum-likelihood fit of margin = spread_line + bias + scale * t(nu), log scale quadratic in |spread|."""
    d = df.dropna(subset=["spread_line"])
    X, r = _design(d.spread_line), (d.margin - d.spread_line).to_numpy()

    def nll(p):
        return -tdist.logpdf(r, np.exp(p[4]) + 2, p[0], np.exp(X @ p[1:4])).mean()
    # bounded so the search can't overflow: scale 3-60 pts at a pick'em, nu 3-200 (200 is a normal curve in all but name)
    bounds = [(-10, 10), (np.log(3), np.log(60)), (-3, 3), (-3, 3), (0, np.log(198))]
    p = minimize(nll, np.array([0.0, np.log(12.0), 0.0, 0.0, np.log(8.0)]), method="L-BFGS-B", bounds=bounds).x
    return dict(bias=float(p[0]), coef=[float(c) for c in p[1:4]], nu=float(np.exp(p[4]) + 2), n=int(len(d)))


def scale(params: dict, spread_line) -> np.ndarray:
    return np.exp(_design(spread_line) @ np.asarray(params["coef"]))


def recent_scale(params: dict, recent: pd.DataFrame, n: int = RECENT_GAMES) -> float:
    """How far the last n games missed their lines relative to the model, as a multiplier on its scale (bounded)."""
    d = recent.dropna(subset=["spread_line"]).sort_values(["date", "event_id"]).tail(n)
    if len(d) < n:
        return 1.0
    z2 = ((d.margin - d.spread_line - params["bias"]) / scale(params, d.spread_line)) ** 2
    nu = params["nu"]
    return float(np.clip(np.sqrt(z2.mean() / (nu / (nu - 2))), *SCALE_BOUNDS))


def probs(params: dict, spread_line, threshold: float = 20, mult: float = 1.0) -> pd.DataFrame:
    """Per game: P(home wins by threshold+), P(away wins by threshold+), and their sum, P(blowout).

    Margins are whole numbers, so 'by 20 or more' is the mass above 19.5."""
    mu = np.asarray(spread_line, float) + params["bias"]
    sc = scale(params, spread_line) * mult
    cut = threshold - 0.5
    home = tdist.sf(cut, params["nu"], mu, sc)
    away = tdist.cdf(-cut, params["nu"], mu, sc)
    return pd.DataFrame({"p_home": home, "p_away": away, "p_blowout": home + away})


def rolling_mult(params: dict, df: pd.DataFrame) -> pd.Series:
    """recent_scale for every game in df (sorted by date, with a spread), from the RECENT_GAMES before its date only."""
    z2 = ((df.margin - df.spread_line - params["bias"]) / scale(params, df.spread_line)) ** 2
    # the mean over the n games before each date (same-day games can't see each other's results)
    roll = z2.rolling(RECENT_GAMES, min_periods=RECENT_GAMES).mean().shift(1)
    roll = roll.where(df.groupby("date").cumcount() == 0).ffill()
    nu = params["nu"]
    return np.sqrt(roll / (nu / (nu - 2))).clip(*SCALE_BOUNDS).fillna(1.0)


def walk_forward(df: pd.DataFrame, first_test: int, thresholds=THRESHOLDS) -> pd.DataFrame:
    """Each season from first_test on, predicted from a fit on the seasons before it, rescaled game by game by the
    RECENT_GAMES before that game's date. Columns: season, date, spread_line, margin, p<t> for each threshold."""
    df = df.dropna(subset=["spread_line"]).sort_values(["date", "event_id"]).reset_index(drop=True)
    out = []
    for season in sorted(s for s in df.season.unique() if s >= first_test):
        params = fit(df[df.season < season])
        mult = rolling_mult(params, df)
        te = df[df.season == season]
        o = te[["season", "date", "spread_line", "margin"]].copy()
        for t in thresholds:
            p = probs(params, te.spread_line, t, mult.loc[te.index].to_numpy())
            o[f"p{t}"] = p.p_blowout.to_numpy()
        out.append(o)
    return pd.concat(out, ignore_index=True)


def summary(bt: pd.DataFrame, thresholds=THRESHOLDS) -> dict:
    """Per threshold: games, predicted and actual blowout rates, log loss vs. the season-before base rate, and a
    five-bin calibration table."""
    out = {}
    for t in thresholds:
        y = (bt.margin.abs() >= t).to_numpy(float)
        p = np.clip(bt[f"p{t}"].to_numpy(), 1e-6, 1 - 1e-6)
        rate = pd.Series(y).groupby(bt.season.to_numpy()).mean()
        base = np.clip(bt.season.map(lambda s: rate.get(s - 1, y.mean())).to_numpy(), 1e-6, 1 - 1e-6)
        ll = -np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))
        ll0 = -np.mean(y * np.log(base) + (1 - y) * np.log(1 - base))
        cal = pd.DataFrame({"p": p, "y": y, "bin": pd.qcut(p, 5, duplicates="drop")}).groupby("bin", observed=True) \
            .agg(p=("p", "mean"), y=("y", "mean"), n=("y", "size"))
        out[t] = dict(n=int(len(y)), pred=float(p.mean()), actual=float(y.mean()), log_loss=float(ll),
                      log_loss_base=float(ll0), skill=float(1 - ll / ll0),
                      calibration=[dict(p=float(r.p), y=float(r.y), n=int(r.n)) for r in cal.itertuples()])
    return out


def project(df: pd.DataFrame, slate: list[dict], thresholds=THRESHOLDS) -> tuple[dict, dict]:
    """Blowout chances for slate games that have a spread, from a fit on all of history and the recent rescale.
    Returns (game_id -> dict(chances=[dict(margin, p, p_home, p_away)], games), the fit with its rescale)."""
    params = fit(df)
    params["mult"] = recent_scale(params, df)
    games = [g for g in slate if g.get("spread_line") is not None]
    if not games:
        return {}, params
    spread = [float(g["spread_line"]) for g in games]
    by_t = {t: probs(params, spread, t, params["mult"]) for t in thresholds}
    out = {g["game_id"]: dict(games=params["n"], chances=[
        dict(margin=t, p=float(by_t[t].p_blowout[i]), p_home=float(by_t[t].p_home[i]), p_away=float(by_t[t].p_away[i]))
        for t in thresholds]) for i, g in enumerate(games)}
    return out, params


def main():
    """python -m app.nba.blowout: refresh history, print the backtest, and the blowout chances for the next slate."""
    df = history()
    bt = walk_forward(df, espn.season_for(espn.today_et()) - 5)
    print("\nWalk-forward backtest (each season fitted on the ones before it):")
    print(f"{'margin':>8} {'games':>7} {'pred':>7} {'actual':>7} {'log loss':>9} {'base':>7} {'skill':>7}")
    for t, s in summary(bt).items():
        print(f"{str(t) + '+':>8} {s['n']:>7,} {s['pred']:>7.1%} {s['actual']:>7.1%} {s['log_loss']:>9.4f}"
              f" {s['log_loss_base']:>7.4f} {s['skill']:>7.1%}")
    slate = espn.slate()
    out, p = project(df, slate)
    print(f"\nFit on {p['n']:,} games: t with {p['nu']:.1f} df, scale {scale(p, [0])[0]:.1f} pts at a pick'em,"
          f" {scale(p, [10])[0]:.1f} at 10; recent rescale x{p['mult']:.3f}")
    if not out:
        print("No upcoming games with a spread.")
        return
    print(f"\n{'game':<12} {'date':<11} {'line':>9} {'15+':>6} {'20+':>6} {'25+':>6} {'30+':>6}   20+ by fav / dog")
    rows = sorted((g for g in slate if g["game_id"] in out), key=lambda g: -out[g["game_id"]]["chances"][1]["p"])
    for g in rows:
        c = {x["margin"]: x for x in out[g["game_id"]]["chances"]}
        s = g["spread_line"]
        fav, dog = (g["home"], g["away"]) if s >= 0 else (g["away"], g["home"])
        pf, pd_ = (c[20]["p_home"], c[20]["p_away"]) if s >= 0 else (c[20]["p_away"], c[20]["p_home"])
        line = f"{fav} -{abs(s):g}" if s else "PK"
        print(f"{g['away'] + ' @ ' + g['home']:<12} {g['gameday']:<11} {line:>9} {c[15]['p']:>6.1%} {c[20]['p']:>6.1%}"
              f" {c[25]['p']:>6.1%} {c[30]['p']:>6.1%}   {fav} {pf:.1%} / {dog} {pd_:.1%}")


if __name__ == "__main__":
    main()
