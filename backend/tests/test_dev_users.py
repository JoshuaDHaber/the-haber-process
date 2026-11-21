"""Local test accounts (app/dev_users.py) and LOCAL_IS_ADMIN=0, which shows a laptop the site as a non-admin sees it."""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import dev_users, main
from app.db import Bet, NbaProjection, NbaRun, Projection, Run, SessionLocal, User, UserSlip

NOW = datetime.utcnow()
SOON, PAST = (NOW + timedelta(days=2)).date().isoformat(), (NOW - timedelta(days=2)).date().isoformat()


@pytest.fixture(scope="module", autouse=True)
def board():
    """A latest NFL run with a game to come and one played (on the same run, as the latest run keeps both), and an NBA game."""
    with SessionLocal() as s:
        run = Run(season=2098, week=3, backtest={}, variance={}, teams={})
        s.add(run)
        s.flush()
        for i in range(6):
            for gid, day in (("2098_03_KC_BUF", SOON), ("2098_02_KC_DEN", PAST)):
                s.add(Projection(run_id=run.id, player_id=f"00-D{i}", name=f"Dev Player {i}", pos="WR", team="KC", opp="BUF",
                                 home=True, kind="recy", mu=60 + i, sd=20, vol=6, eff=10, spread=0, game_id=gid, gameday=day,
                                 gametime="13:00", last5=[]))
        nrun = NbaRun(season=2098, slate=[SOON], backtest={}, variance={},
                      games=[{"game_id": "2098_X_BOS_NYK", "start": f"{SOON}T23:30:00Z"}], teams={})
        s.add(nrun)
        s.flush()
        s.add(NbaProjection(run_id=nrun.id, player_id="8001", name="Dev Guard", pos="G", team="BOS", opp="NYK", home=False,
                            kind="pts", mu=22, sd=6, vol=30, eff=0.7, spread=0, game_id="2098_X_BOS_NYK", gameday=SOON,
                            gametime="18:30", last5=[]))
        s.commit()
        ids = run.id, nrun.id
    yield
    with SessionLocal() as s:
        for model, rid in zip((Run, NbaRun), ids):
            s.delete(s.get(model, rid))
        for u in s.scalars(select(User).where(User.email.like(f"%@{dev_users.DOMAIN}"))):
            s.delete(u)
        s.commit()


def test_seed_makes_each_account_in_its_state_and_reruns_cleanly():
    with SessionLocal() as s:
        first = dev_users.seed(s, log=lambda *_: None)
        again = dev_users.seed(s, log=lambda *_: None)
        assert first["users"] == again["users"]   # the same accounts, rebuilt
        users = {k: s.get(User, uid) for k, uid in again["users"].items()}
        assert users["new"].age_confirmed_at is None
        assert all(users[k].age_confirmed_at for k in ("member", "regular", "admin"))
        assert not s.scalars(select(Bet).where(Bet.user_id == users["member"].id)).all()
        bets = s.scalars(select(Bet).where(Bet.user_id == users["regular"].id)).all()
        assert len(bets) == len(again["bets"]) == 8   # no doubles from the second run
        assert {b.status for b in bets} == {"open", "won", "lost", "push"}
        assert {b.league for b in bets} == {"nfl", "nba", "oth"}
        assert any(b.bonus for b in bets) and any(b.boost for b in bets) and any(b.no_sweat for b in bets)
        slip = s.get(UserSlip, users["regular"].id)
        assert len(slip.legs) == 3 and {leg["book"] for leg in slip.legs} >= {"DraftKings"}
        assert users["regular"].favorites["nfl"]["players"] and users["regular"].favorites["nba"]["teams"] == ["BOS"]


def test_a_printed_cookie_signs_the_account_in():
    with SessionLocal() as s:
        dev_users.seed(s, log=lambda *_: None)
        token = dev_users.make_cookie(s, "regular")
    with TestClient(main.app) as c:
        c.cookies.set("sid", token)
        assert c.get("/api/auth/me").json()["user"]["email"] == "regular@haber.test"
        assert c.get("/api/me/bets").status_code == 200
        assert len(c.get("/api/me/slip").json()["legs"]) == 3


@pytest.mark.parametrize("url", ["sqlite:///data/app.db", "postgresql+psycopg://localhost/haber", "postgresql+psycopg:///haber",
                                 "postgresql+psycopg://me@127.0.0.1:5432/haber"])
def test_local_databases_are_allowed(url, monkeypatch):
    monkeypatch.delenv("RAILWAY_ENVIRONMENT_NAME", raising=False)
    dev_users.local_only(url)


@pytest.mark.parametrize("url", ["postgresql+psycopg://claude_ro@127.0.0.1:54329/railway",   # the prod tunnel
                                 "postgresql+psycopg://u:p@db.railway.internal:5432/haber",
                                 "postgresql+psycopg://u@localhost:6543/railway"])
def test_anything_that_could_be_production_is_refused(url):
    with pytest.raises(SystemExit):
        dev_users.local_only(url)


def test_never_on_railway(monkeypatch):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")
    with pytest.raises(SystemExit):
        dev_users.local_only("sqlite:////srv/backend/data/app.db")


def test_local_is_admin_off_makes_this_machine_a_visitor(monkeypatch):
    with TestClient(main.app) as c:
        assert c.get("/api/meta").json()["can_write"]   # default: a request from this machine is admin
        monkeypatch.setenv("LOCAL_IS_ADMIN", "0")
        assert not c.get("/api/meta").json()["can_write"]
        assert c.post("/api/refresh").status_code == 403
        with SessionLocal() as s:
            dev_users.seed(s, log=lambda *_: None)
            admin, regular = dev_users.make_cookie(s, "admin"), dev_users.make_cookie(s, "regular")
        monkeypatch.setenv("ADMIN_EMAILS", "admin@haber.test")
        c.cookies.set("sid", regular)
        assert not c.get("/api/meta").json()["can_write"]
        c.cookies.set("sid", admin)
        assert c.get("/api/meta").json()["can_write"]
