"""NBA data from ESPN's public JSON API (no key): schedule, rosters, per-player game logs and the injury report.

stats.nba.com and NBA's own CDN refuse requests from hosting providers, so everything comes from ESPN. Responses are
cached as JSON on disk (config.NBA_CACHE, on the persistent volume in production): a finished season's game logs never
change, so they are fetched once; the current season's are refreshed every few hours.
"""
import hashlib
import json
import os
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from .. import config
from .odds import TEAM_NAMES

SITE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
WEB = "https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba"
ET = ZoneInfo("America/New_York")
# ESPN season types that count: regular season, playoffs, play-in (preseason and the All-Star game don't)
GAME_TYPES = {2, 3, 5}
NBA_TEAMS = set(TEAM_NAMES)   # ESPN abbreviations of the 30 teams
LOG_COLUMNS = ["player_id", "event_id", "season", "date", "team", "opp", "home", "min", "pts", "reb", "ast", "threes",
               "stl", "blk", "home_score", "away_score"]
# parsed game logs live in this folder; renaming it (and listing the old name below) re-fetches every season once,
# for a column added to LOG_COLUMNS (v2: steals and blocks)
GAMELOG_DIR = "gamelogs_v2"
OLD_GAMELOG_DIRS = ("gamelogs",)
_session = requests.Session()
_session.headers["User-Agent"] = "Mozilla/5.0 (props model)"


def _cache_path(url: str) -> str:
    os.makedirs(config.NBA_CACHE, exist_ok=True)
    return os.path.join(config.NBA_CACHE, hashlib.sha1(url.encode()).hexdigest()[:20] + ".json")


def _download(url: str) -> dict:
    r = _session.get(url, timeout=30)
    r.raise_for_status()
    return r.json()


def _cached(path: str, max_age_hours: Optional[float], fetch):
    """fetch() through a JSON file at path (max_age_hours=None: cached forever). A failed fetch falls back to the
    last good copy, however old."""
    if os.path.exists(path) and (max_age_hours is None or time.time() - os.path.getmtime(path) < max_age_hours * 3600):
        with open(path) as f:
            return json.load(f)
    try:
        js = fetch()
    except (requests.RequestException, ValueError):
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
        raise
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(js, f)
    os.replace(tmp, path)
    return js


def get_json(url: str, max_age_hours: Optional[float] = 6) -> dict:
    """GET a JSON document through the disk cache."""
    return _cached(_cache_path(url), max_age_hours, lambda: _download(url))


def prune_raw_gamelogs() -> int:
    """Delete raw ESPN gamelog responses cached by older versions (~400 KB each, 550+ MB in all: they filled the
    500 MB volume and SQLite then failed with "disk I/O error"). Game logs are now cached as parsed rows. Also drops
    the parsed rows of older cache versions (OLD_GAMELOG_DIRS), which lack columns added since."""
    removed = 0
    if not os.path.isdir(config.NBA_CACHE):
        return 0
    for d in OLD_GAMELOG_DIRS:
        path = os.path.join(config.NBA_CACHE, d)
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
            removed += 1
    for e in os.scandir(config.NBA_CACHE):
        if e.is_file() and e.name.endswith(".json"):
            with open(e.path, "rb") as f:
                head = f.read(12)
            if head.startswith(b'{"filters":'):   # only the gamelog endpoint's responses start this way
                os.remove(e.path)
                removed += 1
    return removed


def season_for(day: date) -> int:
    """ESPN names a season by the year it ends in: games from August 2026 on belong to 2027."""
    return day.year + 1 if day.month >= 8 else day.year


def today_et(now: Optional[datetime] = None) -> date:
    now = now or datetime.utcnow()
    return now.replace(tzinfo=timezone.utc).astimezone(ET).date()


def _et(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ET)


# ---------- teams and rosters ----------
def teams() -> list[dict]:
    """[{id, abbr, name}] for all 30 teams."""
    js = get_json(f"{SITE}/teams", max_age_hours=24 * 7)
    return [dict(id=t["team"]["id"], abbr=t["team"]["abbreviation"], name=t["team"]["displayName"])
            for t in js["sports"][0]["leagues"][0]["teams"]]


def position_group(pos: str) -> str:
    """G, F or C from ESPN's position (PG, SG, G, SF, PF, F, C, G-F, F-C...): the first listed role."""
    p = (pos or "").upper()
    return "C" if p.startswith("C") else "F" if p.startswith(("F", "SF", "PF")) else "G"


def rosters() -> pd.DataFrame:
    """Every rostered player: player_id, name, pos, team, headshot."""
    def one(t):
        js = get_json(f"{SITE}/teams/{t['id']}/roster", max_age_hours=3)
        return [dict(player_id=str(a["id"]), name=a["displayName"], pos=(a.get("position") or {}).get("abbreviation", ""),
                     team=t["abbr"], headshot=(a.get("headshot") or {}).get("href"))
                for a in js.get("athletes", [])]
    with ThreadPoolExecutor(8) as ex:
        rows = [r for rs in ex.map(one, teams()) for r in rs]
    return pd.DataFrame(rows, columns=["player_id", "name", "pos", "team", "headshot"]).drop_duplicates("player_id")


# ---------- schedule ----------
def parse_scoreboard(js: dict) -> list[dict]:
    """Games from one scoreboard response, with ESPN's (DraftKings) spread and total when posted."""
    out = []
    for e in js.get("events", []):
        if (e.get("season") or {}).get("type") not in GAME_TYPES:
            continue
        c = e["competitions"][0]
        side = {t["homeAway"]: t for t in c["competitors"]}
        if "home" not in side or "away" not in side:
            continue
        at = _et(e["date"])
        o = (c.get("odds") or [{}])[0]
        spread = o.get("spread")
        status = c["status"]["type"]
        home, away = side["home"]["team"]["abbreviation"], side["away"]["team"]["abbreviation"]
        season = int(e["season"]["year"])
        out.append(dict(
            event_id=str(e["id"]), game_id=f"{season}_{at:%Y%m%d}_{away}_{home}", season=season,
            gameday=at.strftime("%Y-%m-%d"), gametime=at.strftime("%H:%M"), start=e["date"],
            home=home, away=away, home_name=side["home"]["team"]["displayName"], away_name=side["away"]["team"]["displayName"],
            state=status.get("state", "pre"), neutral=bool(c.get("neutralSite")),
            # ESPN's spread is the home team's line (-4.5 = home favored by 4.5); spread_line is the home margin, as nflverse's
            spread_line=None if spread is None else -float(spread), total=o.get("overUnder"),
        ))
    return out


def schedule(start: date, days: int) -> list[dict]:
    """Games from start through start + days - 1 (Eastern dates)."""
    out = []
    for i in range(days):
        d = start + timedelta(days=i)
        age = 0.5 if d <= today_et() + timedelta(days=2) else 6   # near games: statuses and spreads change
        out += parse_scoreboard(get_json(f"{SITE}/scoreboard?dates={d:%Y%m%d}", max_age_hours=age))
    return out


def slate(now: Optional[datetime] = None, look_ahead: int = 10, n_days: int = 2) -> list[dict]:
    """The games the app covers: the next n_days game dates (Eastern) that still have games to tip off, looking up to
    look_ahead days ahead. Before opening night that is opening night and the day after."""
    now = now or datetime.utcnow()
    games = [g for g in schedule(today_et(now), look_ahead) if g["state"] == "pre"]
    dates = sorted({g["gameday"] for g in games})[:n_days]
    return sorted((g for g in games if g["gameday"] in dates), key=lambda g: (g["gameday"], g["gametime"], g["game_id"]))


# ---------- game logs ----------
def parse_gamelog(js: dict, player_id: str) -> list[dict]:
    """One player's regular-season and playoff games from an ESPN gamelog response."""
    names = js.get("names") or []
    idx = {n: i for i, n in enumerate(names)}
    need = ("minutes", "points", "totalRebounds", "assists")
    threes = "threePointFieldGoalsMade-threePointFieldGoalsAttempted"   # "3-7": made-attempted
    if not all(n in idx for n in need):
        return []
    events = js.get("events") or {}
    rows = []
    for st in js.get("seasonTypes", []):
        label = st.get("displayName", "")
        if "Regular" not in label and "Postseason" not in label and "Play-In" not in label:
            continue
        m = re.match(r"(\d{4})-(\d{2})", label)
        season = int(m.group(1)) + 1 if m else None
        for cat in st.get("categories", []):
            if cat.get("type") != "event":
                continue
            for ev in cat.get("events", []):
                info = events.get(ev["eventId"])
                stats = ev.get("stats") or []
                if not info or len(stats) < len(names):
                    continue
                try:
                    vals = {n: float(stats[idx[n]]) for n in need}
                    vals["threes"] = float(stats[idx[threes]].split("-")[0]) if threes in idx else float("nan")
                    for n in ("steals", "blocks"):
                        vals[n] = float(stats[idx[n]]) if n in idx else float("nan")
                except ValueError:
                    continue
                home = info.get("atVs") == "vs"
                at = _et(info["gameDate"])
                rows.append(dict(player_id=player_id, event_id=str(ev["eventId"]), season=season or season_for(at.date()),
                                 date=at.strftime("%Y-%m-%d"), team=(info.get("team") or {}).get("abbreviation"),
                                 opp=(info.get("opponent") or {}).get("abbreviation"), home=home,
                                 min=vals["minutes"], pts=vals["points"], reb=vals["totalRebounds"], ast=vals["assists"], threes=vals["threes"],
                                 stl=vals["steals"], blk=vals["blocks"],
                                 home_score=_num(info.get("homeTeamScore")), away_score=_num(info.get("awayTeamScore"))))
    return rows


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def gamelogs(player_ids, seasons, current: int, log=print) -> pd.DataFrame:
    """Every listed player's games in the given seasons (ESPN season years). Finished seasons are cached for good.
    Only the parsed rows are kept on disk (a few KB per player-season, vs ~400 KB for ESPN's raw response)."""
    jobs = [(pid, s) for pid in player_ids for s in seasons]
    folder = os.path.join(config.NBA_CACHE, GAMELOG_DIR)
    os.makedirs(folder, exist_ok=True)

    def one(job):
        pid, s = job
        url = f"{WEB}/athletes/{pid}/gamelog?season={s}"
        try:
            return _cached(os.path.join(folder, f"{pid}_{s}.json"), 3 if s >= current else None,
                           lambda: [r for r in parse_gamelog(_download(url), pid) if r["season"] == s])
        except requests.RequestException:
            return []
    t0 = time.time()
    with ThreadPoolExecutor(12) as ex:
        rows = [r for rs in ex.map(one, jobs) for r in rs]
    log(f"  game logs: {len(rows):,} player-games for {len(player_ids)} players ({time.time() - t0:.0f}s)")
    df = pd.DataFrame(rows, columns=LOG_COLUMNS)
    # ESPN files the All-Star weekend games (STARS, STRIPES, WORLD...) under the regular season; they'd count as minutes
    # and look like a trade. Filtered here rather than in parse_gamelog, since finished seasons are already cached.
    df = df[df.team.isin(NBA_TEAMS) & df.opp.isin(NBA_TEAMS)]
    return df.drop_duplicates(["player_id", "event_id"]).sort_values(["date", "event_id"]).reset_index(drop=True)


def played(event_id: str) -> Optional[set]:
    """The athletes (ESPN ids) who got into a finished game, from its box score; None if it isn't final or can't be read.
    Players who sat are either listed with didNotPlay or, when inactive, left out entirely: anyone not in this set didn't
    play. It's authoritative as soon as the game ends, unlike the players' game logs, which can lag a day."""
    try:
        js = get_json(f"{SITE}/summary?event={event_id}", max_age_hours=2)
    except (requests.RequestException, ValueError):
        return None
    status = (js.get("header", {}).get("competitions") or [{}])[0].get("status", {}).get("type", {})
    teams = js.get("boxscore", {}).get("players") or []
    if not status.get("completed") or len(teams) < 2:
        return None
    return {str(a["athlete"]["id"]) for t in teams for g in t.get("statistics", []) for a in g.get("athletes", [])
            if not a.get("didNotPlay") and a.get("stats") and a.get("athlete", {}).get("id")}


def team_games(logs: pd.DataFrame) -> pd.DataFrame:
    """One row per game (home, away, scores, date, season), rebuilt from the players' logs."""
    if logs.empty:
        return pd.DataFrame(columns=["event_id", "season", "date", "home_team", "away_team", "home_score", "away_score"])
    g = logs.dropna(subset=["home_score", "away_score"]).drop_duplicates("event_id")
    return pd.DataFrame({"event_id": g.event_id, "season": g.season, "date": g.date,
                         "home_team": g.team.where(g.home, g.opp), "away_team": g.opp.where(g.home, g.team),
                         "home_score": g.home_score, "away_score": g.away_score}).reset_index(drop=True)


# ---------- injuries ----------
def _athlete_id(a: dict) -> Optional[str]:
    for link in a.get("links", []):
        m = re.search(r"/id/(\d+)", link.get("href", ""))
        if m:
            return m.group(1)
    return None


def injuries(max_age_hours: float = 0.25) -> tuple[list[dict], Optional[str]]:
    """(league-wide injury list, when it was fetched as a naive-UTC ISO string)."""
    url = f"{SITE}/injuries"
    js = get_json(url, max_age_hours=max_age_hours)
    path = _cache_path(url)
    fetched = datetime.utcfromtimestamp(os.path.getmtime(path)).isoformat() if os.path.exists(path) else None
    rows = []
    for t in js.get("injuries", []):
        for i in t.get("injuries", []):
            a = i.get("athlete") or {}
            team = (a.get("team") or {}).get("abbreviation")
            d = i.get("details") or {}
            injury = " ".join(x for x in (d.get("side") if d.get("side") not in (None, "Not Specified") else "", d.get("type"),
                                          d.get("detail") if d.get("detail") not in (None, "Not Specified") else "") if x)
            rows.append(dict(player_id=_athlete_id(a), name=a.get("displayName", ""), team=team,
                             pos=(a.get("position") or {}).get("abbreviation", ""), status=i.get("status", ""),
                             injury=injury.strip(), comment=i.get("shortComment") or "", updated=i.get("date"),
                             return_date=d.get("returnDate")))
    return rows, fetched
