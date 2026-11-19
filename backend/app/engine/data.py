"""Data loading: nflverse weekly player stats, schedules, injuries (cached on disk)."""
import functools
import json
import re
from datetime import datetime
import io
import os
import time
import numpy as np
import pandas as pd
import requests

BASE = "https://github.com/nflverse/nflverse-data/releases/download"
CACHE = os.environ.get("NFLPROPS_CACHE") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".cache")
os.makedirs(CACHE, exist_ok=True)


def _read(src, name, usecols):
    return pd.read_csv(src, low_memory=False, usecols=usecols, compression="gzip" if name.endswith(".gz") else "infer")


def _get_csv(url, name, max_age_hours=6, usecols=None):
    """Download-and-cache a CSV (or .csv.gz). If the download fails, the last good copy is used instead of crashing,
    except a 404 (the file doesn't exist there), which the caller decides how to handle."""
    path = os.path.join(CACHE, name)
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < max_age_hours * 3600:
        return _read(path, name, usecols)
    try:
        r = requests.get(url, timeout=60)
        r.raise_for_status()
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            raise
        if os.path.exists(path):
            return _read(path, name, usecols)
        raise
    except requests.RequestException:
        if os.path.exists(path):
            return _read(path, name, usecols)
        raise
    with open(path, "wb") as f:
        f.write(r.content)
    return _read(io.BytesIO(r.content), name, usecols)


# the only stat columns the model reads; loading ~17 of nflverse's ~150 keeps a full refresh within a small server's memory
PIPELINE_COLUMNS = ["player_id", "player_display_name", "position", "season", "week", "season_type", "team", "opponent_team",
                    "carries", "rushing_yards", "rushing_tds", "targets", "receptions", "receiving_yards", "receiving_tds",
                    "attempts", "passing_yards", "passing_tds"]
PBP_COLUMNS = ["game_id", "play_id", "season", "week", "season_type", "receiver_player_id", "complete_pass", "receiving_yards",
               "rusher_player_id", "rush_attempt", "rushing_yards", "two_point_attempt", "touchdown", "td_player_id",
               "td_team", "rush_touchdown", "pass_touchdown", "yards_gained", "return_yards", "total_home_score", "total_away_score"]


def load_stats(seasons, usecols=None, positions=None):
    """usecols trims the ~150 stat columns when only a few are needed (keeps a small server's memory down).
    A list is matched by name and tolerates columns a season's file lacks.

    positions: gsis_id -> offensive position from the roster (roster_positions). A two-way player the stats file files
    under defense takes it: Travis Hunter is a CB there and a WR on JAX's roster, so his catches were never loaded."""
    if isinstance(usecols, (list, tuple, set)):
        wanted = set(usecols)
        usecols = lambda c: c in wanted  # noqa: E731
    frames = []
    for s in seasons:
        try:
            frames.append(_get_csv(f"{BASE}/stats_player/stats_player_week_{s}.csv", f"stats_{s}.csv", usecols=usecols))
        except requests.HTTPError:
            pass
    df = pd.concat(frames, ignore_index=True)
    df = df[df.season_type == "REG"]
    if positions:
        listed = df.player_id.map(positions)
        df = df.assign(position=df.position.where(df.position.isin(OFFENSE) | listed.isna(), listed))
    return df[df.position.isin(OFFENSE)].copy()


def _catches(p):
    p = p[(p.complete_pass == 1) & p.receiver_player_id.notna()]
    return pd.DataFrame({"player_id": p.receiver_player_id, "season": p.season, "week": p.week,
                         "yards": p.receiving_yards.fillna(0).astype(float)})


def _rushes(p):
    """Every official carry (kneels too, as the box score counts them); two-point tries aren't carries."""
    p = p[(p.rush_attempt == 1) & p.rusher_player_id.notna() & (p.two_point_attempt.fillna(0) != 1)]
    return pd.DataFrame({"player_id": p.rusher_player_id, "season": p.season, "week": p.week,
                         "yards": p.rushing_yards.fillna(0).astype(float)})


def _first_tds(p):
    """Each game's first touchdown: who scored it (any kind: a defensive or return TD counts, as the books settle it)."""
    t = p[(p.touchdown == 1) & p.td_player_id.notna()].sort_values(["game_id", "play_id"]).groupby("game_id").head(1)
    return pd.DataFrame({"player_id": t.td_player_id, "season": t.season, "week": t.week, "game_id": t.game_id})


def _tds(p):
    """Every touchdown and its length: rush and rec (yards gained) for the scorer's offense, ret for any defensive or
    special-teams score (return yards, floored at 0)."""
    t = p[(p.touchdown == 1) & p.td_player_id.notna() & p.td_team.notna()]
    kind = np.where(t.rush_touchdown == 1, "rush", np.where(t.pass_touchdown == 1, "rec", "ret"))
    yards = np.where(kind == "ret", t.return_yards, t.yards_gained)
    out = pd.DataFrame({"player_id": t.td_player_id, "season": t.season, "week": t.week, "game_id": t.game_id, "team": t.td_team,
                        "kind": kind, "yards": yards})
    out = out[out.yards.notna()]
    return out.assign(yards=out.yards.clip(lower=0).astype(int))


def _leads(p):
    """Each game's largest lead for either side (0 if it never led), from the score after every play."""
    d = (p.total_home_score - p.total_away_score).groupby(p.game_id)
    g = pd.DataFrame({"home_lead": d.max().clip(lower=0), "away_lead": (-d.min()).clip(lower=0),
                      "season": p.groupby("game_id").season.first()})
    return g.dropna().reset_index()


PBP_CUTS = {"catches": _catches, "rushes": _rushes, "first_tds": _first_tds, "tds": _tds, "leads": _leads}
CUT_COLUMNS = {"catches": ["player_id", "season", "week", "yards"], "rushes": ["player_id", "season", "week", "yards"],
               "first_tds": ["player_id", "season", "week", "game_id"],
               "tds": ["player_id", "season", "week", "game_id", "team", "kind", "yards"],
               "leads": ["game_id", "home_lead", "away_lead", "season"]}


def _load_pbp(seasons, cut, keep_raw=None):
    """One cut of regular-season play-by-play (see PBP_CUTS), per season. The ~20 MB play-by-play file is cut down
    once per download and each cut is cached too. keep_raw: the seasons whose raw file stays on disk (default all);
    any other finished season is downloaded once into memory and only its small cut is kept, so a long history of
    largest leads doesn't fill the volume with play-by-play."""
    frames = []
    current = max(seasons)
    for s in seasons:
        max_age = 6 if s == current else 24 * 365   # a finished season's plays don't change
        raw, small = os.path.join(CACHE, f"pbp_{s}.csv.gz"), os.path.join(CACHE, f"{cut}_{s}.csv")
        url = f"{BASE}/pbp/play_by_play_{s}.csv.gz"
        if keep_raw is not None and s not in keep_raw and s != current:
            if not os.path.exists(small):
                try:
                    r = requests.get(url, timeout=120)
                    r.raise_for_status()
                except requests.RequestException:
                    continue
                PBP_CUTS[cut](_read(io.BytesIO(r.content), "pbp.csv.gz", lambda c: c in PBP_COLUMNS).query("season_type == 'REG'")) \
                    .to_csv(small, index=False)
            frames.append(pd.read_csv(small))
            continue
        fresh = os.path.exists(raw) and time.time() - os.path.getmtime(raw) < max_age * 3600
        if fresh and os.path.exists(small) and os.path.getmtime(small) >= os.path.getmtime(raw):
            frames.append(pd.read_csv(small))
            continue
        try:
            p = _get_csv(url, f"pbp_{s}.csv.gz", max_age_hours=max_age, usecols=lambda c: c in PBP_COLUMNS)
        except requests.HTTPError:
            continue
        c = PBP_CUTS[cut](p[p.season_type == "REG"])
        c.to_csv(small, index=False)
        frames.append(c)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=CUT_COLUMNS[cut])


def load_catches(seasons):
    """Every regular-season catch (player_id, season, week, yards): the weekly stats have no longest reception."""
    return _load_pbp(seasons, "catches")


def load_rushes(seasons):
    """Every regular-season carry (player_id, season, week, yards): the weekly stats have no longest rush."""
    return _load_pbp(seasons, "rushes")


def load_first_tds(seasons):
    """Each regular-season game's first touchdown scorer (player_id, season, week, game_id)."""
    return _load_pbp(seasons, "first_tds")


def load_tds(seasons):
    """Every regular-season touchdown (player_id, season, week, game_id, team, kind rush/rec/ret, yards)."""
    return _load_pbp(seasons, "tds")


def load_leads(seasons, keep_raw=None):
    """Each regular-season game's largest lead for the home and away side (game_id, home_lead, away_lead, season)."""
    return _load_pbp(seasons, "leads", keep_raw)


def load_schedule():
    """nflverse publishes the schedule as games.csv.gz (the plain games.csv was removed); the old name is a fallback."""
    try:
        g = _get_csv(f"{BASE}/schedules/games.csv.gz", "games.csv.gz")
    except requests.HTTPError:
        g = _get_csv(f"{BASE}/schedules/games.csv", "games.csv")
    return g[g.game_type == "REG"].copy()


def cached_schedule():
    """The schedule as a sync last downloaded it, never downloading (for page loads); None before the first download."""
    for name in ("games.csv.gz", "games.csv"):
        path = os.path.join(CACHE, name)
        if os.path.exists(path):
            g = _read(path, name, None)
            return g[g.game_type == "REG"].copy()
    return None


def load_injuries(season):
    try:
        return _get_csv(f"{BASE}/injuries/injuries_{season}.csv", f"injuries_{season}.csv", max_age_hours=1)
    except requests.HTTPError:
        return pd.DataFrame()


def load_roster(season):
    """Current active roster: gsis_id -> team (latest week in the file, status ACT)."""
    r = _get_csv(f"{BASE}/rosters/roster_{season}.csv", f"roster_{season}.csv", max_age_hours=3)
    r = r[r.week == r.week.max()]
    return dict(zip(r[r.status == "ACT"].gsis_id, r[r.status == "ACT"].team))


def load_reserve(season):
    """Players on a reserve list (IR, PUP, ...), from each team's latest week in the roster file. Empty if unavailable."""
    try:
        r = _get_csv(f"{BASE}/rosters/roster_{season}.csv", f"roster_{season}.csv", max_age_hours=3,
                     usecols=["team", "week", "status", "status_description_abbr", "gsis_id", "full_name", "position"])
    except Exception:
        return pd.DataFrame()
    r = r[r.week == r.groupby("team").week.transform("max")]
    return r[r.status == "RES"].copy()


def load_espn_ids(season):
    """espn_id (as a string) -> gsis_id from the roster file, to match ESPN's injury feed. Empty if unavailable."""
    try:
        r = _get_csv(f"{BASE}/rosters/roster_{season}.csv", f"roster_{season}.csv", max_age_hours=3,
                     usecols=["gsis_id", "espn_id"]).dropna()
    except Exception:
        return {}
    return {str(int(e)) if isinstance(e, float) else str(e): g for e, g in zip(r.espn_id, r.gsis_id)}


ESPN_INJURIES = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries"
ESPN_COLUMNS = ["espn_id", "name", "team", "pos", "status", "injury", "comment", "return_date", "updated"]
# ESPN's abbreviations that differ from nflverse's
ESPN_TEAMS = {"WSH": "WAS", "LAR": "LA"}


def load_espn_injuries(max_age_hours=0.25):
    """ESPN's league-wide injury feed (no key): every player with a status or recent injury news, with the latest
    news line and an estimated return. Cached on disk; a failed fetch falls back to the last good copy, and with no
    copy at all an empty frame (ESPN only adds detail to nflverse's report). Returns (frame, fetched_at naive-UTC ISO)."""
    path = os.path.join(CACHE, "espn_injuries.json")
    js = None
    if not (os.path.exists(path) and time.time() - os.path.getmtime(path) < max_age_hours * 3600):
        try:
            r = requests.get(ESPN_INJURIES, timeout=20, headers={"User-Agent": "Mozilla/5.0 (props model)"})
            r.raise_for_status()
            js = r.json()
            with open(path + ".tmp", "w") as f:
                json.dump(js, f)
            os.replace(path + ".tmp", path)
        except (requests.RequestException, ValueError):
            pass
    if js is None:
        if not os.path.exists(path):
            return pd.DataFrame(columns=ESPN_COLUMNS), None
        with open(path) as f:
            js = json.load(f)
    fetched = datetime.utcfromtimestamp(os.path.getmtime(path)).isoformat()
    real = lambda v: v if v not in (None, "", "Not Specified") else ""  # noqa: E731
    rows = []
    for t in js.get("injuries", []):
        for i in t.get("injuries", []):
            a = i.get("athlete") or {}
            link = next((m.group(1) for m in (re.search(r"/id/(\d+)", x.get("href", "")) for x in a.get("links", [])) if m), None)
            d = i.get("details") or {}
            team = (a.get("team") or {}).get("abbreviation", "")
            rows.append(dict(espn_id=link, name=a.get("displayName", ""), team=ESPN_TEAMS.get(team, team),
                             pos=(a.get("position") or {}).get("abbreviation", ""), status=i.get("status", ""),
                             injury=" ".join(x for x in (real(d.get("side")), real(d.get("type")), real(d.get("detail"))) if x),
                             comment=i.get("shortComment") or "", return_date=d.get("returnDate"), updated=i.get("date")))
    return pd.DataFrame(rows, columns=ESPN_COLUMNS), fetched


OFFENSE = ("QB", "RB", "FB", "WR", "TE")


def roster_positions(season) -> dict:
    """gsis_id -> position for every active offensive skill player (load_active_players). Empty if unavailable."""
    a = load_active_players(season)
    return dict(zip(a.player_id, a.pos))


def load_active_players(season):
    return _active_players(season, int(time.time() // 3600))


@functools.lru_cache(maxsize=2)
def _active_players(season, _hour):
    """Every active offensive skill player (QB, RB, FB, WR, TE) on a roster in each team's latest week, for player
    search: gsis_id, name, pos, team, headshot. Empty when the roster can't be loaded."""
    cols = ["team", "week", "status", "position", "full_name", "gsis_id", "headshot_url"]
    try:
        r = _get_csv(f"{BASE}/rosters/roster_{season}.csv", f"roster_{season}.csv", max_age_hours=3, usecols=cols)
    except Exception:
        return pd.DataFrame(columns=["player_id", "name", "pos", "team", "headshot"])
    r = r[(r.week == r.groupby("team").week.transform("max")) & (r.status == "ACT") & r.position.isin(OFFENSE)].dropna(subset=["gsis_id"])
    r = r.drop_duplicates("gsis_id", keep="last")
    return pd.DataFrame(dict(player_id=r.gsis_id, name=r.full_name.fillna(""), pos=r.position, team=r.team,
                             headshot=r.headshot_url.where(r.headshot_url.notna(), None))).reset_index(drop=True)


def load_headshots(season):
    return _headshots(season, int(time.time() // 3600))


@functools.lru_cache(maxsize=2)
def _headshots(season, _hour):
    """gsis_id -> headshot URL from the roster file; empty when the roster can't be loaded (images are decoration)."""
    try:
        r = _get_csv(f"{BASE}/rosters/roster_{season}.csv", f"roster_{season}.csv", max_age_hours=3,
                     usecols=["gsis_id", "headshot_url"]).dropna()
    except Exception:
        return {}
    return dict(zip(r.gsis_id, r.headshot_url))
