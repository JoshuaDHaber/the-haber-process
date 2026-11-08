from datetime import datetime
import pandas as pd

import pytest
from fastapi.testclient import TestClient

from app import main
from app.db import AltLine, OddsLine, Projection, Run, SessionLocal


@pytest.fixture(scope="module")
def client():
    with SessionLocal() as s:
        run = Run(season=2026, week=4, backtest={"rush": dict(n=10, mae_model=20.0, mae_naive=25.0, bias=0.5)},
                  variance={"rush": [1.0, 0.1]}, excluded=["Hurt Guy (Out)"],
                  teams={"2026_04_BBB_AAA": dict(home="AAA", away="BBB", gameday="2026-10-04", gametime="13:00", margin=4.0,
                                                 p_home=0.6, home_rating=2.0, away_rating=-0.5, spread=3.5)})
        for i, (name, mu) in enumerate([("Alpha Back", 90.0), ("Beta Back", 40.0)]):
            run.projections.append(Projection(
                player_id=f"p{i}", name=name, pos="RB", team="AAA", opp="BBB", home=True, kind="rush", mu=mu, sd=30.0,
                vol=15.0, eff=4.5, spread=3.0, status="", game_id="2026_04_BBB_AAA", gameday="2026-10-04",
                gametime="13:00", last5=[50.0, 60.0, 70.0, 80.0, 90.0]))
        s.add(run)
        t = datetime(2026, 10, 3, 12)
        s.add_all([OddsLine(fetched_at=t, player="Alpha Back", market="rush", line=60.5, over_odds=-110, under_odds=-110, book="X", game_id="2026_04_BBB_AAA"),
                   OddsLine(fetched_at=t, player="Beta Back", market="rush", line=40.5, over_odds=-110, under_odds=-110, book="X", game_id="2026_04_BBB_AAA"),
                   # moneyline: AAA/BBB aren't real teams, so the quotes use two real names mapped onto them below
                   OddsLine(fetched_at=t, player="Arizona Cardinals", market="ml", line=0.0, over_odds=-150, under_odds=130, book="X", game_id="2026_04_BBB_AAA")])
        s.commit()
    with TestClient(main.app) as c:
        yield c


def test_meta_summarises_run_odds_and_games(client):
    m = client.get("/api/meta").json()
    assert m["run"]["week"] == 4 and m["run"]["excluded"] == ["Hurt Guy (Out)"]
    assert m["odds"]["rush"]["lines"] == 2
    assert m["games"][0]["label"] == "BBB @ AAA"
    assert m["backtest"]["rush"]["mae_naive"] == 25.0
    assert m["has_odds_key"] is False
    assert m["games"][0]["records"] == {}   # ESPN unreachable: no records, and the page still loads


def test_meta_carries_both_teams_records(client, monkeypatch):
    js = {"children": [{"standings": {"entries": [
        {"team": {"abbreviation": t}, "stats": [{"type": "total", "summary": r}, {"type": "home", "summary": "1-0"}, {"type": "road", "summary": "0-1"}]}
        for t, r in [("AAA", "3-1"), ("BBB", "1-3"), ("CCC", "2-2")]]}}]}
    monkeypatch.setattr(main.standings, "_download", lambda url: js)
    main.standings._records.cache_clear()
    assert client.get("/api/meta").json()["games"][0]["records"] == {
        "AAA": dict(overall="3-1", home="1-0", road="0-1"), "BBB": dict(overall="1-3", home="1-0", road="0-1")}


def test_moneylines_price_both_teams_of_every_game(client, monkeypatch):
    monkeypatch.setitem(main.mlm.odds.TEAM_NAMES, "AAA", "Arizona Cardinals")
    games = client.get("/api/moneylines").json()
    assert len(games) == 1 and games[0]["label"] == "BBB @ AAA" and games[0]["margin"] == 4.0
    away, home = games[0]["sides"]
    assert (away["team"], home["team"]) == ("BBB", "AAA") and away["p_model"] == 0.4
    assert home["odds"] == -150 and home["book"] == "X" and 0.55 < home["p_mkt"] < 0.6
    assert away["odds"] is None and away["p_mkt"] is None   # no quote stored for BBB's side
    assert client.get("/api/moneylines", params={"book": "Other"}).json()[0]["sides"][1]["odds"] is None


def test_projections_filter_and_search(client):
    assert [p["name"] for p in client.get("/api/projections").json()] == ["Alpha Back", "Beta Back"]  # sorted by mu
    assert len(client.get("/api/projections", params={"q": "beta"}).json()) == 1
    assert client.get("/api/projections", params={"kind": "pass"}).json() == []


def test_picks_rank_by_ev_and_respect_filters(client):
    picks = client.get("/api/picks", params={"min_ev": 0.0}).json()
    assert picks[0]["name"] == "Alpha Back" and picks[0]["side"] == "Over"
    assert all(p["ev"] >= 0 for p in picks)
    assert client.get("/api/picks", params={"min_ev": 0.9}).json() == []
    assert client.get("/api/picks", params={"game_id": "nope"}).json() == []


def test_picks_can_be_limited_to_one_sportsbook(client):
    assert client.get("/api/meta").json()["books"] == ["X"]
    assert client.get("/api/picks", params={"min_ev": 0.0, "book": "X"}).json()
    assert client.get("/api/picks", params={"min_ev": -1, "book": "Other"}).json() == []


def test_market_weight_changes_the_pricing(client):
    a = client.get("/api/picks", params={"min_ev": -1, "market_weight": 0}).json()[0]
    b = client.get("/api/picks", params={"min_ev": -1, "market_weight": 1}).json()[0]
    assert a["prob"] != b["prob"]


def test_invalid_parameters_are_rejected(client):
    assert client.get("/api/picks", params={"market_weight": 3}).status_code == 422
    assert client.get("/api/projections", params={"kind": "bogus"}).status_code == 422


def test_unknown_player_is_a_404(client):
    assert client.get("/api/players/nobody").status_code == 404


def test_refresh_runs_once_at_a_time(client, monkeypatch):
    monkeypatch.setattr(main.pipeline, "run_projections", lambda log=print, **kw: log("fake run"))
    monkeypatch.setattr(main.pipeline, "refresh_odds", lambda mode, log=print, **kw: None)
    main._job.update(state="running")
    assert client.post("/api/refresh", json={"odds": "none"}).status_code == 409
    main._job.update(state="idle")
    r = client.post("/api/refresh", json={"odds": "none"})
    assert r.status_code == 202
    assert client.get("/api/refresh/status").json()["state"] == "done"


def _seed_alt(monkeypatch):
    from app import config
    monkeypatch.setattr(config, "ODDS_API_KEY", lambda: "test-key")
    monkeypatch.setattr(main.config, "ODDS_API_KEY", lambda: "test-key")


def test_ladder_lists_main_quotes_priced(client):
    d = client.get("/api/players/p0/lines", params={"kind": "rush"}).json()
    assert d["alt_fetched_at"] is None
    row = d["quotes"][0]
    assert row["line"] == 60.5 and row["alt"] is False and row["over"]["ev"] > 0 > row["under"]["ev"]


def test_alt_fetch_needs_a_key(client):
    assert client.post("/api/players/p0/alt-lines").status_code == 400


def test_alt_fetch_stores_alternates_and_shows_them_in_the_ladder(client, monkeypatch):
    _seed_alt(monkeypatch)
    calls = []

    def fake(game_id, kinds, log=print):
        calls.append((game_id, kinds))
        from datetime import datetime
        with SessionLocal() as s:
            s.add_all([AltLine(fetched_at=datetime.utcnow(), game_id=game_id, player="Alpha Back", market="rush", line=ln,
                               over_odds=o, under_odds=None, book="X", over_link="https://book/slip", under_link=None,
                               event_link=None) for ln, o in [(80.5, 250), (100.5, 600)]])
            s.commit()
        return {"alt_quotes": 2, "linked": 0, "credits_remaining": "100"}

    monkeypatch.setattr(main.pipeline, "fetch_game_lines", fake)
    r = client.post("/api/players/p0/alt-lines")
    assert r.status_code == 200 and r.json()["alt_quotes"] == 2 and calls == [("2026_04_BBB_AAA", ["rush"])]
    d = client.get("/api/players/p0/lines", params={"kind": "rush", "odds_range": 0}).json()
    alts = [q for q in d["quotes"] if q["alt"]]
    assert [q["line"] for q in alts] == [80.5, 100.5] and alts[0]["over"]["link"] == "https://book/slip"
    assert d["alt_fetched_at"] is not None
    # default view hides prices beyond -300..+300: the +600 longshot is gone, the +250 stays
    d = client.get("/api/players/p0/lines", params={"kind": "rush"}).json()
    assert [q["line"] for q in d["quotes"] if q["alt"]] == [80.5]


def test_alt_fetch_reports_upstream_failures(client, monkeypatch):
    _seed_alt(monkeypatch)

    def boom(game_id, kinds, log=print):
        raise RuntimeError("quota exceeded")

    monkeypatch.setattr(main.pipeline, "fetch_game_lines", boom)
    assert client.post("/api/players/p0/alt-lines").status_code == 502


def test_meta_reports_a_lan_address_for_sharing_to_phones(client):
    assert "lan_url" in client.get("/api/meta").json()


def test_other_devices_are_read_only(monkeypatch):
    """Phones on the LAN can read but not POST (refreshes and alt-line fetches spend API credits)."""
    with TestClient(main.app, client=("192.168.1.50", 50000)) as phone:
        assert phone.get("/api/meta").status_code == 200
        assert phone.post("/api/players/p0/alt-lines").status_code == 403
        assert phone.post("/api/refresh", json={"odds": "none"}).status_code == 403


def test_tailscale_address_is_recognised_in_ifconfig_output():
    sample = """lo0: flags=8049<UP,LOOPBACK,RUNNING,MULTICAST> mtu 16384
\tinet 127.0.0.1 netmask 0xff000000
en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
\tinet 192.168.1.194 netmask 0xffffff00 broadcast 192.168.1.255
utun4: flags=8051<UP,POINTOPOINT,RUNNING,MULTICAST> mtu 1280
\tinet 100.101.102.103 --> 100.101.102.103 netmask 0xffffffff
"""
    assert main.tailscale_ip(sample) == "100.101.102.103"
    assert main.tailscale_ip("inet 100.20.1.1 netmask 0xff") is None  # outside 100.64.0.0/10
    assert main.tailscale_ip("inet 192.168.1.5 netmask 0xff") is None


def test_app_url_override_wins_for_sharing(client, monkeypatch):
    monkeypatch.setenv("APP_URL", "http://my-mac.tail1234.ts.net:8000/")
    assert client.get("/api/meta").json()["lan_url"] == "http://my-mac.tail1234.ts.net:8000"


# ---------- hosted-deployment behaviour: who may write, CORS, health, database URL ----------
def test_remote_writes_need_the_owners_account(monkeypatch):
    from conftest import sign_in_admin
    monkeypatch.setattr(main.pipeline, "run_projections", lambda log=print, **kw: None)
    monkeypatch.setattr(main.pipeline, "refresh_odds", lambda mode, log=print, **kw: None)
    with TestClient(main.app, client=("203.0.113.9", 4000)) as remote:
        assert remote.post("/api/refresh", json={"odds": "none"}).status_code == 403
        # the retired admin password no longer opens anything
        monkeypatch.setenv("ADMIN_TOKEN", "s3cret")
        assert remote.post("/api/refresh", json={"odds": "none"}, headers={"Authorization": "Bearer s3cret"}).status_code == 403
        sign_in_admin(remote, monkeypatch)
        assert remote.post("/api/refresh", json={"odds": "none"}).status_code == 202
        main._job.update(state="idle")


def test_meta_tells_each_client_whether_it_can_write(monkeypatch):
    from conftest import sign_in_admin
    with TestClient(main.app, client=("203.0.113.9", 4000)) as remote:
        assert remote.get("/api/meta").json()["can_write"] is False
        assert sign_in_admin(remote, monkeypatch).get("/api/meta").json()["can_write"] is True
    with TestClient(main.app) as local:   # this machine (a laptop, or a shell on the host) is always the owner
        assert local.get("/api/meta").json()["can_write"] is True



def test_forbidden_responses_still_carry_cors_headers_so_the_browser_can_read_them():
    with TestClient(main.app, client=("203.0.113.9", 4000)) as remote:
        r = remote.post("/api/refresh", json={"odds": "none"}, headers={"Origin": "http://localhost:5173"})
        assert r.status_code == 403 and r.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_health_check(client):
    assert client.get("/healthz").json() == {"ok": True}


def test_projection_refresh_can_be_turned_off_for_small_hosts(client, monkeypatch):
    calls = []
    monkeypatch.setenv("PROJECTIONS_ON_SERVER", "0")
    monkeypatch.setattr(main.pipeline, "run_projections", lambda log=print, **kw: calls.append("projections"))
    monkeypatch.setattr(main.pipeline, "refresh_odds", lambda mode, log=print, **kw: calls.append(f"odds:{mode}"))
    assert client.get("/api/meta").json()["can_run_projections"] is False
    client.post("/api/refresh", json={"odds": "missing"})
    assert calls == ["odds:missing"]
    main._job.update(state="idle")


def test_hosted_postgres_urls_are_pointed_at_the_installed_driver():
    from app import config
    assert config.normalize_db_url("postgres://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert config.normalize_db_url("postgresql://u:p@h/db?sslmode=require") == "postgresql+psycopg://u:p@h/db?sslmode=require"
    assert config.normalize_db_url("sqlite:///x.db") == "sqlite:///x.db"


def test_a_kind_can_be_excluded_from_picks_and_projections(client):
    assert client.get("/api/projections", params={"exclude_kind": "rush"}).json() == []
    assert len(client.get("/api/projections", params={"exclude_kind": "td"}).json()) == 2
    assert client.get("/api/picks", params={"min_ev": -1, "exclude_kind": "rush"}).json() == []
    assert client.get("/api/picks", params={"min_ev": -1, "exclude_kind": "td"}).json()


def test_picks_can_be_limited_to_overs_or_unders(client):
    both = client.get("/api/picks", params={"min_ev": -1}).json()
    assert {p["side"] for p in both} == {"Over", "Under"}
    overs = client.get("/api/picks", params={"min_ev": -1, "side": "Over"}).json()
    unders = client.get("/api/picks", params={"min_ev": -1, "side": "Under"}).json()
    assert overs and unders and {p["side"] for p in overs} == {"Over"} and {p["side"] for p in unders} == {"Under"}
    assert len(overs) + len(unders) == len(both)
    assert client.get("/api/picks", params={"side": "Sideways"}).status_code == 422


def test_old_runs_with_only_gamma_parameters_still_price(client):
    from app.db import Run
    run = Run(season=2026, week=4, backtest={}, variance={"rush": [1.0, 0.1]}, excluded=[])
    assert main._dists(run) == {}  # legacy [a, b] lists are ignored, so pricing falls back to the gamma curve
    run.variance = {"rush": dict(mu=[1.0], sd=[1.0], z=[0.0]), "td": [1.0, 0.0]}
    assert list(main._dists(run)) == ["rush"]


# ---------- the weekly sync from the admin UI ----------
def _two_games():
    import pandas as pd
    return pd.DataFrame([dict(game_id="2026_05_AAA_BBB", away_team="AAA", home_team="BBB", season=2026, week=5, gameday="2026-10-11"),
                         dict(game_id="2026_05_CCC_DDD", away_team="CCC", home_team="DDD", season=2026, week=5, gameday="2026-10-12")])


def test_the_sync_plan_says_what_would_run_and_what_it_costs(client, monkeypatch):
    monkeypatch.setattr(main.pipeline, "upcoming_games", _two_games)
    p = client.get("/api/refresh/plan").json()
    assert p["season"] == 2026 and p["week"] == 5 and p["total_games"] == 2
    assert [g["label"] for g in p["games"]] == ["AAA @ BBB", "CCC @ DDD"]
    assert p["games"][0]["need_main"] == ["ftd", "lng", "ml", "pass", "ptd", "rec", "recs", "rlng", "rr", "rush", "td"] and p["games"][0]["need_alt"] is True
    assert p["credits"] == 38 and p["will_run_projections"] is True
    none = client.get("/api/refresh/plan", params={"odds": "none"}).json()
    assert none["credits"] == 0 and none["games"] == [] and none["week"] == 5


def test_only_the_admin_can_ask_for_a_sync_plan(monkeypatch):
    from conftest import sign_in_admin
    monkeypatch.setattr(main.pipeline, "upcoming_games", _two_games)
    with TestClient(main.app, client=("203.0.113.30", 4000)) as remote:
        assert remote.get("/api/refresh/plan").status_code == 403
        assert sign_in_admin(remote, monkeypatch).get("/api/refresh/plan").status_code == 200


def test_a_finished_sync_drops_the_caches_the_new_data_outdates():
    main._pick_cache["stale"] = object()
    main._priced_cache["stale"] = object()
    main._release_memory()
    assert main._pick_cache == {} and main._priced_cache == {}


# ---------- progress and options of the weekly sync ----------
def test_a_running_sync_reports_its_step_stage_and_odds_progress(client, monkeypatch):
    seen = {}

    def fake_model(log=print, full=False, **kw):
        seen["full"] = full
        seen["during_model"] = dict(main._job)
        log("Using the saved calibration from week 4 (run 3): skipping the backtest")

    def fake_odds(mode, log=print, progress=None, **kw):
        seen["mode"] = mode
        seen["during_odds_start"] = dict(main._job)
        progress(0, 2); seen["half"] = main._job["progress"], main._job["detail"]
        progress(2, 2)

    monkeypatch.setattr(main.pipeline, "run_projections", fake_model)
    monkeypatch.setattr(main.pipeline, "refresh_odds", fake_odds)
    assert client.post("/api/refresh", json={"odds": "thin", "full": True}).status_code == 202
    assert seen["full"] is True and seen["mode"] == "thin"
    m = seen["during_model"]
    assert (m["step"], m["steps"]) == (1, 2) and m["stage"] == "Running the model (full recalibration)"
    o = seen["during_odds_start"]
    assert (o["step"], o["stage"]) == (2, "Fetching odds")
    assert seen["half"] == (0.0, "game 1 of 2")
    done = client.get("/api/refresh/status").json()
    assert done["state"] == "done" and done["stage"] == "Done" and done["progress"] == 1.0 and done["steps"] == 2
    assert "Using the saved calibration" in done["log"][0]


def test_a_model_only_sync_has_one_step(client, monkeypatch):
    monkeypatch.setattr(main.pipeline, "run_projections", lambda log=print, **kw: None)
    monkeypatch.setattr(main.pipeline, "refresh_odds", lambda mode, log=print, **kw: None)
    client.post("/api/refresh", json={"odds": "none"})
    assert client.get("/api/refresh/status").json()["steps"] == 1


def test_the_plan_says_whether_the_model_will_refit_and_what_calibration_it_has(client, monkeypatch):
    monkeypatch.setattr(main.pipeline, "upcoming_games", _two_games)
    monkeypatch.setattr(main.pipeline, "reusable_calibration", lambda season, week=None: dict(variance={}, backtest={}, run_id=3))
    assert client.get("/api/refresh/plan").json()["will_recalibrate"] is False           # a fresh calibration exists: fast
    assert client.get("/api/refresh/plan", params={"full": True}).json()["will_recalibrate"] is True
    monkeypatch.setattr(main.pipeline, "reusable_calibration", lambda season, week=None: None)
    assert client.get("/api/refresh/plan").json()["will_recalibrate"] is True            # nothing to reuse: it must refit
    thin = client.get("/api/refresh/plan", params={"odds": "thin"}).json()
    assert thin["games"] and all(g["reason"] in ("missing", "thin") for g in thin["games"])


def test_injury_report_covers_teams_on_the_slate(client, monkeypatch):
    rows = [dict(week=4, team=t, gsis_id=pid, full_name=n, position=pos, report_status=st, report_primary_injury="Knee",
                 practice_primary_injury="Knee", practice_status="Did Not Participate In Practice")
            for t, pid, n, pos, st in [("AAA", "p0", "Alpha Back", "RB", "Questionable"), ("BBB", "x1", "Their Corner", "CB", "Out"),
                                       ("ZZZ", "x2", "Not Playing", "WR", "Out")]]
    monkeypatch.setattr(main.data, "load_injuries", lambda season: main.pd.DataFrame(rows))
    monkeypatch.setattr(main.data, "load_reserve", lambda season: main.pd.DataFrame())
    espn = main.pd.DataFrame([("9", "Alpha Back", "AAA", "RB", "Questionable", "Knee", "Limited in practice Friday.", "2026-10-18", None)],
                             columns=main.data.ESPN_COLUMNS)
    monkeypatch.setattr(main.data, "load_espn_injuries", lambda: (espn, "2026-10-18T12:00:00"))
    monkeypatch.setattr(main.data, "load_espn_ids", lambda season: {"9": "p0"})
    main._injury_report.cache_clear()
    rep = client.get("/api/injuries").json()
    main._injury_report.cache_clear()
    assert rep["week"] == 4 and rep["report_week"] == 4 and set(rep["teams"]) == {"AAA", "BBB"}
    assert rep["news_fetched_at"] == "2026-10-18T12:00:00" and rep["teams"]["AAA"][0]["comment"] == "Limited in practice Friday."
    assert rep["teams"]["AAA"][0]["kind"] == "rush"            # projected: the UI can open his drawer
    assert rep["teams"]["BBB"][0]["side"] == "def" and rep["teams"]["BBB"][0]["kind"] is None


def test_player_search_covers_projected_and_rostered_players(client, monkeypatch):
    import pandas as pd
    roster = pd.DataFrame(dict(player_id=["p0", "r1", "r2"], name=["Alpha Back", "Alpine Receiver", "Zed Tight"],
                               pos=["RB", "WR", "TE"], team=["AAA", "CCC", "DDD"], headshot=[None, "http://x/r1.png", None]))
    monkeypatch.setattr(main.data, "load_active_players", lambda season: roster)
    monkeypatch.setattr(main.data, "load_headshots", lambda season: {"p0": "http://x/p0.png"})
    hits = client.get("/api/player-search", params={"q": "alp"}).json()
    assert [(h["name"], h["projected"]) for h in hits] == [("Alpha Back", True), ("Alpine Receiver", False)]
    assert hits[0]["headshot"] == "http://x/p0.png" and hits[1]["team"] == "CCC"
    assert [h["name"] for h in client.get("/api/player-search", params={"q": "back"}).json()] == ["Alpha Back", "Beta Back"]
    assert client.get("/api/player-search", params={"q": " "}).json() == []


def test_grades_report_is_empty_before_anything_is_graded(client):
    r = client.get("/api/grades")
    assert r.status_code == 200
    body = r.json()
    assert body["graded"] == 0 and body["model_vs_line"] == [] and body["board"][0]["n"] == 0


def test_leaving_out_anytime_td_leaves_out_first_td_too():
    assert main._excluded("td") == ("td", "ftd", "dd", "tpl")     # the "all over/unders" views
    assert main._excluded("rush") == ("rush",)


def test_correlations_are_empty_until_a_run_has_fitted_them():
    from app.main import build_correlations
    assert build_correlations(None).pairs == {}
    run = Run(season=2026, week=6, variance={"_correlation": {"pairs": {"team|pass:QB|rec:WR": [0.36, 1306]}, "check": {}}})
    assert build_correlations(run).pairs == {"team|pass:QB|rec:WR": 0.36}


# ---------- hiding the offshore books (admin page) ----------
def test_hidden_offshore_books_leave_every_list_and_price(client, monkeypatch):
    with SessionLocal() as s:
        bov = OddsLine(fetched_at=datetime(2026, 10, 3, 12), player="Alpha Back", market="rush", line=60.5, over_odds=+150,
                       under_odds=-200, book="Bovada", game_id="2026_04_BBB_AAA")
        s.add(bov)
        s.commit()
    try:
        over = lambda: next(p for p in client.get("/api/picks", params={"min_ev": -1}).json() if p["name"] == "Alpha Back" and p["side"] == "Over")
        assert over()["book"] == "Bovada" and "Bovada" in client.get("/api/meta").json()["books"]

        assert client.post("/api/admin/settings", json={"hide_offshore": True}).json() == {"hide_offshore": True}
        assert client.get("/api/admin").json()["hide_offshore"] is True
        assert "Bovada" not in client.get("/api/meta").json()["books"]
        assert over()["book"] == "X" and over()["books"] == 2   # the consensus still counts Bovada's quote
        assert client.get("/api/picks", params={"book": "Bovada", "min_ev": -1}).json() == []
        assert {q["book"] for q in client.get("/api/players/p0/lines", params={"kind": "rush", "odds_range": 0}).json()["quotes"]} == {"X"}

        with TestClient(main.app, client=("203.0.113.31", 4000)) as remote:   # only the admin can change it
            assert remote.post("/api/admin/settings", json={"hide_offshore": False}).status_code == 403
    finally:
        client.post("/api/admin/settings", json={"hide_offshore": False})
        with SessionLocal() as s:
            s.delete(s.merge(bov))
            s.commit()


def test_picks_default_to_fitted_weights_and_carry_movement_fields(client):
    p = client.get("/api/picks", params={"min_ev": -1}).json()[0]
    assert p["w"] == pytest.approx(0.35)          # no fit stored yet: the default weight
    assert p["open_line"] is None and p["move"] is None and p["steam"] == 0   # no line history yet
    assert client.get("/api/picks", params={"min_ev": -1, "market_weight": 0.8}).json()[0]["w"] == pytest.approx(0.8)


def test_history_and_ladder_curve_endpoints(client):
    assert client.get("/api/players/p0/history", params={"kind": "rush"}).json() == []
    lad = client.get("/api/players/p0/lines", params={"kind": "rush"}).json()
    assert "curve" in lad and "w" in lad
    assert client.get("/api/players/nobody/history", params={"kind": "rush"}).status_code == 404


def test_grades_carry_clv_and_the_market_fit(client):
    g = client.get("/api/grades").json()
    assert g["clv"][0]["n"] == 0 and g["market_fit"]["n"] == 0
    assert [b["book"] for b in g["market_fit"]["books"]] == ["X"]   # the books on the current lines


def test_meta_previews_next_weeks_schedule_from_the_cached_copy(client, monkeypatch):
    sched = pd.DataFrame([dict(game_id="2026_05_CCC_DDD", season=2026, week=5, gameday="2026-10-11", gametime="13:00", game_type="REG",
                               away_team="CCC", home_team="DDD"),
                          dict(game_id="2026_06_EEE_FFF", season=2026, week=6, gameday="2026-10-18", gametime="13:00", game_type="REG",
                               away_team="EEE", home_team="FFF")])
    monkeypatch.setattr(main.data, "cached_schedule", lambda: sched)
    main._next_week_schedule.cache_clear()
    m = client.get("/api/meta").json()
    nxt = m["next_games"]
    assert [g["game_id"] for g in nxt] == ["2026_05_CCC_DDD"] and nxt[0]["label"] == "CCC @ DDD"
    assert [b["team"] for b in m["next_byes"]] == ["EEE", "FFF"]   # on the season's schedule, no game in week 5
    main._next_week_schedule.cache_clear()


def test_meta_has_no_preview_before_a_schedule_is_downloaded(client, monkeypatch):
    monkeypatch.setattr(main.data, "cached_schedule", lambda: None)
    main._next_week_schedule.cache_clear()
    m = client.get("/api/meta").json()
    assert m["next_games"] == [] and m["next_byes"] == []
    main._next_week_schedule.cache_clear()


def test_fetching_the_schedule_in_the_background_refreshes_the_preview(monkeypatch):
    calls = []
    monkeypatch.setattr(main.data, "load_schedule", lambda: calls.append(1))
    cleared = []
    monkeypatch.setattr(main._next_week_schedule, "cache_clear", lambda: cleared.append(1))
    main._fetch_schedule()
    assert calls == [1] and cleared == [1]


def test_a_failed_schedule_fetch_leaves_the_server_running(monkeypatch):
    def boom():
        raise OSError("offline")
    monkeypatch.setattr(main.data, "load_schedule", boom)
    main._fetch_schedule()   # logs and returns; the next sync fetches it


def test_a_teams_moneyline_ladder_and_hot_teams(client, monkeypatch):
    monkeypatch.setitem(main.mlm.odds.TEAM_NAMES, "AAA", "Arizona Cardinals")
    lad = client.get("/api/moneylines/2026_04_BBB_AAA/AAA/lines").json()
    assert lad["kind"] == "ml" and lad["mu"] == 4.0 and len(lad["quotes"]) == 1
    q = lad["quotes"][0]
    assert (q["book"], q["line"], q["over"]["odds"], q["under"]["odds"]) == ("X", 0.0, -150, 130)
    assert client.get("/api/moneylines/2026_04_BBB_AAA/CCC/lines").status_code == 404
    assert client.get("/api/hot-teams").json() == []   # this run's game kicked off long ago
