"""Teammate volume redistribution when a regular player is out.

When a starter misses a game, his carries, targets or pass attempts go to his teammates, and the books
price that in. The yardage model projects each player from his own history, so without this it misses
the beneficiaries (and rates them as unders). For each team-game:

  * find absent regulars: players who were recently regular contributors (projected carries / targets /
    attempts above a floor) but have no role in this game,
  * take the volume they would have had, minus the part teammates' recent history already reflects
    (the longer someone has been out, the more of his absence is already in the teammates' recent games),
  * hand a fitted share rho of it to the active teammates in the SAME position group (running backs for
    carries, etc.), in proportion to their own projected volume.

Quarterbacks are different: the replacement does not get a share, he takes over. When a starting QB is out, the
QB who plays throws about as many passes as the starter was projected for (a median 29 for a true backup against a
median 32 for the QB who plays when a starter is out, and unbiased at the starter's projected attempts: a backup's
own history, a handful of snaps, misses by 130 yards). So the lead active QB's projected attempts are raised to the
missing starter's (REPLACE_ROLES), with no fitted share; QBs further down the depth chart are untouched.

rho per role (carries, targets, attempts) is fit in the walk-forward backtest. Only the roles where the
held-out test shows an improvement are switched on (ACTIVE_ROLES): rushing, where teammates of an absent
back averaged +8 yards over their projection and the correction removes that without hurting accuracy.
Receiving (+2 yards, overshoots) and passing (+12 yards, badly overshoots) made held-out error worse, so
they stay off.
"""
import numpy as np
import pandas as pd

ROLES = {"rush": "carries", "rec": "targets", "pass": "attempts"}   # kind -> actual volume column
MIN_REGULAR = {"rush": 8.0, "rec": 5.0, "pass": 15.0}                # projected volume that makes someone a regular
MAX_GAP = 8                    # team games; someone gone longer is fully reflected in teammates' history
HALFLIFE = 5.0                 # same decay as the yardage model
HIST_GAMES = 16
LIFT_CAP = {"rush": 2.5, "rec": 2.5, "pass": None}   # a teammate's volume can't more than 2.5x from this alone
DEFAULT_RHO = {"rush": 0.6, "rec": 0.0, "pass": 0.0}
ACTIVE_ROLES = ("rush",)       # roles redistribution is applied to (see module docstring)
REPLACE_ROLES = ("pass",)      # roles where the lead replacement takes over the missing starter's volume outright
# The share fitted on volume overshoots once it is turned into yards (extra carries don't all come with average
# efficiency, and projections regress toward the mean). Held-out weeks and a replay of past weeks both put the
# best share at 50-75% of the fit (bias ~0, error no worse), so the fit is shrunk by this factor.
SHRINK = 0.65


def reflected_fraction(missed_before: int) -> float:
    """Share of a missing player's absence that teammates' recency-weighted history already contains,
    given how many team games he missed before this one."""
    w = 0.5 ** (np.arange(HIST_GAMES) / HALFLIFE)
    return float(w[:min(missed_before, HIST_GAMES)].sum() / w.sum())


class Logs:
    """Fast lookups over the stats frame: each team's game list and each player's rows."""

    def __init__(self, df: pd.DataFrame):
        self.team_games = {t: np.sort(g.t.unique()) for t, g in df.groupby("team")}
        self.by_pid = {pid: g.sort_values("t").reset_index(drop=True) for pid, g in df.groupby("player_id")}
        self.pid_t = {pid: g.t.to_numpy() for pid, g in self.by_pid.items()}
        self.active_at = {t: set(g.player_id) for t, g in df.groupby("t")}


def find_absent(logs: Logs, team: str, t: int, active: set, exclude_roles=()) -> list:
    """Players whose last row before t is on this team, within MAX_GAP team games, with no role at t.

    Returns (pid, history rows before t, team games missed before this one)."""
    games = logs.team_games.get(team)
    if games is None:
        return []
    n = int(np.searchsorted(games, t, side="left"))  # team games already played before t
    out = []
    for pid, ts in logs.pid_t.items():
        if pid in active:
            continue
        j = int(np.searchsorted(ts, t, side="left")) - 1
        if j < 0:
            continue
        g = logs.by_pid[pid]
        if g.team.iat[j] != team:
            continue
        k = int(np.searchsorted(games, ts[j]))
        gap = n - k
        if 1 <= gap <= MAX_GAP:
            out.append((pid, g.iloc[: j + 1], gap - 1))
    return out


def group(kind: str, pos: str) -> str:
    """Who competes for the same volume: running backs for carries, receivers for targets, quarterbacks for attempts."""
    if kind == "rush":
        return "RB" if pos in ("RB", "FB") else ("QB" if pos == "QB" else "OTH")
    if kind == "rec":
        return "RB" if pos in ("RB", "FB") else pos
    return "QB"


def lost_volume(absent: list, comp_fn) -> dict:
    """{(role, position group): volume the absent regulars would have had, net of what teammates' history already
    shows}. Volume stays inside the absent player's own position group."""
    lost = {}
    for pid, hist, missed in absent:
        comp = comp_fn(hist)
        keep = 1.0 - reflected_fraction(missed)
        pos = hist.position.iat[-1]
        for kind in ROLES:
            c = comp.get(kind)
            if c and c[1] >= MIN_REGULAR[kind]:
                key = (kind, group(kind, pos))
                if kind in REPLACE_ROLES:   # one replacement plays: the biggest missing starter's volume, not a sum, and no discount
                    lost[key] = max(lost.get(key, 0.0), c[1])
                else:
                    lost[key] = lost.get(key, 0.0) + c[1] * keep
    return lost


def lift(kind: str, rho: float, lost: float, active_volume: float) -> float:
    """Multiplier on every active teammate's projected volume in this role and group."""
    if lost <= 0 or active_volume <= 0:
        return 1.0
    f = 1.0 + rho * lost / active_volume
    cap = LIFT_CAP[kind]
    return min(f, cap) if cap else f


def fit_rho(records: list) -> dict:
    """Slope of teammates' extra volume on the volume we'd expect to be handed to them, per role."""
    out = dict(DEFAULT_RHO)
    for kind in ROLES:
        r = [x for x in records if x[0] == kind]
        if len(r) >= 40:
            z, obs = np.array([x[1] for x in r]), np.array([x[2] for x in r])
            if z.std() > 1e-9:
                out[kind] = float(np.clip(np.polyfit(z, obs, 1)[0], 0.0, 1.2))
    return out


def applied(rho: dict) -> dict:
    """The shares actually used: the fit, shrunk, for the active roles; zero for the rest."""
    return {k: (v * SHRINK if k in ACTIVE_ROLES else 0.0) for k, v in rho.items()}



# "Teammate out": receiving targets aren't redistributed (it overshot), and a receiver's own games without the missing
# teammate are too few to project from (blending them in raised held-out error at every strength). What the backtest
# does show: in a rotation receiver's (4+ projected targets) FIRST game out, his same-position teammates beat their
# receiving projection by 3.2 yards against 0.9 otherwise (from the second game on, their recent games already show it),
# but from a few big games: they beat their projection no more often than anyone (42.6% vs 42.7%). So the plays are
# tagged as context for the reader, and not flagged or adjusted.
TAG_MIN_TARGETS = 4.0
TAG_KINDS = ("rec",)


def teammates_out(absent: list, comp_fn) -> dict:
    """{receiving position group: names of rotation receivers in their first game out}. absent: find_absent's output."""
    out = {}
    for pid, hist, missed in absent:
        if missed != 0:
            continue
        rec = comp_fn(hist).get("rec")
        if rec and rec[1] >= TAG_MIN_TARGETS:
            out.setdefault(group("rec", hist.position.iat[-1]), []).append(str(hist.player_display_name.iat[-1]))
    return {g: sorted(n) for g, n in out.items()}


STARTER_WINDOW = 16            # team games looked back over to decide who starts at quarterback
PASSER_MIN_GAMES = 3           # team starts needed before a passer ratio is measured


def passer_ratio(starters, starter, eff) -> float:
    """How the expected starter's passing compares with the QBs his receivers' recent games came from, capped at 1.

    starters: the team's starting QBs in its games before this one, oldest first. eff: pid -> that QB's projected yards
    per attempt now. The baseline is the recency-weighted (HALFLIFE, like the receivers' own projections) average of
    the starters' current efficiency over the last STARTER_WINDOW games, so a team whose starter played every one of
    those games gets exactly 1. Only a downgrade counts: in the backtest, receivers ran 10% under projection behind a
    starter below 0.95 (5% over with the QB unchanged), while a better QB taking over showed no lift."""
    s = list(starters)[-STARTER_WINDOW:]
    if starter is None or len(s) < PASSER_MIN_GAMES:
        return 1.0
    w = 0.5 ** (np.arange(len(s))[::-1] / HALFLIFE)
    base = float(sum(wi * eff(p) for wi, p in zip(w, s)) / w.sum())
    return min(float(eff(starter)) / base, 1.0) if base > 0 else 1.0


def pick_starter(starts: pd.DataFrame, team_games, cands, ruled_out=frozenset(), season=None, sat_out=frozenset()):
    """The quarterback expected to start: the candidate who made the team's most recent start, with two kinds of
    start looked past. A start made while another candidate was ruled out that week or the next (the game he got
    hurt in, then the games he missed) is a fill-in, so a starter back from injury gets his job back. And a new
    season looks past last season's week 18, often a rest game for a clinched starter. None when no candidate has
    a counted start.

    starts: the team's starts (player_id, t), one per game. team_games: the team's game times before this one,
    sorted. ruled_out: (t, player_id) pairs ruled Out or Doubtful. sat_out: (t, player_id) pairs listed Questionable
    who then didn't play, a fill-in for that game only. season: the season being projected."""
    team_games = list(team_games)
    games = set(team_games[-STARTER_WINDOW:])
    nxt = dict(zip(team_games, team_games[1:] + [None]))
    best = {}
    for pid, t in zip(starts.player_id, starts.t):
        if pid not in cands or t not in games:
            continue
        if season is not None and t // 100 < season and t % 100 >= 18:
            continue
        if any(o != pid and ((t, o) in ruled_out or (nxt[t], o) in ruled_out or (t, o) in sat_out) for o in cands):
            continue
        best[pid] = max(best.get(pid, 0), t)
    return max(best, key=best.get) if best else None


def arriving_starter(incumbent, season_starts: dict, arrivals) -> object:
    """pick_starter only sees starts made for the team, so a quarterback who joined mid-season after starting for his
    old team looks like he has none (Jacoby Brissett: ARI's starter weeks 1-8 of 2026, on BAL's week-10 roster, where
    Joe Fagnano's two fill-in starts were the latest). The arrival with the most starts this season takes over when he
    has more than the incumbent, or there is no incumbent. A starter who's held the job keeps it against a veteran
    signed as his backup.

    season_starts: pid -> starts this season, for any team. arrivals: the candidates with no game for this team yet."""
    arrivals = [a for a in arrivals if season_starts.get(a, 0) > 0]
    if not arrivals:
        return incumbent
    best = max(sorted(arrivals), key=lambda a: season_starts[a])
    return best if incumbent is None or season_starts[best] > season_starts.get(incumbent, 0) else incumbent
