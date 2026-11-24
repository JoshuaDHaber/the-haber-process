"""The game page's context: each team's implied points and what the spread does to the projections (the game script),
each player's share of his team's targets and carries over its last few games against the rest of the season, his
share of the team's touches inside the 20, and the chance he scores two or more touchdowns.

All of it is read from numbers the app already has: the schedule's spread and total, the weekly stats, play-by-play
inside the 20, and the anytime-TD projection. Context for reading a game, not new pricing.
"""
import math
from typing import Optional

import numpy as np
import pandas as pd

RECENT_GAMES = 5      # the team's latest games for the "last 5" shares (counting only the ones he played)
TREND_PTS = 0.03      # a share this far above or below the rest of the season gets a ▲ / ▼
SHARE_ROWS = 6        # players listed per team, biggest recent role first
HIGH_TOTAL, LOW_TOTAL = 48.0, 40.0


def implied(total: Optional[float], spread_home: Optional[float]) -> tuple:
    """(home, away) implied points from the total and the home team's spread (positive = home favored, as nflverse's
    spread_line), or (None, None) without both."""
    if total is None or spread_home is None or pd.isna(total) or pd.isna(spread_home):
        return None, None
    return (total + spread_home) / 2, (total - spread_home) / 2


def script_read(team: str, opp: str, spread: float, total: Optional[float], rush_per_pt: float, rec_per_pt: float) -> list:
    """Plain sentences on the game script for one team: who's favored and by how much, what the model's spread terms
    already do to its rushing and passing (rush_per_pt / rec_per_pt: the model's change per point, favorite positive),
    and whether the total points to a shootout or a grind. spread: this team's, positive = favored."""
    out = []
    if abs(spread) < 1:
        out.append(f"A pick'em: the spread leaves {team}'s run/pass mix alone.")
    else:
        fav, by = (team, spread) if spread > 0 else (opp, -spread)
        rush, rec = rush_per_pt * spread, rec_per_pt * spread
        lean = "leans on the run" if spread > 0 else "chases with the pass"
        out.append(f"{fav} favored by {by:g}: {team} usually {lean}, and the model has its rushing "
                   f"{'up' if rush >= 0 else 'down'} {abs(rush):.0%} and passing {'up' if rec >= 0 else 'down'} {abs(rec):.0%} for it.")
    if total is not None and not pd.isna(total):
        if total >= HIGH_TOTAL:
            out.append(f"A {total:g} total is one of the higher ones: more plays and more touchdowns to go around.")
        elif total <= LOW_TOTAL:
            out.append(f"A {total:g} total is low: fewer scoring drives, so yardage and touchdown overs need more of the pie.")
    return out


def shares(stats: pd.DataFrame, team: str, season: int, before_week: int, players: set) -> dict:
    """player_id -> dict(tgt_l5, tgt_prior, car_l5, car_prior, games, played): his share of the team's targets and
    carries in the games he played among the team's last RECENT_GAMES this season, and in the games he played before
    those (None with fewer than 2), so a starter back from injury isn't read as a backup. stats: weekly rows with
    player_id, team, season, week, targets, carries. Only `players` are returned (this week's projected ones)."""
    t = stats[(stats.team == team) & (stats.season == season) & (stats.week < before_week)]
    if t.empty:
        return {}
    t = t.assign(targets=t.targets.fillna(0), carries=t.carries.fillna(0))
    weeks = sorted(t.week.unique())
    recent = set(weeks[-RECENT_GAMES:])
    team_wk = t.groupby("week")[["targets", "carries"]].sum()

    def share(rows, col):
        tot = team_wk.loc[rows.week.unique(), col].sum()
        return float(rows[col].sum() / tot) if tot > 0 else 0.0
    out = {}
    for pid in players:
        mine = t[t.player_id == pid]
        r, p = mine[mine.week.isin(recent)], mine[~mine.week.isin(recent)]
        if r.empty:
            continue
        out[pid] = dict(tgt_l5=share(r, "targets"), car_l5=share(r, "carries"),
                        tgt_prior=share(p, "targets") if len(p) >= 2 else None,
                        car_prior=share(p, "carries") if len(p) >= 2 else None, games=len(recent), played=len(r))
    return out


def redzone(rz: pd.DataFrame, team: str, season: int, before_week: int) -> dict:
    """player_id -> (share of the team's targets + carries inside the 20 this season, his count, the team's count)."""
    t = rz[(rz.team == team) & (rz.season == season) & (rz.week < before_week)] if not rz.empty else rz
    if t is None or t.empty:
        return {}
    n = len(t)
    return {pid: (c / n, int(c), n) for pid, c in t.groupby("player_id").size().items()}


def two_plus(p_any: float) -> tuple:
    """(expected TDs, chance of 2+) from the anytime-TD chance, as a Poisson count whose chance of 1+ is p_any: the same
    count the TD model prices, kept consistent with the calibrated anytime number."""
    p = min(max(float(p_any), 0.0), 0.999)
    lam = -math.log(1 - p)
    return lam, 1 - math.exp(-lam) * (1 + lam)


def trend(now: float, before: Optional[float]) -> int:
    """+1 (▲) when the recent share is TREND_PTS above the rest of the season, -1 (▼) below, 0 otherwise or unknown."""
    if before is None:
        return 0
    return 1 if now - before >= TREND_PTS else -1 if before - now >= TREND_PTS else 0


def team_rows(proj: pd.DataFrame, stats: pd.DataFrame, rz: pd.DataFrame, team: str, season: int, week: int) -> list:
    """The share table for one team: this week's projected skill players, biggest recent role (targets + carries share)
    first, with their trends, red-zone share and TD chances."""
    p = proj[(proj.team == team) & proj.pos.isin(["RB", "WR", "TE", "FB", "QB"])]
    if p.empty:
        return []
    names = p.drop_duplicates("player_id").set_index("player_id")
    sh = shares(stats, team, season, week, set(names.index))
    red = redzone(rz, team, season, week)
    tds = {r.player_id: float(r.mu) for r in p[p.kind == "td"].itertuples()}
    rows = []
    for pid, who in names.iterrows():
        s = sh.get(pid)
        if s is None or (s["tgt_l5"] + s["car_l5"] == 0 and pid not in tds):
            continue
        if who.pos == "QB" and s["tgt_l5"] == 0 and s["car_l5"] < 0.1:   # a pocket passer's few scrambles: not a role here
            continue
        lam, p2 = two_plus(tds[pid]) if pid in tds else (None, None)
        r = red.get(pid)
        rows.append(dict(player_id=pid, name=str(who["name"]), pos=str(who.pos), team=team, games=s["games"], played=s["played"],
                         tgt_l5=s["tgt_l5"], tgt_prior=s["tgt_prior"], tgt_trend=trend(s["tgt_l5"], s["tgt_prior"]),
                         car_l5=s["car_l5"], car_prior=s["car_prior"], car_trend=trend(s["car_l5"], s["car_prior"]),
                         rz_share=None if r is None else r[0], rz=0 if r is None else r[1], rz_team=next(iter(red.values()))[2] if red else 0,
                         p_td=tds.get(pid), exp_td=lam, p_td2=p2))
    rows.sort(key=lambda x: -(x["tgt_l5"] + x["car_l5"]))
    return rows[:SHARE_ROWS]


def game_context(proj: pd.DataFrame, stats: pd.DataFrame, rz: pd.DataFrame, game: dict, season: int, week: int,
                 rush_per_pt: float, rec_per_pt: float) -> dict:
    """The game page's context for one game. game: home, away, total, spread (home's, positive = home favored)."""
    home, away, total, sp = game["home"], game["away"], game.get("total"), game.get("spread")
    ih, ia = implied(total, sp)
    teams = []
    for team, opp, pts, opp_pts, s in ((away, home, ia, ih, None if sp is None else -sp), (home, away, ih, ia, sp)):
        teams.append(dict(team=team, implied=pts, opp_implied=opp_pts, spread=s, total=total,
                          script=script_read(team, opp, s, total, rush_per_pt, rec_per_pt) if s is not None and not pd.isna(s) else [],
                          players=team_rows(proj, stats, rz, team, season, week)))
    return dict(game_id=game["game_id"], teams=teams)


NBA_HIGH_TOTAL, NBA_LOW_TOTAL = 236.0, 216.0
NBA_BIG_SPREAD = 9.5


def nba_script(team: str, opp: str, spread: float, total: Optional[float]) -> list:
    """The NBA's game-script sentences: the model has no spread term there, so it says what a big spread risks
    (starters sitting a decided fourth quarter) and what the total says about pace."""
    out = []
    if abs(spread) < 1:
        out.append("A pick'em: expect starters' full minutes if it stays close.")
    else:
        fav, by = (team, spread) if spread > 0 else (opp, -spread)
        out.append(f"{fav} favored by {by:g}." + (" A spread this size raises the chance of a blowout, when starters on both sides "
                                                   "can sit the fourth quarter (the minutes projections don't model it)." if by >= NBA_BIG_SPREAD else ""))
    if total is not None and not pd.isna(total):
        if total >= NBA_HIGH_TOTAL:
            out.append(f"A {total:g} total is a fast, high-scoring game by the market's read: more possessions for every counting stat.")
        elif total <= NBA_LOW_TOTAL:
            out.append(f"A {total:g} total is a slow one: fewer possessions to go around.")
    return out


def nba_context(game: dict) -> dict:
    """The NBA game page's context: implied points and the script read (no shares or touchdowns there)."""
    home, away, total, sp = game["home"], game["away"], game.get("total"), game.get("spread")
    ih, ia = implied(total, sp)
    return dict(game_id=game["game_id"], teams=[
        dict(team=t, implied=pts, opp_implied=o, spread=s, total=total, players=[],
             script=nba_script(t, opp, s, total) if s is not None and not pd.isna(s) else [])
        for t, opp, pts, o, s in ((away, home, ia, ih, None if sp is None else -sp), (home, away, ih, ia, sp))])
