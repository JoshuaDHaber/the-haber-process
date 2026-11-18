import pandas as pd
import pytest

from app.engine.ladder import build_ladder, price_quote


def test_price_quote_matches_the_odds_math():
    p, ev = price_quote(mu=80, sd=30, line=60.5, side="over", american=100)
    assert ev == pytest.approx(p * 1.0 - (1 - p))  # even money
    p_under, _ = price_quote(80, 30, 60.5, "under", 100)
    assert p + p_under == pytest.approx(1.0)


def test_longshot_alt_lines_are_priced_by_the_model():
    low, _ = price_quote(60, 25, 80.5, "over", 300)
    high, _ = price_quote(60, 25, 120.5, "over", 3000)
    assert low > high > 0


def test_ladder_handles_one_sided_quotes_and_missing_links():
    q = pd.DataFrame([
        dict(line=75.5, book="B", alt=True, over_odds=400, under_odds=None, over_link="", under_link=None, event_link=None),
        dict(line=50.5, book="A", alt=False, over_odds=-110, under_odds=-110, over_link="https://x/o", under_link="https://x/u",
             event_link="https://x/e"),
    ])
    rows = build_ladder(70, 28, q)
    assert [r["line"] for r in rows] == [50.5, 75.5]  # sorted by line
    assert rows[1]["under"] is None and rows[1]["over"]["link"] is None  # '' normalised to None
    assert rows[0]["over"]["link"] == "https://x/o" and rows[0]["event_link"] == "https://x/e"


def test_odds_range_hides_prices_outside_the_window_per_side():
    q = pd.DataFrame([
        dict(line=40.5, book="A", alt=True, over_odds=-450, under_odds=330, over_link=None, under_link=None, event_link=None),
        dict(line=60.5, book="A", alt=False, over_odds=-110, under_odds=-110, over_link=None, under_link=None, event_link=None),
        dict(line=90.5, book="A", alt=True, over_odds=900, under_odds=None, over_link=None, under_link=None, event_link=None),
    ])
    rows = build_ladder(60, 25, q, max_abs_odds=300)
    assert [r["line"] for r in rows] == [60.5]  # 40.5 has no side in range, 90.5's only side is +900
    assert len(build_ladder(60, 25, q)) == 3  # 0 = no limit
    edge = pd.DataFrame([dict(line=50.5, book="A", alt=True, over_odds=-300, under_odds=300, over_link=None, under_link=None, event_link=None)])
    assert build_ladder(60, 25, edge, max_abs_odds=300)[0]["over"] and build_ladder(60, 25, edge, max_abs_odds=300)[0]["under"]  # inclusive


def test_calibrated_mu_moves_the_projection_so_the_main_line_matches_the_curve():
    from app.engine import market as mkt
    from app.engine.ladder import calibrated_mu
    from app.engine.model import Z_PROBS, prob_over_under_dist
    from scipy.stats import norm
    dist = dict(mu=[5.0, 40.0], sd=[5.0, 5.0], z=[float(norm.ppf(p)) for p in Z_PROBS])   # normal errors, sd 5
    curve = dict(a=0.0, b=0.3)                                  # the model is far surer than it should be
    assert calibrated_mu(30.0, 6.0, 24.5, "pa", dist, None) == 30.0      # no curve, no main line, yes/no: unchanged
    assert calibrated_mu(30.0, 6.0, None, "pa", dist, curve) == 30.0
    assert calibrated_mu(0.4, 0.0, 0.5, "dd", dist, curve) == 0.4
    raw = prob_over_under_dist(30.0, 24.5, dist)
    mu = calibrated_mu(30.0, 6.0, 24.5, "pa", dist, curve)
    assert 24.5 < mu < 30.0
    # the main line now reads what the Best props board shows ...
    assert prob_over_under_dist(mu, 24.5, dist)[0] == pytest.approx(mkt.calibrate(*raw, curve)[0], abs=1e-3)
    # ... and the rungs still fall off with the line, instead of all reading the curve's near-coin-flip
    rungs = [prob_over_under_dist(mu, line, dist)[0] for line in (14.5, 24.5, 34.5)]
    assert rungs[0] > 0.9 and rungs[0] > rungs[1] > rungs[2] and rungs[2] < 0.15
    # Poisson counts (threes, steals, blocks) too
    pois = calibrated_mu(3.0, 0.0, 1.5, "threes", {"poisson": True}, curve)
    assert 1.5 < pois < 3.0
