"""Tracked bets (app/bets.py): settling singles and parlays, the API's rules, and grading from NFL stats and NBA game logs."""
import re
from datetime import date, datetime, timedelta

import pandas as pd
from sqlalchemy import delete
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import auth, bets, main
from app.nba import espn
from app.db import Bet, BetLeg, NbaProjection, NbaRun, Projection, Run, SessionLocal, User

SOON = (datetime.utcnow() + timedelta(days=3)).date().isoformat()
PAST = (datetime.utcnow() - timedelta(days=3)).date().isoformat()
NFL_GAME, NFL_OLD = f"2099_05_HOU_JAX", f"2099_04_HOU_DAL"
NBA_GAME = "2099_20991010_BOS_NYK"   # NBA ids carry the date, as espn.slate makes them


@pytest.fixture(scope="module", autouse=True)
def board():
    """A latest NFL run with one future game and one already played, and a latest NBA run with a future game."""
    with SessionLocal() as s:
        run = Run(season=2099, week=5, backtest={}, variance={},
                  teams={NFL_GAME: dict(home="JAX", away="HOU", gameday=SOON, gametime="13:00", margin=1.5, p_home=0.55)})
        s.add(run)
        s.flush()
        for gid, day, pid, name in ((NFL_GAME, SOON, "00-R1", "Rusher One"), (NFL_GAME, SOON, "00-W1", "Wideout One"),
                                    (NFL_OLD, PAST, "00-R1", "Rusher One")):
            s.add(Projection(run_id=run.id, player_id=pid, name=name, pos="RB", team="HOU", opp="JAX", home=False, kind="rush",
                             mu=70, sd=25, vol=15, eff=4.5, spread=0, game_id=gid, gameday=day, gametime="13:00", last5=[]))
        nrun = NbaRun(season=2100, slate=[SOON], backtest={}, variance={}, games=[{"game_id": NBA_GAME, "start": f"{SOON}T23:30:00Z"}],
                      teams={NBA_GAME: dict(home="NYK", away="BOS", gameday=SOON, gametime="19:30", margin=-2.0, p_home=0.43)})
        s.add(nrun)
        s.flush()
        s.add(NbaProjection(run_id=nrun.id, player_id="9001", name="Guard One", pos="G", team="BOS", opp="NYK", home=False, kind="pts",
                            mu=25, sd=6, vol=34, eff=0.7, spread=0, game_id=NBA_GAME, gameday=SOON, gametime="19:30", last5=[]))
        s.commit()
        ids = run.id, nrun.id
    yield
    # later test files read "the latest run": leave theirs as it was
    with SessionLocal() as s:
        for model, rid in zip((Run, NbaRun), ids):
            s.delete(s.get(model, rid))
        s.commit()


@pytest.fixture
def sent(monkeypatch):
    out = []
    monkeypatch.setattr(auth, "send_email", lambda to, subject, text, html: out.append(re.search(r"#t=([\w-]+)", text).group(1)))
    auth._hits.clear()
    return out


def member(sent, email, ip):
    """A remote visitor signed in as `email`, 21+ confirmed."""
    c = TestClient(main.app, client=(ip, 5000))
    c.post("/api/auth/email", json={"email": email})
    c.post("/api/auth/email/verify", json={"token": sent[-1]})
    c.post("/api/auth/age")
    return c


def leg(pid="00-R1", kind="rush", side="Over", line=69.5, odds=-110, game=NFL_GAME, **kw):
    return dict(game_id=game, player_id=pid, kind=kind, side=side, line=line, odds=odds, **kw)


# ---------- settling ----------
def make(odds=+250, stake=10.0, results=(None,), leg_odds=None):
    bet = Bet(stake=stake, odds=odds, kind="parlay" if len(results) > 1 else "single", status="open")
    for i, r in enumerate(results):
        bet.legs.append(BetLeg(result=r, odds=(leg_odds or [-110] * len(results))[i]))
    return bet


def test_settle_singles():
    for r, status, payout in (("win", "won", 20.0), ("loss", "lost", 0.0), ("push", "push", 10.0), ("void", "void", 10.0)):
        b = make(odds=+100, results=(r,))
        bets.settle(b)
        assert (b.status, b.payout, b.settled_by) == (status, payout, "grading")


def test_settle_parlays():
    lost_early = make(results=("loss", None))
    bets.settle(lost_early)
    assert lost_early.status == "lost"                       # a losing leg settles it before the rest are in
    waiting = make(results=("win", None))
    bets.settle(waiting)
    assert waiting.status == "open" and waiting.payout is None
    # a void leg drops out: +250 over two -110 legs, one void, pays on the other leg's price
    repriced = make(odds=+250, results=("win", "void"), leg_odds=[-110, -110])
    bets.settle(repriced)
    assert repriced.status == "won" and repriced.payout == pytest.approx(10 * 3.5 / (1 + 100 / 110), abs=0.01)
    all_out = make(results=("void", "push"))
    bets.settle(all_out)
    assert (all_out.status, all_out.payout) == ("push", 10.0)
    by_hand = make(results=("loss",))
    by_hand.settled_by, by_hand.status = "user", "won"
    bets.settle(by_hand)
    assert by_hand.status == "won"                          # settled by hand: grading leaves it alone


def test_settle_bonus_bets():
    """A bonus bet's stake never comes back: a win pays only the profit, anything else returns nothing."""
    for r, status, payout in (("win", "won", 15.0), ("loss", "lost", 0.0), ("push", "push", 0.0), ("void", "void", 0.0)):
        b = make(odds=+150, results=(r,))
        b.bonus = True
        bets.settle(b)
        assert (b.status, b.payout) == (status, payout)
    won = make(odds=+150, results=("win",))
    won.bonus = True
    bets.settle(won)
    won.settled_at = datetime.utcnow()
    s = bets.summarize([won])
    assert bets.cost(won) == 0 and (s.staked, s.bonus_staked, s.profit, s.roi) == (0, 10, 15.0, None)


def test_settle_profit_boosts_and_no_sweat_bets():
    """A profit boost multiplies the winnings (the stake comes back unboosted), dropped legs included; a lost no-sweat bet
    earns its refund back as a bonus bet, which isn't profit until it's bet."""
    b = make(odds=+150, results=("win",))
    b.boost = 50
    bets.settle(b)
    assert b.payout == 32.5                                   # $15 of winnings x 1.5, plus the $10 stake
    p = make(odds=+600, results=("win", "push"), leg_odds=(+100, +100))
    p.boost = 25
    bets.settle(p)
    assert p.payout == pytest.approx(10 + 10 * 2.5 * 1.25)    # 7.0 / 2.0 = 3.5 decimal, its $25 of winnings boosted
    for r, payout, back in (("loss", 0.0, 10.0), ("win", 25.0, 0.0), ("push", 10.0, 0.0)):
        n = make(odds=+150, results=(r,))
        n.no_sweat = 10.0
        bets.settle(n)
        n.settled_at = datetime.utcnow()
        assert (n.payout, bets.refund(n)) == (payout, back)
    lost = make(odds=+150, results=("loss",))
    lost.no_sweat, lost.stake = 25.0, 10.0
    bets.settle(lost)
    lost.settled_at = datetime.utcnow()
    s = bets.summarize([lost])
    assert bets.refund(lost) == 10.0 and (s.profit, s.staked, s.refunds) == (-10.0, 10.0, 10.0)   # never more than the stake


# ---------- the API ----------
def test_track_and_list(sent):
    with member(sent, "tracker@example.com", "198.51.100.1") as c:
        r = c.post("/api/me/bets", json={"book": "DraftKings", "kind": "parlay", "stake": 20, "odds": 264,
                                         "legs": [leg(), leg("00-W1", "rec", line=54.5, alt=True, prob=0.58, ev=0.04)]})
        assert r.status_code == 200, r.text
        b = r.json()
        assert b["league"] == "nfl" and b["status"] == "open" and not b["started"] and b["to_pay"] == pytest.approx(72.8)
        assert [lg["name"] for lg in b["legs"]] == ["Rusher One", "Wideout One"]   # names come from the board, not the client
        out = c.get("/api/me/bets").json()
        assert out["summary"]["open"] == 1 and out["summary"]["open_stake"] == 20 and out["books"] == ["DraftKings"]
        single = c.post("/api/me/bets", json={"book": "FanDuel", "kind": "single", "stake": 5, "legs": [leg(game=NBA_GAME, pid="9001", kind="pts", line=24.5)]})
        assert single.json()["league"] == "nba" and single.json()["odds"] == -110
        assert [x["book"] for x in c.get("/api/me/bets?league=nba").json()["bets"]] == ["FanDuel"]


def test_track_refuses_bad_bets(sent):
    with member(sent, "strict@example.com", "198.51.100.2") as c:
        post = lambda **body: c.post("/api/me/bets", json={"book": "FanDuel", "kind": "single", "stake": 10, **body})
        assert post(legs=[leg(pid="nobody")]).status_code == 422                           # not on the board
        assert post(legs=[leg(), leg("00-W1")]).status_code == 422                         # a single with two legs
        assert post(kind="parlay", odds=200, legs=[leg(), leg(game=NBA_GAME, pid="9001", kind="pts")]).status_code == 422  # two leagues
        assert post(kind="parlay", odds=200, legs=[leg(), leg(line=80.5)]).status_code == 422   # the same prop twice
        assert post(stake=0, legs=[leg()]).status_code == 422
        assert post(legs=[leg(odds=50)]).status_code == 422                                # not American odds
        assert c.post("/api/me/bets", headers={"Origin": "https://evil.example"},
                      json={"book": "FanDuel", "kind": "single", "stake": 10, "legs": [leg()]}).status_code == 403


def test_bonus_bets_count_profit_but_not_stake(sent):
    with member(sent, "bonus@example.com", "198.51.100.40") as c:
        cash = c.post("/api/me/bets", json={"book": "FanDuel", "kind": "single", "stake": 10, "legs": [leg(odds=100)]}).json()
        free = c.post("/api/me/bets", json={"book": "FanDuel", "kind": "single", "stake": 25, "bonus": True, "legs": [leg(odds=200)]}).json()
        assert free["bonus"] and free["to_pay"] == 50.0 and not cash["bonus"] and cash["to_pay"] == 20.0
        assert c.get("/api/me/bets").json()["summary"]["open_stake"] == 10   # only the bettor's own money is at stake
        c.patch(f"/api/me/bets/{cash['id']}", json={"status": "lost"})
        r = c.patch(f"/api/me/bets/{free['id']}", json={"status": "won"}).json()
        assert (r["payout"], r["profit"]) == (50.0, 50.0)
        s = c.get("/api/me/bets").json()["summary"]
        assert (s["staked"], s["bonus_staked"], s["profit"], s["roi"]) == (10, 25, 40.0, 4.0)
        # unticking it later makes it a cash bet again, settled the same way
        r = c.patch(f"/api/me/bets/{free['id']}", json={"bonus": False}).json()
        assert (r["bonus"], r["payout"], r["profit"]) == (False, 75.0, 50.0)
        lost = c.patch(f"/api/me/bets/{free['id']}", json={"bonus": True, "status": "lost"}).json()
        assert (lost["payout"], lost["profit"]) == (0.0, 0.0)
        assert "bonus_bet" in c.get("/api/me/bets.csv").text.splitlines()[0]


def test_profit_boosts_and_no_sweat_bets(sent):
    with member(sent, "promo@example.com", "198.51.100.41") as c:
        post = lambda **body: c.post("/api/me/bets", json={"book": "DraftKings", "kind": "single", "stake": 20, "legs": [leg(odds=100)], **body})
        boost = post(boost=50).json()
        assert (boost["boost"], boost["odds"], boost["to_pay"]) == (50, 100, 50.0)   # $20 of winnings, boosted to $30
        assert post(boost=0).json()["boost"] is None
        assert post(boost=501).status_code == 422
        assert post(boost=25, bonus=True).status_code == 422                         # one promo a bet
        assert post(no_sweat=10, boost=25).status_code == 422
        ns = post(no_sweat=50).json()
        assert ns["no_sweat"] == 20.0 and ns["refund"] == 0.0                        # refunded up to the stake
        r = c.patch(f"/api/me/bets/{ns['id']}", json={"status": "lost"}).json()
        assert (r["profit"], r["refund"]) == (-20.0, 20.0)
        r = c.patch(f"/api/me/bets/{ns['id']}", json={"stake": 15}).json()
        assert (r["no_sweat"], r["refund"]) == (15.0, 15.0)
        r = c.patch(f"/api/me/bets/{boost['id']}", json={"status": "won"}).json()
        assert (r["payout"], r["profit"]) == (50.0, 30.0)
        # switching promos: the old one is taken off in the same edit
        r = c.patch(f"/api/me/bets/{boost['id']}", json={"bonus": True, "boost": 0, "no_sweat": 0}).json()
        assert (r["bonus"], r["boost"], r["payout"], r["profit"]) == (True, None, 20.0, 20.0)   # a bonus bet: the winnings only, unboosted
        assert c.patch(f"/api/me/bets/{boost['id']}", json={"boost": 20}).status_code == 422
        s = c.get("/api/me/bets").json()["summary"]
        assert s["refunds"] == 15.0
        head, *rows = c.get("/api/me/bets.csv").text.splitlines()
        assert "profit_boost_pct" in head and "no_sweat_refund" in head
        custom = c.post("/api/me/bets", json={"book": "FanDuel", "kind": "custom", "stake": 10, "odds": 120, "description": "Chiefs -3.5",
                                              "starts_at": (datetime.utcnow() + timedelta(days=1)).isoformat(), "boost": 30}).json()
        assert custom["to_pay"] == pytest.approx(10 + 12 * 1.3)


def test_a_forgotten_bet_can_be_added_late_for_a_week(sent):
    """A game that started (NFL_OLD, 3 days ago): the bet is added, marked late, and grading treats it like any other.
    Older than LATE_DAYS, it's refused."""
    with member(sent, "forgot@example.com", "198.51.100.41") as c:
        r = c.post("/api/me/bets", json={"book": "DraftKings", "kind": "single", "stake": 10, "legs": [leg(game=NFL_OLD)]})
        assert r.status_code == 200, r.text
        b = r.json()
        assert b["late"] and b["started"] and b["legs"][0]["name"] == "Rusher One" and b["legs"][0]["gameday"] == PAST
        on_time = c.post("/api/me/bets", json={"book": "DraftKings", "kind": "single", "stake": 10, "legs": [leg()]}).json()
        assert not on_time["late"]
        assert "added_late" in c.get("/api/me/bets.csv").text.splitlines()[0]
        # the player search offers the started game too, flagged, after the ones still to start
        hits = c.get("/api/me/bet-players", params={"league": "nfl", "q": "Rusher"}).json()
        assert {(h["game_id"], h["started"]) for h in hits} >= {(NFL_GAME, False), (NFL_OLD, True)}
        # an other bet whose event started yesterday is late too; one from last month is refused
        past = (datetime.utcnow() - timedelta(days=1)).isoformat() + "Z"
        other = c.post("/api/me/bets", json={"book": "FanDuel", "kind": "custom", "description": "Chiefs -3.5", "starts_at": past,
                                             "odds": -110, "stake": 5}).json()
        assert other["late"]
        old = (datetime.utcnow() - timedelta(days=bets.LATE_DAYS + 1)).isoformat() + "Z"
        assert "too long ago" in c.post("/api/me/bets", json={"book": "FanDuel", "kind": "custom", "description": "Chiefs -3.5",
                                                              "starts_at": old, "odds": -110, "stake": 5}).json()["detail"]


def test_a_game_off_the_latest_board_is_found_on_the_last_run_that_had_it(sent):
    """Last night's game has gone from the latest NBA run: the run before, which had it, gives its players and tip-off."""
    yday = (datetime.utcnow() - timedelta(hours=20)).replace(microsecond=0)
    old_game = f"2099_{yday:%Y%m%d}_CHA_LAL"
    with SessionLocal() as s:
        older = NbaRun(season=2100, slate=[yday.date().isoformat()], backtest={}, variance={},
                       games=[{"game_id": old_game, "start": yday.isoformat() + "Z"}])
        s.add(older)
        s.flush()
        s.add(NbaProjection(run_id=older.id, player_id="7001", name="Luka Test", pos="G", team="LAL", opp="CHA", home=True, kind="pts",
                            mu=33, sd=8, vol=30, eff=1.1, spread=0, game_id=old_game, gameday=yday.date().isoformat(), gametime="23:00", last5=[]))
        newer = NbaRun(season=2100, slate=[SOON], backtest={}, variance={}, games=[{"game_id": NBA_GAME, "start": f"{SOON}T23:30:00Z"}])
        s.add(newer)
        s.flush()
        s.add(NbaProjection(run_id=newer.id, player_id="9001", name="Guard One", pos="G", team="BOS", opp="NYK", home=False, kind="pts",
                            mu=25, sd=6, vol=34, eff=0.7, spread=0, game_id=NBA_GAME, gameday=SOON, gametime="19:30", last5=[]))
        s.commit()
        ids = older.id, newer.id
    try:
        with member(sent, "lastnight@example.com", "198.51.100.42") as c:
            hit = c.get("/api/me/bet-players", params={"league": "nba", "q": "Luka Test"}).json()
            assert [(h["game_id"], h["started"], h["kinds"]) for h in hit] == [(old_game, True, ["pts"])]
            b = c.post("/api/me/bets", json={"book": "DraftKings", "kind": "single", "stake": 4.86,
                                             "legs": [leg(game=old_game, pid="7001", kind="pts", line=34.5, odds=-120)]}).json()
            assert b["late"] and b["league"] == "nba" and b["legs"][0]["team"] == "LAL" and b["legs"][0]["kickoff"].startswith(yday.isoformat()[:16])
    finally:
        with SessionLocal() as s:
            for rid in ids:
                s.delete(s.get(NbaRun, rid))
            # the late bet stays open on yesterday's date, which sent later grading tests to fetch its player's log
            for bet in s.scalars(select(Bet).join(BetLeg).where(BetLeg.game_id == old_game)).unique():
                s.delete(bet)
            s.commit()


def test_bets_need_a_confirmed_account(sent):
    with TestClient(main.app, client=("198.51.100.3", 5000)) as c:
        assert c.get("/api/me/bets").status_code == 401
        c.post("/api/auth/email", json={"email": "young@example.com"})
        c.post("/api/auth/email/verify", json={"token": sent[-1]})
        assert c.get("/api/me/bets").status_code == 403      # signed in, but not 21+ confirmed yet


def test_bets_are_private(sent):
    with member(sent, "owner1@example.com", "198.51.100.4") as a:
        bid = a.post("/api/me/bets", json={"book": "FanDuel", "kind": "single", "stake": 10, "legs": [leg()]}).json()["id"]
    with member(sent, "snoop@example.com", "198.51.100.5") as b:
        assert b.get("/api/me/bets").json()["bets"] == []
        assert b.patch(f"/api/me/bets/{bid}", json={"status": "won"}).status_code == 404
        assert b.delete(f"/api/me/bets/{bid}").status_code == 404


def test_edit_settle_by_hand_and_delete(sent):
    with member(sent, "editor@example.com", "198.51.100.6") as c:
        bid = c.post("/api/me/bets", json={"book": "FanDuel", "kind": "single", "stake": 10, "legs": [leg(odds=150)]}).json()["id"]
        r = c.patch(f"/api/me/bets/{bid}", json={"stake": 20, "odds": 120}).json()
        assert (r["stake"], r["odds"], r["legs"][0]["odds"], r["status"]) == (20, 120, 120, "open")
        r = c.patch(f"/api/me/bets/{bid}", json={"status": "won"}).json()
        assert (r["status"], r["payout"], r["profit"], r["settled_by"]) == ("won", 44.0, 24.0, "user")
        s = c.get("/api/me/bets").json()["summary"]
        assert (s["wins"], s["profit"], s["roi"]) == (1, 24.0, 1.2)
        r = c.patch(f"/api/me/bets/{bid}", json={"status": "open"}).json()
        assert (r["status"], r["settled_by"]) == ("open", None)   # handed back to grading
        assert c.delete(f"/api/me/bets/{bid}").status_code == 204
        assert c.get("/api/me/bets").json()["bets"] == []


def test_deleting_the_account_deletes_its_bets(sent):
    with member(sent, "leaver@example.com", "198.51.100.7") as c:
        bid = c.post("/api/me/bets", json={"book": "FanDuel", "kind": "single", "stake": 10, "legs": [leg()]}).json()["id"]
        c.delete("/api/auth/me")
    with SessionLocal() as s:
        assert s.get(Bet, bid) is None and not s.query(BetLeg).filter(BetLeg.bet_id == bid).count()


# ---------- grading ----------
def tracked(legs, kind="parlay", odds=300, stake=10.0):
    """A bet stored straight in the database (its games can be in the past, as when grading runs)."""
    with SessionLocal() as s:
        owner = s.query(User).filter(User.email == "grader@example.com").first() or User(email="grader@example.com")
        s.add(owner)
        s.flush()
        b = Bet(user_id=owner.id, league=bets.league_of(legs[0]["kind"], legs[0]["game_id"]), book="FanDuel", kind=kind, stake=stake, odds=odds, status="open")
        for lg in legs:
            b.legs.append(BetLeg(name="x", team="HOU", opp="JAX", kickoff=datetime(2000, 1, 1), alt=False, **lg))
        s.add(b)
        s.commit()
        return b.id


def test_grade_nfl_settles_main_and_alternate_lines_and_dnps():
    g = "2098_03_HOU_JAX"
    win_alt = tracked([dict(game_id=g, gameday="2098-09-20", player_id="00-A", kind="rush", side="Over", line=99.5, odds=+300),
                       dict(game_id=g, gameday="2098-09-20", player_id="00-B", kind="rec", side="Under", line=40.5, odds=-110)], odds=+700)
    dnp = tracked([dict(game_id=g, gameday="2098-09-20", player_id="00-GONE", kind="rush", side="Over", line=50.5, odds=-110)], kind="single", odds=-110)
    stats = pd.DataFrame([dict(player_id="00-A", season=2098, week=3, team="HOU", rushing_yards=120, receiving_yards=5),
                          dict(player_id="00-B", season=2098, week=3, team="JAX", rushing_yards=0, receiving_yards=31)])
    sched = pd.DataFrame([dict(game_id=g, home_score=24)])
    with SessionLocal() as s:
        assert bets.grade_nfl(s, stats, sched) == 3
        a, d = s.get(Bet, win_alt), s.get(Bet, dnp)
        assert [(lg.actual, lg.result) for lg in a.legs] == [(120.0, "win"), (31.0, "win")]
        assert (a.status, a.payout) == ("won", 80.0)
        assert (d.status, d.payout, d.legs[0].result) == ("void", 10.0, "void")   # didn't play: stake back, as the books do


def test_grade_nba_from_game_logs():
    day = (date.today() - timedelta(days=1)).isoformat()
    bid = tracked([dict(game_id="2098_11_BOS_NYK", gameday=day, player_id="77", kind="pra", side="Over", line=30.5, odds=-120)],
                  kind="single", odds=-120)
    logs = pd.DataFrame([dict(player_id="77", date=day, team="BOS", min=34, pts=20, reb=8, ast=6),
                         dict(player_id="88", date=day, team="NYK", min=30, pts=10, reb=3, ast=2)])
    with SessionLocal() as s:
        assert bets.grade_nba(s, logs, date.today(), fetch=lambda *a, **k: pd.DataFrame()) == 1
        b = s.get(Bet, bid)
        assert (b.legs[0].actual, b.status) == (34.0, "won") and b.payout == pytest.approx(10 * (1 + 100 / 120), abs=0.01)


def test_a_player_left_out_of_the_final_box_score_is_void_straight_away():
    """Inactive players aren't in ESPN's box score at all, and the game logs can lag a day: the final box score settles it."""
    today = espn.today_et()
    day = (today - timedelta(days=1)).isoformat()
    game = "2098_20981103_BOS_NYK"
    sat = tracked([dict(game_id=game, gameday=day, player_id="901", kind="pts", side="Over", line=20.5, odds=-110)], kind="single", odds=-110)
    lagging = tracked([dict(game_id=game, gameday=day, player_id="902", kind="pts", side="Over", line=20.5, odds=-110)], kind="single", odds=-110)
    logs = pd.DataFrame([dict(player_id="88", event_id="e3", season=2099, date=day, team="BOS", opp="NYK", home=False, home_score=101,
                              away_score=99, min=30, pts=10, reb=3, ast=2),
                         dict(player_id="89", event_id="e3", season=2099, date=day, team="NYK", opp="BOS", home=True, home_score=101,
                              away_score=99, min=30, pts=12, reb=3, ast=2)])
    reads = []
    box = lambda event: (reads.append(event), {"88", "89", "902"})[1]   # 902 played; his log just hasn't arrived
    with SessionLocal() as s:
        bets.grade_nba(s, logs, today, fetch=lambda *a, **k: pd.DataFrame(), box_of=box)
        assert (s.get(Bet, sat).legs[0].result, s.get(Bet, sat).status) == ("void", "void")
        assert s.get(Bet, lagging).status == "open"
    assert reads == ["e3"]   # one box score read for the game
    with SessionLocal() as s:   # no final box score yet: wait, as before
        bets.grade_nba(s, logs, today, fetch=lambda *a, **k: pd.DataFrame(), box_of=lambda e: None)
        assert s.get(Bet, lagging).status == "open"
        for bid in (sat, lagging):   # an open leg from "yesterday" would send later tests' grading off to fetch him
            s.execute(delete(BetLeg).where(BetLeg.bet_id == bid))
            s.delete(s.get(Bet, bid))
        s.commit()


def test_a_voided_legs_missing_price_comes_from_the_books_quote_or_the_bettor_enters_the_payout(sent):
    """A parlay pays on its other legs when one voids, dividing out the voided leg's price. A slip rarely shows each leg's
    price: it's taken from the book's stored quote for that line, and when there isn't one the bettor enters the payout."""
    from app.db import NbaAltLine
    ko = datetime.utcnow() - timedelta(hours=10)
    def parlay(names):
        bid = tracked([dict(game_id="2098_20981104_BOS_NYK", gameday="2098-11-04", player_id=f"p{i}", kind="threes", side="Over", line=1.5,
                            odds=-150 if i == 0 else 0, result="win" if i == 0 else "void") for i, _ in enumerate(names)],
                      kind="parlay", odds=300)
        with SessionLocal() as s:
            for lg, n in zip(s.get(Bet, bid).legs, names):
                lg.name, lg.kickoff = n, ko
            s.commit()
        return bid
    priced, unknown = parlay(["Cedric Coward", "Jayson Tatum"]), parlay(["Cedric Coward", "Nobody Quoted"])
    with SessionLocal() as s:
        q = NbaAltLine(fetched_at=ko - timedelta(hours=2), game_id="2098_20981104_BOS_NYK", player="Jayson Tatum", market="threes", line=1.5,
                       over_odds=120.0, under_odds=None, book="FanDuel")
        late = NbaAltLine(fetched_at=ko + timedelta(hours=1), game_id="2098_20981104_BOS_NYK", player="Jayson Tatum", market="threes", line=1.5,
                          over_odds=900.0, under_odds=None, book="FanDuel")   # after tip-off: not the price taken
        s.add_all([q, late])
        s.commit()
        for bid in (priced, unknown):
            b = s.get(Bet, bid)
            bets._settle_touched(s, list(b.legs), datetime.utcnow())
        s.commit()
        p, u = s.get(Bet, priced), s.get(Bet, unknown)
        assert p.legs[1].odds == 120 and p.status == "won" and p.payout == pytest.approx(10 * 4.0 / 2.2, abs=0.01)
        assert u.status == "open" and bets.needs_payout(u)
        s.delete(q), s.delete(late)
        s.commit()
    with member(sent, "voided@example.com", "198.51.100.44") as c:
        with SessionLocal() as s:   # hand the stuck bet to this member to settle it through the API
            s.get(Bet, unknown).user_id = s.query(User).filter(User.email == "voided@example.com").one().id
            s.commit()
        out = next(b for b in c.get("/api/me/bets").json()["bets"] if b["id"] == unknown)
        assert out["needs_payout"] and out["status"] == "open"
        r = c.patch(f"/api/me/bets/{unknown}", json={"status": "won", "payout": 16.5}).json()
        assert (r["status"], r["payout"], r["profit"], r["settled_by"], r["needs_payout"]) == ("won", 16.5, 6.5, "user", False)
        assert c.patch(f"/api/me/bets/{unknown}", json={"status": "won", "payout": -1}).status_code == 422


def test_a_leg_the_book_voided_though_he_played_drops_out_and_can_be_handed_back(sent):
    """Injury protection: the player got hurt and the book voided his leg, but he has a stat line, so grading lost the
    parlay. Marking the leg voided by the book pays the parlay on the rest; unmarking grades it from that line again."""
    g = "2098_04_HOU_JAX"
    bid = tracked([dict(game_id=g, gameday="2098-09-27", player_id="00-A", kind="rush", side="Over", line=59.5, odds=-110),
                   dict(game_id=g, gameday="2098-09-27", player_id="00-HURT", kind="rush", side="Over", line=49.5, odds=+100),
                   dict(game_id=g, gameday="2098-09-27", player_id="00-B", kind="rec", side="Under", line=40.5, odds=-110)], odds=+500)
    single = tracked([dict(game_id=g, gameday="2098-09-27", player_id="00-A", kind="rush", side="Over", line=59.5, odds=-110)], kind="single", odds=-110)
    stats = pd.DataFrame([dict(player_id="00-A", season=2098, week=4, team="HOU", rushing_yards=80, receiving_yards=0),
                          dict(player_id="00-HURT", season=2098, week=4, team="HOU", rushing_yards=12, receiving_yards=0),
                          dict(player_id="00-B", season=2098, week=4, team="JAX", rushing_yards=0, receiving_yards=20)])
    with SessionLocal() as s:
        bets.grade_nfl(s, stats, pd.DataFrame([dict(game_id=g, home_score=17)]))
        assert s.get(Bet, bid).status == "lost"
    with member(sent, "protected@example.com", "198.51.100.45") as c:
        with SessionLocal() as s:
            uid = s.query(User).filter(User.email == "protected@example.com").one().id
            for b in (bid, single):
                s.get(Bet, b).user_id = uid
            hurt, solo = s.get(Bet, bid).legs[1].id, s.get(Bet, single).legs[0].id
            s.commit()
        r = c.patch(f"/api/me/bets/{bid}/legs/{hurt}", json={"book_void": True}).json()
        out = r["legs"][1]
        assert (out["result"], out["book_void"], out["actual"]) == ("void", True, 12.0)   # his stat line stays
        assert (r["status"], r["settled_by"]) == ("won", "grading")
        assert r["payout"] == pytest.approx(10 * 6.0 / 2.0, abs=0.01)   # the +100 leg divided out of +500
        with SessionLocal() as s:   # the next grading run leaves it alone
            assert bets.grade_nfl(s, stats, pd.DataFrame([dict(game_id=g, home_score=17)])) == 0
        r = c.patch(f"/api/me/bets/{bid}/legs/{hurt}", json={"book_void": False}).json()
        assert (r["legs"][1]["result"], r["legs"][1]["book_void"], r["status"], r["payout"]) == ("loss", False, "lost", 0.0)
        # settled by hand with the book's payout, then the void undone: that payout no longer applies, so grading takes it back
        c.patch(f"/api/me/bets/{bid}/legs/{hurt}", json={"book_void": True})
        assert c.patch(f"/api/me/bets/{bid}", json={"status": "won", "payout": 29.5}).json()["settled_by"] == "user"
        r = c.patch(f"/api/me/bets/{bid}/legs/{hurt}", json={"book_void": False}).json()
        assert (r["status"], r["payout"], r["settled_by"]) == ("lost", 0.0, "grading")
        assert c.patch(f"/api/me/bets/{single}/legs/{solo}", json={"book_void": True}).status_code == 422   # a single: mark the bet void
        assert c.patch(f"/api/me/bets/{bid}/legs/{solo}", json={"book_void": True}).status_code == 404     # not this bet's leg
        future = c.post("/api/me/bets", json={"book": "FanDuel", "kind": "parlay", "stake": 10, "odds": 260,
                                               "legs": [leg(), leg(pid="00-W1")]}).json()
        assert c.patch(f"/api/me/bets/{future['id']}/legs/{future['legs'][0]['id']}", json={"book_void": True}).status_code == 422
    with member(sent, "snoop@example.com", "198.51.100.46") as c:
        assert c.patch(f"/api/me/bets/{bid}/legs/{hurt}", json={"book_void": True}).status_code == 404


def test_moneylines_are_tracked_with_props_and_graded_from_the_final_score(sent):
    with member(sent, "mlfan@example.com", "198.51.100.30") as c:
        ml = lambda team, game=NFL_GAME, **kw: dict(game_id=game, player_id=team, kind="ml", side="Over", line=0, odds=-130, **kw)
        r = c.post("/api/me/bets", json={"book": "DraftKings", "kind": "parlay", "stake": 10, "odds": 300, "legs": [ml("HOU", prob=0.6), leg()]})
        assert r.status_code == 200, r.text
        b = r.json()
        assert b["league"] == "nfl" and [(x["name"], x["team"], x["opp"], x["kind"]) for x in b["legs"]][0] == ("Houston Texans", "HOU", "JAX", "ml")
        nba = c.post("/api/me/bets", json={"book": "FanDuel", "kind": "single", "stake": 5, "legs": [ml("NYK", NBA_GAME)]}).json()
        assert nba["league"] == "nba" and nba["legs"][0]["opp"] == "BOS"
        both = c.post("/api/me/bets", json={"book": "DraftKings", "kind": "parlay", "stake": 10, "odds": 300, "legs": [ml("HOU"), ml("JAX")]})
        assert both.status_code == 422 and "Both teams" in both.json()["detail"]
        assert c.post("/api/me/bets", json={"book": "DraftKings", "kind": "single", "stake": 10, "legs": [ml("DAL")]}).status_code == 422
        mixed = c.post("/api/me/bets", json={"book": "DraftKings", "kind": "parlay", "stake": 10, "odds": 300, "legs": [ml("NYK", NBA_GAME), leg()]})
        assert mixed.status_code == 422   # an NBA moneyline and an NFL prop

    g = "2098_04_HOU_JAX"
    won = tracked([dict(game_id=g, gameday="2098-09-27", player_id="HOU", kind="ml", side="Over", line=0.0, odds=+120)], kind="single", odds=120)
    lost = tracked([dict(game_id=g, gameday="2098-09-27", player_id="JAX", kind="ml", side="Over", line=0.0, odds=-140)], kind="single", odds=-140)
    stats = pd.DataFrame([dict(player_id="00-Z", season=2098, week=1, team="KC", rushing_yards=1, receiving_yards=0)])
    sched = pd.DataFrame([dict(game_id=g, home_score=17, away_score=24)])
    with SessionLocal() as s:
        assert bets.grade_nfl(s, stats, sched) == 2   # no box score needed: the final score settles it
        assert (s.get(Bet, won).status, s.get(Bet, won).legs[0].actual) == ("won", 7.0)
        assert (s.get(Bet, lost).status, s.get(Bet, lost).legs[0].actual) == ("lost", -7.0)

    # the NBA's day is Eastern, as grading runs it (espn.today_et): the runner's date.today() is a day ahead from 8 PM ET in
    # UTC, which made other tests' open props from "today" look like yesterday's and sent this one to fetch them
    today = espn.today_et()
    day = (today - timedelta(days=1)).isoformat()
    nba = tracked([dict(game_id="2098_20981101_BOS_NYK", gameday=day, player_id="NYK", kind="ml", side="Over", line=0.0, odds=-110)],
                  kind="single", odds=-110)
    logs = pd.DataFrame([dict(player_id="77", event_id="e1", season=2099, date=day, team="BOS", opp="NYK", home=False, home_score=101,
                              away_score=99, min=34, pts=20, reb=8, ast=6)])
    with SessionLocal() as s:
        assert bets.grade_nba(s, logs, today, fetch=lambda *a, **k: pytest.fail("a team isn't a player to fetch")) >= 1
        assert (s.get(Bet, nba).status, s.get(Bet, nba).legs[0].actual) == ("won", 2.0)


def test_an_early_win_moneyline_wins_once_its_team_led_by_enough(sent):
    """FanDuel's early-win token: the moneyline pays as a win once the team leads by the token's points, even if it loses."""
    with member(sent, "earlywin@example.com", "198.51.100.43") as c:
        ml = dict(game_id=NBA_GAME, player_id="NYK", kind="ml", side="Over", line=0, odds=180)
        b = c.post("/api/me/bets", json={"book": "FanDuel", "kind": "single", "stake": 10, "early_win": 10, "legs": [ml]}).json()
        assert b["early_win"] == 10 and b["to_pay"] == 28.0   # it pays the moneyline's price, early or not
        prop = c.post("/api/me/bets", json={"book": "FanDuel", "kind": "single", "stake": 10, "early_win": 10, "legs": [leg()]})
        assert prop.status_code == 422 and "single moneyline" in prop.json()["detail"]
        two = c.post("/api/me/bets", json={"book": "FanDuel", "kind": "single", "stake": 10, "early_win": 10, "boost": 25, "legs": [ml]})
        assert two.status_code == 422 and "one promo" in two.json()["detail"]
        assert c.post("/api/me/bets", json={"book": "FanDuel", "kind": "single", "stake": 10, "early_win": 99, "legs": [ml]}).status_code == 422
        assert c.patch(f"/api/me/bets/{b['id']}", json={"early_win": 0}).json()["early_win"] is None

    today = espn.today_et()
    day = (today - timedelta(days=1)).isoformat()
    game = "2098_20981102_BOS_NYK"
    def bet(team, early, odds=180):
        bid = tracked([dict(game_id=game, gameday=day, player_id=team, kind="ml", side="Over", line=0.0, odds=odds)], kind="single", odds=odds)
        with SessionLocal() as s:
            s.get(Bet, bid).early_win = early
            s.commit()
        return bid
    led_and_lost = bet("BOS", 10)    # away team: up 12 at one point, lost by 2
    short = bet("BOS", 15)           # needed 15, led by 12: a plain loss
    won = bet("NYK", 10, -220)       # won outright: no lead needed
    logs = pd.DataFrame([dict(player_id="78", event_id="e2", season=2099, date=day, team="BOS", opp="NYK", home=False, home_score=101,
                              away_score=99, min=34, pts=20, reb=8, ast=6)])
    asked = []
    lead_of = lambda event: (asked.append(event), (6.0, 12.0))[1]   # (home's largest lead, away's)
    with SessionLocal() as s:
        bets.grade_nba(s, logs, today, fetch=lambda *a, **k: pytest.fail("no players to fetch"), lead_of=lead_of)
        early, plain, outright = s.get(Bet, led_and_lost), s.get(Bet, short), s.get(Bet, won)
        assert (early.status, early.payout, early.legs[0].actual, early.legs[0].lead) == ("won", 28.0, -2.0, 12.0)
        assert (plain.status, plain.legs[0].lead) == ("lost", 12.0)
        assert (outright.status, outright.legs[0].lead) == ("won", None)
    assert asked == ["e2", "e2"]   # only the bets that didn't win outright ask for the lead

    # ESPN without the lead yet: it waits rather than calling it a loss
    waiting = bet("BOS", 10)
    with SessionLocal() as s:
        bets.grade_nba(s, logs, today, fetch=lambda *a, **k: pytest.fail("no players to fetch"), lead_of=lambda e: (None, None))
        assert s.get(Bet, waiting).status == "open"

    # NFL: the play-by-play's largest lead
    g = "2098_05_HOU_JAX"
    nfl = tracked([dict(game_id=g, gameday="2098-10-04", player_id="HOU", kind="ml", side="Over", line=0.0, odds=150)], kind="single", odds=150)
    with SessionLocal() as s:
        s.get(Bet, nfl).early_win = 17
        s.commit()
        stats = pd.DataFrame([dict(player_id="00-Z", season=2098, week=1, team="KC", rushing_yards=1, receiving_yards=0)])
        bets.grade_nfl(s, stats, pd.DataFrame([dict(game_id=g, home_score=24, away_score=20)]),
                       leads=pd.DataFrame([dict(game_id=g, home_lead=3.0, away_lead=17.0)]))
        assert (s.get(Bet, nfl).status, s.get(Bet, nfl).legs[0].lead) == ("won", 17.0)   # HOU is away: up 17, lost by 4


def test_a_boost_capped_at_part_of_the_stake_lifts_only_that_part(sent):
    """DraftKings' "20% SGP Boost, boosted up to max $10 wager" on a $15 stake at +445: $10 returns $10 + $44.50 x 1.2 =
    $63.40 and the other $5 returns $27.25, so it pays $90.65 (a 20% boost on all of it would pay $95.10)."""
    with member(sent, "capped@example.com", "198.51.100.45") as c:
        post = lambda **kw: c.post("/api/me/bets", json={"book": "DraftKings", "kind": "parlay", "stake": 15, "odds": 445,
                                                         "legs": [leg(), leg("00-W1", "rec", line=54.5)], **kw})
        b = post(boost=20, boost_cap=10).json()
        assert (b["boost"], b["boost_cap"], b["to_pay"]) == (20, 10, 90.65)
        assert post(boost=20).json()["to_pay"] == 95.1                         # uncapped: all $15 boosted
        assert post(boost=20, boost_cap=50).json()["to_pay"] == 95.1           # a cap above the stake covers all of it
        assert post(boost=20, boost_cap=-1).status_code == 422
        r = c.patch(f"/api/me/bets/{b['id']}", json={"status": "won"}).json()
        assert (r["payout"], r["profit"]) == (90.65, 75.65)
        r = c.patch(f"/api/me/bets/{b['id']}", json={"boost_cap": 0}).json()   # the cap off: the whole stake boosted again
        assert (r["boost_cap"], r["payout"]) == (None, 95.1)
        r = c.patch(f"/api/me/bets/{b['id']}", json={"boost_cap": 10, "boost": 0}).json()   # no boost, no cap
        assert (r["boost"], r["boost_cap"]) == (None, None)
        assert "boost_max_wager" in c.get("/api/me/bets.csv").text.splitlines()[0]


def test_tags_and_filters(sent):
    with member(sent, "organizer@example.com", "198.51.100.8") as c:
        post = lambda book, legs, **kw: c.post("/api/me/bets", json={"book": book, "kind": "single", "stake": 10, "legs": legs, **kw}).json()
        a = post("DraftKings", [leg()], tags=[" SGP ", "sunday", "sgp", "x" * 40])
        assert a["tags"] == ["SGP", "sunday", "x" * 24]          # trimmed, de-duplicated ignoring case, capped in length
        b = post("FanDuel", [leg(game=NBA_GAME, pid="9001", kind="pts", line=24.5)], tags=["Tailing"])
        out = c.get("/api/me/bets").json()
        assert out["tags"] == ["SGP", "sunday", "Tailing", "x" * 24] and out["books"] == ["DraftKings", "FanDuel"]
        assert [x["id"] for x in c.get("/api/me/bets?tag=sgp").json()["bets"]] == [a["id"]]
        assert [x["id"] for x in c.get("/api/me/bets?league=nba").json()["bets"]] == [b["id"]]
        assert [x["id"] for x in c.get("/api/me/bets?book=DraftKings").json()["bets"]] == [a["id"]]
        r = c.patch(f"/api/me/bets/{b['id']}", json={"tags": ["Tailing", "Late"]}).json()
        assert r["tags"] == ["Tailing", "Late"]
        # an old bet drops out of the 1 and 3 month views but stays in 12 months and all time
        with SessionLocal() as s:
            s.get(Bet, a["id"]).placed_at = datetime.utcnow() - timedelta(days=120)
            s.commit()
        count = lambda q: len(c.get(f"/api/me/bets{q}").json()["bets"])
        assert (count("?months=1"), count("?months=3"), count("?months=12"), count("")) == (1, 1, 2, 2)


def test_months_ago_is_calendar_months():
    assert bets.months_ago(datetime(2026, 3, 31, 12), 1) == datetime(2026, 2, 28, 12)
    assert bets.months_ago(datetime(2026, 1, 15), 3) == datetime(2025, 10, 15)
    assert bets.months_ago(datetime(2026, 11, 1), 12) == datetime(2025, 11, 1)


def test_closing_line_value_and_csv_export(sent):
    with member(sent, "clvfan@example.com", "198.51.100.9") as c:
        good = c.post("/api/me/bets", json={"book": "DraftKings", "kind": "single", "stake": 10, "legs": [leg(odds=+120)], "tags": ["Sunday"]}).json()
        bad = c.post("/api/me/bets", json={"book": "FanDuel", "kind": "parlay", "stake": 5, "odds": 250,
                                           "legs": [leg(), leg("00-W1", "rec", line=54.5)]}).json()
        assert c.get("/api/me/bets").json()["summary"]["clv_n"] == 0          # nothing has closed yet
        with SessionLocal() as s:
            s.get(Bet, good["id"]).legs[0].close_p = 0.50                    # +120 needs 45.5%: beat the close
            for lg, p in zip(s.get(Bet, bad["id"]).legs, (0.45, 0.50)):       # 0.225 x 3.5 = 0.79: didn't
                lg.close_p = p
            s.commit()
        out = c.get("/api/me/bets").json()
        by_id = {b["id"]: b for b in out["bets"]}
        assert by_id[good["id"]]["ev_close"] == pytest.approx(0.10) and by_id[good["id"]]["legs"][0]["ev_close"] == pytest.approx(0.10)
        assert by_id[bad["id"]]["ev_close"] == pytest.approx(0.45 * 0.5 * 3.5 - 1)
        s_ = out["summary"]
        assert (s_["clv_n"], s_["beat_close"]) == (2, 0.5) and s_["ev_close"] == pytest.approx((0.10 + 0.45 * 0.5 * 3.5 - 1) / 2, abs=1e-4)

        r = c.get("/api/me/bets.csv")
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv") and "attachment" in r.headers["content-disposition"]
        rows = [line.split(",") for line in r.text.strip().splitlines()]
        assert rows[0][:3] == ["bet_id", "placed_at", "league"] and len(rows) == 1 + 3      # one row per leg
        assert [int(x[0]) for x in rows[1:]] == [good["id"], bad["id"], bad["id"]]           # oldest bet first
        assert rows[1][11] == "Sunday" and rows[1][-1] == "0.5"
        assert len(c.get("/api/me/bets.csv?book=DraftKings").text.strip().splitlines()) == 2  # the view's filters apply
    with TestClient(main.app, client=("198.51.100.10", 5000)) as anon:
        assert anon.get("/api/me/bets.csv").status_code == 401


# ---------- bets placed elsewhere ----------
def test_finding_a_player_to_add_a_prop_placed_elsewhere(sent):
    with member(sent, "elsewhere@example.com", "198.51.100.11") as c:
        hits = c.get("/api/me/bet-players?league=nfl&q=wideout").json()
        assert [(h["name"], h["game_id"], h["kinds"]) for h in hits] == [("Wideout One", NFL_GAME, ["rush"])]
        # the started game (NFL_OLD, 3 days ago) shows too, flagged: a bet the bettor forgot can be added late
        assert sorted((h["game_id"], h["started"]) for h in c.get("/api/me/bet-players?league=nfl&q=rusher").json()) == [
            (NFL_OLD, True), (NFL_GAME, False)]
        assert c.get("/api/me/bet-players?league=nba&q=guard").json()[0]["player_id"] == "9001"
        assert c.get("/api/me/bet-players?league=nfl&q=").json() == []
    with TestClient(main.app, client=("198.51.100.12", 5000)) as anon:
        assert anon.get("/api/me/bet-players?league=nfl&q=r").status_code == 401


def test_custom_bets_are_tracked_before_they_start_and_settled_by_hand(sent):
    from app import market_fit
    soon = (datetime.utcnow() + timedelta(hours=5)).isoformat() + "Z"
    with member(sent, "custom@example.com", "198.51.100.13") as c:
        post = lambda **kw: c.post("/api/me/bets", json={"book": "Hard Rock", "kind": "custom", "stake": 25, "odds": -110,
                                                         "description": "Chiefs -3.5 vs Bills", "starts_at": soon, "league": "nfl", **kw})
        b = post(tags=["Spreads"]).json()
        assert (b["kind"], b["league"], b["description"], b["status"], b["book"]) == ("custom", "nfl", "Chiefs -3.5 vs Bills", "open", "Hard Rock")
        assert b["legs"][0]["kind"] == "custom" and b["to_pay"] == pytest.approx(25 + 25 * 100 / 110, abs=0.01)
        assert post(league="oth", description="Arsenal to win").json()["league"] == "oth"
        # a real description and real odds; one that started an hour ago is added late
        past = (datetime.utcnow() - timedelta(hours=1)).isoformat() + "Z"
        assert post(starts_at=past).json()["late"] and not b["late"]
        assert post(description="x").status_code == 422 and post(odds=50).status_code == 422 and post(starts_at="soon").status_code == 422
        # nothing grades it or prices its close: it stays open until it's marked
        with SessionLocal() as s:
            bet = s.get(Bet, b["id"])
            bet.legs[0].kickoff = datetime.utcnow() - timedelta(hours=1)
            s.commit()
            assert bets.grade_nfl(s, pd.DataFrame([dict(player_id="x", season=2099, week=5, team="HOU", rushing_yards=1)]),
                                  pd.DataFrame([dict(game_id=NFL_GAME, home_score=1)])) == 0
            assert market_fit.attach_bet_closing(s, "nfl") == 0
            assert s.get(Bet, b["id"]).status == "open"
        r = c.patch(f"/api/me/bets/{b['id']}", json={"status": "won"}).json()
        assert (r["status"], r["profit"]) == ("won", pytest.approx(22.73, abs=0.01))
        csv_rows = c.get("/api/me/bets.csv?tag=Spreads").text.strip().splitlines()
        assert len(csv_rows) == 2 and "Chiefs -3.5 vs Bills" in csv_rows[1]
