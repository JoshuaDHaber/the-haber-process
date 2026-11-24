"""Defense vs position: how much each defense gives up to each position group, for the matchup grades.

A raw "points allowed to guards" mostly measures which guards a defense happened to face, so each game is compared
with what those same players usually produce (the NBA: their per-minute rate x that game's minutes; the NFL: their
average per game). A defense's factor for a position and stat is (what they produced against it + K rows of par) /
(what they usually produce + K rows of par), so a handful of games can't make a defense look extreme. Rank 1 is the
softest matchup: the defense that lets that position beat its usual numbers by the most.

The grades are context. The models price one factor per defense and stat (no position split), and in the backtests the
position-level factor added little or nothing on top of it: NBA points, rebounds, assists, threes and PRA went over
their projections 43-47% of the time from the softest fifth of defenses to the toughest, with no MAE gain at any
strength; NFL rushing and receiving were noise, and passing yards the one hint (softest fifth 61% over, toughest 52%,
0.3% MAE at half strength) on 1,079 QB games, too few to build in.

NFL grades go one level finer than the position, to the depth slot: WR1/WR2/WR3, RB1/RB2 and TE1/TE2. A player's slot
in a game is his rank on his team's depth at that position by his role over his previous SLOT_GAMES games (targets for
receivers and tight ends, carries + targets for backs), so a defense that shuts down WR1s but leaks to slot receivers
reads as such. Slots past the depth tracked (a WR4) count only toward the position's grade, and a slot with too few
rows falls back to it. The slot grade is shrunk to par like the position's, with the same K rows.
"""
import numpy as np
import pandas as pd

GRADES = "ABCDF"   # fifths of the league, softest first
SLOT_DEPTH = {"WR": 3, "RB": 2, "TE": 2, "QB": 1}   # NFL depth slots graded on their own (WR1-3, RB1-2, TE1-2); the QB1 is "QB"
SLOT_GAMES = 4     # previous games whose average role ranks a player on his team's depth


def slot_label(grp: str, rank: int):
    """"WR2" for the second receiver; the group itself for the starting QB and where slots aren't tracked; None past
    the depth tracked (a WR4, a backup QB)."""
    depth = SLOT_DEPTH.get(grp)
    if depth is None:
        return grp
    if depth == 1:
        return grp if rank == 1 else None
    return f"{grp}{rank}" if rank <= depth else None


def rank_slots(role: dict) -> dict:
    """player -> slot for one team's players in one game. role: player -> (group, role volume); the bigger role ranks
    first (ties keep the order given)."""
    out, by = {}, {}
    for pid, (grp, vol) in role.items():
        by.setdefault(grp, []).append((pid, vol))
    for grp, members in by.items():
        for i, (pid, _) in enumerate(sorted(members, key=lambda m: -m[1]), 1):
            out[pid] = slot_label(grp, i)
    return out


def slots(frame: pd.DataFrame) -> pd.Series:
    """Each player-game's depth slot (slot_label), indexed like frame. frame: player_id, team, t, grp and vol (that
    game's role volume). A player is ranked by his average over his previous SLOT_GAMES games, so who led the team in
    targets that day (an outcome) doesn't decide it; a first game with no history falls back to that game's volume."""
    f = frame[["player_id", "team", "t", "grp", "vol"]].copy()
    f["vol"] = f.vol.fillna(0.0)
    f = f.sort_values(["player_id", "t"])
    prior = (f.groupby("player_id").vol.shift(1).groupby(f.player_id).rolling(SLOT_GAMES, min_periods=1).mean()
             .reset_index(level=0, drop=True))
    f["key"] = prior.fillna(f.vol)
    f = f.sort_values(["team", "t", "grp", "key", "vol"], ascending=[True, True, True, False, False])
    rank = f.groupby(["team", "t", "grp"]).cumcount() + 1
    lab = [slot_label(g, int(r)) for g, r in zip(f.grp, rank)]
    return pd.Series(lab, index=f.index, dtype=object).reindex(frame.index)


def factors(frame: pd.DataFrame, kinds, k_rows: float) -> dict:
    """kind -> {(defense, position group): factor}. frame: one row per player-game with columns opp, grp, w (recency
    weight) and act_<kind> / exp_<kind> (what he did, what he usually does)."""
    out = {}
    for k in kinds:
        f = frame.dropna(subset=[f"act_{k}", f"exp_{k}"])
        if f.empty:
            out[k] = {}
            continue
        par = float(np.average(f[f"exp_{k}"], weights=f.w)) * k_rows   # K rows of a typical player's production
        g = f.assign(a=f[f"act_{k}"] * f.w, e=f[f"exp_{k}"] * f.w).groupby(["opp", "grp"])[["a", "e"]].sum()
        out[k] = {key: float((r.a + par) / (r.e + par)) for key, r in g.iterrows()}
    return out


def ranks(fac: dict) -> dict:
    """kind -> {(defense, group): (factor, rank, teams ranked)}, rank 1 the softest within each position group."""
    out = {}
    for k, by in fac.items():
        out[k] = {}
        for grp in {g for _, g in by}:
            teams = sorted(((f, t) for (t, g), f in by.items() if g == grp), reverse=True)
            for i, (f, t) in enumerate(teams, 1):
                out[k][(t, grp)] = (f, i, len(teams))
    return out


def grade(rank: int, n: int) -> str:
    """A for the softest fifth of the league's defenses against this position, F for the toughest."""
    return GRADES[min(int((rank - 1) * len(GRADES) / max(n, 1)), len(GRADES) - 1)]


RECENT_GAMES = 6   # the defense's latest games shown in the drawer's "recent games vs this defense"
USUAL_GAMES = 8    # a player's previous games averaged for his usual number beside each result


def recent_vs(frame: pd.DataFrame, opp: str, key: str, before_t: int, n_games: int = RECENT_GAMES) -> pd.DataFrame:
    """The players in one depth slot (or position) who faced this defense in its last n_games before before_t, newest
    first, with what each did and his usual number going in. frame: one row per player-game with player_id, name, team,
    opp, t, season, week, slot, grp, val (the stat; NaN where he had no role in it). key: a slot ("WR2") or group ("WR")."""
    f = frame.sort_values("t")
    usual = (f.val.groupby(f.player_id).shift(1).groupby(f.player_id).rolling(USUAL_GAMES, min_periods=1).mean()
             .reset_index(level=0, drop=True))
    f = f.assign(usual=usual)
    col = "grp" if SLOT_DEPTH.get(key, 1) > 1 else "slot"   # a WR4 is compared with all receivers; WR2, RB1, QB1 with their slot
    mine = f[(f.opp == opp) & (f.t < before_t) & f.val.notna()]
    games = sorted(mine.t.unique())[-n_games:]
    out = mine[mine.t.isin(games) & (mine[col] == key)]
    return out.sort_values(["t", "val"], ascending=[False, False])


SPOT_PLAYERS = 4   # this slate's players listed under each soft spot, biggest projection first


def soft_spots(proj: pd.DataFrame, allowed: dict = None, league_avg: dict = None) -> list:
    """The soft-spots board: one row per (defense, depth slot or position, market) that this slate's players line up
    against, softest grade first. proj: a run's projections with opp, kind, pos, dvp, dvp_rank, dvp_n, player_id, name,
    team, mu, game_id and, for the NFL, dvp_slot. A WR4 (graded at his position) isn't a spot of its own. allowed /
    league_avg (NFL): kind -> {(defense, slot): (per game, games)} and kind -> {slot: league per game}, this season."""
    if proj.empty or "dvp_rank" not in proj:
        return []
    p = proj[proj.dvp_rank.notna() & proj.dvp.notna()].copy()
    if "thin" in p:
        p = p[~p.thin.fillna(False).astype(bool)]
    if p.empty:
        return []
    slot = p.dvp_slot if "dvp_slot" in p else pd.Series(None, index=p.index, dtype=object)
    p["key"] = slot.where(slot.notna(), p.pos)
    p = p[~p.key.isin([g for g, d in SLOT_DEPTH.items() if d > 1])]
    out = []
    for (opp, key, kind), g in p.groupby(["opp", "key", "kind"], sort=False):
        r = g.iloc[0]
        n = int(r.dvp_n)
        a = (allowed or {}).get(kind, {}).get((opp, key))
        lg = (league_avg or {}).get(kind, {}).get(key)
        top = g.sort_values("mu", ascending=False).head(SPOT_PLAYERS)
        out.append(dict(defense=opp, key=key, kind=kind, factor=float(r.dvp), rank=int(r.dvp_rank), n=n,
                        grade=grade(int(r.dvp_rank), n), allowed=None if a is None else float(a[0]),
                        games=None if a is None else int(a[1]), league=None if lg is None else float(lg),
                        players=[dict(player_id=t.player_id, name=t.name, team=t.team, pos=t.pos, mu=float(t.mu), game_id=t.game_id)
                                 for t in top.itertuples()]))
    return sorted(out, key=lambda s: (-s["factor"], s["defense"]))
