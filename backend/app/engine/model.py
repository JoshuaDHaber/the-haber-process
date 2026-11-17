"""Projection model for rushing, receiving and passing yards, receptions, passing TDs and longest reception and rush.

yards = volume (carries | targets) x efficiency (yds/carry | yds/target)
receptions = targets x catch rate (the same machinery, with receptions in place of yards)
passing TDs = attempts x TD rate (the same again), scaled by the points the lines expect the team to score
longest reception = the expected best of the projected catches, each drawn from the league's catch-yardage shape
  (play-by-play, by position) stretched to the player's yards per catch (see long_table); longest rush the same with
  carries and yards per carry
  * volume: exponentially recency-weighted average, shrunk toward a low prior
  * efficiency: recency-weighted, heavily shrunk toward the position mean
  * opponent: shrunk yards-allowed-per-game factor vs. league average
  * game script: team spread nudges rush volume up (favorites) / pass volume down
  * weather: at outdoor stadiums, wind and cold scale yardage by factors fitted on the backtest (engine/weather.py)
The spread and shape of outcomes come from the walk-forward backtest of this same model: the typical error
(a robust spread, binned by projection size) and the empirical distribution of standardized errors. So the
chance of beating a line matches how often actual yardage beat the projection, including skew and blow-up
games. (Older runs used an assumed gamma curve, kept as a fallback in prob_over_under.)
"""
import numpy as np
import pandas as pd
from scipy import stats as st

from . import td as tdm
from . import weather as wxm

HALFLIFE = 5.0          # games
# weight of a QB's non-start games (relief, or the start he left hurt) next to his starts. Fitted on the backtest's
# expected starters: 1 (all games alike) under-projected them by 6 yards, 0 (starts only) over-projected by 7 and
# dropped the real chance of an early exit; 0.35 had the lowest error (passing MAE 62.3 -> 61.9, bias +1.5).
NONSTART_W = 0.35
# receivers behind a downgrade at quarterback: receiving yards x (redistribute.passer_ratio) ** QB_REC_K. On 2021-2026
# (11,168 receiver-games), receivers behind a starter with a ratio under 0.95 ran 0.90x their projection against 1.05x
# with the QB unchanged; 1.5 brought them to 1.03x and cut receiving error in every two-season block (23.85 -> 23.78
# overall; 23.45 -> 22.52 on those games). Receptions showed no effect (every strength made them worse), so they're
# left alone.
QB_REC_K = 1.5
VOL_PRIOR_K = {"rush": 0.5, "rec": 0.5, "pass": 0.2, "recs": 0.5, "ptd": 0.2}   # pseudo-games of shrinkage on volume (QB volume is stable)
EFF_K = {"rush": 40.0, "rec": 30.0, "pass": 120.0, "recs": 30.0, "ptd": 400.0}   # pseudo-attempts of shrinkage on efficiency
OPP_SHRINK_GAMES = 8.0
OPP_STRENGTH = 0.5      # fraction of the (shrunk) opponent deviation applied
SCRIPT_RUSH = 0.012     # per spread point (favorite = positive)
SCRIPT_REC = -0.006
SCRIPT_PASS = -0.006

CFG = {
    "rush": dict(vol="carries", yds="rushing_yards", min_vol=4.0),
    "rec": dict(vol="targets", yds="receiving_yards", min_vol=3.0),
    "pass": dict(vol="attempts", yds="passing_yards", min_vol=15.0),
    "recs": dict(vol="targets", yds="receptions", min_vol=3.0),   # receptions: "yards" here is the catch count
    "ptd": dict(vol="attempts", yds="passing_tds", min_vol=15.0),  # passing TDs: "yards" is the TD count
}
POSITIONS = {"rush": ("QB", "RB", "WR", "TE", "FB"), "rec": ("RB", "WR", "TE", "FB"), "pass": ("QB",), "recs": ("RB", "WR", "TE", "FB"),
             "ptd": ("QB",)}
SCRIPT = {"rush": SCRIPT_RUSH, "rec": SCRIPT_REC, "pass": SCRIPT_PASS, "recs": SCRIPT_REC, "ptd": SCRIPT_PASS}
# "thin history": a non-QB with fewer than this many games in the loaded seasons (a rookie, or back from IR after a game or
# two). Markets the model would otherwise drop for him are still projected, flagged thin, for display only: the backtest's
# error tables were measured on players who clear the volume minimum, so thin rows are never priced (picks.build_picks).
# A starting QB under it is flagged too: taking over the missing starter's attempts gets his volume right (29.5
# projected, 29.1 thrown, 2021-2026), but his yards missed by 78 on average against 59 for established starters, wider
# than the passing error table assumes.
THIN_GAMES = 3
THIN_KINDS = {"RB": ("rush", "rec", "recs"), "FB": ("rush", "rec", "recs"), "WR": ("rec", "recs"), "TE": ("rec", "recs")}
LNG_LAMBDA = np.linspace(0.0, 16.0, 161)   # expected catches at which long_table tabulates the expected longest
RLNG_LAMBDA = np.linspace(0.0, 32.0, 161)  # expected carries, for the longest rush
RLNG_MIN_CARRIES = 6.0   # a longest-rush projection needs a real rushing role (books post it for lead backs and running QBs)


def prep(stats):
    df = stats.sort_values(["season", "week"]).copy()
    df["t"] = df.season * 100 + df.week
    if {"attempts", "position", "team"} <= set(df.columns):
        # the team's starting QB that game: its leading passer (a relief or injury-shortened outing is not a start)
        att = df.attempts.fillna(0)
        df["qb_start"] = (df.position == "QB") & (att > 0) & (att == att.groupby([df.team, df.t]).transform("max"))
    return df


def starts_only(hist, pos):
    """A QB's history weighted toward his starts, when he has any: his relief snaps and the game he got hurt in say
    little about how much he throws when he starts (Darnold's 2-attempt injury exit had him projected at 27
    attempts, then he threw 51). Non-start games keep NONSTART_W of their weight (a `_w` column)."""
    if pos != "QB" or "qb_start" not in hist or not hist.qb_start.any() or hist.qb_start.all():
        return hist
    if NONSTART_W == 0:
        out = hist[hist.qb_start.to_numpy()]
    else:
        out = hist.assign(_w=np.where(hist.qb_start.to_numpy(), 1.0, NONSTART_W))
    out.attrs = dict(hist.attrs)
    return out


def league_priors(df, catches=None, rushes=None):
    """Per (kind, position): league efficiency. With catches (data.load_catches), also ("lng", pos): long_table; with
    rushes (data.load_rushes), ("rlng", pos) the same for carries."""
    pri = {}
    for kind, c in CFG.items():
        if c["yds"] not in df:   # e.g. a stats load without passing TDs
            continue
        d = df[df[c["vol"]] > 0]
        for pos, g in d.groupby("position"):
            if pos not in POSITIONS[kind]:
                continue
            pri[(kind, pos)] = g[c["yds"]].sum() / g[c["vol"]].sum()
    if catches is not None and len(catches):
        pos_of = df.drop_duplicates("player_id", keep="last").set_index("player_id").position
        c = catches.assign(position=catches.player_id.map(pos_of))
        for pos, g in c.groupby("position"):
            if pos in POSITIONS["rec"] and len(g) >= 200:
                pri[("lng", pos)] = long_table(g.yards.to_numpy(float))
    if rushes is not None and len(rushes):
        pos_of = df.drop_duplicates("player_id", keep="last").set_index("player_id").position
        c = rushes.assign(position=rushes.player_id.map(pos_of))
        for pos, g in c.groupby("position"):
            if pos in POSITIONS["rush"] and len(g) >= 200:
                pri[("rlng", pos)] = long_table(g.yards.to_numpy(float), RLNG_LAMBDA)
    return pri


def long_table(yards, grid=LNG_LAMBDA):
    """Expected longest catch, in units of yards per catch, at each `grid` expected catches (or carries, for rushes).

    Catch yards are z x (yards per catch), z drawn from this position's league shape (mean 1). With a Poisson number of
    catches (mean lam) the longest L has P(L <= y) = exp(-lam * P(z > y / ypc)), and no catch counts as 0, so
    E[L] / ypc = integral over z >= 0 of 1 - exp(-lam * P(z' > z)). Lost yardage counts as 0 here: a longest rush
    below zero is rare enough not to matter."""
    y = np.clip(np.asarray(yards, float), 0, None)
    probs = np.linspace(0, 1, 401)
    z = np.quantile(y / y.mean(), probs)
    surv = np.concatenate([[1.0], 1 - (probs[:-1] + probs[1:]) / 2])   # P(z' > z) on [0, z0] and each quantile gap
    width = np.diff(np.concatenate([[0.0], z]))
    return [float(x) for x in ((1 - np.exp(-np.outer(grid, surv))) * width).sum(axis=1)]


def add_longest(df, catches, rushes=None):
    """Each player-game's longest catch as receiving_long (0 without a catch), and with rushes his longest carry as
    rushing_long (0 without one); for the backtest, grading and game logs."""
    out = df
    for col, plays in (("receiving_long", catches), ("rushing_long", rushes)):
        if plays is None:
            continue
        lng = plays.groupby(["player_id", "season", "week"]).yards.max().rename(col)
        out = out.drop(columns=col, errors="ignore").join(lng, on=["player_id", "season", "week"])
        out[col] = out[col].fillna(0.0)
    return out


def add_first_td(df, first_tds):
    """first_td: 1 when the player scored his game's first touchdown, else 0; for the backtest, grading and game logs."""
    hit = set(zip(first_tds.player_id, first_tds.season, first_tds.week))
    return df.assign(first_td=[float(k in hit) for k in zip(df.player_id, df.season, df.week)])


def long_reception(comp, table):
    """(expected longest catch, expected catches, yards per catch), or None without a real receiving role.

    Catches are the receptions projection, lifted by whatever redistribution added to targets; yards per catch come from
    the receiving-yards projection, so the opponent, game script and weather carry over."""
    r, c = comp.get("rec"), comp.get("recs")
    if table is None or not r or not c or r[1] < CFG["rec"]["min_vol"] or c[0] <= 0 or c[1] <= 0:
        return None
    lam = c[0] * r[1] / c[1]
    ypc = r[0] / lam
    return float(ypc * np.interp(lam, LNG_LAMBDA, table)), float(lam), float(ypc)


def long_rush(comp, table):
    """(expected longest carry, expected carries, yards per carry), or None without a real rushing role. Carries and
    yards per carry come from the rushing-yards projection, so redistribution, the opponent, script and weather carry over."""
    r = comp.get("rush")
    if table is None or not r or r[1] < RLNG_MIN_CARRIES or r[0] <= 0:
        return None
    lam, ypc = r[1], r[0] / r[1]
    return float(ypc * np.interp(lam, RLNG_LAMBDA, table)), float(lam), float(ypc)


def opp_table(df, upto_t):
    """Yards allowed per game by defense, split by stat kind, relative to league avg."""
    d = df[df.t < upto_t]
    out = {}
    for kind, c in CFG.items():
        if c["yds"] not in d:
            continue
        per_game = d.groupby(["opponent_team", "season", "week"])[c["yds"]].sum()
        per_def = per_game.groupby("opponent_team").agg(["mean", "count"])
        lg = per_game.mean()
        shrunk = (per_def["mean"] * per_def["count"] + lg * OPP_SHRINK_GAMES) / (per_def["count"] + OPP_SHRINK_GAMES)
        out[kind] = (shrunk / lg - 1.0).to_dict()
    return out


DVP_KINDS = ("rush", "rec", "recs", "pass")   # the matchup grades' markets (engine/dvp.py)
DVP_K_ROWS = 12.0      # player-games of par mixed into each defense's factor for a position


def dvp_group(pos: str) -> str:
    return "RB" if pos == "FB" else pos


def dvp_table(df, upto_t):
    """kind -> {(defense, position): (factor, rank, teams)} for the matchup grades, from the season before upto_t
    (earlier weeks at half weight): what each defense allowed each position against those players' usual game."""
    from . import dvp
    d = df[(df.t < upto_t) & (df.t >= upto_t - 100)]
    if d.empty:
        return {}
    frame = pd.DataFrame({"opp": d.opponent_team.to_numpy(), "grp": d.position.map(dvp_group).to_numpy(),
                          "w": np.where(d.season.to_numpy() < upto_t // 100, 0.5, 1.0)})
    for kind in DVP_KINDS:
        c = CFG[kind]
        if c["yds"] not in d or c["vol"] not in d:
            continue
        role = d[c["vol"]].fillna(0).to_numpy() > 0
        yds = d[c["yds"]].where(role)
        frame[f"act_{kind}"] = yds.to_numpy()
        frame[f"exp_{kind}"] = d.player_id.map(yds.groupby(d.player_id).mean()).where(role).to_numpy()
    return dvp.ranks(dvp.factors(frame, [k for k in DVP_KINDS if f"act_{k}" in frame], DVP_K_ROWS))


def dvp_fields(table, kind, opp, pos) -> dict:
    """The matchup-grade columns for one projection row (None where the market has no grade)."""
    f = table.get(kind, {}).get((opp, dvp_group(pos)))
    return dict(dvp=f[0], dvp_rank=f[1], dvp_n=f[2]) if f else dict(dvp=None, dvp_rank=None, dvp_n=None)


def project_player(hist, kind, prior_eff, opp_dev=0.0, spread=0.0):
    """hist: that player's prior games (sorted). Returns (mu, vol_hat, eff_hat) or None."""
    c = CFG[kind]
    h = hist[hist[c["vol"]] > 0]
    if h.empty:
        return None
    # age in the player's own games, measured over ALL his team-games in hist (including 0-usage)
    ages = np.arange(len(hist))[::-1]
    w_all = 0.5 ** (ages / HALFLIFE)
    if "_w" in hist:
        w_all = w_all * hist["_w"].to_numpy()
    vol_all = hist[c["vol"]].to_numpy(float)
    vol_hat = (w_all * vol_all).sum() / (w_all.sum() + VOL_PRIOR_K[kind])
    w = w_all[(hist[c["vol"]] > 0).to_numpy()]
    yds, vol = h[c["yds"]].to_numpy(float), h[c["vol"]].to_numpy(float)
    eff_hat = ((w * yds).sum() + EFF_K[kind] * prior_eff) / ((w * vol).sum() + EFF_K[kind])
    script = SCRIPT[kind]
    vol_adj = vol_hat * float(np.clip(1 + script * spread, 0.85, 1.15))
    mu = vol_adj * eff_hat * (1 + OPP_STRENGTH * opp_dev)
    return mu, vol_adj, eff_hat


def components(hist, pos, priors, opp, spread, wx=None):
    """Per-kind (mu, vol, eff) for one player, no volume floor applied. wx: per-kind weather multipliers on mu
    (volume is left alone, so touchdown and redistribution math don't change)."""
    out = {}
    hist = starts_only(hist, pos)
    for kind in CFG:
        pe = priors.get((kind, pos))
        if pe is None:
            continue
        res = project_player(hist, kind, pe, opp.get(kind, {}).get(hist.attrs["opp"], 0.0), spread)
        if res:
            f = (wx or {}).get(kind, 1.0)
            out[kind] = (res[0] * f, res[1], res[2]) if f != 1.0 else res
    return out


def passer_eff(hist, priors):
    """A QB's projected yards per attempt from his games in hist (weighted toward his starts); the league QB rate
    without any."""
    prior = priors.get(("pass", "QB"))
    if prior is None or hist.empty:
        return prior
    res = project_player(starts_only(hist, "QB"), "pass", prior)
    return res[2] if res else prior


def scale_receiving(members, f):
    """Receiving yards x f for every non-QB member (in place on each comp). The longest reception (yards per catch come
    from mu) and rush + rec follow; targets, receptions and the stored yards per target are left alone, so the Why
    panel shows the cut as its own step (qb_fields), like weather."""
    for c in members:
        if c["pos"] != "QB" and "rec" in c["comp"]:
            mu, vol, eff = c["comp"]["rec"]
            c["comp"]["rec"] = (mu * f, vol, eff)


def _passer_adjust(ctx, df, priors):
    """Backtest: scale receivers' yards in every team-game by the passer ratio of the QB who started it."""
    from . import redistribute as rd
    if "qb_start" not in df or QB_REC_K == 0:
        return
    st = df[df.qb_start].sort_values("t")
    by_team = {tm: (g.t.to_numpy(), g.player_id.to_numpy()) for tm, g in st.groupby("team")}
    hists = {pid: g.sort_values("t") for pid, g in df[df.position == "QB"].groupby("player_id")}
    cache = {}
    groups = {}
    for c in ctx:
        if c["pos"] != "QB":
            groups.setdefault((c["team"], c["t"]), []).append(c)
    for (team, t), members in groups.items():
        ts, ps = by_team.get(team, (np.array([]), np.array([])))
        i = int(np.searchsorted(ts, t))
        if i >= len(ts) or ts[i] != t:
            continue

        def eff(pid, _t=t):
            if (pid, _t) not in cache:
                h = hists.get(pid)
                cache[(pid, _t)] = passer_eff(h[h.t < _t] if h is not None else df.iloc[:0], priors)
            return cache[(pid, _t)]

        f = rd.passer_ratio(ps[:i], ps[i], eff) ** QB_REC_K
        if f < 1:
            scale_receiving(members, f)


QB_KINDS = ("rec", "rr", "lng")   # the markets a downgrade at quarterback scales (scale_receiving)


def qb_fields(kind, pos, comp, f, name) -> dict:
    """A projection row's qb_adj (the multiplier scale_receiving put in its mu; for rush + rec, the receiving part's share
    of it) and qb_name (the starting QB behind it); None and "" where it has none."""
    if f >= 1 or pos == "QB" or kind not in QB_KINDS or "rec" not in comp:
        return dict(qb_adj=None, qb_name="")
    if kind == "rr":
        rush, rec = comp["rush"][0], comp["rec"][0]
        f = (rush + rec) / (rush + rec / f) if rush + rec > 0 else 1.0
    return dict(qb_adj=float(f), qb_name=name)


def wx_ratio(kind, comp, wx):
    """The weather multiplier a projection carries; for rush + rec, its parts' factors weighted by their yards."""
    if not wx:
        return 1.0
    if kind != "rr":
        return wx.get(kind, 1.0)
    adj = comp["rush"][0] + comp["rec"][0]
    raw = comp["rush"][0] / wx.get("rush", 1.0) + comp["rec"][0] / wx.get("rec", 1.0)
    return adj / raw if raw > 0 else 1.0


def combined(comp):
    """Rush+rec projection from components; needs a real rush role. The receiving part counts at any volume: a lead
    back projected under the targets minimum (Henry, Cook) still has rush + rec lines posted, and a small receiving
    projection beats dropping the market."""
    r, c = comp.get("rush"), comp.get("rec")
    if not r or not c or r[1] < CFG["rush"]["min_vol"]:
        return None
    return (r[0] + c[0], r[1] + c[1], (r[0] + c[0]) / (r[1] + c[1]))


def all_kinds(hist, pos, priors, opp, spread, comp=None, implied=None):
    """Every market projected for one player. implied: the points the lines expect his team to score (passing TDs)."""
    comp = comp if comp is not None else components(hist, pos, priors, opp, spread)
    res = {k: v for k, v in comp.items() if v[1] >= CFG[k]["min_vol"]}
    if pos != "QB" and (rr := combined(comp)):
        res["rr"] = rr
    if "ptd" in res and "pass" in comp and res["ptd"][1] > 0:
        # follow any attempts redistribution gave the passing-yards projection, and how many points the team should score
        mu, vol, eff = res["ptd"]
        f = comp["pass"][1] / vol
        res["ptd"] = (mu * f * tdm.scoring_factor(implied), vol * f, eff)
    if pos != "QB" and (lng := long_reception(comp, priors.get(("lng", pos)))):
        res["lng"] = lng
    if rlng := long_rush(comp, priors.get(("rlng", pos))):
        res["rlng"] = rlng
    return res


LONG_COLS = {"lng": "receiving_long", "rlng": "rushing_long"}


def actual_hist(df, kind):
    if kind == "rr":
        return df["rushing_yards"].fillna(0) + df["receiving_yards"].fillna(0)
    if kind in LONG_COLS:
        return df[LONG_COLS[kind]]
    return df[CFG[kind]["yds"]]


def _lift_members(members, lost, rho, records=None, t=None, affected=None, only=None, lead_pid=None):
    """Hand lost volume to active teammates of the same position group (in place on each member's comp).

    members: dicts with pid, pos, comp (and row, for fitting). only: pids allowed to receive (default: all).
    With records, collect (role, expected extra volume, observed extra volume, t) to fit rho.
    Replacement roles (quarterback attempts) are not shared: the lead active QB takes over the missing starter's
    attempts whenever redistribution is on (rho given). lead_pid: the QB expected to start, when known (else the one
    projected for the most attempts)."""
    from . import redistribute as rd
    receivers = [c for c in members if only is None or c["pid"] in only]
    if rho is not None:
        for kind in rd.REPLACE_ROLES:
            groups = {}
            for c in receivers:
                key = (kind, rd.group(kind, c["pos"]))
                if kind in c["comp"] and lost.get(key, 0) > 0:
                    groups.setdefault(key, []).append(c)
            for key, cands in groups.items():
                lead = next((c for c in cands if c["pid"] == lead_pid), None) or max(cands, key=lambda c: c["comp"][kind][1])
                mu, vol, eff = lead["comp"][kind]
                if vol < lost[key] and vol > 0:
                    lead["comp"][kind] = (mu * lost[key] / vol, lost[key], eff)
                    if affected is not None:
                        affected.add((lead["pid"], t))
    active_vol = {}
    for c in receivers:
        for kind in rd.ROLES:
            if kind in c["comp"] and kind not in rd.REPLACE_ROLES:
                key = (kind, rd.group(kind, c["pos"]))
                active_vol[key] = active_vol.get(key, 0.0) + c["comp"][kind][1]
    for c in receivers:
        for kind, col in rd.ROLES.items():
            if kind in rd.REPLACE_ROLES:
                continue
            key = (kind, rd.group(kind, c["pos"]))
            if kind not in c["comp"] or lost.get(key, 0) <= 0 or active_vol.get(key, 0) <= 0:
                continue
            if affected is not None:
                affected.add((c["pid"], t))
            vol = c["comp"][kind][1]
            if records is not None:
                records.append((kind, lost[key] * vol / active_vol[key], float(c["row"][col]) - vol, t))
            if rho is not None and rho.get(kind, 0.0) > 0:
                f = rd.lift(kind, rho[kind], lost[key], active_vol[key])
                mu, v, eff = c["comp"][kind]
                c["comp"][kind] = (mu * f, v * f, eff)


def _apply_redistribution(ctx, logs, priors, opp_cache, spread_map, rho, records):
    """Backtest: move absent regulars' volume to their active teammates in every team-game."""
    from . import redistribute as rd
    groups = {}
    for c in ctx:
        groups.setdefault((c["team"], c["t"]), []).append(c)
    affected = set()
    for (team, t), members in groups.items():
        opp_team, sp = members[0]["opp"], members[0]["sp"]
        absent = rd.find_absent(logs, team, t, logs.active_at.get(t, set()))
        if not absent:
            continue

        def comp_fn(hist, _opp=opp_team, _sp=sp):  # only volume is read from these, so weather doesn't matter
            hist = hist.copy()
            hist.attrs["opp"] = _opp
            return components(hist, hist.position.iat[-1], priors, opp_cache[t], _sp)

        lost = rd.lost_volume(absent, comp_fn)
        if lost:
            _lift_members(members, lost, rho, records, t, affected)
    return affected


def walk_forward(df, priors, sched, start_t, td_priors=None, rho=None, records=None, wx_coef=None):
    """Project every historical player-game from t >= start_t using only prior data.

    With td_priors, anytime-TD rows are added too (kind "td": mu = unscaled P(>=1 TD), actual = 0/1), and with a
    first_td column (add_first_td) first-TD rows (kind "ftd": mu = unscaled P(first TD of the game)).
    With rho (per-role redistribution shares), absent regulars' volume is handed to their teammates.
    With records (a list), the data to fit rho is collected instead/as well. Rows carry an `affected` flag:
    True when a teammate was absent in that game (computed only when rho or records is given).
    Rows also carry the game's weather (wx_temp, wx_wind: NaN indoors) and wx, the multiplier applied with wx_coef.
    Like the live projections, only each team-game's expected starting QB is scored (backup_qbs), not a relief arm.
    """
    spread_map = _spread_lookup(sched)
    wx_hist = wxm.history(sched)
    implied_map = tdm.implied_points(sched)
    opp_cache = {t: opp_table(df, t) for t in sorted(df[df.t >= start_t].t.unique())}
    ctx = []
    for pid, g in df.groupby("player_id"):
        g = g.reset_index(drop=True)
        for i in range(3, len(g)):
            r = g.iloc[i]
            if r.t < start_t:
                continue
            hist = g.iloc[:i]  # a view: copying every player-game's history is what made full refreshes big
            hist.attrs["opp"] = r.opponent_team
            sp = spread_map.get((r.season, r.week, r.team), 0.0)
            temp, wind = wx_hist.get((r.season, r.week, r.team), (np.nan, np.nan))
            wx = wxm.factors(wx_coef, temp, wind) if wx_coef and not np.isnan(temp) else None
            ctx.append(dict(pid=pid, name=r.player_display_name, pos=r.position, team=r.team, opp=r.opponent_team, season=r.season,
                            week=r.week, t=r.t, hist=hist, sp=sp, row=r, one=g.iloc[[i]], temp=temp, wind=wind, wx=wx,
                            comp=components(hist, r.position, priors, opp_cache[r.t], sp, wx)))
    affected = set()
    if rho is not None or records is not None:
        from . import redistribute as rd
        affected = _apply_redistribution(ctx, rd.Logs(df), priors, opp_cache, spread_map, rho, records)
    _passer_adjust(ctx, df, priors)
    skip = backup_qbs(df, ctx)
    rows = []
    for c in ctx:
        pid, hist, one, r = c["pid"], c["hist"], c["one"], c["row"]
        if (pid, c["t"]) in skip:
            continue
        aff = (pid, c["t"]) in affected
        implied = implied_map.get((c["season"], c["week"], c["team"]))
        who = (c["pos"], c["team"], c["opp"])   # for the same-game correlations (engine/correlation.py)
        for kind, (mu, vol, eff) in all_kinds(hist, c["pos"], priors, opp_cache[c["t"]], c["sp"], c["comp"], implied).items():
            rows.append(who + (pid, c["name"], kind, c["t"], mu, float(actual_hist(one, kind).iloc[0]),
                         float(actual_hist(hist, kind).tail(5).mean()), aff, c["temp"], c["wind"], wx_ratio(kind, c["comp"], c["wx"])))
        if td_priors is not None:
            res = tdm.project_td(hist, c["comp"], c["pos"], td_priors, implied)
            if res:
                rows.append(who + (pid, c["name"], "td", c["t"], res[0], float(tdm.td_count(one).iloc[0] >= 1),
                             float((tdm.td_count(hist).tail(8) >= 1).mean()), aff, c["temp"], c["wind"], 1.0))
                if "first_td" in one:   # unscaled, like the anytime row: the pipeline fits both scales
                    game_lam = tdm.game_tds(implied, implied_map.get((c["season"], c["week"], c["opp"])))
                    rows.append(who + (pid, c["name"], "ftd", c["t"], tdm.first_td(res[1], game_lam), float(one.first_td.iloc[0]),
                                 float(hist.first_td.tail(8).mean()), aff, c["temp"], c["wind"], 1.0))
    return pd.DataFrame(rows, columns=["pos", "team", "opp", "player_id", "name", "kind", "t", "mu", "actual", "naive5", "affected",
                                       "wx_temp", "wx_wind", "wx"])


Z_PROBS = np.linspace(0, 1, 41)


def backup_qbs(df, ctx):
    """(player_id, t) of backtest QBs who weren't their team's expected starter among the QBs who played that game.
    The live projections drop backups, so scoring their relief snaps would only skew the error tables."""
    from . import redistribute as rd
    if "qb_start" not in df:
        return set()
    starts = {tm: g for tm, g in df[df.qb_start].groupby("team")}
    games = {tm: np.sort(g.t.unique()) for tm, g in df.groupby("team")}
    by_game = {}
    for c in ctx:
        if c["pos"] == "QB":
            by_game.setdefault((c["team"], c["t"]), set()).add(c["pid"])
    skip = set()
    for (team, t), qbs in by_game.items():
        if len(qbs) < 2 or team not in starts:
            continue
        past = games[team][games[team] < t]
        starter = rd.pick_starter(starts[team], past, qbs, season=t // 100)
        if starter is not None:
            skip |= {(pid, t) for pid in qbs - {starter}}
    return skip


def fit_dist(bt):
    """Per kind: robust spread by projection size, plus the empirical quantiles of standardized errors.

    sd(mu) is the IQR-based spread of (actual - mu) in bins of mu, interpolated between bin centers; z is
    (actual - mu) / sd(mu), so its quantiles carry the real skew and tails.
    """
    out = {}
    for kind, g in bt.groupby("kind"):
        g = g.copy()
        nb = int(np.clip(len(g) // 60, 3, 6))
        g["bin"] = pd.qcut(g.mu, nb, duplicates="drop")
        mus, sds = [], []
        for _, b in g.groupby("bin", observed=True):
            res = b.actual - b.mu
            mus.append(float(b.mu.mean()))
            sds.append(float(max(np.subtract(*np.percentile(res, [75, 25])) / 1.349, 1e-3)))
        z = (g.actual - g.mu) / np.interp(g.mu, mus, sds)
        out[kind] = dict(mu=mus, sd=sds, z=[float(x) for x in np.quantile(z, Z_PROBS)])
    return out


def sd_at(mu, dist):
    """Typical error (standard-deviation scale) at a projection, from a fit_dist table (or {"poisson": True})."""
    if dist.get("poisson"):
        return float(np.sqrt(max(mu, 0.0)))
    return float(np.interp(mu, dist["mu"], dist["sd"]))


def prob_over_under_dist(mu, line, dist):
    """(p_over, p_under) from the empirical error distribution. Whole-number lines leave room for a push.
    A {"poisson": True} table prices small whole-number counts (NBA threes, steals, blocks) as Poisson(mu) instead: the smooth error
    table underrated their overs by 4-5 points at 1.5+ in the backtest."""
    if dist.get("poisson"):
        k = np.floor(float(line))
        po = float(st.poisson.sf(k, max(mu, 1e-6)))
        pu = float(st.poisson.cdf(k - 1 if float(line) == k else k, max(mu, 1e-6)))
        return min(max(po, 0.01), 0.99), min(max(pu, 0.01), 0.99)
    sd = sd_at(mu, dist)

    def cdf(y):  # P(actual <= y)
        return float(np.interp((y - mu) / sd, dist["z"], Z_PROBS))

    if float(line) == int(line):
        po, pu = 1 - cdf(line + 0.5), cdf(line - 0.5)
    else:
        po, pu = 1 - cdf(line), cdf(line)
    return min(max(po, 0.01), 0.99), min(max(pu, 0.01), 0.99)  # the data can't justify certainty


def median_dist(mu, dist):
    """The outcome the player beats half the time, from a fit_dist table: below mu when the errors skew right (longest
    rush or reception, where a few long plays pull the average up)."""
    if dist.get("poisson"):
        return float(st.poisson.median(max(mu, 1e-6)))
    return float(mu + sd_at(mu, dist) * np.interp(0.5, Z_PROBS, dist["z"]))


def median_gamma(mu, var):
    """median_dist for prob_over_under's gamma curve."""
    return float(st.gamma(mu * mu / var, scale=var / mu).median())


def backtest_summary(bt):
    out = {}
    for kind, g in bt.groupby("kind"):
        out[kind] = dict(
            n=len(g),
            mae_model=float((g.actual - g.mu).abs().mean()),
            mae_naive=float((g.actual - g.naive5).abs().mean()),
            bias=float((g.actual - g.mu).mean()),
        )
    return out


def _spread_lookup(sched):
    m = {}
    for _, r in sched.iterrows():
        if pd.notna(r.spread_line):
            m[(r.season, r.week, r.home_team)] = float(r.spread_line)
            m[(r.season, r.week, r.away_team)] = -float(r.spread_line)
    return m


def prob_over_under(mu, line, var):
    """Gamma outcome distribution; integer yards so continuity-correct. Returns (p_over, p_under)."""
    shape, scale = mu * mu / var, var / mu
    d = st.gamma(shape, scale=scale)
    if float(line) == int(line):                      # whole-number line: push possible
        return float(d.sf(line + 0.5)), float(d.cdf(line - 0.5))
    return float(d.sf(line)), float(d.cdf(line))


def upcoming_projections(df, sched, priors, var_params, week_t, roster, td_priors=None, td_scale=1.0, rho=None, absent_ids=(),
                         game_wx=None, ruled_out=frozenset(), questionable=(), sat_out=frozenset(), ftd_scale=None):
    """Project all active players for games not yet played in the target week.

    With rho, volume from absent regulars (ruled out/doubtful via absent_ids, or no longer on the active roster)
    is handed to their teammates in the same position group. game_wx: game_id -> per-kind weather multipliers.

    Only the expected starting QB is projected (redistribute.pick_starter, using ruled_out: (t, player_id) pairs ruled
    Out or Doubtful in past weeks, and sat_out: listed Questionable and didn't play): a backup's handful of snaps isn't
    a market, and projecting him split the team's passing between the two. While the starter is listed in
    `questionable`, his backups are kept (and his own past Questionable weeks don't count, as he's not back yet).
    A mid-season arrival who started for his old team can take the job (redistribute.arriving_starter).

    With ftd_scale (the backtest's first-TD scale), every anytime-TD row gets a first-TD row too.

    Receiving rows carry teammate_out: the same-position rotation receivers in their first game out
    (redistribute.teammates_out), whatever rho is."""
    season, week = divmod(week_t, 100)
    games = sched[(sched.season == season) & (sched.week == week) & sched.home_score.isna()]
    opp = opp_table(df, week_t)
    dv = dvp_table(df, week_t)
    implied_map = tdm.implied_points(sched)
    latest = df.sort_values("t").groupby("player_id").tail(1).set_index("player_id")
    by_player = {pid: g for pid, g in df.groupby("player_id")}
    from . import redistribute as rd
    starts = df[df.qb_start] if "qb_start" in df else df.iloc[:0]
    ruled = set(ruled_out) | {(week_t, pid) for pid in absent_ids}
    sat = {(t, pid) for t, pid in sat_out if pid not in questionable}
    logs = rd.Logs(df)
    rows = []
    for _, gm in games.iterrows():
        wx = (game_wx or {}).get(gm.game_id)
        for team, other, sp in ((gm.home_team, gm.away_team, gm.spread_line), (gm.away_team, gm.home_team, -gm.spread_line)):
            sp = 0.0 if pd.isna(sp) else float(sp)
            on_team = latest[latest.index.map(lambda i: roster.get(i) == team) & (latest.t >= (season - 1) * 100)]
            # skip offseason arrivals with no games for the new team: their old role doesn't carry over.
            # Mid-season trades (last game this season, for another team) keep their current-season role.
            on_team = on_team[(on_team.team == team) | (on_team.t >= season * 100)]
            members, thin = [], []
            for pid, p in on_team.iterrows():
                hist = by_player[pid]
                hist = hist.iloc[:]  # a view of the player's rows; attrs are set per use
                hist.attrs["opp"] = other
                if len(hist) < THIN_GAMES and p.position in THIN_KINDS:
                    thin.append((pid, p, hist))
                if len(hist) < (1 if p.position == "QB" else 2):   # a QB's one game can make him the starter
                    continue
                members.append(dict(pid=pid, p=p, hist=hist, pos=p.position, comp=components(hist, p.position, priors, opp, sp, wx)))
            qbs = {c["pid"] for c in members if c["pos"] == "QB" and c["pid"] not in absent_ids}
            past = np.sort(df[(df.team == team) & (df.t < week_t)].t.unique())
            starter = rd.pick_starter(starts[starts.team == team], past, qbs, ruled, season, sat) if qbs else None
            # a QB who arrived mid-season as his old team's starter has no start here for pick_starter to count
            qb_hist = {c["pid"]: c["hist"] for c in members if c["pid"] in qbs}
            arrivals = [pid for pid, h in qb_hist.items() if not (h.team == team).any()]
            if arrivals:
                n_starts = {pid: int((h.qb_start & (h.t >= season * 100)).sum()) if "qb_start" in h else 0
                            for pid, h in qb_hist.items()}
                starter = rd.arriving_starter(starter, n_starts, arrivals)
            # not absent: healthy members, and anyone active on another team's roster (traded away, not missing)
            active = {pid for pid, tm in roster.items() if not (tm == team and pid in absent_ids)}
            receivers = {c["pid"] for c in members if c["pid"] not in absent_ids}
            absent = rd.find_absent(logs, team, week_t, active)

            def comp_fn(h, _other=other, _sp=sp):
                h = h.copy()
                h.attrs["opp"] = _other
                return components(h, h.position.iat[-1], priors, opp, _sp)

            tagged = rd.teammates_out(absent, comp_fn) if absent else {}
            if rho is not None and absent:
                lost = rd.lost_volume(absent, comp_fn)
                if lost:
                    _lift_members(members, lost, rho, only=receivers, lead_pid=starter)
            if starter is not None and starter not in questionable:
                # a ruled-out QB stays, so the pipeline lists him among the excluded
                members = [c for c in members if c["pos"] != "QB" or c["pid"] == starter or c["pid"] in absent_ids]
            # a one-game QB is projected only as the starter (Joe Fagnano, BAL 2026 week 9, with Jackson and Huntley out)
            members = [c for c in members if c["pos"] != "QB" or len(c["hist"]) >= 2 or c["pid"] == starter]
            thin_qbs = {c["pid"] for c in members if c["pos"] == "QB" and len(c["hist"]) < THIN_GAMES}
            qb_f = 1.0
            if starter is not None and QB_REC_K:
                before = starts[(starts.team == team) & (starts.t < week_t)].sort_values("t").player_id.tolist()
                qb_f = rd.passer_ratio(before, starter, lambda q: passer_eff(by_player[q] if q in by_player else df.iloc[:0], priors))
                qb_f = qb_f ** QB_REC_K
                if qb_f < 1:
                    scale_receiving(members, qb_f)
            qb_name = str(by_player[starter].player_display_name.iat[-1]) if qb_f < 1 else ""
            for c in members:
                pid, p, hist, comp = c["pid"], c["p"], c["hist"], c["comp"]
                if td_priors is not None:
                    res = tdm.project_td(hist, comp, p.position, td_priors, implied_map.get((season, week, team)), td_scale)
                    if res:  # kind "td": mu = P(>=1 TD), vol = expected touches, eff = expected TDs
                        rows.append(dict(player_id=pid, name=p.player_display_name, pos=p.position, team=team,
                                         opp=other, home=team == gm.home_team, kind="td", mu=res[0], vol=res[2], eff=res[1],
                                         sd=0.0, spread=sp, gameday=gm.gameday, gametime=gm.gametime, game_id=gm.game_id,
                                         last5=tdm.td_count(hist).tail(5).tolist(), wx=1.0, thin=pid in thin_qbs))
                        if ftd_scale is not None:   # kind "ftd": mu = P(first TD of the game), from the same expected TDs
                            game_lam = tdm.game_tds(implied_map.get((season, week, team)), implied_map.get((season, week, other)))
                            rows.append(dict(rows[-1], kind="ftd", mu=tdm.first_td(res[1], game_lam, ftd_scale),
                                             last5=(hist.first_td.tail(5).tolist() if "first_td" in hist else [])))
                out_here = "" if pid in absent_ids else ", ".join(tagged.get(rd.group("rec", p.position), []))
                for kind, (mu, vol, eff) in all_kinds(hist, p.position, priors, opp, sp, comp, implied_map.get((season, week, team))).items():
                    rows.append(dict(player_id=pid, name=p.player_display_name, pos=p.position, team=team,
                                     opp=other, home=team == gm.home_team, kind=kind, mu=mu, vol=vol, eff=eff,
                                     sd=sd_at(mu, var_params[kind]), spread=sp,
                                     gameday=gm.gameday, gametime=gm.gametime, game_id=gm.game_id,
                                     last5=actual_hist(hist, kind).tail(5).tolist(), wx=wx_ratio(kind, comp, wx),
                                     teammate_out=out_here if kind in rd.TAG_KINDS else "", thin=pid in thin_qbs,
                                     **qb_fields(kind, p.position, comp, qb_f, qb_name), **dvp_fields(dv, kind, other, p.position)))
            # thin-history players: the markets the volume minimum dropped, projected as-is and flagged (not in redistribution)
            have = {(r["player_id"], r["kind"]) for r in rows}
            for pid, p, hist in thin:
                comp = components(hist, p.position, priors, opp, sp, wx)
                if qb_f < 1:
                    scale_receiving([dict(pos=p.position, comp=comp)], qb_f)
                for kind in THIN_KINDS[p.position]:
                    if kind not in comp or (pid, kind) in have:
                        continue
                    mu, vol, eff = comp[kind]
                    rows.append(dict(player_id=pid, name=p.player_display_name, pos=p.position, team=team,
                                     opp=other, home=team == gm.home_team, kind=kind, mu=mu, vol=vol, eff=eff,
                                     sd=sd_at(mu, var_params[kind]), spread=sp,
                                     gameday=gm.gameday, gametime=gm.gametime, game_id=gm.game_id,
                                     last5=actual_hist(hist, kind).tail(5).tolist(), wx=wx_ratio(kind, comp, wx),
                                     teammate_out="", thin=True, **qb_fields(kind, p.position, comp, qb_f, qb_name)))
    return pd.DataFrame(rows)
