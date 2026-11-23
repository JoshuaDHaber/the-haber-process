"""Backtest for the Parlays tab's Starters tiles (frontend/src/parlays.ts, buildStarterParlays), one leg at a time.

A 4-leg tile hits about one time in eight, so its own record says little for hundreds of slates. Everything worth tuning is
a question about legs: is -200 the right floor, how big a role is big enough, does the matchup grade matter after all,
which markets run hot. So this replays every graded past slate and prices every over in the starter markets, main and
alternate lines, at every role size and grade, and slices the results.

Per leg: the projection from the run the board snapshot used (pick_log.run_id), its matchup grade and role percentile, the
last price before kickoff at each book (NFL: odds_history up to the game's last snapshot; NBA: the game's stored main and
alternate lines, which are its last fetch), and the result from the graded pick_log rows.

The chance is the model's (its run's error table) blended with the price the way the tiles blend a rung (parlays.ts,
LADDER_MARKET_WEIGHT). The live ladder also mixes in the market curve read off every book; that isn't replayed, so read
`prob` as a near stand-in and lean on hit rate against the price (`roi`), which needs no model at all.

Caveats: rush + rec had no matchup grade before Nov 2026, so older runs borrow the back's rushing grade or the receiver's
receiving grade. A player's rungs are one outcome counted several times: `players` gives the honest sample size.

First run, Nov 23, 2026, when the tiles still required an A/B matchup (NFL Oct 19 - Nov 22, 70 games; NBA Oct 20 -
Nov 22, 211 games; best price per line, overs -200 or longer and 50%+ likely). It's why they now rank by role alone:
- The matchup grade didn't sort winners from losers. NFL A 46% / B 48% / C 60% / D 39% / F 56% hit; NBA A 56% / B 59% /
  C 53% / D 59% / F 65%. Each is ~130-450 players, so a few points either way is noise, and no order shows up.
- Role size did: A/B NBA legs from the top third of roles hit 61% (+3% ROI) against 52% (-12%) from the bottom third.
- The A/B NFL legs hit 47% where the blend said 57% (-18% at the price): six weeks of one-sided overs, too few to fit
  anything to yet. NBA was close to the price (-3%).
- Alternate lines beat main lines in both (NFL -14% vs -20%, NBA 0% vs -6%), and the price floor showed no cliff at -200.

Second run, same data, every grade (the tiles as they are now):
- The tiles' legs (top-half roles) came out NBA +1% ROI against -2% for any role at the same floor; NFL -12% against -11%.
- NBA's bottom third of roles is the part worth leaving out (54% hit, -8%); the top third (59%, -3%) did no better than
  the middle (62%, +4%). NFL showed no role effect: every third lost 4-14% at the price.
- Grades still sorted nothing among the tiles' legs (NBA F 75%, C 53%; NFL C 59%, A 43%).

    python -m app.starter_backtest [--sport nfl|nba|both] [--csv legs.csv]
"""
import argparse
import math
from typing import Optional

import pandas as pd
from sqlalchemy import select

from .db import (NbaAltLine, NbaOddsLine, NbaPickLog, NbaProjection, NbaRun, OddsHistory, PickLog, Projection,
                 Run, SessionLocal)
from .engine import odds
from .engine.dvp import grade as dvp_grade
from .engine.ladder import price_quote

# mirrors parlays.ts: starterKinds, STARTER_MIN_ODDS, STARTER_MIN_PROB, LADDER_MARKET_WEIGHT
STARTER_KINDS = {"nfl": ("rush", "rec", "rr", "pass"), "nba": ("pts", "reb", "ast", "pra", "pr", "pa")}
STARTER_MIN_ODDS = -200
STARTER_MIN_PROB = 0.5
MARKET_WEIGHT = 0.35
# legs outside this price range aren't kept at all (far from anything the tiles could use)
KEEP_ODDS = (-400, 300)
# the role percentile a leg needs in the as-built slice: the tiles search the slate's 12 biggest roles with a usable line,
# which on a full slate is about the top half of each market's priced overs
TOP_ROLE = 0.5
PRICE_BUCKETS = [(-400, -301, "-400 to -301"), (-300, -201, "-300 to -201"), (-200, -151, "-200 to -151"),
                 (-150, -101, "-150 to -101"), (-100, 300, "-100 to +300")]
LEG_COLUMNS = ["sport", "slate", "game_id", "player_id", "name", "pos", "kind", "line", "odds", "book", "alt", "mu",
               "grade", "role", "p_model", "prob", "actual", "result"]


def _dists(run) -> dict:
    return {k: v for k, v in ((run.variance if run else None) or {}).items() if isinstance(v, dict) and not k.startswith("_")}


def _grade(rank, n) -> Optional[str]:
    return dvp_grade(int(rank), int(n)) if rank is not None and n and not pd.isna(rank) and not pd.isna(n) else None


def role_percentiles(log: pd.DataFrame) -> dict:
    """(slate, player_id, kind) -> where his projection ranks among that slate's priced overs in that market, 0 (the
    smallest) to 1 (the biggest): parlays.ts roleScores, on the overs the board logged."""
    out = {}
    overs = log[log.side == "Over"].drop_duplicates(["slate", "player_id", "kind"])
    for (slate, kind), g in overs.groupby(["slate", "kind"]):
        g = g.sort_values("mu", kind="stable")
        n = len(g)
        for i, pid in enumerate(g.player_id):
            out[(slate, pid, kind)] = i / (n - 1) if n > 1 else 1.0
    return out


def price_legs(proj: pd.DataFrame, quotes: pd.DataFrame, actuals: dict, roles: dict, dists: dict, sport: str, slate) -> list[dict]:
    """Every over in `quotes` (columns player_key, market, line, over_odds, book, alt) that matches a projection in `proj`
    (one game, one run) with a graded result: one row per player, market, line and book."""
    rows = []
    for p in proj.itertuples():
        actual = actuals.get((p.player_id, p.kind))
        if actual is None or getattr(p, "thin", False):
            continue
        q = quotes[(quotes.player_key == odds.norm(p.name)) & (quotes.market == p.kind) & quotes.over_odds.notna()]
        q = q[(q.over_odds >= KEEP_ODDS[0]) & (q.over_odds <= KEEP_ODDS[1])]
        for r in q.itertuples():
            p_model, _ = price_quote(p.mu, p.sd, float(r.line), "over", float(r.over_odds), p.kind, dists.get(p.kind))
            dec = float(odds.american_to_dec(r.over_odds))
            result = "win" if actual > r.line else "push" if actual == r.line else "loss"
            rows.append(dict(sport=sport, slate=slate, game_id=p.game_id, player_id=p.player_id, name=p.name, pos=p.pos,
                             kind=p.kind, line=float(r.line), odds=int(r.over_odds), book=r.book, alt=bool(r.alt), mu=float(p.mu),
                             grade=p.grade, role=roles.get((slate, p.player_id, p.kind)), p_model=float(p_model),
                             prob=(1 - MARKET_WEIGHT) * p_model + MARKET_WEIGHT / dec, actual=float(actual), result=result))
    return rows


def _rr_grade_fallback(proj: pd.DataFrame) -> pd.DataFrame:
    """Runs from before rush + rec was graded: a back borrows his rushing grade, anyone else his receiving grade."""
    by = {(r.player_id, r.kind): r.grade for r in proj.itertuples()}
    missing = (proj.kind == "rr") & proj.grade.isna()
    proj.loc[missing, "grade"] = [by.get((pid, "rush" if pos in ("RB", "FB", "QB") else "rec"))
                                  for pid, pos in zip(proj.loc[missing, "player_id"], proj.loc[missing, "pos"])]
    return proj


def _proj_frame(rows, kinds) -> pd.DataFrame:
    df = pd.DataFrame([dict(player_id=r.player_id, name=r.name, pos=r.pos, kind=r.kind, game_id=r.game_id, mu=r.mu, sd=r.sd,
                            thin=bool(getattr(r, "thin", False)), grade=_grade(r.dvp_rank, r.dvp_n)) for r in rows if r.kind in kinds],
                      columns=["player_id", "name", "pos", "kind", "game_id", "mu", "sd", "thin", "grade"])
    return _rr_grade_fallback(df) if "rr" in kinds else df


def _quote_frame(rows, alt: Optional[bool] = None) -> pd.DataFrame:
    return pd.DataFrame([dict(player_key=odds.norm(r.player), market=r.market, line=r.line, over_odds=r.over_odds, book=r.book,
                              alt=r.alt if alt is None else alt) for r in rows],
                        columns=["player_key", "market", "line", "over_odds", "book", "alt"])


def _graded_log(session, table, slate_of) -> pd.DataFrame:
    rows = session.scalars(select(table).where(table.result.is_not(None))).all()
    return pd.DataFrame([dict(game_id=r.game_id, player_id=r.player_id, kind=r.kind, side=r.side, mu=r.mu, actual=r.actual,
                              result=r.result, run_id=r.run_id, snapped_at=r.snapped_at, slate=slate_of(r)) for r in rows])


def nfl_legs(session) -> pd.DataFrame:
    kinds = STARTER_KINDS["nfl"]
    log = _graded_log(session, PickLog, lambda r: f"{r.season}-W{r.week:02d}")
    if log.empty:
        return pd.DataFrame(columns=LEG_COLUMNS)
    roles = role_percentiles(log)
    out = []
    for gid, g in log.groupby("game_id"):
        played = g[g.result != "void"]
        actuals = {(r.player_id, r.kind): r.actual for r in played.itertuples() if r.actual is not None and not pd.isna(r.actual)}
        run_id, last = int(g.run_id.max()), g.snapped_at.max()
        proj = _proj_frame(session.scalars(select(Projection).where(Projection.run_id == run_id, Projection.game_id == gid)).all(), kinds)
        hist = session.scalars(select(OddsHistory).where(OddsHistory.game_id == gid, OddsHistory.market.in_(kinds),
                                                         OddsHistory.fetched_at <= last)).all()
        if proj.empty or not hist:
            continue
        q = pd.DataFrame([dict(player=r.player, market=r.market, line=r.line, over_odds=r.over_odds, book=r.book, alt=r.alt,
                               fetched_at=r.fetched_at) for r in hist])
        # the last price each book showed for each line before the game's last snapshot
        q = q.sort_values("fetched_at").drop_duplicates(["player", "market", "line", "book"], keep="last")
        q = q.assign(player_key=q.player.map(odds.norm))
        out += price_legs(proj, q, actuals, roles, _dists(session.get(Run, run_id)), "nfl", g.slate.iloc[0])
    return pd.DataFrame(out, columns=LEG_COLUMNS)


def nba_legs(session) -> pd.DataFrame:
    kinds = STARTER_KINDS["nba"]
    log = _graded_log(session, NbaPickLog, lambda r: str(r.gameday))
    if log.empty:
        return pd.DataFrame(columns=LEG_COLUMNS)
    roles = role_percentiles(log)
    out = []
    for gid, g in log.groupby("game_id"):
        played = g[g.result != "void"]
        actuals = {(r.player_id, r.kind): r.actual for r in played.itertuples() if r.actual is not None and not pd.isna(r.actual)}
        run_id = int(g.run_id.max())
        proj = _proj_frame(session.scalars(select(NbaProjection).where(NbaProjection.run_id == run_id, NbaProjection.game_id == gid)).all(), kinds)
        main = session.scalars(select(NbaOddsLine).where(NbaOddsLine.game_id == gid, NbaOddsLine.market.in_(kinds))).all()
        alts = session.scalars(select(NbaAltLine).where(NbaAltLine.game_id == gid, NbaAltLine.market.in_(kinds))).all()
        if proj.empty or not (main or alts):
            continue
        q = pd.concat([_quote_frame(main, False), _quote_frame(alts, True)], ignore_index=True)
        out += price_legs(proj, q.drop_duplicates(["player_key", "market", "line", "book"], keep="last"), actuals, roles,
                          _dists(session.get(NbaRun, run_id)), "nba", g.slate.iloc[0])
    return pd.DataFrame(out, columns=LEG_COLUMNS)


def best_price(legs: pd.DataFrame) -> pd.DataFrame:
    """One row per player, market and line: the best price any book had (what the tiles' book picker can find)."""
    return legs.sort_values("odds").drop_duplicates(["sport", "game_id", "player_id", "kind", "line"], keep="last")


def summarize(legs: pd.DataFrame, label: str) -> dict:
    """Hit rate against the predicted chance and the price, for one slice. Pushes are left out of the rates."""
    g = legs[legs.result != "push"]
    n = len(g)
    if not n:
        return dict(slice=label, legs=0, players=0)
    wins = (g.result == "win").to_numpy()
    dec = g.odds.map(lambda a: float(odds.american_to_dec(a))).to_numpy()
    hit = float(wins.mean())
    return dict(slice=label, legs=n, players=int(g[["game_id", "player_id", "kind"]].drop_duplicates().shape[0]),
                hit=hit, se=math.sqrt(hit * (1 - hit) / n), prob=float(g.prob.mean()), p_model=float(g.p_model.mean()),
                breakeven=float((1 / dec).mean()), roi=float((wins * dec).mean() - 1))


def usable(legs: pd.DataFrame) -> pd.DataFrame:
    """Legs at the tiles' price floor: STARTER_MIN_ODDS or longer, at least STARTER_MIN_PROB likely (any role, any grade)."""
    return legs[(legs.odds >= STARTER_MIN_ODDS) & (legs.prob >= STARTER_MIN_PROB)]


def in_section(legs: pd.DataFrame) -> pd.DataFrame:
    """The legs the tiles could use: usable, from the top TOP_ROLE of their market's roles."""
    u = usable(legs)
    return u[u.role >= TOP_ROLE]


def report(legs: pd.DataFrame) -> list[dict]:
    """The slices that answer the tuning questions, on each line's best price."""
    legs = best_price(legs)
    use, sec = usable(legs), in_section(legs)
    out = [summarize(legs, "all legs (-400 to +300)"),
           summarize(use, "-200 or longer, 50%+, any role"),
           summarize(sec, "as built: top-half roles, -200 or longer, 50%+")]
    # role at the price floor: do the biggest roles beat the smallest at the same price?
    for lo, hi, name in ((0, 1 / 3, "bottom third"), (1 / 3, 2 / 3, "middle third"), (2 / 3, 1.01, "top third")):
        out.append(summarize(use[(use.role >= lo) & (use.role < hi)], f"role {name} (-200 or longer, 50%+)"))
    # grade among the tiles' legs: kept to see whether it ever starts to matter
    out += [summarize(sec[sec.grade == g], f"as built, grade {g}") for g in "ABCDF"]
    out.append(summarize(sec[sec.grade.isna()], "as built, no grade"))
    top = legs[legs.role >= TOP_ROLE]
    out += [summarize(top[(top.odds >= lo) & (top.odds <= hi)], f"top-half roles priced {name}") for lo, hi, name in PRICE_BUCKETS]
    out += [summarize(sec[sec.kind == k], f"as built, {k}") for k in sorted(sec.kind.unique())]
    out += [summarize(sec[sec.alt == a], f"as built, {'alternate' if a else 'main'} lines") for a in (False, True)]
    return out


def format_report(rows: list[dict], title: str) -> str:
    lines = [title, f"{'slice':<44}{'legs':>6}{'plyrs':>6}{'hit':>8}{'±':>6}{'prob':>7}{'model':>7}{'b/e':>7}{'roi':>8}"]
    for r in rows:
        if not r["legs"]:
            lines.append(f"{r['slice']:<44}{0:>6}")
            continue
        lines.append(f"{r['slice']:<44}{r['legs']:>6}{r['players']:>6}{r['hit']:>8.1%}{r['se']:>6.1%}{r['prob']:>7.1%}"
                     f"{r['p_model']:>7.1%}{r['breakeven']:>7.1%}{r['roi']:>+8.1%}")
    return "\n".join(lines)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sport", choices=("nfl", "nba", "both"), default="both")
    ap.add_argument("--csv", help="also write every priced leg to this CSV")
    a = ap.parse_args(argv)
    frames = []
    with SessionLocal() as s:
        for sport, fn in (("nfl", nfl_legs), ("nba", nba_legs)):
            if a.sport in (sport, "both"):
                legs = fn(s)
                frames.append(legs)
                print(format_report(report(legs), f"\n{sport.upper()} starter legs (best price per line)"))
    if a.csv and frames:
        pd.concat(frames, ignore_index=True).to_csv(a.csv, index=False)
        print(f"\nwrote {a.csv}")


if __name__ == "__main__":
    main()
