"""Test accounts for local development: signed-in features without the emailed code, in states worth looking at.

    python -m app.dev_users seed              # make (or rebuild) the accounts below from the current boards
    python -m app.dev_users cookie regular    # sign one in: prints a session cookie for the browser
    python -m app.dev_users list

Uses DATABASE_URL like the app (run it with the same DATABASE_URL as the server, e.g. postgresql://localhost/haber).
It refuses anything that isn't a database on this machine, and the prod tunnel's port, so it can't write to production.

The accounts (all @haber.test, a domain that can't receive mail):
  new      signed in, 21+ not confirmed yet: the age gate, nothing can be saved
  member   21+, nothing saved: every empty state (My bets, favorites, the slip)
  regular  21+, with preferences, favorites, a saved slip and tracked bets: open and settled, singles, parlays,
           a custom bet, promos (bonus bet, profit boost, no-sweat), an NBA bet when the NBA board has games
  admin    21+, the admin page, once ADMIN_EMAILS includes admin@haber.test on the server

Only a request from another machine is ever a non-admin by default; to see the site as these accounts do, start the
server with LOCAL_IS_ADMIN=0 (and ADMIN_EMAILS=admin@haber.test for the admin account). Re-run seed after refreshing
the database, so their bets and slip point at games on the new boards.
"""
import argparse
import os
import secrets
import sys
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlsplit

from . import config

DOMAIN = "haber.test"
ACCOUNTS = {
    "new": dict(name="New Visitor", age=False),
    "member": dict(name="Empty Member", age=True),
    "regular": dict(name="Regular Bettor", age=True),
    "admin": dict(name="Site Admin", age=True),
}
PROD_TUNNEL_PORT = 54329   # README: railway connect Postgres --tunnel-only --port 54329
LOCAL_HOSTS = {"", "localhost", "127.0.0.1", "::1"}


def email(key: str) -> str:
    return f"{key}@{DOMAIN}"


def local_only(url: str) -> None:
    """Stop unless the database is on this machine and isn't production reached through a tunnel."""
    if os.environ.get("RAILWAY_ENVIRONMENT_NAME"):
        sys.exit("Not on Railway: test accounts are for a laptop's database.")
    if url.startswith("sqlite"):
        return
    parts = urlsplit(url)
    if (parts.hostname or "") not in LOCAL_HOSTS or parts.port == PROD_TUNNEL_PORT or parts.path.strip("/") == "railway":
        sys.exit(f"Refusing {parts.hostname}:{parts.port}{parts.path}: test accounts only go in a local database.")


# app.db connects (and creates tables) as it's imported, so the check comes first when this runs as a command
if __name__ == "__main__":
    local_only(config.DATABASE_URL)

from sqlalchemy import delete, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from .auth import SESSION_DAYS, _hash  # noqa: E402
from .bets import _kickoff, _recent_runs, settle  # noqa: E402
from .db import (Bet, BetLeg, NbaOddsLine, NbaProjection, NbaRun, OddsLine, Projection, Run, SessionLocal, User,  # noqa: E402
                 UserSession, UserSlip)


# ---------- picking real props from the boards ----------
def _upcoming(db: Session, run_t, proj_t, now: datetime) -> list:
    """The latest run's projections whose game hasn't started, soonest first."""
    run = db.scalar(select(run_t).order_by(run_t.id.desc()).limit(1))
    if not run:
        return []
    rows = [(r, ko) for r in db.scalars(select(proj_t).where(proj_t.run_id == run.id))
            if not getattr(r, "thin", False) and (ko := _kickoff(run, r)) and ko > now]
    return sorted(rows, key=lambda x: (x[1], -x[0].mu))


def _finished(db: Session, run_t, proj_t, now: datetime) -> list:
    """Projections from recent runs whose game started at least 4 hours ago (graded or about to be), latest run first."""
    out, seen = [], set()
    for run in _recent_runs(db, run_t, now):
        for r in db.scalars(select(proj_t).where(proj_t.run_id == run.id)):
            ko = _kickoff(run, r)
            if ko and ko < now - timedelta(hours=4) and (r.game_id, r.player_id, r.kind) not in seen:
                seen.add((r.game_id, r.player_id, r.kind))
                out.append((r, ko))
    return sorted(out, key=lambda x: -x[0].mu)


def _quote(db: Session, odds_t, row, side: str = "Over") -> tuple[float, int, str]:
    """(line, American odds, book) from the latest quote for this prop, or a made-up -110 at the projection."""
    q = db.scalar(select(odds_t).where(odds_t.player == row.name, odds_t.market == row.kind, odds_t.game_id == row.game_id)
                  .order_by(odds_t.fetched_at.desc()).limit(1))
    price = q and (q.over_odds if side == "Over" else q.under_odds)
    if q and price:
        return q.line, int(price), q.book
    return max(0.5, round(row.mu) - 0.5), -110, "FanDuel"


def _distinct_players(rows: list, n: int, same_game: Optional[str] = None) -> list:
    picked, players = [], set()
    for r, ko in rows:
        if r.player_id in players or (same_game and r.game_id != same_game):
            continue
        players.add(r.player_id)
        picked.append((r, ko))
        if len(picked) == n:
            break
    return picked


def _leg(db: Session, odds_t, row, ko: datetime, side: str = "Over", result: Optional[str] = None) -> BetLeg:
    line, odds, _ = _quote(db, odds_t, row, side)
    leg = BetLeg(game_id=row.game_id, gameday=row.gameday, kickoff=ko, player_id=row.player_id, name=row.name, team=row.team,
                 opp=row.opp, kind=row.kind, side=side, line=line, odds=odds, alt=False, prob=0.55, ev=0.04, result=result)
    if result:
        leg.actual = line + (2 if (result == "win") == (side == "Over") else -2)
        leg.graded_at = ko + timedelta(hours=4)
    return leg


def _bet(user: User, league: str, book: str, kind: str, stake: float, odds: int, placed: datetime, legs: list, **kw) -> Bet:
    bet = Bet(user_id=user.id, league=league, book=book, kind=kind, stake=stake, odds=odds, status="open", placed_at=placed,
              tags=kw.pop("tags", []), **kw)
    bet.legs.extend(legs)
    if any(leg.result for leg in legs):
        settle(bet, max(leg.kickoff for leg in legs) + timedelta(hours=4))
    return bet


def seed_bets(db: Session, user: User, now: datetime) -> list[str]:
    """Tracked bets covering the My bets states, from props on the current boards. Returns what was made."""
    made = []
    nfl_up = _upcoming(db, Run, Projection, now)
    nfl_done = _finished(db, Run, Projection, now)
    nba_up = _upcoming(db, NbaRun, NbaProjection, now)

    if (one := _distinct_players(nfl_up, 1)):
        r, ko = one[0]
        leg = _leg(db, OddsLine, r, ko)
        db.add(_bet(user, "nfl", "FanDuel", "single", 25, leg.odds, now - timedelta(hours=2), [leg], tags=["Sunday"]))
        made.append("open NFL single")
    if nfl_up and (sgp := _distinct_players(nfl_up, 3, same_game=nfl_up[0][0].game_id)) and len(sgp) >= 2:
        legs = [_leg(db, OddsLine, r, ko, side=s) for (r, ko), s in zip(sgp, ("Over", "Under", "Over"))]
        db.add(_bet(user, "nfl", "DraftKings", "parlay", 10, +550, now - timedelta(hours=1), legs, tags=["SGP"], boost=30))
        made.append(f"open NFL same-game parlay ({len(legs)} legs, 30% boost)")
    if (one := _distinct_players(nba_up, 1)):
        r, ko = one[0]
        leg = _leg(db, NbaOddsLine, r, ko)
        db.add(_bet(user, "nba", "BetMGM", "single", 20, leg.odds, now - timedelta(minutes=30), [leg], no_sweat=20))
        made.append("open NBA single (no-sweat)")
    done = _distinct_players(nfl_done, 5)
    if len(done) >= 1:
        r, ko = done[0]
        leg = _leg(db, OddsLine, r, ko, result="win")
        db.add(_bet(user, "nfl", "Caesars", "single", 50, leg.odds, ko - timedelta(days=1), [leg], tags=["Sunday"]))
        made.append("won NFL single")
    if len(done) >= 3:
        legs = [_leg(db, OddsLine, r, ko, result=res) for (r, ko), res in zip(done[1:3], ("win", "loss"))]
        db.add(_bet(user, "nfl", "FanDuel", "parlay", 15, +260, legs[0].kickoff - timedelta(days=1), legs))
        made.append("lost NFL parlay")
    if len(done) >= 4:
        r, ko = done[3]
        leg = _leg(db, OddsLine, r, ko, result="win")
        db.add(_bet(user, "nfl", "bet365", "single", 10, leg.odds, ko - timedelta(days=2), [leg], bonus=True))
        made.append("won NFL bonus bet")
    if len(done) >= 5:
        r, ko = done[4]
        leg = _leg(db, OddsLine, r, ko, result="push")
        db.add(_bet(user, "nfl", "DraftKings", "single", 20, leg.odds, ko - timedelta(days=1), [leg]))
        made.append("pushed NFL single")
    text, starts = "Chiefs -3.5 vs. a long-named opponent in a very long custom bet description", now + timedelta(days=2)
    custom = Bet(user_id=user.id, league="oth", book="FanDuel", kind="custom", stake=30, odds=-115, status="open",
                 description=text, placed_at=now - timedelta(days=1), tags=["Spread"])
    # as bets._track_custom records one: a single leg for when its event starts
    custom.legs.append(BetLeg(game_id="custom", gameday=starts.date().isoformat(), kickoff=starts, player_id="", name=text[:80],
                              team="", opp="", kind="custom", side="", line=0.0, odds=-115))
    db.add(custom)
    made.append("open custom bet")
    return made


def seed_slip(db: Session, user: User, now: datetime) -> int:
    """A saved betslip of real props across two books, as the frontend's slip.ts records legs."""
    legs = []
    for i, (r, ko) in enumerate(_distinct_players(_upcoming(db, Run, Projection, now), 3)):
        line, odds, book = _quote(db, OddsLine, r)
        book = book if i < 2 else "DraftKings"
        legs.append(dict(id=f"{r.player_id}|{r.kind}|Over|{line}|{book}", playerId=r.player_id, name=r.name, team=r.team,
                         opp=r.opp, home=bool(r.home), kind=r.kind, side="Over", line=line, odds=odds, book=book, link=None,
                         eventLink=None, alt=False, prob=0.56, ev=0.05, gameId=r.game_id))
    db.add(UserSlip(user_id=user.id, legs=legs, stakes={"FanDuel": 10} if legs else {}, updated_at=now))
    return len(legs)


def favorites(db: Session, now: datetime) -> dict:
    fav = {}
    for league, run_t, proj_t in (("nfl", Run, Projection), ("nba", NbaRun, NbaProjection)):
        rows = _distinct_players(_upcoming(db, run_t, proj_t, now), 2)
        fav[league] = dict(teams=sorted({r.team for r, _ in rows}),
                           players=[dict(player_id=r.player_id, name=r.name, pos=r.pos, team=r.team, headshot=None) for r, _ in rows])
    return fav


# ---------- commands ----------
def seed(db: Session, now: Optional[datetime] = None, log=print) -> dict:
    """Make the accounts, or wipe and rebuild what's saved on them. Safe to re-run."""
    now = now or datetime.utcnow()
    users = {}
    for key, spec in ACCOUNTS.items():
        user = db.scalar(select(User).where(User.email == email(key)))
        if user:
            db.execute(delete(Bet).where(Bet.user_id == user.id))
            db.execute(delete(UserSlip).where(UserSlip.user_id == user.id))
        else:
            user = User(email=email(key), created_at=now - timedelta(days=30))
            db.add(user)
        user.name, user.prefs, user.favorites = spec["name"], None, None
        user.age_confirmed_at = now - timedelta(days=30) if spec["age"] else None
        db.flush()
        users[key] = user
    regular = users["regular"]
    regular.prefs = {"game_book": "DraftKings", "one_per_player": False, "detailed_cards": True}
    regular.favorites = favorites(db, now)
    made = seed_bets(db, regular, now)
    n_slip = seed_slip(db, regular, now)
    db.commit()
    log(f"Seeded {', '.join(email(k) for k in ACCOUNTS)}")
    log(f"  {email('regular')}: {len(made)} bets ({'; '.join(made)}), {n_slip} slip legs")
    if not any("open NFL" in m for m in made):
        log("  (no NFL games still to start on the latest board: refresh the database for open bets and a slip)")
    return dict(users={k: u.id for k, u in users.items()}, bets=made, slip=n_slip)


def make_cookie(db: Session, key: str, now: Optional[datetime] = None) -> str:
    """A new session for a test account; the returned token is the `sid` cookie's value."""
    user = db.scalar(select(User).where(User.email == email(key)))
    if not user:
        raise SystemExit(f"No {email(key)} yet: run `python -m app.dev_users seed` first.")
    now = now or datetime.utcnow()
    token = secrets.token_urlsafe(32)
    db.add(UserSession(id=_hash(token), user_id=user.id, created_at=now, expires_at=now + timedelta(days=SESSION_DAYS),
                       last_seen_at=now, user_agent="dev_users"))
    user.last_login_at = now
    db.commit()
    return token


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("seed", help="make or rebuild the test accounts")
    c = sub.add_parser("cookie", help="print a session cookie for one account")
    c.add_argument("account", choices=list(ACCOUNTS))
    sub.add_parser("list", help="show the test accounts")
    a = ap.parse_args(argv)
    local_only(config.DATABASE_URL)   # again, for a caller that imported this module
    with SessionLocal() as db:
        if a.cmd == "seed":
            seed(db)
        elif a.cmd == "cookie":
            token = make_cookie(db, a.account)
            print(token)
            # signing out first clears a real sign-in's cookie, which is httpOnly: the page's script can't replace it
            print(f"\n# in the browser's console on the app's page (any localhost port); it reloads signed in:\n"
                  f"await fetch('/api/auth/logout', {{method: 'POST'}}); "
                  f"document.cookie = 'sid={token}; path=/; max-age={SESSION_DAYS * 86400}'; location.reload()\n"
                  f"# or with curl: -b 'sid={token}'", file=sys.stderr)
        else:
            for key in ACCOUNTS:
                u = db.scalar(select(User).where(User.email == email(key)))
                bets = len(db.scalars(select(Bet.id).where(Bet.user_id == u.id)).all()) if u else 0
                print(f"{key:8} {email(key):20} {'present' if u else 'missing':8} {'21+' if u and u.age_confirmed_at else '   '} "
                      f"{bets} bets")


if __name__ == "__main__":
    main()
