"""Early win: the chance an NFL team leads by `threshold` points or more at any point in a game.

FanDuel's NFL early-win promo pays a moneyline as a winner once the team goes up 17, so the bet cashes if the team goes
up 17 OR wins. The math is the NBA's (nba/lead.py: the home margin as a drifting Brownian path, an overshoot allowance
for scores that jump, and separate settings for going up and still losing), fitted here on NFL games.

History: each regular-season game's largest lead for both sides, from play-by-play (data.load_leads: the score after
every play) since LEADS_FIRST_SEASON, with the nflverse closing spread. Seasons older than the model's own three keep
only that small cut on disk.

Walk-forward over 2021 to 2026 (main()): up 17 is predicted 21.6% against 21.0% actual, within about 2 points in every
spread band, and up 17 and still lost 0.8% against 0.8%. Smaller leads run high (up 10: 48% against 43%): touchdowns
jump the score by 7, which one overshoot allowance can't fit at every lead, so the page offers 14 and up.
"""
import numpy as np
import pandas as pd

from ..nba import lead

THRESHOLD = 17            # FanDuel's NFL early win: up by 17
LEADS = (14, 17, 21)      # the leads the page offers (smaller ones run high, see above)
LEADS_FIRST_SEASON = 2018


def history(sched: pd.DataFrame, leads: pd.DataFrame) -> pd.DataFrame:
    """Played regular-season games with a closing spread and both sides' largest leads: date, game_id, season,
    spread_line (the home side's), margin, home_lead, away_lead, home, away."""
    g = sched[sched.home_score.notna() & sched.spread_line.notna()]
    df = g.merge(leads[["game_id", "home_lead", "away_lead"]], on="game_id")
    return pd.DataFrame({"date": df.gameday.astype(str), "game_id": df.game_id, "season": df.season, "spread_line": df.spread_line.astype(float),
                         "margin": (df.home_score - df.away_score).astype(float), "home_lead": df.home_lead.astype(float),
                         "away_lead": df.away_lead.astype(float), "home": df.home_team, "away": df.away_team}) \
        .sort_values(["date", "game_id"]).reset_index(drop=True)


def fit(df: pd.DataFrame) -> dict:
    """The lead path and went-up-and-lost settings (nba/lead.fit). NFL spreads are unbiased and the game-to-game spread
    doesn't drift the way NBA scoring does, so no bias and no recent rescale."""
    return lead.fit(df, {"bias": 0.0}, np.ones(len(df)))


def project(df: pd.DataFrame, teams: dict, season: int) -> tuple[dict, dict]:
    """Each upcoming game's path (teams: moneyline.project's games, with the nflverse spread), plus both teams' largest
    leads this season. Returns (game_id -> nba/lead's per-game dict, the fit): what lead.token prices a token from."""
    lf = fit(df)
    rec: dict = {}
    for r in df[df.season == season].itertuples():
        rec.setdefault(r.home, []).append([float(r.home_lead), bool(r.margin > 0)])
        rec.setdefault(r.away, []).append([float(r.away_lead), bool(r.margin < 0)])
    out = {}
    for gid, g in teams.items():
        if g.get("spread") is None:
            continue
        drift, sd = lead.path(lf, [float(g["spread"])], 0.0, 1.0)
        out[gid] = dict(mu=float(drift[0]), sd=float(sd[0]), **{k: lf[k] for k in lead.FIT_KEYS}, games=lf["n"],
                        season={t: rec.get(t, []) for t in (g["home"], g["away"])})
    return out, lf


def walk_forward(df: pd.DataFrame, first_test: int, thresholds=(10, 14, THRESHOLD, 21)) -> pd.DataFrame:
    """Each season from first_test on, predicted from a fit on the seasons before it. One row per team-game: season,
    spread (the team's), lead, won, and per threshold p_lead<t> and p_lost<t> (up by t and still lost)."""
    out = []
    for season in sorted(s for s in df.season.unique() if s >= first_test):
        lf = fit(df[df.season < season])
        te = df[df.season == season]
        drift, sd = lead.path(lf, te.spread_line, 0.0, np.ones(len(te)))
        s = te.spread_line.to_numpy()
        for d, spread, lead_, won in ((drift, s, te.home_lead, te.margin > 0), (-drift, -s, te.away_lead, te.margin < 0)):
            o = pd.DataFrame({"season": season, "spread": spread, "lead": lead_.to_numpy(), "won": won.to_numpy()})
            for t in thresholds:
                o[f"p_lead{t}"], o[f"p_lost{t}"] = lead.chances(lf, t, d, sd)
            out.append(o)
    return pd.concat(out, ignore_index=True)


def main():
    """python -m app.engine.early_win: the walk-forward backtest by lead and by spread band."""
    from . import data
    sched = data.load_schedule()
    season = int(sched[sched.home_score.isna()].season.min())
    df = history(sched, data.load_leads(range(LEADS_FIRST_SEASON, season + 1), keep_raw=range(season - 2, season + 1)))
    bt = walk_forward(df, LEADS_FIRST_SEASON + 3)
    print(f"Walk-forward over {len(bt):,} team-games (each season fitted on the ones before it):")
    for t in (10, 14, THRESHOLD, 21):
        up = bt.lead >= t
        print(f"  up {t:>2}: pred {bt[f'p_lead{t}'].mean():.1%} actual {up.mean():.1%} | up and lost: pred"
              f" {bt[f'p_lost{t}'].mean():.1%} actual {(up & ~bt.won).mean():.1%}")
    band = pd.cut(bt.spread, [-30, -10, -6.5, -3.5, -1, 1, 3.5, 6.5, 10, 30])
    print(f"Up {THRESHOLD} by the team's spread:")
    for b, d in bt.groupby(band, observed=True):
        print(f"  {str(b):>14} {len(d):>5,}  pred {d[f'p_lead{THRESHOLD}'].mean():.1%}  actual {(d.lead >= THRESHOLD).mean():.1%}")


if __name__ == "__main__":
    main()
