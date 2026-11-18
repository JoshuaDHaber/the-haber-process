"""Team moneyline: each team's chance to win, priced against the sportsbooks' moneylines.

Ratings are a ridge regression of every recent game's point margin on (home rating - away rating + home field),
recency-weighted (HALFLIFE weeks, and earlier seasons count PRIOR_SEASON as much). A predicted margin becomes a win
probability with a normal curve whose spread (sigma) is fitted on a walk-forward backtest.

Checked against closing moneylines the ratings are a weak signal: better than always picking the home team, but the
market is sharper, and no blend in the backtest beat the market alone. So a moneyline price always leans at least
ML_MIN_MARKET_WEIGHT on the market's no-vig chance, whatever the "trust market" slider says (as anytime TD does).
"""
import numpy as np
import pandas as pd
from scipy.stats import norm

from . import odds
from .market import book_weight, power_devig
from .picks import book_set

HALFLIFE = 10.0          # weeks
RIDGE = 2.0              # pseudo-games pulling every rating toward zero (an average team)
PRIOR_SEASON = 0.5       # extra weight on games from earlier seasons (rosters turn over)
HISTORY_SEASONS = 2      # seasons of games behind a rating, besides the current one
BACKTEST_SEASONS = 2     # walk-forward over this many earlier seasons plus the current one
ML_MIN_MARKET_WEIGHT = 0.8
ML_LINE = 0.0            # stored like an over/under: over = this team's price, under = the opponent's
SIGMA_GRID = np.arange(10.0, 22.01, 0.25)


def week_index(season, week):
    return season * 20 + week   # the gap between seasons counts as a few weeks


def ratings(played: pd.DataFrame, season: int, week: int) -> tuple[dict, float]:
    """(team -> rating in points, home-field points) from games played before (season, week)."""
    now = week_index(season, week)
    idx = week_index(played.season, played.week)
    hist = played[(idx < now) & (played.season >= season - HISTORY_SEASONS)]
    if hist.empty:
        return {}, 0.0
    teams = sorted(set(hist.home_team) | set(hist.away_team))
    ti = {t: i for i, t in enumerate(teams)}
    n, rows = len(teams), np.arange(len(hist))
    x = np.zeros((len(hist), n + 1))
    x[rows, hist.home_team.map(ti).to_numpy()] = 1.0
    x[rows, hist.away_team.map(ti).to_numpy()] = -1.0
    x[:, n] = 1.0 - _neutral(hist)
    y = (hist.home_score - hist.away_score).to_numpy(float)
    w = 0.5 ** ((now - week_index(hist.season, hist.week).to_numpy()) / HALFLIFE)
    w = w * np.where(hist.season.to_numpy() < season, PRIOR_SEASON, 1.0)
    reg = np.eye(n + 1) * RIDGE
    reg[n, n] = 0.01   # home field isn't shrunk
    with np.errstate(all="ignore"):   # some BLAS builds raise spurious matmul warnings
        beta = np.linalg.solve(x.T @ (x * w[:, None]) + reg, x.T @ (w * y))
    return {t: float(beta[i]) for t, i in ti.items()}, float(beta[n])


def _neutral(df: pd.DataFrame) -> np.ndarray:
    return (df["location"] == "Neutral").to_numpy(float) if "location" in df else np.zeros(len(df))


def margin(r: dict, hfa: float, home: str, away: str, neutral: bool = False) -> float:
    """Predicted home margin in points."""
    return r.get(home, 0.0) - r.get(away, 0.0) + (0.0 if neutral else hfa)


def win_prob(m: float, sigma: float) -> float:
    return float(np.clip(norm.cdf(m / sigma), 0.01, 0.99))


def no_vig(price, other):
    """This side's market chance with the margin stripped (two-way market, power method: market.power_devig)."""
    return power_devig(odds.implied(price), odds.implied(other))


def walk_forward(sched: pd.DataFrame, season: int, week: int) -> pd.DataFrame:
    """Every game from BACKTEST_SEASONS seasons ago up to (season, week), rated with only earlier games."""
    played = sched[sched.home_score.notna()]
    todo = played[(played.season >= season - BACKTEST_SEASONS) & (week_index(played.season, played.week) < week_index(season, week))]
    out = []
    for (s, w), g in todo.groupby(["season", "week"]):
        r, hfa = ratings(played, s, w)
        if not r:
            continue
        for row, neutral in zip(g.itertuples(), _neutral(g)):
            hml, aml = getattr(row, "home_moneyline", np.nan), getattr(row, "away_moneyline", np.nan)
            nv = float(no_vig(hml, aml)) if pd.notna(hml) and pd.notna(aml) else np.nan
            out.append((s, w, margin(r, hfa, row.home_team, row.away_team, bool(neutral)), float(row.home_score - row.away_score), nv))
    return pd.DataFrame(out, columns=["season", "week", "margin", "result", "p_mkt"])


def fit_sigma(bt: pd.DataFrame) -> float:
    """The spread of the margin -> win probability curve that maximizes the backtest's likelihood (ties left out)."""
    d = bt[bt.result != 0]
    if len(d) < 50:
        return 15.0
    y = (d.result > 0).to_numpy(float)
    m = d.margin.to_numpy()

    def loss(s):
        p = np.clip(norm.cdf(m / s), 1e-6, 1 - 1e-6)
        return -np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))
    return float(min(SIGMA_GRID, key=loss))


def summary(bt: pd.DataFrame, sigma: float) -> dict:
    """Backtest scorecard (Brier scores, like anytime TD): the model vs. always giving the home team its average win
    rate (mae_naive), and vs. the closing moneyline on the games that have one (mae_market, model scored on the same games)."""
    d = bt[bt.result != 0]
    if d.empty:
        return dict(n=0, mae_model=0.0, mae_naive=0.0, bias=0.0, metric="brier", calibration=[])
    p, y = norm.cdf(d.margin.to_numpy() / sigma), (d.result > 0).to_numpy(float)
    mk = d.p_mkt.notna().to_numpy()
    cal = pd.DataFrame({"p": p, "y": y, "bin": pd.qcut(p, 5, duplicates="drop")}).groupby("bin", observed=True) \
        .agg(p=("p", "mean"), y=("y", "mean"), n=("y", "size"))
    return dict(n=int(len(y)), mae_model=float(np.mean((p - y) ** 2)), mae_naive=float(np.mean((y.mean() - y) ** 2)),
                bias=float(np.mean(y - p)), metric="brier",
                mae_market=float(np.mean((d.p_mkt.to_numpy()[mk] - y[mk]) ** 2)) if mk.any() else None,
                mae_model_market_games=float(np.mean((p[mk] - y[mk]) ** 2)) if mk.any() else None,
                n_market=int(mk.sum()),
                calibration=[dict(predicted=float(r.p), actual=float(r.y), n=int(r.n)) for r in cal.itertuples()])


def project(sched: pd.DataFrame, season: int, week: int, sigma: float) -> dict:
    """game_id -> the model's view of each unplayed game in (season, week)."""
    played = sched[sched.home_score.notna()]
    r, hfa = ratings(played, season, week)
    games = sched[(sched.season == season) & (sched.week == week) & sched.home_score.isna()]
    out = {}
    for g, neutral in zip(games.itertuples(), _neutral(games)):
        m = margin(r, hfa, g.home_team, g.away_team, bool(neutral))
        out[g.game_id] = dict(home=g.home_team, away=g.away_team, gameday=str(g.gameday),
                              gametime="" if pd.isna(g.gametime) else str(g.gametime), margin=m, p_home=win_prob(m, sigma),
                              home_rating=r.get(g.home_team, 0.0), away_rating=r.get(g.away_team, 0.0),
                              spread=None if pd.isna(getattr(g, "spread_line", np.nan)) else float(g.spread_line),
                              total=None if pd.isna(getattr(g, "total_line", np.nan)) else float(g.total_line))
    return out


def _none(v):
    return None if v is None or (isinstance(v, float) and np.isnan(v)) or v == "" else v


def price(games: dict, lines: pd.DataFrame, market_weight: float = 0.35, only_book: str = None, names: dict = None,
          min_market_weight: float = ML_MIN_MARKET_WEIGHT, book_weights: dict = None) -> list[dict]:
    """Both sides of every game: model, market and blended win chance, and the best price (at only_book, if given).
    The market's chance is the no-vig consensus across every book, sharp books weighted up, as on the props board. names maps team
    abbreviations to the Odds API's team names (the NFL's by default)."""
    names = names or odds.TEAM_NAMES
    abbr = {v: k for k, v in names.items()}
    lines = lines[lines.market == "ml"] if not lines.empty else lines
    w = max(market_weight, min_market_weight)
    out = []
    for gid, g in sorted(games.items(), key=lambda kv: (kv[1]["gameday"], kv[1]["gametime"], kv[0])):
        gl = lines[(lines.game_id == gid)] if not lines.empty else lines
        sides = []
        for team, home in ((g["away"], False), (g["home"], True)):
            p_model = g["p_home"] if home else 1 - g["p_home"]
            q = gl[gl.player.map(abbr) == team] if not gl.empty else gl
            both = q[q.over_odds.notna() & q.under_odds.notna()] if not q.empty else q
            p_mkt = (float(np.average(no_vig(both.over_odds, both.under_odds), weights=[book_weight(b, book_weights) for b in both.book]))
                     if not both.empty else None)
            prob = p_model if p_mkt is None else (1 - w) * p_model + w * p_mkt
            side = dict(team=team, name=names.get(team, team), home=home, p_model=p_model, p_mkt=p_mkt, prob=prob,
                        books=int(q.book.nunique()) if not q.empty else 0,
                        odds=None, book=None, ev=None, kelly=None, link=None, event_link=None)
            pool = q[q.over_odds.notna()] if not q.empty else q
            if only_book:
                pool = pool[pool.book.isin(book_set(only_book))]
            if not pool.empty:
                best = pool.loc[pool.over_odds.idxmax()]
                dec = float(odds.american_to_dec(best.over_odds))
                ev = prob * (dec - 1) - (1 - prob)
                side.update(odds=int(best.over_odds), book=best.book, ev=ev, kelly=max(0.0, ev / (dec - 1)) / 4,
                            link=_none(best.get("over_link")), event_link=_none(best.get("event_link")))
            sides.append(side)
        out.append(dict(game_id=gid, label=f"{g['away']} @ {g['home']}", gameday=g["gameday"], gametime=g["gametime"],
                        margin=g["margin"], spread=g.get("spread"), total=g.get("total"), sides=sides,
                        blowout=g.get("blowout")))
    return out


# ---------- a team's recent results (Who's hot) and one side's price at every book (the betslip) ----------
FORM_GAMES = 15          # results kept per team on each run, most recent last
MIN_STREAK = 3           # wins in a row to count as a team on a winning streak


def form(played: pd.DataFrame, teams, n: int = FORM_GAMES) -> dict:
    """team -> its last n results this season, oldest first: {date, opp, home, pf, pa}. played: one row per finished game
    with date, home_team, away_team, home_score and away_score."""
    out = {t: [] for t in teams}
    if played.empty:
        return out
    for g in played.sort_values("date").itertuples():
        for team, opp, home, pf, pa in ((g.home_team, g.away_team, True, g.home_score, g.away_score),
                                        (g.away_team, g.home_team, False, g.away_score, g.home_score)):
            if team in out:
                out[team].append(dict(date=str(g.date)[:10], opp=opp, home=home, pf=float(pf), pa=float(pa)))
    return {t: r[-n:] for t, r in out.items()}


def win_streak(margins) -> int:
    """Wins in a row, most recent back (a tie ends it)."""
    n = 0
    for m in reversed(list(margins)):
        if m <= 0:
            break
        n += 1
    return n


def hot_teams(priced: list[dict], games: dict, window: int, min_streak: int = MIN_STREAK) -> list[dict]:
    """Teams on a winning streak, each with its moneyline (priced: engine/moneyline.price's games). games: the run's
    game_id -> team view, whose "form" holds each team's recent results. Longest streak first, then the likeliest winner.
    A streak isn't an edge on its own: the price already knows the team has been winning, so the EV beside it says
    whether the run is in the number."""
    out = []
    for g in priced:
        form_ = (games.get(g["game_id"]) or {}).get("form") or {}
        for i, s in enumerate(g["sides"]):
            margins = [r["pf"] - r["pa"] for r in form_.get(s["team"]) or []]
            streak = win_streak(margins)
            if streak < min_streak:
                continue
            recent = margins[-window:]
            out.append(dict(s, game_id=g["game_id"], label=g["label"], gameday=g["gameday"], gametime=g["gametime"],
                            opp=g["sides"][1 - i]["team"], streak=streak, window=len(recent), wins=sum(m > 0 for m in recent),
                            recent=recent, avg_margin=float(np.mean(recent))))
    return sorted(out, key=lambda r: (-r["streak"], -r["prob"], r["team"]))


def side_ladder(games: dict, lines: pd.DataFrame, game_id: str, team: str, market_weight: float = 0.35, only_book: str = None,
                names: dict = None, min_market_weight: float = ML_MIN_MARKET_WEIGHT, book_weights: dict = None):
    """One team's moneyline at every book, in the shape of a player's line ladder (line 0: over = this team wins, under =
    its opponent does), so the betslip can shop it between books and see its price move. None if the team isn't in that game."""
    g = games.get(game_id)
    if not g or team not in (g["home"], g["away"]):
        return None
    names = names or odds.TEAM_NAMES
    side = next(s for s in price({game_id: g}, lines, market_weight, only_book, names, min_market_weight, book_weights)[0]["sides"]
                if s["team"] == team)
    prob = side["prob"]
    abbr = {v: k for k, v in names.items()}
    q = lines[(lines.market == "ml") & (lines.game_id == game_id)] if not lines.empty else lines
    q = q[q.player.map(abbr) == team] if not q.empty else q
    if only_book and not q.empty:
        q = q[q.book.isin(book_set(only_book))]

    def quote(price_, p, link):
        if pd.isna(price_):
            return None
        dec = float(odds.american_to_dec(price_))
        return dict(odds=int(price_), prob=p, ev=p * dec - 1, link=_none(link), p_model=None)
    rows = [dict(line=ML_LINE, book=r.book, alt=False, event_link=_none(r.event_link),
                 over=quote(r.over_odds, prob, r.over_link), under=quote(r.under_odds, 1 - prob, r.under_link))
            for r in q.itertuples()]
    return dict(kind="ml", mu=g["margin"] if team == g["home"] else -g["margin"], sd=0.0, game_id=game_id, alt_fetched_at=None,
                quotes=rows, w=max(market_weight, min_market_weight))
