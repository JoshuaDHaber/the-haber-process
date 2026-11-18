"""Reading the market (engine/market.py): power devig, the weighted consensus, the fitted book and market weights, the
ladder's market curve and steam."""
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from app.engine import market as mkt
from app.engine import odds
from app.engine.model import Z_PROBS


def quotes(rows):
    df = pd.DataFrame(rows, columns=["player", "market", "line", "over_odds", "under_odds", "book"])
    return df.assign(key=df.player.map(odds.norm))


def test_power_devig_matches_proportional_at_even_prices_and_shades_the_longshot():
    assert mkt.power_devig(odds.implied(-110), odds.implied(-110)) == pytest.approx(0.5)
    p, q = odds.implied(-300), odds.implied(240)
    fair = float(mkt.power_devig(p, q))
    fair_under = float(mkt.power_devig(q, p))
    assert fair + fair_under == pytest.approx(1.0, abs=1e-9)
    assert fair > p / (p + q)          # the favorite keeps more of its chance than the proportional split gives it
    assert np.isnan(mkt.power_devig(np.nan, 0.5))


def test_consensus_weights_sharp_books_and_splits_twins():
    ln = quotes([("A B", "rush", 50.5, -130, 110, "Pinnacle"),        # sharp: over favored
                 ("A B", "rush", 50.5, -110, -110, "Soft"),
                 ("A B", "rush", 50.5, -110, -110, "BetOnline.ag"),
                 ("A B", "rush", 50.5, -110, -110, "LowVig.ag")])
    c = mkt.consensus(ln).loc[("a b", "rush")]
    sharp = float(mkt.power_devig(odds.implied(-130), odds.implied(110)))
    # Pinnacle 3, Soft 1, BetOnline + LowVig 1.5 shared between them
    assert c.nv_o == pytest.approx((3 * sharp + 1 * 0.5 + 1.5 * 0.5) / 5.5)
    assert c.line == 50.5 and c.books == 4
    # fitted weights replace the prior
    even = mkt.consensus(ln, weights={"Pinnacle": {"w": 1.0}}).loc[("a b", "rush")]
    assert even.nv_o < c.nv_o


def test_consensus_moves_each_book_to_the_median_line_along_the_model_slope_and_keeps_one_sided_players():
    ln = quotes([("A B", "rush", 48.5, -110, -110, "X"), ("A B", "rush", 50.5, -110, -110, "Y"),
                 ("A B", "rush", 50.5, -110, -110, "Z"), ("C D", "rush", 30.5, -110, None, "X")])
    slope = lambda k, m, line: 0.5 - (line - 50.5) * 0.02   # noqa: E731
    c = mkt.consensus(ln, slope)
    assert c.loc[("a b", "rush")].nv_o == pytest.approx((0.46 + 0.5 + 0.5) / 3)
    one = c.loc[("c d", "rush")]
    assert one.line == 30.5 and np.isnan(one.nv_o) and one.books == 1


def test_book_weights_favor_the_book_whose_early_price_sat_at_the_close():
    t0, t1 = datetime(2026, 10, 15, 14), datetime(2026, 10, 18, 15)
    rows = []
    rng = np.random.default_rng(1)
    for i in range(60):
        close_over = int(rng.choice([-130, -120, -110, 100, 110]))
        close_under = -close_over if abs(close_over) > 100 else -120
        p = f"Player {chr(65 + i % 26)}{chr(65 + i // 26)}"   # (names, not numbers: odds.norm drops digits)
        rows += [(t1, "g", p, "rec", 40.5, close_over, close_under, b) for b in ("Leader", "Follower", "Other")]
        rows += [(t0, "g", p, "rec", 40.5, close_over, close_under, "Leader"),      # already at the close
                 (t0, "g", p, "rec", 40.5, -110, -110, "Follower")]                # moved to it later
    hist = pd.DataFrame(rows, columns=["fetched_at", "game_id", "player", "market", "line", "over_odds", "under_odds", "book"])
    w = mkt.fit_book_weights(hist, prior={}, n0=10)
    assert w["Leader"]["w"] > 1.5 and w["Follower"]["w"] < 1 and w["Leader"]["n"] == 60
    assert "Other" not in w   # no early quotes to score


def test_market_weights_lean_on_whichever_side_predicts_and_fall_back_by_level():
    rng = np.random.default_rng(3)
    n = 4000
    truth = rng.uniform(0.2, 0.8, n)
    y = (rng.uniform(size=n) < truth).astype(float)
    noise = rng.uniform(0.2, 0.8, n)
    rows = pd.DataFrame(dict(kind=["rec"] * n, hours=np.where(np.arange(n) % 2, 2.0, 80.0), p_model=noise, p_mkt=truth, y=y))
    fit = mkt.fit_market_weights(rows, n0=50)
    assert fit["kinds"]["rec"]["w"] > 0.85
    assert fit["kinds"]["rec"]["ll_market"] < fit["kinds"]["rec"]["ll_model"]
    assert mkt.weight_for(fit, "rec", 1.0) == fit["kinds"]["rec"]["buckets"]["game day"]["w"]
    assert mkt.weight_for(fit, "rush", 1.0) == fit["w"]          # no rush picks: the overall fit
    assert mkt.weight_for(None, "rec", 1.0) == mkt.DEFAULT_WEIGHT
    flipped = mkt.fit_market_weights(rows.assign(p_model=truth, p_mkt=noise), n0=50)
    assert flipped["kinds"]["rec"]["w"] < 0.15
    # few picks: pulled toward the default
    assert abs(mkt.fit_market_weights(rows.head(10), n0=150)["w"] - mkt.DEFAULT_WEIGHT) < 0.1


def test_buckets():
    assert [mkt.bucket(h) for h in (1, 6, 47, 48, None)] == ["game day", "6–48 h", "6–48 h", "48 h+", "48 h+"]


DIST = dict(mu=[20.0, 80.0], sd=[15.0, 15.0], z=list(np.quantile(np.random.default_rng(0).standard_normal(20000), Z_PROBS)))


def test_the_market_curve_is_read_off_a_ladder():
    from scipy.stats import norm
    m, s, k = 62.0, 15.0, 1.08   # the book's margin, power-style: it posts p ** (1 / k)
    am = lambda implied: int(round(100 / implied - 100)) if implied <= 0.5 else int(round(-100 * implied / (1 - implied)))   # noqa: E731
    rows = []
    for line in (40.5, 50.5, 60.5, 70.5, 80.5):
        p = float(1 - norm.cdf((line - m) / s))
        rows.append(dict(line=line, book="X", over_odds=am(p ** (1 / k)), under_odds=None))
    p = float(1 - norm.cdf((60.5 - m) / s))
    rows.append(dict(line=60.5, book="X", over_odds=am(p ** (1 / k)), under_odds=am((1 - p) ** (1 / k))))
    pts = mkt.ladder_fair(pd.DataFrame(rows))
    curve = mkt.fit_market_curve(pts, mu=55.0, dist=DIST)
    assert curve["m"] == pytest.approx(m, abs=1.0) and curve["s"] == pytest.approx(s, rel=0.1) and curve["n_lines"] == 5
    assert mkt.curve_over(curve, 60.5, DIST) == pytest.approx(1 - norm.cdf(-1.5 / 15), abs=0.02)
    assert mkt.fit_market_curve(pts, 55.0, {"poisson": True}) is None


def test_steam_needs_several_books_moving_together():
    def fetch(chances):
        ln = quotes([("A B", "rec", 50.5, None, None, b) for b in chances])
        return ln.assign(nv=list(chances.values()), nv_c=list(chances.values()), cline=50.5, w=1.0)
    prev = fetch({"X": 0.5, "Y": 0.5, "Z": 0.5, "W": 0.5})
    assert mkt.steam(prev, fetch({"X": 0.55, "Y": 0.56, "Z": 0.54, "W": 0.5})).loc[("a b", "rec")] == 1
    assert mkt.steam(prev, fetch({"X": 0.45, "Y": 0.44, "Z": 0.45, "W": 0.5})).loc[("a b", "rec")] == -1
    assert mkt.steam(prev, fetch({"X": 0.55, "Y": 0.56, "Z": 0.5, "W": 0.5})).loc[("a b", "rec")] == 0      # two books
    assert mkt.steam(prev, fetch({"X": 0.58, "Y": 0.56, "Z": 0.55, "W": 0.46})).loc[("a b", "rec")] == 0    # one against


def test_a_tight_ladder_fits_only_the_center():
    pts = pd.DataFrame(dict(line=[260.5, 261.5, 263.5], book=["A", "B", "C"], nv=[0.505, 0.5, 0.5], two_sided=True))
    skew = dict(mu=[200.0, 300.0], sd=[60.0, 60.0], z=[float(v) for v in np.quantile(np.random.default_rng(2).gamma(4, 1, 20000) - 3.7, Z_PROBS)])
    c = mkt.fit_market_curve(pts, mu=207.0, dist=skew)
    assert c["s"] == pytest.approx(60.0) and c["median"] == pytest.approx(261.5, abs=1.5)


def test_calibration_flattens_an_overconfident_model_and_leaves_thin_or_yes_no_markets_alone():
    rng = np.random.default_rng(7)
    n = 6000
    pm = rng.uniform(0.1, 0.9, n)
    lg = np.log(pm / (1 - pm))
    truth = 1 / (1 + np.exp(-0.4 * lg))                  # the model is far surer than it should be
    y = (rng.uniform(size=n) < truth).astype(float)
    noise = rng.uniform(0.1, 0.9, n)                     # a model that knows nothing
    rows = pd.DataFrame(dict(kind=["pts"] * n + ["pa"] * n + ["reb"] * 50 + ["dd"] * n,
                             p_model=np.r_[pm, noise, pm[:50], pm], y=np.r_[y, y, y[:50], y]))
    cal = mkt.fit_calibration(rows)
    assert 0.3 < cal["pts"]["b"] < 0.5 and abs(cal["pts"]["a"]) < 0.1 and cal["pts"]["n"] == n
    assert cal["pa"]["b"] < 0.1                          # no signal: the curve all but ignores the model
    assert "reb" not in cal and "dd" not in cal          # too few picks; yes/no markets have their own curve
    # few picks: pulled toward no change
    few = mkt.fit_calibration(rows[rows.kind == "pts"].head(150), n0=150)
    assert few["pts"]["b"] > cal["pts"]["b"]


def test_calibrate_moves_both_sides_and_keeps_a_push_between_them():
    assert mkt.calibrate(0.7, 0.3, None) == (0.7, 0.3)
    over, under = mkt.calibrate(0.7, 0.3, dict(a=0.0, b=0.5))
    assert 0.5 < over < 0.7 and over + under == pytest.approx(1.0)
    over, under = mkt.calibrate(0.6, 0.3, dict(a=0.0, b=0.5))    # whole-number line: 10% push
    assert over + under < 1.0
    assert mkt.curve_for({"calibration": {"pts": dict(a=0.0, b=0.5)}}, "pts") == dict(a=0.0, b=0.5)
    assert mkt.curve_for({"w": 0.5}, "pts") is None and mkt.curve_for(None, "pts") is None
