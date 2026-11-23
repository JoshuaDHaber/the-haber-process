"""Reading a FanDuel share link (My bets → Add a bet, a pasted link, or text shared to the app): FanDuel's share button
gives "sportsbook.fanduel.com/addToBetslip?shareCode=…". FanDuel's page decodes the code with an unauthenticated lookup
that returns each pick's market and selection ids, and nothing else. The odds sync already stores FanDuel's betslip
links for every line it fetches, main and alternate ("addToBetslip?marketId=42.6…&selectionId=237476"), so each pick is
found by its ids among those, which says who, which market, which side and line, and FanDuel's price at the last sync.

The lookup isn't a published API: if FanDuel changes or blocks it, reading fails with a message, and a screenshot still
works. The response also names the sharer's FanDuel customer id; it's never kept or logged.

DraftKings' share links are read differently, from the post's preview image: see the DraftKings section below.
"""
import re
from datetime import datetime, timedelta
from typing import Optional

import requests
from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .db import AltLine, NbaAltLine, NbaOddsLine, OddsLine

DECODE = "https://api.sportsbook.fanduel.com/share-bet/decode-short-url/{}"
SHARE = re.compile(r"sportsbook\.fanduel\.com/addToBetslip\?\S*?\bshareCode=([A-Za-z0-9_-]{4,32})", re.I)
# a betslip link with the picks in it (as the odds sync stores them, or copied from FanDuel's site): marketId / selectionId
# pairs, plain or indexed ("marketId[0]=…")
DIRECT = re.compile(r"sportsbook\.fanduel\.com/addToBetslip\?(\S+)", re.I)
PAIR = re.compile(r"\bmarketId(?:%5B|\[)?(\d*)(?:%5D|\])?=([\d.]+)", re.I)
SEL = re.compile(r"\bselectionId(?:%5B|\[)?(\d*)(?:%5D|\])?=(\d+)", re.I)
# how a leg reads when the site can't grade it (kept in words for marking by hand)
WORDS = {"pts": "points", "reb": "rebounds", "ast": "assists", "threes": "threes", "pra": "pts + reb + ast", "pr": "pts + reb",
         "pa": "pts + ast", "stl": "steals", "blk": "blocks", "dd": "double-double", "tpl": "triple-double", "pass": "passing yards",
         "rush": "rushing yards", "rec": "receiving yards", "recs": "receptions", "rr": "rush + rec yards", "ptd": "passing TDs",
         "td": "anytime TD", "ftd": "first TD", "lng": "longest reception", "rlng": "longest rush"}
RECENT_DAYS = 10    # quotes this old or newer: a share is of a bet placed lately
MAX_PICKS = 25


def find_link(text: str) -> Optional[tuple]:
    """("code", share code) or ("picks", [(market id, selection id), …]) for the first FanDuel betslip link in the text."""
    m = SHARE.search(text or "")
    if m:
        return "code", m.group(1)
    m = DIRECT.search(text or "")
    if m:
        markets = {i or "0": v for i, v in PAIR.findall(m.group(1))}
        sels = {i or "0": v for i, v in SEL.findall(m.group(1))}
        picks = [(markets[k], sels[k]) for k in markets if k in sels]
        if picks:
            return "picks", picks[:MAX_PICKS]
    return None


def decode(code: str, get=None) -> list:
    """The picks behind a share code: [(market id, selection id), …]. Raises HTTPException when FanDuel won't say."""
    get = get or requests.get   # looked up per call, so tests can stand in for FanDuel
    try:
        r = get(DECODE.format(code), timeout=8, headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"})
    except requests.RequestException:
        raise HTTPException(502, "FanDuel didn't answer. Try again in a minute, or upload a screenshot of the slip.")
    if r.status_code in (400, 404):
        raise HTTPException(422, "FanDuel doesn't know that share link. Check it's the whole link, or upload a screenshot.")
    if r.status_code != 200:
        raise HTTPException(502, "FanDuel wouldn't read that link right now. Upload a screenshot of the slip instead.")
    try:
        content = r.json().get("content") or []   # the sharer's customerId is in this response too: deliberately unread
    except ValueError:
        raise HTTPException(502, "FanDuel wouldn't read that link right now. Upload a screenshot of the slip instead.")
    picks = [(str(c.get("marketId", "")), str(c.get("selectionId", ""))) for c in content if isinstance(c, dict)]
    return [p for p in picks if re.fullmatch(r"[\d.]+", p[0]) and p[1].isdigit()][:MAX_PICKS]


# where the picks are looked for, in order: the main lines, then the alternate lines (a "3+ threes" pick is an alternate
# line, and FanDuel lists every player of a game in one alternate market, each a selection of it)
TABLES = (NbaOddsLine, OddsLine, NbaAltLine, AltLine)


def _leg(row, over: bool) -> dict:
    """A stored quote, from the side whose link is the pick, as a slip leg (bet_screens.SCHEMA's shape)."""
    odds = row.over_odds if over else row.under_odds
    side = "Over" if over else "Under"
    text = f"{row.player} moneyline" if row.market == "ml" else f"{row.player} {side} {row.line:g} {WORDS.get(row.market, row.market)}"
    return {"player": row.player, "kind": row.market, "side": side, "line": row.line,
            "odds": None if odds is None else int(round(odds)), "text": text}


def _quotes(db: Session, picks: list, now: datetime) -> dict:
    """Each pick's latest stored FanDuel quote whose betslip link is that pick, as a slip leg: {(market, selection): leg}.
    One pass per table for all the picks still unfound (the alternate tables are large and indexed only by game)."""
    since = now - timedelta(days=RECENT_DAYS)
    found: dict = {}
    for model in TABLES:
        todo = [p for p in picks if p not in found]
        if not todo:
            break
        likes = [f"%marketId={m}&selectionId={sel}%" for m, sel in todo]
        rows = db.scalars(select(model).where(model.book == "FanDuel", model.fetched_at >= since,
                                              or_(*(model.over_link.like(x) for x in likes), *(model.under_link.like(x) for x in likes)))
                          .order_by(model.fetched_at.desc())).all()
        for m, sel in todo:
            want = re.compile(rf"\bmarketId={re.escape(m)}&selectionId={sel}(?!\d)")   # not a longer id that starts the same
            for row in rows:   # newest first
                over = bool(row.over_link and want.search(row.over_link))
                if not over and not (row.under_link and want.search(row.under_link)):
                    continue
                if row.market == "ml" and not over:
                    continue   # a moneyline's "under" link is the other team's pick: its own row (where it's the over) names it
                found[(m, sel)] = _leg(row, over)
                break
    return found


def read(text: str, db: Session, now: datetime, get=None) -> Optional[dict]:
    """The bet behind a FanDuel link in the text, in bet_screens.SCHEMA's shape (for to_draft), or None if there's no link."""
    found = find_link(text)
    if not found:
        return None
    picks = decode(found[1], get) if found[0] == "code" else found[1]
    if not picks:
        raise HTTPException(422, "That FanDuel link has no picks in it. Upload a screenshot of the slip instead.")
    quotes = _quotes(db, picks, now)
    legs = [quotes[p] for p in picks if p in quotes]
    missing = len(picks) - len(legs)
    odds = None
    if len(picks) == 1 and legs:
        odds = legs[0]["odds"]
    elif legs and not missing and all(leg["odds"] for leg in legs):
        dec = 1.0
        for leg in legs:
            o = leg["odds"]
            dec *= 1 + o / 100 if o > 0 else 1 + 100 / -o
        odds = round((dec - 1) * 100) if dec >= 2 else -round(100 / (dec - 1))
    return {"sportsbook": "FanDuel", "bet_type": "parlay" if len(picks) > 1 else "single", "odds": odds, "stake": None,
            "bonus_stake": None, "to_return": None, "event": {"teams": [], "starts": None}, "legs": legs,
            "share": {"picks": len(picks), "missing": missing, "priced_from_legs": len(legs) > 1 and odds is not None}}


# ---------- DraftKings ----------
# DraftKings' share button posts the bet to DraftKings Social and gives that post's link
# ("sportsbook.draftkings.com/social/post/<uuid>?slipAdd"). The post itself is only shown to a signed-in DraftKings user and
# its API refuses anything but DraftKings' own pages, so its picks can't be looked up the way FanDuel's can. What is public
# is the post page's preview image (its og:image, for link previews in messages): a picture of the slip's top, with the
# price, the stake, the payout, every leg's player, and the first leg in full. That image is read like a screenshot.
DK_POST = re.compile(r"(?:sportsbook\.draftkings\.com/(?:[a-z]{2}/)?social|dksb://sb/social(?:web)?)/post/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.I)
DK_PAGE = "https://sportsbook.draftkings.com/social/post/{}"
# the preview image, only from DraftKings' own image path (never a URL the page could point anywhere)
DK_IMAGE = re.compile(r'<meta\s+property="og:image"\s+content="(https://sportsbook\.draftkings\.com/comments/seo/assets/[A-Za-z0-9._-]+\.png)"', re.I)
DK_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36",
              "Accept-Language": "en-US,en;q=0.9"}
DK_MAX_BYTES = 2 * 1024 * 1024
DK_BUSY = "DraftKings wouldn't open that link right now. Upload a screenshot of the slip instead."


def find_dk_post(text: str) -> Optional[str]:
    """The post id of the first DraftKings share (Social post) link in the text, or None."""
    m = DK_POST.search(text or "")
    return m.group(1).lower() if m else None


def dk_preview(post: str, get=None) -> bytes:
    """The PNG DraftKings shows as the post's link preview. Raises HTTPException when DraftKings won't give it."""
    get = get or requests.get   # looked up per call, so tests can stand in for DraftKings
    try:
        page = get(DK_PAGE.format(post), timeout=10, headers={**DK_HEADERS, "Accept": "text/html"})
        if page.status_code == 404:
            raise HTTPException(422, "DraftKings doesn't know that link. Check it's the whole link, or upload a screenshot.")
        if page.status_code != 200:
            raise HTTPException(502, DK_BUSY)
        m = DK_IMAGE.search(page.text)
        if not m:
            # a post that's gone, or one DraftKings didn't make a preview for
            raise HTTPException(422, "DraftKings didn't show that bet in its link. Upload a screenshot of the slip instead.")
        img = get(m.group(1), timeout=10, headers={**DK_HEADERS, "Accept": "image/png"})
    except requests.RequestException:
        raise HTTPException(502, "DraftKings didn't answer. Try again in a minute, or upload a screenshot of the slip.")
    if img.status_code != 200 or not img.content.startswith(b"\x89PNG") or len(img.content) > DK_MAX_BYTES:
        raise HTTPException(502, DK_BUSY)
    return img.content
