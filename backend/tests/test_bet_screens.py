"""Reading bet slip screenshots (app/bet_screens.py): the request to Claude, matching legs to the board, the endpoint's
limits, and saving a parlay that mixes graded legs with ones the bettor marks. Claude is never called: the reads below are
what the two sample slips (a bet365 and a FanDuel same-game parlay, CHI @ SEA) say."""
import base64
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
        assert empty["legs"] == [] and "bet365 and FanDuel" in empty["notes"][0]
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
    slip = {"sportsbook": "bet365", "odds": 471, "stake": 15.06, "boost": 15.0, "legs": [
        {"player": "LA Lakers", "kind": "ml", "side": "Over", "line": 0.0, "odds": -575, "text": "LA Lakers moneyline -575"},
        {"player": "NY Knicks", "kind": "ml", "side": "Over", "line": 0.0, "odds": -600, "text": "NY Knicks moneyline -600"},
        {"player": "NY Giants", "kind": "ml", "side": "Over", "line": 0.0, "odds": -150, "text": "NY Giants moneyline -150"},
        {"player": "Toronto Raptors", "kind": "ml", "side": "Over", "line": 0.0, "odds": 120, "text": "Toronto Raptors moneyline +120"}]}
    with SessionLocal() as s:
        d = bet_screens.to_draft(slip, s, datetime.utcnow())
    assert [(l.graded, l.player and l.player.player_id) for l in d.legs] == [(True, "LAL"), (True, "NY"), (True, "NYG"), (False, None)]
    assert d.legs[0].kind == "ml" and d.legs[0].player.opp == "SAC" and d.legs[3].text == "Toronto Raptors moneyline +120"
    assert any("15% boost" in n for n in d.notes)


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
