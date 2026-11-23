"""Reading a bet slip screenshot (My bets → Add a bet → Upload a screenshot): Claude reads the image into a fixed JSON
shape, then each leg is matched to a player with a game still to start and to one of the site's markets. The result only
fills in the Add a bet sheet: the bettor checks it and saves, so a misread never reaches their record unseen. The image is
read in memory and never stored.
"""
import base64
import json
import re
import time
from datetime import datetime
from typing import Optional

import anthropic
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from . import auth, config, share_links, slip_text
from .bets import ML, NBA_KINDS, recent_players, recent_teams, require_member, upcoming_players, upcoming_teams
from .db import User
from .engine import odds
from .schemas import BetPlayer

router = APIRouter(prefix="/api/me", tags=["bets"])

MODEL = "claude-opus-5-5"
MAX_IMAGE_BYTES = 5 * 1024 * 1024      # the API's per-image limit
MEDIA = {"image/png", "image/jpeg", "image/webp", "image/gif"}
DAILY_READS = 30                        # per account: each read costs a few cents
_reads: dict[int, list[float]] = {}

# What a leg's market means, in the site's terms: the model maps the book's wording onto these (or "other")
KINDS = {
    "ml": "a team's moneyline: to win the game (player is the team's name)",
    "rush": "NFL rushing yards", "rec": "NFL receiving yards", "pass": "NFL passing yards", "rr": "NFL rushing + receiving yards",
    "recs": "NFL receptions", "ptd": "NFL passing touchdowns", "lng": "NFL longest reception (yards)", "rlng": "NFL longest rush (yards)",
    "td": "NFL anytime touchdown scorer", "ftd": "NFL first touchdown scorer",
    "pts": "NBA points", "reb": "NBA rebounds", "ast": "NBA assists", "threes": "NBA 3-pointers made", "pra": "NBA points + rebounds + assists",
    "pr": "NBA points + rebounds", "pa": "NBA points + assists", "stl": "NBA steals", "blk": "NBA blocks", "dd": "NBA double-double",
    "tpl": "NBA triple-double",
}

SYSTEM = (
    "You read screenshots of sports bets placed at a sportsbook and return what the bet was, exactly as shown. Read only what "
    "is visible; use null for anything not shown, and never guess a number.\n\n"
    "For each leg, set `kind` to the market it is, from this list, or \"other\" when it is none of them (a team bet, a spread, "
    "a total, a combined market not listed such as passing + rushing yards, or anything else):\n"
    + "\n".join(f"- {k}: {v}" for k, v in KINDS.items())
    + "\n\nLines: \"90+ Receiving Yards\", \"90 or more\" and \"To record 90+\" are Over 89.5. \"Over 89.5\" is Over 89.5; "
    "\"Under 45.5\" is Under 45.5. Anytime and first touchdown scorer, double-double and triple-double legs are yes/no: side "
    "Over (Under for \"No\"), line 0.5. `text` is the leg as written on the slip, including the player's name.\n\n"
    "`odds` is the whole bet's American price (+1300). `stake` is the total wager; if part of it is bonus bets or a free bet, "
    "put that part in `bonus_stake` too. `to_return` is the payout shown, whatever the book calls it.\n\n"
    "A profit boost (\"50% Profit Boost\", \"25% SGP boost\"): `boost` is its percent (50), and `odds` stays the price "
    "before the boost (the one struck through, if both are shown). `no_sweat` is true when the slip says the stake comes "
    "back as a bonus bet if it loses (\"No Sweat\", \"bet insurance\", \"Second Chance\")."
)

_num = {"type": ["number", "null"]}
_str = {"type": ["string", "null"]}
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["sportsbook", "bet_type", "odds", "stake", "bonus_stake", "to_return", "boost", "no_sweat", "event", "legs"],
    "properties": {
        "sportsbook": _str,
        "bet_type": {"type": "string", "enum": ["single", "parlay"]},
        "odds": {"type": ["integer", "null"]},
        "stake": _num, "bonus_stake": _num, "to_return": _num, "boost": _num, "no_sweat": {"type": "boolean"},
        "event": {"type": "object", "additionalProperties": False, "required": ["teams", "starts"],
                  "properties": {"teams": {"type": "array", "items": {"type": "string"}}, "starts": _str}},
        "legs": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["player", "kind", "side", "line", "odds", "text"],
            "properties": {
                "player": _str,
                "kind": {"type": "string", "enum": [*KINDS, "other"]},
                "side": {"type": "string", "enum": ["Over", "Under"]},
                "line": _num,
                "odds": {"type": ["integer", "null"]},
                "text": {"type": "string"},
            }}},
    },
}


class ScreenLeg(BaseModel):
    """A leg of the draft: matched to a player and market the site grades, or kept as words for the bettor to mark."""
    graded: bool
    player: Optional[BetPlayer] = None
    kind: str
    side: str
    line: float
    odds: Optional[int] = None
    text: str


class ScreenDraft(BaseModel):
    book: Optional[str] = None
    league: Optional[str] = None
    kind: str                         # single | parlay
    odds: Optional[int] = None
    stake: Optional[float] = None
    bonus_stake: Optional[float] = None
    to_return: Optional[float] = None
    boost: Optional[int] = None       # a profit boost, in percent, on top of `odds`
    no_sweat: bool = False            # the stake comes back as a bonus bet if it loses
    legs: list[ScreenLeg]
    notes: list[str]                  # what the bettor should check before saving


class ScreenIn(BaseModel):
    image: str                        # a data URL: data:image/png;base64,...


class SlipTextIn(BaseModel):
    text: str = Field(max_length=20000)   # what OCR read off the screenshot, on the bettor's device


def available() -> bool:
    """Reading screenshots needs an Anthropic API key on the server (ANTHROPIC_API_KEY)."""
    return bool(config.ANTHROPIC_API_KEY())


_client: Optional[anthropic.Anthropic] = None


# a DraftKings share link's preview image: the top of the slip, cut off after the first leg
DK_PREVIEW = ("This is DraftKings' link preview of a bet slip. It is cut off after the first leg. Return a leg for every player "
              "named under the bet's title, in that order. For a player whose leg is shown, read it as usual. For one whose leg "
              "isn't shown, use kind \"other\", side \"Over\", line null, odds null, and `text` exactly the player's name. "
              "\"To Pay\" is `to_return`; a \"Bonus Bet\" tag under the wager means that much of it is `bonus_stake`.")


def read_slip(media_type: str, data_b64: str, prompt: str = "Read this bet slip.") -> dict:
    """Claude's reading of the slip, in SCHEMA's shape. Raises HTTPException with a message for the bettor."""
    global _client
    _client = _client or anthropic.Anthropic()
    try:
        resp = _client.beta.messages.create(
            model=MODEL, max_tokens=8000, system=SYSTEM,
            # a rare safety decline is re-run on Anthropic's recommended fallback model instead of failing the read
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data_b64}},
                {"type": "text", "text": prompt},
            ]}],
        )
    except anthropic.RateLimitError:
        raise HTTPException(503, "Too many screenshots are being read right now. Try again in a minute.")
    except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
        print(f"[bet_screens] Claude API error: {type(e).__name__}: {e}")
        raise HTTPException(502, "Couldn't read the screenshot right now. Try again, or add the bet by hand.")
    if resp.stop_reason == "refusal":
        raise HTTPException(422, "Couldn't read that screenshot. Add the bet by hand.")
    if resp.stop_reason == "max_tokens":
        raise HTTPException(422, "That slip has too many legs to read. Add the bet by hand.")
    text = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except ValueError:
        raise HTTPException(502, "Couldn't read the screenshot right now. Try again, or add the bet by hand.")


def _norm(name: str) -> str:
    return odds.norm(re.sub(r"\s+(jr|sr|ii|iii|iv)\.?$", "", name.strip(), flags=re.I))


def _same_but_short(a: str, b: str) -> bool:
    """One name is the other with its first name cut short: "Chig Okonkwo" and "Chigoziem Okonkwo", "Cam Ward" and "Cameron Ward"."""
    a, b = _norm(a).split(), _norm(b).split()
    if len(a) < 2 or len(b) < 2 or a[1:] != b[1:] or a[0] == b[0]:
        return False
    first, other = sorted((a[0], b[0]), key=len)
    return len(first) >= 3 and other.startswith(first)


def _nearest(p: dict, now: datetime) -> tuple:
    """Sort key for a player's (or team's) games: still to start first, soonest first; then started ones, latest first."""
    gap = (datetime.fromisoformat(p["kickoff"]) - now).total_seconds()
    return (gap <= 0, abs(gap))


def to_draft(slip: dict, db: Session, now: datetime) -> ScreenDraft:
    """Match each leg to a player on the board (preferring the game whose teams the slip shows, then one still to start,
    then the most recent) and a market he's projected in: games from the last bets.LATE_DAYS days count too, for a bet
    the bettor forgot to record. Anything else stays a leg in words, which the bettor marks when it settles."""
    legs, notes, league_votes = [], [], []
    pools = {}
    teams = {t.lower() for t in slip.get("event", {}).get("teams") or []}
    for raw in slip.get("legs") or []:
        kind, name = raw.get("kind"), raw.get("player")
        league = None if kind in ("other", ML) else "nba" if kind in NBA_KINDS else "nfl"
        match = None
        if kind == ML and name:
            # a team's moneyline: by its nickname, which every book shows ("LA Lakers", "Los Angeles Lakers"), in either league
            nick = _norm(name).split()[-1:] or [""]
            for lg in ("nba", "nfl"):
                pool = pools.setdefault(f"ml-{lg}", upcoming_teams(db, lg, now) + recent_teams(db, lg, now))
                found = [t for t in pool if _norm(t["name"]).split()[-1:] == nick]
                if found:
                    match, league = min(found, key=lambda t: _nearest(t, now)), lg
                    break
            raw = {**raw, "line": 0.0, "side": "Over"}
        elif league and name:
            pool = pools.setdefault(league, upcoming_players(db, league, now) + recent_players(db, league, now))
            found = [p for p in pool if _norm(p["name"]) == _norm(name) and kind in p["kinds"]]
            if not found:
                # the book's full first name and the board's short one, or the other way ("Chigoziem" / "Chig Okonkwo"): only
                # when the surname is the same and it's the one such player
                short = [p for p in pool if kind in p["kinds"] and _same_but_short(p["name"], name)]
                found = short if len({p["player_id"] for p in short}) == 1 else []
            # a player with several games: the one whose teams the slip names, else the next one, else the latest played
            found.sort(key=lambda p: (not any(t in team for team in teams for t in (p["team"].lower(), p["opp"].lower())), *_nearest(p, now)))
            match = found[0] if found else None
        line = raw.get("line")
        if match and line is not None:
            league_votes.append(league)
            legs.append(ScreenLeg(graded=True, player=BetPlayer(**match), kind=kind, side=raw.get("side") or "Over", line=float(line),
                                  odds=raw.get("odds"), text=raw.get("text") or ""))
        else:
            legs.append(ScreenLeg(graded=False, kind="custom", side="", line=0.0, odds=raw.get("odds"), text=raw.get("text") or name or "Leg"))
    if any(leg.graded and leg.player.started for leg in legs):
        notes.append("This game has started, so the bet is saved as added late: My bets marks it, since the result may have been known.")
    by_hand = [leg for leg in legs if not leg.graded]
    if by_hand:
        n = len(by_hand)
        notes.append(f"{n} {'leg is' if n == 1 else 'legs are'} marked \"You mark it\": the site doesn't grade {'that market or player' if n == 1 else 'those'}, "
                     "so you'll settle the bet yourself once the rest are graded.")
    if not legs:
        notes.append("No legs could be read from that screenshot.")
    if slip.get("bonus_stake") and slip.get("stake") and slip["bonus_stake"] >= slip["stake"]:
        notes.append("Placed with a bonus bet, so Bonus bet is ticked: its stake isn't your money and isn't paid back.")
    elif slip.get("bonus_stake"):
        notes.append(f"Includes ${slip['bonus_stake']:.2f} in bonus bets: the stake below is the whole wager, so the payout here is "
                     f"${slip['bonus_stake']:.2f} more than the slip's (a bonus bet's stake isn't paid back).")
    # a boost already in odds worked out from the payout stays a note; one on top of the slip's own price is set
    boost = round(slip["boost"]) if slip.get("boost") and not slip.get("odds_from_return") and 1 <= slip["boost"] <= 500 else None
    if slip.get("boost") and boost is None:
        notes.append(f"Includes a {slip['boost']:g}% boost: the odds here are worked out from what the slip says it returns.")
    elif boost:
        notes.append(f"Profit boost is set to {boost}%: the odds are the price before it.")
    no_sweat = bool(slip.get("no_sweat")) and not slip.get("bonus_stake") and not boost
    if no_sweat:
        notes.append("No sweat is set: if it loses, My bets shows the stake coming back as a bonus bet.")
    if slip.get("odds") is None:
        notes.append("The slip didn't show the bet's odds: enter them before saving.")
    if slip.get("stake") is None:
        notes.append("The slip didn't show the stake: enter it before saving.")
    return ScreenDraft(book=slip.get("sportsbook"), league=max(set(league_votes), key=league_votes.count) if league_votes else None,
                       kind="parlay" if len(legs) > 1 else "single", odds=slip.get("odds"), stake=slip.get("stake"),
                       bonus_stake=slip.get("bonus_stake"), to_return=slip.get("to_return"), boost=boost, no_sweat=no_sweat, legs=legs, notes=notes)


@router.post("/bets/read-screenshot", response_model=ScreenDraft, dependencies=[Depends(auth.same_origin)])
def read_screenshot(body: ScreenIn, user: User = Depends(require_member), db: Session = Depends(auth.get_db)):
    """A draft bet from a screenshot of a slip, for the bettor to check in the Add a bet sheet. Nothing is saved here."""
    if not available():
        raise HTTPException(404, "Reading screenshots isn't set up on this server")
    m = re.match(r"^data:(image/[a-z]+);base64,(.+)$", body.image, re.S)
    if not m or m.group(1) not in MEDIA:
        raise HTTPException(422, "Upload a PNG, JPEG, WebP or GIF screenshot")
    media_type, data = m.group(1), re.sub(r"\s", "", m.group(2))
    try:
        size = len(base64.b64decode(data, validate=True))
    except ValueError:
        raise HTTPException(422, "That file isn't a readable image")
    if size > MAX_IMAGE_BYTES:
        raise HTTPException(422, "That screenshot is over 5 MB. Crop it or take a smaller one.")
    _count_read(user)
    return to_draft(read_slip(media_type, data), db, datetime.utcnow())


def _count_read(user: User) -> None:
    """One more of the account's daily Claude reads (screenshots and DraftKings links), or 429 past the limit."""
    now = time.time()
    recent = [t for t in _reads.get(user.id, []) if now - t < 86400]
    if len(recent) >= DAILY_READS:
        raise HTTPException(429, f"You've read {DAILY_READS} screenshots today. Add the bet by hand, or try again tomorrow.")
    _reads[user.id] = recent + [now]


def _name_only_note(slip: dict, draft: ScreenDraft) -> ScreenDraft:
    """Legs read as just a player's name (DraftKings' card, cut off in a share link's preview) named in one note at the top."""
    cut = [leg.get("player") for leg in slip.get("legs") or []
           if leg.get("kind") == "other" and leg.get("line") is None and (leg.get("text") or "").strip() == (leg.get("player") or "").strip()]
    if cut:
        names = cut[0] if len(cut) == 1 else ", ".join(cut[:-1]) + " and " + cut[-1]
        if sum(not leg.graded for leg in draft.legs) == len(cut):   # the name-only legs are the only ones in words: said here instead
            draft.notes = [n for n in draft.notes if "You mark it" not in n]
        draft.notes.insert(0, f"The slip's picture doesn't show the market for {names}, so {'that leg' if len(cut) == 1 else 'those legs'} "
                              f"came in as just {'a name' if len(cut) == 1 else 'names'}. Remove {'it' if len(cut) == 1 else 'each'} and add "
                              f"{'it' if len(cut) == 1 else 'them'} again with the market and line, or upload a screenshot of the whole slip.")
    return draft


def _dk_draft(post: str, user: User, db: Session, now: datetime) -> ScreenDraft:
    """A DraftKings share link's bet as a draft, read from the post's preview image (share_links.dk_preview). The preview
    stops after the first leg, so later legs come back as their player's name only, for the bettor to finish."""
    if not available():   # the browser reads the preview itself instead (GET bets/dk-preview, then OCR and read-slip-text)
        raise HTTPException(422, "This site can't read DraftKings links yet. Upload a screenshot of the slip instead.")
    _count_read(user)
    png = share_links.dk_preview(post)
    try:
        slip = read_slip("image/png", base64.b64encode(png).decode(), DK_PREVIEW)
    except HTTPException as e:
        if e.status_code == 502:   # said for a link, not a screenshot
            raise HTTPException(502, "Couldn't read that DraftKings link right now. Try again, or upload a screenshot of the slip.")
        raise
    slip["sportsbook"] = slip.get("sportsbook") or "DraftKings"
    return _name_only_note(slip, to_draft(slip, db, now))


def _share_draft(slip: dict, db: Session, now: datetime) -> ScreenDraft:
    """A FanDuel share link's bet as a draft: the picks found among FanDuel's stored lines, matched to the board as usual,
    with notes saying what the link couldn't carry (the stake, any pick the site never loaded, a parlay's own price)."""
    info = slip.pop("share")
    draft = to_draft(slip, db, now)
    notes = [n for n in draft.notes if "stake" not in n and "odds" not in n]   # a link never carries them: said below instead
    if info["missing"]:
        n = info["missing"]
        notes.insert(0, f"{n} of the {info['picks']} picks {'is' if n == 1 else 'are'}n't in the FanDuel lines this site has loaded "
                        f"(an alternate line, say), so {'it' if n == 1 else 'they'} couldn't be filled in. Add {'it' if n == 1 else 'them'} by hand.")
    if info["priced_from_legs"]:
        games = {leg.player.game_id for leg in draft.legs if leg.graded}
        if len(games) == 1:
            notes.append("The parlay's price is each leg's FanDuel price at the last odds update, multiplied: FanDuel prices same-game "
                         "parlays its own way, so put in the price from your slip.")
        else:
            notes.append("The parlay's price is each leg's FanDuel price at the last odds update, multiplied: check it against your slip, "
                         "since prices move.")
    elif draft.legs:
        notes.append("Prices are FanDuel's at the last odds update: check them against your slip.")
    if not draft.legs:
        notes.append("None of the link's picks are in the FanDuel lines this site has loaded. Upload a screenshot of the slip instead.")
    notes.append("A share link doesn't include the stake: enter it before saving.")
    draft.notes = notes
    return draft


@router.post("/bets/read-slip-text", response_model=ScreenDraft, dependencies=[Depends(auth.same_origin)])
def read_slip_text(body: SlipTextIn, user: User = Depends(require_member), db: Session = Depends(auth.get_db)):
    """A draft bet from the text of a slip screenshot, read by OCR in the browser (free; used when Claude isn't set up), or
    from a FanDuel or DraftKings share link in the text (pasted, or shared to the app). Nothing is saved here."""
    now = datetime.utcnow()
    post = share_links.find_dk_post(body.text)
    if post:
        return _dk_draft(post, user, db, now)
    shared = share_links.read(body.text, db, now)
    if shared is not None:
        return _share_draft(shared, db, now)
    slip = slip_text.parse(body.text)
    draft = _name_only_note(slip, to_draft(slip, db, now))
    if not draft.legs:
        draft.notes = ["Couldn't find the bet's legs in that screenshot. Reading works with bet365, FanDuel, DraftKings and Fanatics slips; for "
                       "others, add the bet by hand."]
    return draft


@router.get("/bets/dk-preview/{post}")
def dk_preview(post: str, user: User = Depends(require_member)):
    """A DraftKings share link's preview image, for the bettor's browser to read with OCR when Claude isn't set up here."""
    post = share_links.find_dk_post(f"dksb://sb/social/post/{post}")
    if not post:
        raise HTTPException(422, "That isn't a DraftKings share link.")
    return Response(share_links.dk_preview(post), media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})
