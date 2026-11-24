"""nflverse loading: the .csv.gz schedule, the old-name fallback, and surviving a failed download."""
import gzip
import io
import os

import pytest
import requests

from app.engine import data

CSV = "game_id,season,game_type,week,home_team,away_team\n2026_05_AAA_BBB,2026,REG,5,AAA,BBB\n2026_05_CCC_DDD,2026,PRE,5,CCC,DDD\n"


class Resp:
    def __init__(self, status=200, content=b""):
        self.status_code, self.content = status, content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "CACHE", str(tmp_path))
    return tmp_path


def test_schedule_is_read_from_the_gzipped_file(cache, monkeypatch):
    urls = []

    def fake_get(url, timeout=0):
        urls.append(url)
        return Resp(200, gzip.compress(CSV.encode()))

    monkeypatch.setattr(data.requests, "get", fake_get)
    g = data.load_schedule()
    assert urls == [f"{data.BASE}/schedules/games.csv.gz"]
    assert list(g.game_id) == ["2026_05_AAA_BBB"]            # regular season only
    assert os.path.exists(cache / "games.csv.gz")


def test_the_old_plain_csv_name_is_still_tried_if_the_gz_is_gone(cache, monkeypatch):
    def fake_get(url, timeout=0):
        return Resp(404) if url.endswith(".gz") else Resp(200, CSV.encode())

    monkeypatch.setattr(data.requests, "get", fake_get)
    assert len(data.load_schedule()) == 1


def test_a_failed_download_falls_back_to_the_last_good_copy(cache, monkeypatch):
    (cache / "games.csv.gz").write_bytes(gzip.compress(CSV.encode()))
    old = 1_000_000_000
    os.utime(cache / "games.csv.gz", (old, old))              # stale, so a refresh is attempted

    def boom(url, timeout=0):
        raise requests.ConnectionError("network down")

    monkeypatch.setattr(data.requests, "get", boom)
    assert len(data.load_schedule()) == 1                      # served from the stale cache instead of crashing

    monkeypatch.setattr(data.requests, "get", lambda url, timeout=0: Resp(503))
    assert len(data.load_schedule()) == 1                      # a server error too


def test_a_failed_download_with_no_cache_still_raises(cache, monkeypatch):
    monkeypatch.setattr(data.requests, "get", lambda url, timeout=0: (_ for _ in ()).throw(requests.ConnectionError("down")))
    with pytest.raises(requests.ConnectionError):
        data.load_schedule()


def test_a_missing_season_is_skipped_not_fatal(cache, monkeypatch):
    stats = "player_id,season,week,season_type,position\np1,2025,1,REG,RB\n"
    monkeypatch.setattr(data.requests, "get", lambda url, timeout=0: Resp(200, stats.encode()) if "2025" in url else Resp(404))
    df = data.load_stats([2025, 2026])
    assert list(df.player_id) == ["p1"]


def test_a_two_way_player_filed_under_defense_takes_his_roster_position(cache, monkeypatch):
    """Travis Hunter: a CB in the stats file, a WR on JAX's roster."""
    stats = "player_id,season,week,season_type,position\nhunter,2026,1,REG,CB\nrb,2026,1,REG,RB\ndb,2026,1,REG,CB\n"
    monkeypatch.setattr(data.requests, "get", lambda url, timeout=0: Resp(200, stats.encode()) if "2026" in url else Resp(404))
    assert list(data.load_stats([2026]).player_id) == ["rb"]
    df = data.load_stats([2026], positions={"hunter": "WR", "rb": "FB"})
    assert dict(zip(df.player_id, df.position)) == {"hunter": "WR", "rb": "RB"}   # an offensive label from the stats file stays


def test_redzone_cut_keeps_targets_and_carries_inside_the_20():
    import pandas as pd
    from app.engine import data
    p = pd.DataFrame([
        dict(yardline_100=15, two_point_attempt=0, pass_attempt=1, receiver_player_id="w", rush_attempt=0, rusher_player_id=None, season=2026, week=3, posteam="KC"),
        dict(yardline_100=5, two_point_attempt=0, pass_attempt=0, receiver_player_id=None, rush_attempt=1, rusher_player_id="r", season=2026, week=3, posteam="KC"),
        dict(yardline_100=40, two_point_attempt=0, pass_attempt=1, receiver_player_id="w", rush_attempt=0, rusher_player_id=None, season=2026, week=3, posteam="KC"),
        dict(yardline_100=2, two_point_attempt=1, pass_attempt=1, receiver_player_id="w", rush_attempt=0, rusher_player_id=None, season=2026, week=3, posteam="KC"),
    ])
    out = data._redzone(p)
    assert sorted(zip(out.player_id, out.kind)) == [("r", "car"), ("w", "tgt")]
