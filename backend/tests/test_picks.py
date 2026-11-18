import numpy as np
import pandas as pd
import pytest

from app.engine.picks import build_picks


def proj(**kw):
    base = dict(player_id="p1", name="Test Back", pos="RB", team="AAA", opp="BBB", home=True, kind="rush",
                mu=80.0, sd=30.0, status="", game_id="2026_04_BBB_AAA", gameday="2026-10-04", gametime="13:00")
    base.update(kw)
    return pd.DataFrame([base])


def lines(rows):
    return pd.DataFrame(rows, columns=["player", "market", "line", "over_odds", "under_odds", "book"])


def test_big_projection_gap_makes_the_over_the_best_play():
    p = proj(mu=90)
    picks = build_picks(p, lines([("Test Back", "rush", 60.5, -110, -110, "A")]), market_weight=0.0)
    over = picks[picks.side == "Over"].iloc[0]
    assert over.ev > 0.1 and over.p_model > 0.65
    assert picks[picks.side == "Under"].iloc[0].ev < 0


def test_market_weight_pulls_probability_toward_the_market():
    p = proj(mu=90)
    ln = lines([("Test Back", "rush", 60.5, -110, -110, "A")])
    model_only = build_picks(p, ln, 0.0).query("side == 'Over'").iloc[0].prob
    blended = build_picks(p, ln, 1.0).query("side == 'Over'").iloc[0].prob
    assert blended == pytest.approx(0.5, abs=0.01)
    assert model_only > blended


def test_best_price_across_books_is_kept():
    ln = lines([("Test Back", "rush", 60.5, -120, -110, "Worse"), ("Test Back", "rush", 60.5, -105, -110, "Better")])
    over = build_picks(proj(mu=90), ln).query("side == 'Over'")
    assert len(over) == 1 and over.iloc[0].book == "Better"


def test_alternate_lines_far_from_consensus_are_ignored():
    ln = lines([("Test Back", "rush", 60.5, -110, -110, "A"), ("Test Back", "rush", 61.5, -110, -110, "B"),
                ("Test Back", "rush", 62.5, -110, -110, "C"), ("Test Back", "rush", 9.5, -300, 220, "Stale")])
    assert "Stale" not in set(build_picks(proj(mu=90), ln).book)


def test_large_model_market_disagreement_is_flagged():
    ln = lines([("Test Back", "rush", 30.5, -110, -110, "A")])
    assert build_picks(proj(mu=80), ln).flagged.all()
    assert not build_picks(proj(mu=33), ln).flagged.any()


def test_unmatched_players_and_empty_inputs_are_safe():
    assert build_picks(proj(), lines([("Someone Else", "rush", 50.5, -110, -110, "A")])).empty
    assert build_picks(proj(), lines([])).empty


def test_one_sided_quotes_still_price_without_crashing():
    ln = lines([("Test Back", "rush", 60.5, -110, None, "A")])
    picks = build_picks(proj(mu=90), ln)
    assert list(picks.side) == ["Over"]


def test_book_filter_keeps_only_that_books_quotes_but_prices_against_all():
    ln = lines([("Test Back", "rush", 60.5, -110, -110, "A"), ("Test Back", "rush", 60.5, -105, -115, "B")])
    only_a = build_picks(proj(mu=90), ln, only_book="A")
    assert set(only_a.book) == {"A"} and len(only_a) == 2
    assert set(build_picks(proj(mu=90), ln).book) == {"A", "B"}  # unfiltered: best price per side comes from either book
    assert build_picks(proj(mu=90), ln, only_book="Nowhere").empty
    # several books (the ones legal in the bettor's state), comma-separated
    assert set(build_picks(proj(mu=90), ln, only_book="A,Nowhere").book) == {"A"}
    assert set(build_picks(proj(mu=90), ln, only_book="A,B").book) == {"A", "B"}
    # the market view still comes from both books, so it is identical with or without the filter
    full = build_picks(proj(mu=90), ln).query("side == 'Over'").iloc[0]
    assert only_a.query("side == 'Over'").iloc[0].p_mkt == pytest.approx(full.p_mkt)


def test_picks_use_the_backtest_error_table_when_the_run_has_one():
    from app.engine.model import prob_over_under_dist
    # a table where outcomes land well ABOVE the projection: standardized errors centred at +1
    dist = dict(mu=[20.0, 100.0], sd=[10.0, 30.0], z=[float(z) for z in np.linspace(-1, 3, 41)])
    ln = lines([("Test Back", "rush", 80.5, -110, -110, "A")])
    with_dist = build_picks(proj(mu=80), ln, market_weight=0.0, dists={"rush": dist}).query("side == 'Over'").iloc[0]
    expected_over, _ = prob_over_under_dist(80.0, 80.5, dist)
    assert with_dist.p_model == pytest.approx(expected_over)
    without = build_picks(proj(mu=80), ln, market_weight=0.0).query("side == 'Over'").iloc[0]  # gamma fallback
    assert with_dist.p_model > without.p_model  # the table's upward skew moves the answer


def test_a_missing_kind_in_the_tables_falls_back_to_the_gamma_curve():
    ln = lines([("Test Back", "rush", 80.5, -110, -110, "A")])
    assert build_picks(proj(mu=80), ln, dists={"pass": dict(mu=[1.0], sd=[1.0], z=[0.0])}).p_model.notna().all()


def test_a_quote_is_only_priced_against_the_game_it_belongs_to():
    base = [("Test Back", "rush", 60.5, -110, -110, "A")]
    own = lines(base).assign(game_id="2026_04_BBB_AAA")
    other_week = lines(base).assign(game_id="2026_03_ZZZ_AAA")     # last week's quote for the same player
    legacy = lines(base).assign(game_id=None)                        # stored before games were tracked
    assert len(build_picks(proj(mu=90), own)) == 2
    assert build_picks(proj(mu=90), other_week).empty
    assert build_picks(proj(mu=90), legacy).empty
    mixed = pd.concat([own, other_week.assign(book="Old")], ignore_index=True)
    assert set(build_picks(proj(mu=90), mixed).book) == {"A"}       # the stale quote doesn't leak into the market view either


def test_passing_tds_are_flagged_on_chances_not_the_projection_to_line_ratio():
    # 2.6 projected TDs against a 1.5 line is a ratio of 1.73, but only a modest gap in chances: not a blind spot
    p = proj(name="Test Qb", pos="QB", kind="ptd", mu=2.6, sd=1.3)
    picks = build_picks(p, lines([("Test Qb", "ptd", 1.5, -140, 110, "A")]), market_weight=0.35)
    over = picks[picks.side == "Over"].iloc[0]
    assert over.p_model > over.p_mkt and not over.flagged
    stale = build_picks(p, lines([("Test Qb", "ptd", 1.5, -140, 110, "A"), ("Test Qb", "ptd", 1.5, -135, 105, "B"),
                                  ("Test Qb", "ptd", 2.5, 150, -190, "C")]), market_weight=0.35)
    assert set(stale.line) == {1.5}   # a whole TD off the consensus is another line, not this market's


def test_a_teammate_out_tags_both_sides_without_flagging_either():
    p = proj(name="Wide Out", pos="WR", kind="rec", mu=33.0, sd=20.0, teammate_out="Missing Receiver")
    picks = build_picks(p, lines([("Wide Out", "rec", 40.5, -110, -110, "A")]))
    assert (picks.teammate_out == "Missing Receiver").all() and not picks.flagged.any() and (picks.flag == "").all()
    plain = build_picks(proj(name="Wide Out", pos="WR", kind="rec", mu=33.0, sd=20.0), lines([("Wide Out", "rec", 40.5, -110, -110, "A")]))
    assert not plain.flagged.any() and (plain.flag == "").all()          # runs stored before the tag existed have no column
    gap = build_picks(proj(name="Wide Out", pos="WR", kind="rec", mu=10.0, sd=20.0, teammate_out="X"),
                      lines([("Wide Out", "rec", 40.5, -110, -110, "A")]))
    assert (gap.flag == "large gap").all()                                # the stronger reason wins


def test_thin_history_projections_are_never_priced():
    ln = lines([("Test Back", "rush", 60.5, -110, -110, "A")])
    assert build_picks(proj(mu=90, thin=True), ln).empty
    assert not build_picks(proj(mu=90, thin=False), ln).empty


def test_a_books_extra_main_market_lines_are_alternates_and_never_priced():
    # Bovada posts a ladder in the main market; only its line nearest the consensus counts, for the board and the market
    ln = lines([("Test Back", "rush", 68.5, -115, -115, "A"), ("Test Back", "rush", 67.5, -110, -120, "B"),
                ("Test Back", "rush", 48.5, 315, -450, "Bovada"), ("Test Back", "rush", 58.5, 150, -190, "Bovada"),
                ("Test Back", "rush", 68.5, -115, -115, "Bovada"), ("Test Back", "rush", 78.5, -170, 135, "Bovada"),
                ("Test Back", "rush", 88.5, -300, 225, "Bovada")])
    picks = build_picks(proj(mu=70), ln, best_only=False)
    assert set(picks[picks.book == "Bovada"].line) == {68.5}
    assert (picks.books == 3).all()
    # a book's lone quote is kept even when it's off the others (that's for the stale-line gap to judge)
    lone = build_picks(proj(mu=70), lines([("Test Back", "rush", 68.5, -115, -115, "A"),
                                           ("Test Back", "rush", 62.5, -115, -115, "C")]), best_only=False)
    assert set(lone.book) == {"A", "C"}


def test_yardage_projections_far_from_the_line_are_flagged_sooner_than_receptions():
    ln = lambda kind, line: lines([("Test Back", kind, line, -110, -110, "A")])
    assert not build_picks(proj(mu=74.0), ln("rush", 60.5)).flagged.any()      # 22% over the line
    assert build_picks(proj(mu=78.0), ln("rush", 60.5)).flagged.all()          # 29%: past YARDS_GAP
    assert build_picks(proj(mu=44.0), ln("rush", 60.5)).flagged.all()          # 27% under
    assert build_picks(proj(kind="pass", mu=180.0), ln("pass", 250.5)).flagged.all()
    # receptions keep the wide band: the model's disagreement there held up in the graded picks
    assert not build_picks(proj(kind="recs", mu=5.5), ln("recs", 4.5)).flagged.any()
