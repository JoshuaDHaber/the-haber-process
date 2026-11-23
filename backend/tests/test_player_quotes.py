"""player_quotes: one player's current lines, looked up instead of filtering every line ever fetched."""
from datetime import datetime

import pandas as pd
import pytest

from app.db import OddsLine, SessionLocal, current_lines, player_quotes
from app.engine import odds

G1, G2 = "2026_99_AWY_HOM", "2026_99_CCC_DDD"


def line(player, market, game, at, book="X", value=60.5):
    return OddsLine(player=player, market=market, line=value, over_odds=-110, under_odds=-110, book=book,
                    game_id=game, fetched_at=datetime(2026, 11, 1, at))


@pytest.fixture(autouse=True)
def lines():
    with SessionLocal() as s:
        s.query(OddsLine).delete()
        s.add_all([
            line("A.J. Brown", "rec", G1, 9), line("AJ Brown", "rec", G1, 12, book="Y"),   # same player, two spellings
            line("A.J. Brown", "rec", G1, 12, book="Z", value=61.5),
            line("A.J. Brown", "recs", G1, 12),
            line("Other Back", "rush", G2, 10),
            line("A.J. Brown", "rec", None, 8),                                             # a row from before game_id
        ])
        s.commit()
    yield
    with SessionLocal() as s:
        s.query(OddsLine).delete(); s.commit()


def filtered(s, market, key, game):
    """The full scan player_quotes replaces."""
    L = current_lines(s)
    return L[(L.market == market) & (L.player.map(odds.norm) == key) & (L.game_id == game)]


@pytest.mark.parametrize("market,name,game", [
    ("rec", "A.J. Brown", G1), ("recs", "AJ Brown", G1), ("rush", "Other Back", G2),
    ("rec", "A.J. Brown", G2), ("rec", "Nobody", G1), ("rec", "A.J. Brown", None),
])
def test_same_rows_order_and_index_as_filtering_everything(market, name, game):
    key = odds.norm(name)
    with SessionLocal() as s:
        pd.testing.assert_frame_equal(player_quotes(s, market, key, game), filtered(s, market, key, game), check_exact=True)


def test_only_the_newest_fetch_for_the_game_and_both_spellings():
    with SessionLocal() as s:
        got = player_quotes(s, "rec", odds.norm("AJ Brown"), G1)
    assert sorted(got.book) == ["Y", "Z"]          # the 9 AM fetch is superseded; spellings normalize together


def test_new_lines_show_up_without_a_restart():
    key = odds.norm("Other Back")
    with SessionLocal() as s:
        assert len(player_quotes(s, "rush", key, G2)) == 1
        s.add(line("Other Back", "rush", G2, 14, book="W")); s.commit()
        assert list(player_quotes(s, "rush", key, G2).book) == ["W"]
