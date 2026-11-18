"""King of the Endzone: the chance each player scores the longest touchdown of a game.

The promo pays when your player scores the game's longest TD (ties count, usually settled as a dead heat). Each
simulated game draws every listed player's touchdowns from his anytime-TD chance (the board's blended one: model and
market), and each touchdown's length from his own past TDs, pulled toward his position's (K_LEN pseudo-TDs). Whether a
TD is a run or a catch comes from his own split, pulled toward his position's (K_SPLIT). The game's longest TD wins.

Two more entries per team cover everyone else: the rest of the offense (the team's expected offensive TDs from its
implied points, less the listed players', at least OTHER_FLOOR of them, with league-wide lengths), and its defense and
special teams (return TDs at the league rate, with return lengths, which are nearly always long). When the listed
players add up to more TDs than the team total allows, they're scaled down to fit.

Lengths come from the last three seasons' play-by-play (data.load_tds). Ties are split evenly: dead-heat equity.
"""
import numpy as np

SIMS = 100_000
K_LEN = 12            # pseudo-TDs pulling a player's own lengths toward his position's
K_SPLIT = 6           # and his run/catch split toward his position's
OTHER_FLOOR = 0.12    # the rest of an offense gets at least this share of its team's TDs
MAX_YARDS = 100
POSITIONS = ("QB", "RB", "WR", "TE")


def summarize(tds, positions: dict, played) -> dict:
    """League-wide inputs, stored with each run: length histograms (yards 0-99) per position and kind, all offensive TDs
    ("other") and return TDs ("ret"); each position's share of rushing TDs; offensive TDs per team point; return TDs
    per team-game; and the average game total. tds: data.load_tds; positions: player_id -> position; played: finished
    schedule rows over the same seasons."""
    t = tds.assign(pos=tds.player_id.map(positions).replace({"FB": "RB"}))
    off = t[t.kind != "ret"]
    hist = lambda y: np.bincount(np.clip(np.asarray(y, int), 0, MAX_YARDS - 1), minlength=MAX_YARDS).tolist()  # noqa: E731
    pools = {f"{p}_{k}": hist(off[(off.pos == p) & (off.kind == k)].yards) for p in POSITIONS for k in ("rush", "rec")}
    pools["other"], pools["ret"] = hist(off.yards), hist(t[t.kind == "ret"].yards)
    rush_share = {p: float((off[off.pos == p].kind == "rush").mean()) if (off.pos == p).any() else 0.5 for p in POSITIONS}
    games = played[played.game_id.isin(set(tds.game_id))] if len(played) else played
    team_games = 2 * max(len(games), 1)
    points = float((games.home_score + games.away_score).sum()) if len(games) else 0.0
    return dict(pools=pools, rush_share=rush_share, td_per_point=len(off) / points if points else 2.36 / 22.45,
                ret_per_game=float((t.kind == "ret").sum()) / team_games,
                avg_total=points / len(games) if len(games) else 45.0, tds=int(len(t)))


def own_lengths(tds, player_ids) -> dict:
    """player_id -> {"rush": [yards...], "rec": [yards...]} over the play-by-play's seasons, for the given players."""
    t = tds[tds.player_id.isin(set(player_ids)) & tds.kind.isin(["rush", "rec"])]
    out: dict = {}
    for (pid, kind), g in t.groupby(["player_id", "kind"]):
        out.setdefault(pid, {"rush": [], "rec": []})[kind] = [int(y) for y in g.yards]
    return out


def _from_hist(rng, hist, n):
    h = np.asarray(hist, float)
    return rng.choice(len(h), size=n, p=h / h.sum()) if n and h.sum() else np.zeros(n)


def _lengths(rng, n, rush_share, own, pools, pos) -> np.ndarray:
    """n touchdown lengths for one listed player: run or catch, then his own TD or his position's."""
    rush = rng.random(n) < rush_share
    out = np.empty(n)
    for kind, mask in (("rush", rush), ("rec", ~rush)):
        m, mine = int(mask.sum()), np.asarray(own.get(kind, []), float)
        pool = _from_hist(rng, pools.get(f"{pos}_{kind}", pools["other"]), m)
        if len(mine):
            use_own = rng.random(m) < len(mine) / (len(mine) + K_LEN)
            pool[use_own] = rng.choice(mine, size=int(use_own.sum()))
        out[mask] = pool
    return out


def board(players: list[dict], game: dict, ez: dict, sims: int = SIMS, seed: int = 0) -> dict:
    """The game's King of the Endzone board. players: dict(player_id, name, pos, team, prob (anytime TD), own (his
    lengths, own_lengths)); game: dict(home, away, spread (the home side's), total or None); ez: summarize()'s output.
    Returns dict(rows best first, median longest TD, chance of no TD, chance the longest is tied, team totals)."""
    rng = np.random.default_rng(seed)
    total = game.get("total") or ez["avg_total"]
    spread = game.get("spread") or 0.0
    implied = {game["home"]: (total + spread) / 2, game["away"]: (total - spread) / 2}
    entries = []
    for p in players:
        prob = min(max(float(p["prob"]), 0.0), 0.99)
        own = p.get("own") or {}
        n_rush, n_rec = len(own.get("rush", [])), len(own.get("rec", []))
        prior = ez["rush_share"].get(p["pos"], 0.5)
        entries.append(dict(p, kind="player", lam=-np.log1p(-prob), n_tds=n_rush + n_rec,
                            rush_share=(n_rush + K_SPLIT * prior) / (n_rush + n_rec + K_SPLIT),
                            med=float(np.median(own.get("rush", []) + own.get("rec", []))) if n_rush + n_rec else None))
    teams = {}
    for team, pts in implied.items():
        expected = pts * ez["td_per_point"]
        listed = sum(e["lam"] for e in entries if e["team"] == team)
        scale = min(1.0, (1 - OTHER_FLOOR) * expected / listed) if listed else 1.0
        for e in entries:
            if e["team"] == team:
                e["lam"] *= scale
        other = max(expected - listed * scale, OTHER_FLOOR * expected)
        teams[team] = dict(points=pts, tds=expected, scale=scale)
        entries.append(dict(player_id=None, name=f"Rest of {team} offense", pos="", team=team, kind="other", lam=other))
        entries.append(dict(player_id=None, name=f"{team} defense & returns", pos="", team=team, kind="ret", lam=ez["ret_per_game"]))
    best = np.full((sims, len(entries)), -1.0)
    for j, e in enumerate(entries):
        cnt = rng.poisson(e["lam"], sims)
        n = int(cnt.sum())
        if e["kind"] == "player":
            yards = _lengths(rng, n, e["rush_share"], e.get("own") or {}, ez["pools"], e["pos"])
        else:
            yards = _from_hist(rng, ez["pools"][e["kind"]], n)
        np.maximum.at(best[:, j], np.repeat(np.arange(sims), cnt), yards)
    longest = best.max(axis=1)
    scored = longest >= 0
    win = (best == longest[:, None]) & scored[:, None]
    n_win = win.sum(axis=1)
    equity = (win / np.maximum(n_win, 1)[:, None]).mean(axis=0)
    rows = []
    for j, e in enumerate(entries):
        p_td = 1 - np.exp(-e["lam"])
        rows.append(dict(player_id=e["player_id"], name=e["name"], pos=e["pos"], team=e["team"], kind=e["kind"],
                         p_td=float(p_td), p_long=float(equity[j]), p_long_if_td=float(win[:, j].mean() / p_td) if p_td else 0.0,
                         median_td=e.get("med"), n_tds=e.get("n_tds", 0)))
    rows.sort(key=lambda r: -r["p_long"])
    return dict(rows=rows, median_longest=float(np.median(longest[scored])) if scored.any() else None,
                p_no_td=float(1 - scored.mean()), p_tie=float((n_win > 1).mean()), total=float(total),
                total_assumed=game.get("total") is None, teams=teams)
