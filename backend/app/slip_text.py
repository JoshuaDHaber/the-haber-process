"""Reading a bet slip from its text (My bets → Add a bet → Upload a screenshot, when Claude isn't set up): the browser reads
the screenshot with OCR (Tesseract.js, on the bettor's device) and sends only the text here. Each sportsbook lays its slip
out differently, so each has its own rules; the result has bet_screens.SCHEMA's shape, and bet_screens.to_draft matches it
to players exactly as it does Claude's reading. Rules exist for bet365, FanDuel and DraftKings.
"""
import re
from typing import Optional

# American odds standing alone (+1300, -110): not part of a dollar amount or a longer number
ODDS = re.compile(r"(?<![\w$.,])([+-]\d{3,5})(?![\d.,])")
MONEY = re.compile(r"\$\s?(\d[\d,]*\.\d{2})")
# OCR's misreadings of a roman suffix (Luther Burden III → "lll", "Ill", "1II")
ROMAN = {"iii": "III", "ii": "II", "iv": "IV"}

# What a leg's words mean, most specific first: the first pattern found in the leg's text gives its market
MARKETS = [
    (r"longest (reception|catch)", "lng"), (r"longest rush", "rlng"),
    (r"pass(ing)? (\+|and|&) r", "other"),   # passing + rushing yards (FanDuel cuts it to "PASSING + R..."): not projected
    (r"rush(ing)? (\+|and|&) rec|rush(ing)? \+ re|rush ?\+ ?rec", "rr"),
    (r"pass(ing)? (td|tds|touchdowns?)", "ptd"), (r"first (td|touchdown)", "ftd"),
    (r"any ?time (td|touchdown)|to score|anytime scorer", "td"),
    (r"pass(ing)? (yds|yards)|\bpass(ing)?\b", "pass"), (r"rush(ing)? (yds|yards)|\brush(ing)?\b", "rush"),
    (r"receptions|\bcatches\b", "recs"), (r"rec(eiving)? (yds|yards)|\breceiv", "rec"),
    (r"points ?\+ ?rebounds ?\+ ?assists|pts ?\+ ?reb\w* ?\+ ?a|\bpra\b", "pra"),   # "PTS + REB + A..." cut short
    (r"points ?\+ ?rebounds|pts ?\+ ?reb", "pr"), (r"points ?\+ ?assists|pts ?\+ ?ast", "pa"),
    # combos the site does not project (checked after PRA, PR and PA): kept for marking by hand, never read as one stat
    (r"rebounds ?\+ ?assists|reb\w* ?\+ ?ast|steals ?\+ ?blocks|stl ?\+ ?blk", "other"),
    (r"double.double", "dd"), (r"triple.double", "tpl"),
    (r"three|3.pt|3.point", "threes"), (r"rebounds", "reb"), (r"assists", "ast"), (r"steals", "stl"), (r"blocks", "blk"),
    (r"points", "pts"),
]
YES_NO = {"td", "ftd", "dd", "tpl"}


def _kind(text: str) -> str:
    t = " ".join(text.lower().split())
    return next((kind for pat, kind in MARKETS if re.search(pat, t)), "other")


def _name(raw: str) -> str:
    """A player's name from an OCR line: drop the marks a bullet or icon reads as ("©", "O", "T", "“="), fix roman suffixes."""
    words = raw.replace("’", "'").split()
    while words and (not re.search(r"[A-Za-z]{2}", words[0]) or len(words[0]) == 1):
        words.pop(0)
    out = []
    for w in words:
        roman = re.fullmatch(r"[lI1|]{2,3}|[lI1|]V", w)
        out.append(ROMAN.get(w.lower().replace("l", "i").replace("1", "i").replace("|", "i"), w) if roman else w)
    name = " ".join(out).strip(" :-")
    if name.isupper():
        name = " ".join(ROMAN.get(w.lower(), w.title()) for w in name.split())
    return name


def _line(text: str, kind: str) -> tuple[str, Optional[float]]:
    """Side and line: "90+" and "90 or more" are Over 89.5; "Over 45.5" / "Under 45.5" as written; yes/no markets 0.5."""
    t = text.lower()
    m = re.search(r"\b(over|under|o|u)\s?(\d+(\.\d)?)\b", t)
    if m and (m.group(1) in ("over", "under") or m.group(3)):
        return ("Under" if m.group(1).startswith("u") else "Over"), float(m.group(2))
    m = re.search(r"(\d+(\.\d)?)\s?(\+|or more)", t)
    if m:
        return "Over", float(m.group(1)) - 0.5
    if kind in YES_NO:
        return ("Under" if re.search(r"\bno\b", t) else "Over"), 0.5
    return "Over", None


def _leg(name: str, market_text: str, title: str) -> dict:
    kind = _kind(market_text)
    side, line = _line(title, kind)
    return {"player": name or None, "kind": kind, "side": side, "line": line, "odds": None,
            "text": " ".join(title.split())}


def _label(ln: str) -> bool:
    """A market line under a leg ("Receiving Yds", "Longest Reception"): short, no numbers, and a market."""
    return len(ln.split()) <= 4 and not re.search(r"\d", ln) and _kind(ln) != "other"


def _bet365(lines: list[str]) -> list[dict]:
    """bet365: "Jaxon Smith-Njigba: 90+ Rec Yards", wrapping onto further lines, then the market ("Receiving Yds")."""
    blocks, cur = [], None
    for ln in lines:
        # a leg starts "Name: ..."; the name ends in a letter, so a time ("17:33", "8:15 PM") isn't one
        if re.match(r"^.*[A-Za-z]{2}[A-Za-z.'\- ]*?[A-Za-z.]:\s*\S", ln) and not re.match(r"(?i)^\W*(wager|stake|net return|to win)", ln):
            cur = [ln]
            blocks.append(cur)
        elif cur is not None:
            cur.append(ln)
            if _label(ln):
                cur = None   # the market line ends the leg
    out = []
    for block in blocks:
        name, rest = block[0].split(":", 1)
        name = _name(name)
        words = block[1:-1] if len(block) > 1 and _label(block[-1]) else block[1:]
        title = " ".join([rest.strip(), *words])
        out.append(_leg(name, " ".join([rest, *block[1:]]), f"{name} {title}"))
    return out


def _caps(ln: str) -> bool:
    letters = re.sub(r"[^A-Za-z]", "", ln)
    return len(letters) >= 8 and sum(c.isupper() for c in letters) / len(letters) > 0.85


def _fanduel(lines: list[str]) -> list[dict]:
    """FanDuel: "Jaxon Smith-Njigba 90+ Yards" over the market in capitals ("JAXON SMITH-NJIGBA - ALT RECEIV..."), or over
    just the market for a yes/no leg ("ANY TIME TOUCHDOWN SCORER")."""
    out = []
    for i, ln in enumerate(lines):
        if not i or not _caps(ln) or _caps(lines[i - 1]):
            continue
        # the title line, the leg's own price beside it or on a line of its own between (Apple's reader)
        j, leg_odds = i - 1, None
        if ODDS.fullmatch(lines[j].strip()) and j > 0 and not _caps(lines[j - 1]):
            leg_odds, j = int(lines[j].strip()), j - 1
        title = lines[j]
        priced = re.search(r"\s([+-]\d{3,5})\s*$", title)
        if priced:
            leg_odds, title = int(priced.group(1)), title[:priced.start()].rstrip()
        m = re.match(r"^\W*([A-Z][A-Z'.\-|1 ]+?)\s+-\s+(.+)$", ln)   # "|" and "1": a misread roman suffix (I||)
        if m:
            name, market = _name(m.group(1)), m.group(2)
        elif _kind(ln) != "other":
            name, market = _name(re.split(r"\s\d", title)[0]), ln
        else:
            continue
        # the title line is the name and what the leg needs ("Rome Odunze 20+ Yards"), the bullet's marks before it
        at = title.lower().find(name.lower())
        if at > 0 and re.search(r"[A-Za-z]{2}", title[:at]):
            at = -1   # only the end of the name ("K TOWNS" → "Towns" in "Karl-Anthony Towns"): take the title's whole name
        if at >= 0:
            name, title = title[at:at + len(name)], title[at:]   # the name as the slip writes it ("AJ", not "Aj")
        else:
            # the capitals shorten it ("K TOWNS"): the title has it whole ("Karl-Anthony Towns Over 31.5")
            title = _name(title)
            name = re.split(r"(?i)\s+(?:over|under)\b|\s+\d", title)[0].strip() or name
        market = re.sub(r"^\W+|[.…]+$", "", market.split(" - ")[-1]).strip().title()
        leg = _leg(name, f"{market} {title}", title)
        leg["odds"] = leg_odds
        if leg["kind"] in YES_NO and leg["line"] == 0.5:
            leg["text"] = f"{name} {market}"
        elif re.search(r"(?i)pass(ing)? \+ r", market):
            leg["text"] = re.sub(r"(?i)\byards\b", "Passing + Rushing Yards", title)
        elif leg["kind"] == "other":
            leg["text"] = f"{title} ({market}…)"   # "Yards" alone doesn't say which: keep the slip's market
        out.append(leg)
    return out


# DraftKings' market names, longest first so "Points + Assists" wins over "Points"
DK_MARKETS = re.compile(r"(?i)\b(points\s?\+\s?rebounds\s?\+\s?assists|points\s?\+\s?rebounds|points\s?\+\s?assists|rebounds\s?\+\s?assists"
                        r"|steals\s?\+\s?blocks|three pointers made|threes made|points|rebounds|assists|steals|blocks|turnovers"
                        r"|rushing\s?\+\s?receiving yards|passing yards|rushing yards|receiving yards|receptions|passing touchdowns"
                        r"|longest reception|longest rush|anytime (?:td|touchdown) scorer|interceptions thrown|pass completions)\b")
# what a leg needs on a line of its own: "35+", or "Over 24.5" / "O 24.5" for a main line
DK_NEED = re.compile(r"(?i)(?<![\d.])(\d+(?:\.\d)?)\+|\b(over|under|o|u)\s?(\d+\.\d)\b")


def _draftkings(lines: list[str]) -> list[dict]:
    """DraftKings: what the leg needs ("35+") on one line, beside the icons OCR reads as noise ("© @ 20+0©"), then the player
    and market on the next ("Luka Doncic Three Pointers Made"). The header's list of every leg's need ("35+, 20+, 5+") is
    skipped: a leg's line has one."""
    out = []
    for ln, nxt in zip(lines, lines[1:]):
        needs = DK_NEED.findall(ln)
        market = DK_MARKETS.search(nxt)
        if len(needs) != 1 or not market or re.search(r"(?i)parlay|wager|paid", ln):
            continue
        plus, side_word, ou_line = needs[0]
        # the player is the capitalised words right before the market; icon noise before them ("ify", "a4,", "g") is dropped
        words = nxt[:market.start()].split()
        name = []
        for w in reversed(words):
            if not re.fullmatch(r"[A-Z][A-Za-z.'\-]*|[A-Z]{2,3}|(?:Jr|Sr)\.?|I{2,3}|IV", w):
                break
            name.insert(0, w)
        name = _name(" ".join(name))
        label = re.sub(r"\s+", " ", market.group(1)).title().replace(" + ", " + ")
        need = f"{plus}+" if plus else f"{'Under' if side_word.lower().startswith('u') else 'Over'} {ou_line}"
        out.append(_leg(name, label, f"{name} {need} {label}"))
    return out


def _moneylines(lines: list[str]) -> list[dict]:
    """bet365 moneylines: "LA Lakers -575" over "Money Line" (Apple's reader may put the price on a line of its own)."""
    out = []
    for i, ln in enumerate(lines):
        if not re.fullmatch(r"(?i)\W*money ?line\W*", ln) or i == 0:
            continue
        prev = re.sub(r"(?i)\|?\s*early pay\w*.*$", "", lines[i - 1]).strip(" |")
        if ODDS.fullmatch(prev.strip()) and i > 1:   # the price alone: the team is the line above
            prev = f"{lines[i - 2]} {prev}"
        m = re.match(r"^(.*?)\s*([+-]\d{3,5})\b", prev)
        team = _name(m.group(1) if m else prev)
        if not re.search(r"[A-Za-z]{2}", team):
            continue
        odds = int(m.group(2)) if m else None
        out.append({"player": team, "kind": "ml", "side": "Over", "line": 0.0, "odds": odds,
                    "text": f"{team} moneyline" + (f" {m.group(2)}" if m else "")})
    return out


def _american(dec: float) -> int:
    return round((dec - 1) * 100) if dec >= 2 else -round(100 / (dec - 1))


def parse(text: str) -> dict:
    """The slip in bet_screens.SCHEMA's shape, from OCR text; legs it couldn't make out are simply missing."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    low = text.lower()
    fd, b365, dk = _fanduel(lines), _bet365(lines) + _moneylines(lines), _draftkings(lines)
    legs = fd if len(fd) >= len(b365) else b365
    if dk and ("draftkings" in low or len(dk) > len(legs)):
        legs = dk
    book = "bet365" if "bet365" in low or (legs is b365 and legs) else "FanDuel" if "fanduel" in low or (legs is fd and legs) else None
    for b in ("DraftKings", "BetMGM", "Caesars", "ESPN BET", "Fanatics", "Hard Rock", "BetRivers"):
        if b.lower() in low:
            book = b
    # the bet's price: beside "Parlay" / "Single" or on the line after it (Apple's reader splits "SAME GAME PARLAY" / "+1300")
    head = next((ln if ODDS.search(ln) else nxt for ln, nxt in zip(lines, lines[1:] + [""])
                 if re.search(r"(?i)parlay|single|straight", ln) and not re.search(r"(?i)boost", ln)
                 and (ODDS.search(ln) or ODDS.fullmatch(nxt.strip()))), None)
    odds_m = ODDS.search(head) if head else ODDS.search(text) if not any(leg["odds"] for leg in legs) else None
    stake = bonus = to_return = None
    for i, ln in enumerate(lines):
        if re.search(r"(?i)\b(wager|stake|bet amount)\b", ln):
            here = MONEY.findall(ln)
            amounts = here or (MONEY.findall(lines[i + 1]) if i + 1 < len(lines) else [])
            if amounts:
                stake = float(amounts[0].replace(",", ""))
                if len(amounts) > 1 and re.search(r"(?i)return|payout|to win|paid", ln):
                    to_return = float(amounts[1].replace(",", ""))
            break
    # the payout on its own line, its amount beside it or on the next line (Apple's reader splits "Net Return" / "$110.82")
    for i, ln in enumerate(lines):
        if to_return is None and re.fullmatch(r"(?i)\W*(net return|(total )?to return|returns?|payout|potential payout|to win|to pay)\W*(\$.*)?", ln):
            amounts = MONEY.findall(ln) or (MONEY.findall(lines[i + 1]) if i + 1 < len(lines) else [])
            if amounts:
                to_return = float(amounts[0].replace(",", ""))
    m = re.search(r"\$\s?(\d[\d,]*\.\d{2})\s*(bonus|free) bet", text, re.I)
    if m:
        bonus = float(m.group(1).replace(",", ""))
    teams = []
    m = re.search(r"([A-Z][A-Za-z.' ]+?)\s+(?:@|vs\.?|at)\s+([A-Z][A-Za-z.' ]+?)(?:\s+\d|\s*$)", text, re.M)
    if m:
        teams = [m.group(1).strip(), m.group(2).strip()]
    odds = int(odds_m.group(1)) if odds_m else None
    if odds is None and len(legs) == 1:
        odds = legs[0]["odds"]
    elif odds is None and stake and to_return and to_return > stake:
        odds = _american(to_return / stake)   # no price shown (a boosted parlay of priced legs): what it pays says it
    boost = re.search(r"(\d+(?:\.\d+)?)\s?%[\s|]*(?:parlay |profit |odds )?boost", text, re.I)
    return {"boost": float(boost.group(1)) if boost else None,
            "sportsbook": book, "bet_type": "parlay" if len(legs) > 1 else "single", "odds": odds,
            "stake": stake, "bonus_stake": bonus, "to_return": to_return, "event": {"teams": teams, "starts": None}, "legs": legs}
