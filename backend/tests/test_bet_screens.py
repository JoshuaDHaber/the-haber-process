"""Reading bet slip screenshots (app/bet_screens.py): the request to Claude, matching legs to the board, the endpoint's
limits, and saving a parlay that mixes graded legs with ones the bettor marks. Claude is never called: the reads below are
what the two sample slips (a bet365 and a FanDuel same-game parlay, CHI @ SEA) say."""
import base64
import json
import re
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import auth, bet_screens, bets, main, slip_text
from app.db import Bet, BetLeg, Projection, Run, SessionLocal

KICKOFF = datetime.utcnow() + timedelta(days=2)
GAME = "2099_09_CHI_SEA"
PLAYERS = {   # name, team, markets
    "jsn": ("Jaxon Smith-Njigba", "SEA", ["rec", "recs", "lng", "td"]), "swift": ("D'Andre Swift", "CHI", ["rush", "rec", "rr", "td"]),
    "caleb": ("Caleb Williams", "CHI", ["pass", "ptd"]), "darnold": ("Sam Darnold", "SEA", ["pass", "ptd"]),
    "burden": ("Luther Burden", "CHI", ["rec", "recs", "lng"]), "odunze": ("Rome Odunze", "CHI", ["rec", "recs"]),
    "loveland": ("Colston Loveland", "CHI", ["rec", "recs"]), "barner": ("AJ Barner", "SEA", ["rec", "recs"]),
    "kupp": ("Cooper Kupp", "SEA", ["rec", "recs"]),
}
PNG = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 100).decode()

BET365 = {"sportsbook": "bet365", "bet_type": "parlay", "odds": 1300, "stake": 8.13, "bonus_stake": 3.0, "to_return": 110.82,
          "event": {"teams": ["CHI Bears", "SEA Seahawks"], "starts": "Mon Nov 2 8:15 PM"},
          "legs": [
              {"player": "Jaxon Smith-Njigba", "kind": "rec", "side": "Over", "line": 89.5, "odds": None, "text": "Jaxon Smith-Njigba: 90+ Rec Yards"},
              {"player": "D'Andre Swift", "kind": "rr", "side": "Over", "line": 74.5, "odds": None, "text": "D'Andre Swift: 75+ Rush and Rec Yards"},
              {"player": "Caleb Williams", "kind": "pass", "side": "Over", "line": 199.5, "odds": None, "text": "Caleb Williams: 200+ Pass Yards"},
              {"player": "Jaxon Smith-Njigba", "kind": "lng", "side": "Over", "line": 29.5, "odds": None,
               "text": "Jaxon Smith-Njigba: 30 or More Yards Longest Reception in the Match"},
              {"player": "Luther Burden III", "kind": "rec", "side": "Over", "line": 39.5, "odds": None, "text": "Luther Burden III: 40+ Rec Yards"},
          ]}
FANDUEL = {"sportsbook": "FanDuel", "bet_type": "parlay", "odds": 2638, "stake": None, "bonus_stake": None, "to_return": None,
           "event": {"teams": ["Chicago Bears", "Seattle Seahawks"], "starts": "8:16PM ET"},
           "legs": [
               {"player": "Caleb Williams", "kind": "other", "side": "Over", "line": 199.5, "odds": None,
                "text": "Caleb Williams 200+ Yards (Alt Passing + Rushing)"},
               {"player": "AJ Barner", "kind": "recs", "side": "Over", "line": 2.5, "odds": None, "text": "AJ Barner 3+ Receptions"},
               {"player": "Jaxon Smith-Njigba", "kind": "td", "side": "Over", "line": 0.5, "odds": None, "text": "Jaxon Smith-Njigba Any Time Touchdown Scorer"},
           ]}


@pytest.fixture(scope="module", autouse=True)
def board():
    with SessionLocal() as s:
        run = Run(season=2099, week=9, backtest={}, variance={})
        s.add(run)
        s.flush()
        day, tm = (KICKOFF - timedelta(hours=5)).date().isoformat(), (KICKOFF - timedelta(hours=5)).strftime("%H:%M")
        for pid, (name, team, kinds) in PLAYERS.items():
            for k in kinds:
                s.add(Projection(run_id=run.id, player_id=pid, name=name, pos="WR", team=team, opp="SEA" if team == "CHI" else "CHI",
                                 home=team == "SEA", kind=k, mu=50, sd=20, vol=5, eff=10, spread=0, game_id=GAME, gameday=day, gametime=tm, last5=[]))
        s.commit()
        rid = run.id
    yield
    with SessionLocal() as s:
        s.delete(s.get(Run, rid))
        s.commit()


@pytest.fixture
def sent(monkeypatch):
    out = []
    monkeypatch.setattr(auth, "send_email", lambda to, subject, text, html: out.append(re.search(r"#t=([\w-]+)", text).group(1)))
    auth._hits.clear()
    bet_screens._reads.clear()
    return out


def member(sent, email, ip):
    c = TestClient(main.app, client=(ip, 5000))
    c.post("/api/auth/email", json={"email": email})
    c.post("/api/auth/email/verify", json={"token": sent[-1]})
    c.post("/api/auth/age")
    return c


# ---------- the request to Claude ----------
def test_the_request_asks_for_the_slip_in_a_fixed_shape(monkeypatch):
    calls = []

    class Fake:
        class beta:
            class messages:
                @staticmethod
                def create(**kw):
                    calls.append(kw)
                    return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text='{"legs": []}')])
    monkeypatch.setattr(bet_screens, "_client", Fake)
    assert bet_screens.read_slip("image/png", "AAAA") == {"legs": []}
    kw = calls[0]
    assert kw["model"] == "claude-opus-5-5" and kw["fallbacks"] == "default" and kw["betas"] == ["server-side-fallback-2026-07-01"]
    assert kw["output_config"]["format"]["schema"] is bet_screens.SCHEMA and kw["output_config"]["effort"] == "low"
    image = kw["messages"][0]["content"][0]
    assert image["type"] == "image" and image["source"] == {"type": "base64", "media_type": "image/png", "data": "AAAA"}

    class Refuses(Fake):
        class beta:
            class messages:
                create = staticmethod(lambda **kw: SimpleNamespace(stop_reason="refusal", content=[]))
    monkeypatch.setattr(bet_screens, "_client", Refuses)
    with pytest.raises(Exception) as e:
        bet_screens.read_slip("image/png", "AAAA")
    assert e.value.status_code == 422


# ---------- matching to the board ----------
def test_the_bet365_slip_matches_every_leg_and_notes_the_bonus_bets():
    with SessionLocal() as s:
        d = bet_screens.to_draft(BET365, s, datetime.utcnow())
    assert (d.book, d.league, d.kind, d.odds, d.stake, d.bonus_stake) == ("bet365", "nfl", "parlay", 1300, 8.13, 3.0)
    assert all(leg.graded for leg in d.legs)
    assert [(leg.player.player_id, leg.kind, leg.line) for leg in d.legs] == [
        ("jsn", "rec", 89.5), ("swift", "rr", 74.5), ("caleb", "pass", 199.5), ("jsn", "lng", 29.5), ("burden", "rec", 39.5)]
    assert d.legs[4].player.name == "Luther Burden"     # "Luther Burden III" on the slip: the suffix doesn't stop the match
    assert any("bonus bets" in n for n in d.notes)


def test_the_fanduel_slip_keeps_a_market_the_site_doesnt_project_for_marking_by_hand():
    with SessionLocal() as s:
        d = bet_screens.to_draft(FANDUEL, s, datetime.utcnow())
    assert [leg.graded for leg in d.legs] == [False, True, True]
    assert d.legs[0].kind == "custom" and "Passing + Rushing" in d.legs[0].text
    assert (d.legs[2].kind, d.legs[2].side, d.legs[2].line) == ("td", "Over", 0.5)
    assert any("settle the bet yourself" in n for n in d.notes) and any("stake" in n for n in d.notes)


def test_a_started_game_matches_as_late_for_a_week_and_a_player_not_on_the_board_never():
    with SessionLocal() as s:
        later = bet_screens.to_draft(BET365, s, KICKOFF + timedelta(hours=1))   # forgot to record it: still matched, late
        too_late = bet_screens.to_draft(BET365, s, KICKOFF + timedelta(days=bets.LATE_DAYS, hours=1))
        nobody = bet_screens.to_draft({"legs": [{"player": "Nobody Atall", "kind": "rec", "side": "Over", "line": 9.5,
                                                  "odds": None, "text": "Nobody 10+"}]}, s, datetime.utcnow())
    graded = [leg for leg in later.legs if leg.graded]
    assert graded and all(leg.player.started for leg in graded) and any("added late" in n for n in later.notes)
    assert not any(leg.graded for leg in too_late.legs) and not nobody.legs[0].graded


# ---------- the endpoint ----------
def test_the_endpoint_needs_a_key_a_real_image_and_has_a_daily_limit(sent, monkeypatch):
    monkeypatch.setattr(bet_screens, "read_slip", lambda media, data: BET365)
    with member(sent, "screens@example.com", "198.51.100.30") as c:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert c.post("/api/me/bets/read-screenshot", json={"image": PNG}).status_code == 404
        assert c.get("/api/me/bets").json()["can_read_screenshots"] is False
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        assert c.get("/api/me/bets").json()["can_read_screenshots"] is True
        r = c.post("/api/me/bets/read-screenshot", json={"image": PNG})
        assert r.status_code == 200 and r.json()["odds"] == 1300 and len(r.json()["legs"]) == 5
        assert c.post("/api/me/bets/read-screenshot", json={"image": "data:application/pdf;base64,AAAA"}).status_code == 422
        assert c.post("/api/me/bets/read-screenshot", json={"image": "data:image/png;base64,not base64!"}).status_code == 422
        big = "data:image/png;base64," + base64.b64encode(b"0" * (bet_screens.MAX_IMAGE_BYTES + 1)).decode()
        assert c.post("/api/me/bets/read-screenshot", json={"image": big}).status_code == 422
        bet_screens._reads.clear()
        codes = [c.post("/api/me/bets/read-screenshot", json={"image": PNG}).status_code for _ in range(bet_screens.DAILY_READS + 1)]
        assert codes[-1] == 429 and set(codes[:-1]) == {200}
    with TestClient(main.app, client=("198.51.100.31", 5000)) as anon:
        assert anon.post("/api/me/bets/read-screenshot", json={"image": PNG}).status_code == 401


# ---------- saving a parlay with legs marked by hand ----------
def test_a_parlay_can_mix_graded_legs_with_ones_marked_by_hand(sent):
    with member(sent, "mixed@example.com", "198.51.100.32") as c:
        r = c.post("/api/me/bets", json={"book": "FanDuel", "kind": "parlay", "stake": 5, "odds": 2638, "legs": [
            {"kind": "custom", "description": "Caleb Williams 200+ Yards (Alt Passing + Rushing)"},
            {"game_id": GAME, "player_id": "barner", "kind": "recs", "side": "Over", "line": 2.5},
            {"game_id": GAME, "player_id": "jsn", "kind": "td", "side": "Over", "line": 0.5}]})
        assert r.status_code == 200, r.text
        b = r.json()
        assert [leg["kind"] for leg in b["legs"]] == ["recs", "td", "custom"] and b["legs"][0]["odds"] == bets.NO_ODDS
        assert c.post("/api/me/bets", json={"book": "X", "kind": "parlay", "stake": 5, "odds": 300,
                                            "legs": [{"kind": "custom", "description": "Bears ML"}, {"kind": "custom", "description": "Seahawks total"}]}).status_code == 422
        assert c.post("/api/me/bets", json={"book": "X", "kind": "parlay", "stake": 5, "odds": 300, "legs": [
            {"game_id": GAME, "player_id": "jsn", "kind": "td", "side": "Over", "line": 0.5}, {"kind": "custom", "description": "x"}]}).status_code == 422
    with SessionLocal() as s:
        bet = s.get(Bet, b["id"])
        recs, td, custom = bet.legs
        recs.result, td.result = "win", "win"
        bets.settle(bet)
        assert bet.status == "open"            # every graded leg won: the bettor marks the leg the site can't grade
        td.result = "loss"
        bets.settle(bet)
        assert bet.status == "lost"            # a graded leg lost: the parlay's lost, whatever the other leg did
        td.result, recs.result = "win", "void"
        bets.settle(bet)
        assert bet.status == "open"            # a leg dropped out and its own price isn't known: the bettor marks it
        s.rollback()


# ---------- reading the slip's text (OCR in the browser) ----------

def slip(name):
    """What a reader made of the sample screenshots, kept as text so these tests run without OCR: Tesseract.js in the browser
    ("bet365"), or Apple's, as an iPhone Shortcut's "Extract Text from Image" sends it ("bet365-apple")."""
    with open(f"{__file__.rsplit('/', 1)[0]}/slips/{name}.txt") as f:
        return f.read()


@pytest.mark.parametrize("reader", ["", "-apple"])
def test_the_bet365_text_reads_every_leg_the_price_and_the_stake(reader):
    s = slip_text.parse(slip("bet365" + reader))
    assert (s["sportsbook"], s["odds"], s["stake"], s["bonus_stake"], s["to_return"]) == ("bet365", 1300, 8.13, 3.0, 110.82)
    assert [(l["player"], l["kind"], l["line"]) for l in s["legs"]] == [
        ("Jaxon Smith-Njigba", "rec", 89.5), ("D'Andre Swift", "rr", 74.5), ("Caleb Williams", "pass", 199.5),
        ("Sam Darnold", "pass", 199.5), ("Jaxon Smith-Njigba", "lng", 29.5), ("Luther Burden III", "lng", 19.5),
        ("Rome Odunze", "rec", 29.5), ("Colston Loveland", "rec", 29.5), ("Luther Burden III", "rec", 39.5)]   # OCR's "lll" fixed
    assert s["legs"][0]["text"] == "Jaxon Smith-Njigba 90+ Rec Yards"


@pytest.mark.parametrize("reader", ["", "-apple"])
def test_the_fanduel_text_reads_every_leg_and_keeps_passing_plus_rushing_for_marking(reader):
    s = slip_text.parse(slip("fanduel" + reader))
    assert (s["sportsbook"], s["odds"], s["stake"]) == ("FanDuel", 2638, None)
    assert s["event"]["teams"] == ["Chicago Bears", "Seattle Seahawks"]
    assert [(l["player"], l["kind"], l["line"]) for l in s["legs"]] == [
        ("Caleb Williams", "other", 199.5), ("Sam Darnold", "other", 224.5), ("Jaxon Smith-Njigba", "rec", 89.5),
        ("AJ Barner", "recs", 2.5), ("Cooper Kupp", "recs", 1.5), ("D'Andre Swift", "rr", 59.5),
        ("Luther Burden III", "recs", 3.5), ("Colston Loveland", "recs", 3.5), ("Rome Odunze", "rec", 19.5),
        ("Jaxon Smith-Njigba", "td", 0.5)]
    assert s["legs"][0]["text"] == "Caleb Williams 200+ Passing + Rushing Yards"


def test_the_text_endpoint_matches_legs_to_the_board(sent):
    with member(sent, "slip-text@example.com", "198.51.100.32") as c:
        d = c.post("/api/me/bets/read-slip-text", json={"text": slip("fanduel")}).json()
        assert d["book"] == "FanDuel" and d["odds"] == 2638 and d["league"] == "nfl"
        assert [l["graded"] for l in d["legs"]] == [False, False] + [True] * 8
        assert any("stake" in n for n in d["notes"])
        empty = c.post("/api/me/bets/read-slip-text", json={"text": "a photo of a dog"}).json()
        assert empty["legs"] == [] and "bet365, FanDuel, DraftKings and Fanatics" in empty["notes"][0]
        assert c.post("/api/me/bets/read-slip-text", json={"text": "x" * 20001}).status_code == 422
    with TestClient(main.app, client=("198.51.100.33", 5000)) as anon:
        assert anon.post("/api/me/bets/read-slip-text", json={"text": slip("bet365")}).status_code == 401


@pytest.mark.parametrize("reader", ["", "-apple"])
def test_a_boosted_moneyline_parlay_reads_its_teams_and_its_price_from_the_return(reader):
    s = slip_text.parse(slip("bet365-ml" + reader))
    assert (s["sportsbook"], s["stake"], s["to_return"], s["boost"]) == ("bet365", 15.06, 85.98, 15.0)
    assert s["odds"] == 471   # no parlay price on the slip: $85.98 back on $15.06
    assert [(l["player"], l["kind"], l["odds"]) for l in s["legs"]] == [
        ("LA Lakers", "ml", -575), ("SA Spurs", "ml", -550), ("HOU Rockets", "ml", -310), ("NO Pelicans", "ml", -220),
        ("MEM Grizzlies", "ml", -260), ("BOS Celtics", "ml", -550), ("NY Knicks", "ml", -600)]


def test_moneyline_legs_match_tonights_games_by_nickname(monkeypatch):
    ko = (datetime.utcnow() + timedelta(hours=3)).isoformat()
    board = {"nba": [dict(player_id=t, name=n, pos="", team=t, opp=o, game_id=f"2027_20261104_{o}_{t}", kickoff=ko, kinds=["ml"])
                     for t, n, o in [("LAL", "Los Angeles Lakers", "SAC"), ("SAC", "Sacramento Kings", "LAL"), ("NY", "New York Knicks", "CHI")]],
             "nfl": [dict(player_id="NYG", name="New York Giants", pos="", team="NYG", opp="DAL", game_id="2026_10_DAL_NYG", kickoff=ko, kinds=["ml"])]}
    monkeypatch.setattr(bet_screens, "upcoming_teams", lambda db, league, now: board[league])
    slip = {"sportsbook": "bet365", "odds": 471, "stake": 15.06, "boost": 15.0, "odds_from_return": True, "legs": [
        {"player": "LA Lakers", "kind": "ml", "side": "Over", "line": 0.0, "odds": -575, "text": "LA Lakers moneyline -575"},
        {"player": "NY Knicks", "kind": "ml", "side": "Over", "line": 0.0, "odds": -600, "text": "NY Knicks moneyline -600"},
        {"player": "NY Giants", "kind": "ml", "side": "Over", "line": 0.0, "odds": -150, "text": "NY Giants moneyline -150"},
        {"player": "Toronto Raptors", "kind": "ml", "side": "Over", "line": 0.0, "odds": 120, "text": "Toronto Raptors moneyline +120"}]}
    with SessionLocal() as s:
        d = bet_screens.to_draft(slip, s, datetime.utcnow())
    assert [(l.graded, l.player and l.player.player_id) for l in d.legs] == [(True, "LAL"), (True, "NY"), (True, "NYG"), (False, None)]
    assert d.legs[0].kind == "ml" and d.legs[0].player.opp == "SAC" and d.legs[3].text == "Toronto Raptors moneyline +120"
    assert any("15% boost" in n for n in d.notes)
    assert d.boost is None   # the boost is already in odds worked out from the payout


def test_a_profit_boost_on_the_slips_own_price_or_a_no_sweat_bet_is_set_on_the_draft():
    base = {"sportsbook": "DraftKings", "bet_type": "single", "odds": 150, "stake": 20, "bonus_stake": None, "to_return": None, "legs": []}
    with SessionLocal() as s:
        boosted = bet_screens.to_draft({**base, "boost": 50, "no_sweat": True}, s, datetime.utcnow())
        insured = bet_screens.to_draft({**base, "boost": None, "no_sweat": True}, s, datetime.utcnow())
        free = bet_screens.to_draft({**base, "boost": None, "no_sweat": True, "bonus_stake": 20}, s, datetime.utcnow())
    assert (boosted.boost, boosted.no_sweat) == (50, False)   # one promo a bet: the boost is what the slip's price leans on
    assert any("50%" in n for n in boosted.notes)
    assert (insured.boost, insured.no_sweat) == (None, True) and not free.no_sweat


@pytest.mark.parametrize("reader", ["", "-apple"])
def test_the_draftkings_text_reads_every_leg_through_the_icons_noise(reader):
    s = slip_text.parse(slip("draftkings" + reader))
    assert (s["sportsbook"], s["bet_type"], s["odds"], s["stake"], s["to_return"]) == ("DraftKings", "parlay", 1700, 4.86, 87.48)
    assert [(l["player"], l["kind"], l["side"], l["line"]) for l in s["legs"]] == [
        ("Luka Doncic", "pts", "Over", 34.5), ("Brandon Miller", "pts", "Over", 19.5), ("Luka Doncic", "threes", "Over", 4.5),
        ("Coby White", "pa", "Over", 19.5),   # Tesseract reads the headshot as "a4," before the name
        ("Quentin Grimes", "pts", "Over", 11.5), ("Matisse Thybulle", "stl", "Over", 1.5)]
    assert s["legs"][3]["text"] == "Coby White 20+ Points + Assists"


@pytest.mark.parametrize("reader", ["", "-apple"])
def test_the_fanatics_text_reads_each_leg_and_the_boost_from_the_struck_through_payout(reader):
    """Fanatics shows a boost as the old payout struck through beside the new one ($48.53 $54.64 on $17.96): the price
    before the boost is what the old one pays (+170), and the new one pays 20% more profit. Apple's reader puts each
    leg's price at the slip's end and reads the logo as "Fantics"."""
    s = slip_text.parse(slip("fanatics" + reader))
    assert (s["sportsbook"], s["bet_type"], s["stake"], s["to_return"]) == ("Fanatics", "parlay", 17.96, 54.64)
    assert (s["odds"], s["boost"], s["odds_from_return"]) == (170, 20.0, False)
    assert [(l["player"], l["kind"], l["side"], l["line"], l["odds"]) for l in s["legs"]] == [
        ("Tetairoa McMillan", "rec", "Over", 49.5, -230), ("George Pickens", "rec", "Over", 49.5, -320), ("Garrett Wilson", "rec", "Over", 49.5, -230)]
    assert s["legs"][0]["text"] == "Tetairoa McMillan 50+ Receiving Yards"


@pytest.mark.parametrize("reader", ["", "-apple"])
def test_a_draftkings_bet_slip_with_a_boost_on_only_part_of_the_stake(reader):
    """DraftKings' bet slip before placing: each leg on one line ("30+ | Stefon Diggs Receiving Yards"; Apple's reader
    splits it), a 20% SGP boost "up to max $10 wager" on a $15 stake ($81.75 struck through, $90.65), the stake only on
    "Place Bet $15.00 (+$0.45)", the bracket a bet-protection fee. The book is never named."""
    s = slip_text.parse(slip("draftkings-slip" + reader))
    assert (s["sportsbook"], s["bet_type"], s["odds"], s["stake"], s["to_return"]) == ("DraftKings", "parlay", 445, 15.0, 90.65)
    assert (s["boost"], s["boost_cap"], s["fee"]) == (20.0, 10.0, 0.45)
    assert [(l["kind"], l["line"]) for l in s["legs"]] == [("rec", 29.5), ("pass", 249.5), ("pass", 169.5), ("rec", 19.5),
                                                           ("recs", 4.5), ("rr", 59.5), ("rec", 24.5), ("rec", 59.5)]
    assert [l["player"] for l in s["legs"]][:2] == ["Stefon Diggs", "Joe Burrow"]


def test_a_capped_boost_and_a_fee_reach_the_draft_and_split_names_still_match(monkeypatch):
    ko = (datetime.utcnow() + timedelta(hours=3)).isoformat()
    board = [dict(player_id="00-JC", name="Ja'Marr Chase", pos="WR", team="CIN", opp="WAS", game_id="2026_12_CIN_WAS", kickoff=ko, kinds=["rec"])]
    monkeypatch.setattr(bet_screens, "upcoming_players", lambda db, league, now: board)
    monkeypatch.setattr(bet_screens, "recent_players", lambda db, league, now: [])
    slip = {"sportsbook": "DraftKings", "odds": 445, "stake": 15.0, "to_return": 90.65, "boost": 20.0, "boost_cap": 10.0, "fee": 0.45,
            "legs": [{"player": "Ja Marr Chase", "kind": "rec", "side": "Over", "line": 59.5, "odds": None, "text": "Ja Marr Chase 60+"}]}
    with SessionLocal() as s:
        d = bet_screens.to_draft(slip, s, datetime.utcnow())
    assert d.legs[0].graded and d.legs[0].player.name == "Ja'Marr Chase"
    assert (d.boost, d.boost_cap) == (20, 10.0)
    assert any("first $10.00" in n for n in d.notes) and any("$0.45 fee" in n for n in d.notes)
    whole = bet_screens.to_draft({**slip, "boost_cap": 25.0}, s, datetime.utcnow())   # a cap above the stake covers it all
    assert whole.boost_cap is None


def test_a_payout_without_a_boost_is_left_alone():
    s = slip_text.parse("Fanatics Sportsbook\nSingle +150\nWager\n$10.00\nPayout\n$25.00\nJosh Allen 250+ -110\nPassing Yards\n")
    assert (s["odds"], s["boost"], s["to_return"]) == (150, None, 25.0)


def test_a_draftkings_main_line_and_a_market_the_site_doesnt_project():
    s = slip_text.parse("DRAFTKINGS\nSGP\n2 Pick Parlay | +264\nWager: $10.00\nO 24.5\nJalen Brunson Points\n"
                        "6+\nJosh Hart Rebounds + Assists\n")
    assert [(l["player"], l["kind"], l["side"], l["line"]) for l in s["legs"]] == [
        ("Jalen Brunson", "pts", "Over", 24.5), ("Josh Hart", "other", "Over", 5.5)]


@pytest.mark.parametrize("reader", ["", "-cleaned"])
def test_a_draftkings_bet_card_reads_each_leg_by_the_names_its_header_lists(reader):
    """DraftKings' bet card (its My Bets, and a share link's post): each leg is the player over the market, with a jersey
    icon before the name that OCR garbles ("0 "1 " Lf Chase"). The header lists every player cleanly, so legs are matched
    to it. Tesseract.js's reading of the card from the reported share link (CIN @ WAS), as it is and cleaned up first
    (bets.ts forOcr), which puts Higgins' jersey number between his name and his market."""
    s = slip_text.parse(slip("draftkings-card" + reader))
    assert (s["sportsbook"], s["bet_type"], s["odds"], s["stake"], s["to_return"]) == ("DraftKings", "parlay", 2800, 4.0, 112.0)
    assert [(l["player"], l["kind"], l["side"], l["line"]) for l in s["legs"]] == [
        ("Ja'Marr Chase", "td", "Over", 0.5), ("Mike Gesicki", "td", "Over", 0.5), ("Tee Higgins", "td", "Over", 0.5), ("Stefon Diggs", "td", "Over", 0.5)]
    assert s["legs"][0]["text"] == "Ja'Marr Chase Anytime TD Scorer"


def test_a_share_links_cut_off_preview_gives_the_first_leg_and_the_rest_by_name():
    """The link's preview as the browser reads it (3x, dark text on white: bets.ts readDkLinkOnDevice): the header, the stake
    and bonus bet, and the first leg before the picture fades out. The rest come back as their players' names; the note
    names them."""
    s = slip_text.parse(slip("draftkings-preview"))
    assert (s["sportsbook"], s["odds"], s["stake"], s["bonus_stake"], s["to_return"]) == ("DraftKings", 2800, 4.0, 4.0, 112.0)
    assert [(l["player"], l["kind"], l["line"]) for l in s["legs"]] == [
        ("Ja'Marr Chase", "td", 0.5), ("Mike Gesicki", "other", None), ("Tee Higgins", "other", None), ("Stefon Diggs", "other", None)]
    with SessionLocal() as db:
        d = bet_screens._name_only_note(s, bet_screens.to_draft(s, db, datetime.utcnow()))
    assert "market for Mike Gesicki, Tee Higgins and Stefon Diggs" in d.notes[0]
    assert any("bonus bet" in n for n in d.notes)


def test_a_photo_of_a_draftkings_slip_reads_every_leg_through_glare_and_misspellings():
    """A phone photo of DraftKings' slip on another screen, with glare and jeans beside it, as the browser reads it
    (adaptive threshold: bets.ts ocrImage). OCR misspells the market ("RggaiVing Yards"), lowers a first name ("stefon
    Diggs") and loses two legs' "40+": the header's "100+, 60+, 40+, 40+" fills those in."""
    s = slip_text.parse(slip("draftkings-photo"))
    assert (s["sportsbook"], s["odds"], s["stake"], s["bonus_stake"], s["to_return"]) == ("DraftKings", 550, 4.0, 4.0, 22.0)
    assert [(l["player"], l["kind"], l["line"]) for l in s["legs"]] == [
        ("Ja'Marr Chase", "rec", 99.5), ("Tee Higgins", "rec", 59.5), ("Stefon Diggs", "rec", 39.5), ("Chigoziem Okonkwo", "rec", 39.5)]
    assert s["legs"][0]["text"] == "Ja'Marr Chase 100+ Receiving Yards"


def test_a_book_writing_a_first_name_in_full_still_finds_the_player():
    """DraftKings writes "Chigoziem Okonkwo", the board "Chig Okonkwo": a first name cut short matches, the surname must be
    the same, and a name that isn't one cut short never does."""
    assert bet_screens._same_but_short("Chig Okonkwo", "Chigoziem Okonkwo") and bet_screens._same_but_short("Cameron Ward", "Cam Ward")
    assert not bet_screens._same_but_short("Chig Okonkwo", "Chig Smith") and not bet_screens._same_but_short("Jo Jones", "Joe Jones")
    with SessionLocal() as s:
        d = bet_screens.to_draft({"legs": [{"player": "Jaxon S Smith-Njigba", "kind": "rec", "side": "Over", "line": 89.5, "odds": None, "text": "x"},
                                           {"player": "Jax Smith-Njigba", "kind": "rec", "side": "Over", "line": 89.5, "odds": None, "text": "y"}]},
                                 s, datetime.utcnow())
    assert [leg.graded for leg in d.legs] == [False, True]


def test_a_misspelt_market_is_only_taken_when_its_clearly_one():
    assert slip_text._dk_market("Tee Higgins Regpiving Yards")[1] == "receiving yards"
    assert slip_text._dk_market("Josh Allen Pasing Yards")[1] == "passing yards"
    assert slip_text._dk_market("Tee Higgins Running Late") is None and slip_text._dk_market("Today 8:15 PM") is None


@pytest.mark.parametrize("reader", ["", "-apple"])
def test_a_14_leg_fanduel_parlay_reads_every_leg(reader):
    s = slip_text.parse(slip("fanduel-14" + reader))
    assert (s["sportsbook"], s["odds"], len(s["legs"])) == ("FanDuel", 7210, 14)
    assert [(l["player"], l["kind"], l["line"]) for l in s["legs"]] == [
        ("Tyrese Maxey", "pts", 21.5), ("Trae Young", "pr", 17.5), ("Paul George", "reb", 4.5), ("Julius Randle", "other", 10.5),
        ("Karl-Anthony Towns", "pra", 31.5), ("Andrew Nembhard", "pts", 13.5), ("Zion Williamson", "pa", 18.5),
        ("James Harden", "pra", 22.5),   # "ALT PTS + REB + A..." cut short
        ("Fred VanVleet", "other", 7.5), ("Immanuel Quickley", "pa", 17.5), ("Deni Avdija", "pr", 26.5),
        ("Dylan Harper", "pra", 22.5),   # Apple's reader puts its price between the title and the market
        ("Austin Reaves", "pr", 21.5), ("Davion Mitchell", "pa", 17.5)]
    assert bets.MAX_LEGS >= 14


# ---------- FanDuel share links ----------
from app import share_links  # noqa: E402
from app.db import OddsLine  # noqa: E402


@pytest.fixture
def fanduel_lines():
    """Two FanDuel quotes the odds sync stored, with their betslip links (the ids a share link decodes to)."""
    now = datetime.utcnow()
    link = "https://sportsbook.fanduel.com/addToBetslip?marketId=42.111&selectionId={}"
    with SessionLocal() as s:
        rows = [OddsLine(fetched_at=now, player="Jaxon Smith-Njigba", market="rec", line=89.5, over_odds=-112.0, under_odds=-118.0,
                         book="FanDuel", over_link=link.format(501), under_link=link.format(502), game_id=GAME),
                OddsLine(fetched_at=now, player="Rome Odunze", market="recs", line=3.5, over_odds=150.0, under_odds=-200.0,
                         book="FanDuel", over_link="https://sportsbook.fanduel.com/addToBetslip?marketId=42.222&selectionId=7",
                         under_link="https://sportsbook.fanduel.com/addToBetslip?marketId=42.222&selectionId=71", game_id=GAME)]
        # a moneyline is two rows, each with the other team's pick as its "under" link (as the odds sync stores them)
        ml = "https://sportsbook.fanduel.com/addToBetslip?marketId=42.333&selectionId={}"
        rows += [OddsLine(fetched_at=now + timedelta(seconds=1), player="Seattle Seahawks", market="ml", line=0, over_odds=-270.0, under_odds=220.0, book="FanDuel",
                          over_link=ml.format(1), under_link=ml.format(2), game_id=GAME),
                 OddsLine(fetched_at=now, player="Chicago Bears", market="ml", line=0, over_odds=220.0, under_odds=-270.0, book="FanDuel",
                          over_link=ml.format(2), under_link=ml.format(1), game_id=GAME)]
        s.add_all(rows)
        s.commit()
        ids = [r.id for r in rows]
    yield
    with SessionLocal() as s:
        for i in ids:
            s.delete(s.get(OddsLine, i))
        s.commit()


def fake_fanduel(content, status=200):
    calls = []
    def get(url, timeout, headers):
        calls.append(url)
        return SimpleNamespace(status_code=status, json=lambda: {"content": content, "customerId": "16330408"})
    get.calls = calls
    return get


def test_a_share_link_is_found_in_whatever_text_comes_with_it():
    shared = "https://sportsbook.fanduel.com/addToBetslip?shareCode=Ii1CDAF \n Check out my bet on FanDuel Sportsbook!"
    assert share_links.find_link(shared) == ("code", "Ii1CDAF")
    assert share_links.find_link("https://sportsbook.fanduel.com/addToBetslip?marketId[0]=42.1&selectionId[0]=5"
                                 "&marketId[1]=42.2&selectionId[1]=6") == ("picks", [("42.1", "5"), ("42.2", "6")])
    assert share_links.find_link("no link here") is None and share_links.find_link("https://fanduel.com.evil.example/?shareCode=abcd") is None


def test_a_share_link_reads_each_pick_from_fanduels_stored_lines(fanduel_lines, monkeypatch):
    get = fake_fanduel([{"marketId": "42.111", "selectionId": "502"}, {"marketId": "42.222", "selectionId": "7"}])
    monkeypatch.setattr(share_links.requests, "get", get)
    with SessionLocal() as s:
        slip = share_links.read("https://sportsbook.fanduel.com/addToBetslip?shareCode=Ii1CDAF", s, datetime.utcnow(), get)
        d = bet_screens._share_draft(slip, s, datetime.utcnow())
    assert get.calls == ["https://api.sportsbook.fanduel.com/share-bet/decode-short-url/Ii1CDAF"]
    assert (d.book, d.kind, d.league, d.stake) == ("FanDuel", "parlay", "nfl", None)
    assert [(l.graded, l.player.name, l.kind, l.side, l.line, l.odds) for l in d.legs] == [
        (True, "Jaxon Smith-Njigba", "rec", "Under", 89.5, -118), (True, "Rome Odunze", "recs", "Over", 3.5, 150)]   # 7, not 71
    assert d.odds == round((1 + 100 / 118) * 2.5 * 100 - 100)
    assert any("same-game" in n for n in d.notes) and any("stake" in n for n in d.notes)
    assert "16330408" not in str(d.model_dump())   # the sharer's FanDuel id is never passed on


def test_a_pick_the_site_never_loaded_is_named_and_fanduel_failing_says_so(fanduel_lines):
    get = fake_fanduel([{"marketId": "42.111", "selectionId": "501"}, {"marketId": "42.999", "selectionId": "1"}])
    with SessionLocal() as s:
        d = bet_screens._share_draft(share_links.read("sportsbook.fanduel.com/addToBetslip?shareCode=abcd1", s, datetime.utcnow(), get),
                                     s, datetime.utcnow())
        assert [l.player.name for l in d.legs] == ["Jaxon Smith-Njigba"] and d.odds is None
        assert "1 of the 2 picks isn't" in d.notes[0]
        with pytest.raises(share_links.HTTPException) as e:
            share_links.read("sportsbook.fanduel.com/addToBetslip?shareCode=gone1", s, datetime.utcnow(), fake_fanduel([], status=404))
        assert e.value.status_code == 422
        with pytest.raises(share_links.HTTPException) as e:
            share_links.read("sportsbook.fanduel.com/addToBetslip?shareCode=busy1", s, datetime.utcnow(), fake_fanduel([], status=403))
        assert e.value.status_code == 502 and "screenshot" in e.value.detail


def test_the_text_endpoint_reads_a_shared_fanduel_link(sent, fanduel_lines, monkeypatch):
    monkeypatch.setattr(share_links.requests, "get", fake_fanduel([{"marketId": "42.111", "selectionId": "501"}]))
    with member(sent, "share-link@example.com", "198.51.100.34") as c:
        d = c.post("/api/me/bets/read-slip-text", json={"text": "https://sportsbook.fanduel.com/addToBetslip?shareCode=Ii1CDAF \n Check out my bet"}).json()
    assert d["book"] == "FanDuel" and d["kind"] == "single" and d["odds"] == -112
    assert [(l["graded"], l["player"]["name"], l["line"]) for l in d["legs"]] == [(True, "Jaxon Smith-Njigba", 89.5)]


def test_a_shared_moneyline_names_the_team_picked_not_its_opponent(fanduel_lines):
    with SessionLocal() as s:
        slip = share_links.read("sportsbook.fanduel.com/addToBetslip?shareCode=mlbet", s, datetime.utcnow(),
                                fake_fanduel([{"marketId": "42.333", "selectionId": "2"}]))
    assert [(l["player"], l["kind"], l["odds"]) for l in slip["legs"]] == [("Chicago Bears", "ml", 220)]


def test_alternate_line_picks_are_found_and_each_names_its_own_player():
    """FanDuel lists a game's "3+ threes" as one alternate market, every player a selection of it: the pick is the player
    whose own link it is, and picks on main and alternate lines mix in one bet."""
    from app.db import NbaAltLine
    now = datetime.utcnow()
    alt = "https://sportsbook.fanduel.com/addToBetslip?marketId=42.627004406&selectionId={}"
    with SessionLocal() as s:
        rows = [NbaAltLine(fetched_at=now, game_id="2027_20261120_CHA_ATL", player=name, market="threes", line=2.5, over_odds=odds,
                           under_odds=None, book="FanDuel", over_link=alt.format(sel))
                for name, sel, odds in [("Coby White", 581301, 144.0), ("Brandon Miller", 58130173, -146.0), ("Naz Reid", 5813017, 108.0)]]
        rows.append(OddsLine(fetched_at=now, player="Jaxon Smith-Njigba", market="rec", line=89.5, over_odds=-112.0, under_odds=-118.0,
                             book="FanDuel", over_link="https://sportsbook.fanduel.com/addToBetslip?marketId=42.5&selectionId=1", game_id=GAME))
        s.add_all(rows)
        s.commit()
        try:
            slip = share_links.read("sportsbook.fanduel.com/addToBetslip?shareCode=alts1", s, now,
                                    fake_fanduel([{"marketId": "42.627004406", "selectionId": "58130173"}, {"marketId": "42.5", "selectionId": "1"}]))
        finally:
            for r in rows:
                s.delete(r)
            s.commit()
    assert [(l["player"], l["kind"], l["side"], l["line"], l["odds"]) for l in slip["legs"]] == [
        ("Brandon Miller", "threes", "Over", 2.5, -146), ("Jaxon Smith-Njigba", "rec", "Over", 89.5, -112)]
    assert slip["share"]["missing"] == 0


# ---------- DraftKings share links (a Social post, read from its preview image) ----------
DK_POST = "2de8c2d5-462d-49fd-b90f-b77db562f5aa"
DK_IMG = "https://sportsbook.draftkings.com/comments/seo/assets/2e404331-06b0-4666-8908-e0abb9b6badf.639310634467557213.1.png"
DK_PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 100
# what Claude reads off the preview: the first leg in full, the rest cut off (names only)
DK_SLIP = {"sportsbook": "DraftKings", "bet_type": "parlay", "odds": 2800, "stake": 4.0, "bonus_stake": 4.0, "to_return": 112.0,
           "boost": None, "no_sweat": False, "event": {"teams": [], "starts": None}, "legs": [
               {"player": "Jaxon Smith-Njigba", "kind": "td", "side": "Over", "line": 0.5, "odds": None, "text": "Jaxon Smith-Njigba Anytime TD Scorer"},
               {"player": "D'Andre Swift", "kind": "other", "side": "Over", "line": None, "odds": None, "text": "D'Andre Swift"},
               {"player": "Caleb Williams", "kind": "other", "side": "Over", "line": None, "odds": None, "text": "Caleb Williams"}]}


def fake_draftkings(page_status=200, page=None, img_status=200, img=DK_PNG):
    calls = []
    page = page if page is not None else f'<head><meta property="og:image" content="{DK_IMG}" /></head>'
    def get(url, timeout, headers):
        calls.append(url)
        if url == DK_IMG:
            return SimpleNamespace(status_code=img_status, content=img)
        return SimpleNamespace(status_code=page_status, text=page)
    get.calls = calls
    return get


def test_a_draftkings_share_link_is_found_in_whatever_text_comes_with_it():
    assert share_links.find_dk_post(f"[DraftKings Social](https://sportsbook.draftkings.com/social/post/{DK_POST}?slipAdd)") == DK_POST
    assert share_links.find_dk_post(f"dksb://sb/social/post/{DK_POST.upper()}") == DK_POST
    assert share_links.find_dk_post(f"https://draftkings.com.evil.example/social/post/{DK_POST}") is None
    assert share_links.find_dk_post("https://sportsbook.draftkings.com/social/post/not-a-post") is None


def test_the_preview_image_comes_only_from_draftkings_own_image_path():
    get = fake_draftkings()
    assert share_links.dk_preview(DK_POST, get) == DK_PNG
    assert get.calls == [f"https://sportsbook.draftkings.com/social/post/{DK_POST}", DK_IMG]
    elsewhere = '<meta property="og:image" content="https://evil.example/x.png" />'
    for get, code in [(fake_draftkings(page=elsewhere), 422), (fake_draftkings(page="<head></head>"), 422),
                      (fake_draftkings(page_status=404), 422), (fake_draftkings(page_status=403), 502),
                      (fake_draftkings(img=b"<html>Access Denied</html>"), 502), (fake_draftkings(img_status=403), 502)]:
        with pytest.raises(share_links.HTTPException) as e:
            share_links.dk_preview(DK_POST, get)
        assert e.value.status_code == code and "screenshot" in e.value.detail
        assert not any("evil" in u for u in get.calls)


def test_the_text_endpoint_reads_a_draftkings_link_and_names_the_legs_it_cut_off(sent, monkeypatch):
    seen = {}
    def read(media, data, prompt="Read this bet slip."):
        seen.update(media=media, data=data, prompt=prompt)
        return json.loads(json.dumps(DK_SLIP))
    monkeypatch.setattr(bet_screens, "read_slip", read)
    monkeypatch.setattr(share_links.requests, "get", fake_draftkings())
    text = f"https://sportsbook.draftkings.com/social/post/{DK_POST}?slipAdd"
    with member(sent, "dk-link@example.com", "198.51.100.36") as c:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        r = c.post("/api/me/bets/read-slip-text", json={"text": text})
        assert r.status_code == 422 and "screenshot" in r.json()["detail"]
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        bet_screens._reads.clear()
        d = c.post("/api/me/bets/read-slip-text", json={"text": text}).json()
    assert seen["media"] == "image/png" and base64.b64decode(seen["data"]) == DK_PNG and "cut off" in seen["prompt"]
    assert (d["book"], d["kind"], d["odds"], d["stake"], d["bonus_stake"]) == ("DraftKings", "parlay", 2800, 4.0, 4.0)
    assert [(l["graded"], l["text"]) for l in d["legs"]] == [
        (True, "Jaxon Smith-Njigba Anytime TD Scorer"), (False, "D'Andre Swift"), (False, "Caleb Williams")]
    assert "doesn't show the market for D'Andre Swift and Caleb Williams" in d["notes"][0]
    assert not any("You mark it" in n for n in d["notes"]) and any("bonus bet" in n for n in d["notes"])
    assert len(bet_screens._reads[next(iter(bet_screens._reads))]) == 1   # counts as one of the day's reads


def test_the_preview_image_is_handed_to_the_browser_for_ocr_when_claude_isnt_set_up(sent, monkeypatch):
    monkeypatch.setattr(share_links.requests, "get", fake_draftkings())
    with member(sent, "dk-ocr@example.com", "198.51.100.37") as c:
        r = c.get(f"/api/me/bets/dk-preview/{DK_POST}")
        assert r.status_code == 200 and r.content == DK_PNG and r.headers["content-type"] == "image/png"
        assert c.get("/api/me/bets/dk-preview/not-a-post").status_code == 422
    with TestClient(main.app, client=("198.51.100.38", 5000)) as anon:
        assert anon.get(f"/api/me/bets/dk-preview/{DK_POST}").status_code == 401
