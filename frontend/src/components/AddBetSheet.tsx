import { useQuery, useQueryClient } from "@tanstack/react-query";
import { IconPhoto, IconPlus, IconX } from "@tabler/icons-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import type { Kind } from "../api";
import { useAuth } from "../auth";
import { addCustomBet, addPropBet, COMMON_BOOKS, LATE_DAYS, findBetPlayers, legLeague, money, myBets, NO_PROMO, parseOdds, promoBody, promoReady, promoReturn, readScreenshot, readSlipOnDevice, readSlipText, screenshotDataUrl,
  type BetPlayer, type ManualLeg, type ScreenDraft } from "../bets";
import { isYesNo, KIND_LABEL, namesItself, americanOdds, playLabel } from "../format";
import { toAmerican, toDecimal, toWin } from "../slip";
import { useDebounced } from "../useDebounced";
import PromoFields from "./PromoFields";
import Sheet from "./Sheet";
import SidePill from "./SidePill";
import TagInput from "./TagInput";

type Mode = "prop" | "other";
const pad = (n: number) => String(n).padStart(2, "0");
/** The next whole hour, as the date and time fields want it (local time). */
function nextHour() {
  const d = new Date(Date.now() + 3600_000);
  return { date: `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`, time: `${pad(d.getHours())}:00` };
}
// The Shortcut's iCloud link (pwa/add-slip.shortcut, shared from Shortcuts): it opens the Shortcuts app from anywhere,
// including the app added to the home screen, which can't download the file itself
const SHORTCUT = "https://www.icloud.com/shortcuts/20ddd9b188774cc4936675bab56a4ac2";
// an iPhone or iPad (iPadOS reports itself as a Mac with touch): it can't share to a web app, but the Shortcut can
const IOS = typeof navigator !== "undefined" && (/iPhone|iPad|iPod/.test(navigator.userAgent) || (/Macintosh/.test(navigator.userAgent) && navigator.maxTouchPoints > 1));
/** The parlay price its legs make together, when every leg's own price is known (most slips show only the parlay's). */
const legsPrice = (legs: ManualLeg[]) =>
  legs.every((l) => l.odds != null) ? toAmerican(legs.reduce((d, l) => d * toDecimal(l.odds!), 1)) : null;
// markets books mostly offer as "n or more" (alternate lines): a new leg in one starts as "At least"
const COUNTS = new Set<string>(["threes", "stl", "blk", "recs", "ptd"]);
const when = (iso: string) => new Date(iso + "Z").toLocaleString(undefined, { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });

/** My bets → "Add a bet": record a bet placed outside the app. Before its game starts is the rule; one the bettor forgot can
 *  still be added up to LATE_DAYS after, and is marked late on My bets (the result may have been known).
 *  A player prop picked from a search is graded from the box scores like a tracked one; anything else (a spread, a total,
 *  another sport, a boost) is described in words and marked by hand. */
export default function AddBetSheet({ onClose, defaultLeague, sharedSlip }: { onClose: () => void; defaultLeague: "nfl" | "nba"; sharedSlip?: File | string }) {
  const auth = useAuth();
  const qc = useQueryClient();
  const known = useQuery({ queryKey: ["my-bets", auth?.user?.email, {}], queryFn: () => myBets({}), staleTime: 60_000, enabled: !!auth?.user?.age_confirmed });
  const [mode, setMode] = useState<Mode>("prop");
  const [league, setLeague] = useState<"nfl" | "nba" | "oth">(defaultLeague);
  const [book, setBook] = useState("");
  const [stake, setStake] = useState("");
  const [promo, setPromo] = useState(NO_PROMO);
  const [tags, setTags] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const [done, setDone] = useState(false);
  // a slip screenshot read into the form (backend bet_screens): the bettor checks it, then saves as usual
  const file = useRef<HTMLInputElement>(null);
  const [reading, setReading] = useState(false);
  const [notes, setNotes] = useState<string[]>([]);
  // a FanDuel share link pasted in: read like shared slip text (the server spots the link: backend share_links)
  const [link, setLink] = useState("");
  const linkOk = /fanduel\.com\/addToBetslip\?\S*(shareCode|marketId)/i.test(link);

  // a player prop: search, then market, side, line and the price taken; legs collect into a single or a parlay
  const [q, setQ] = useState("");
  const query = useDebounced(q.trim(), 250);
  const propLeague = league === "nba" ? "nba" : "nfl";
  const found = useQuery({ queryKey: ["bet-players", propLeague, query], queryFn: () => findBetPlayers(propLeague, query), enabled: mode === "prop" && query.length >= 2 });
  const [player, setPlayer] = useState<BetPlayer | null>(null);
  const [kind, setKind] = useState<Kind | "">("");
  const [side, setSide] = useState<"Over" | "Under">("Over");
  const [line, setLine] = useState("");
  // "At least": the leg as books write their alternate lines ("2+ 3-pointers made"), a whole number that's Over n - 0.5
  const [atLeast, setAtLeast] = useState(false);
  // the leg just added, said back (and read out) so it's clear it went onto the bet
  const [added, setAdded] = useState("");
  const [legOdds, setLegOdds] = useState("");
  const [legs, setLegs] = useState<ManualLeg[]>([]);
  const [parlayOdds, setParlayOdds] = useState("");

  // anything else
  const start = nextHour();
  const [description, setDescription] = useState("");
  const [date, setDate] = useState(start.date);
  const [time, setTime] = useState(start.time);
  const [odds, setOdds] = useState("");

  const stakeN = Number(stake.replace(/[$,\s]/g, ""));
  const stakeOk = Number.isFinite(stakeN) && stakeN > 0;
  const yesNo = !!kind && isYesNo(kind);
  const count = atLeast && !yesNo ? Number(line.trim().replace(/\+$/, "")) : NaN;
  const countOk = Number.isInteger(count) && count >= 1;
  const lineN = yesNo ? 0.5 : atLeast ? (countOk ? count - 0.5 : NaN) : Number(line);
  const legOddsN = parseOdds(legOdds);
  const legReady = !!player && !!kind && Number.isFinite(lineN) && line.trim() !== "" || (yesNo && !!player);
  // a typed "2+" means at least 2, whichever way the leg was set
  const typeLine = (v: string) => {
    const m = v.trim().match(/^(\d+)\s*\+$/);
    if (m && !atLeast) { setAtLeast(true); setSide("Over"); setLine(m[1]); return; }
    setLine(v);
  };
  const legText = (l: { player: BetPlayer; kind: Kind; side: "Over" | "Under"; line: number }) =>
    `${l.player.name} ${isYesNo(l.kind) ? playLabel(l.kind, l.side, l.line) : `${playLabel(l.kind, l.side, l.line)} ${KIND_LABEL[l.kind].toLowerCase()}`}`;
  // a leg's own price is optional in a parlay (books usually show only the parlay's); a single needs one
  const canAddLeg = legReady && (!!legOddsN || legOdds.trim() === "");
  const pickPlayer = (p: BetPlayer) => {
    const k = p.kinds[0] ?? "";
    setPlayer(p); setKind(k); setQ(""); setSide("Over"); setLine(""); setLegOdds(""); setAtLeast(!!k && COUNTS.has(k)); setAdded("");
  };
  const pickKind = (k: Kind) => { setKind(k); if (!line.trim()) setAtLeast(COUNTS.has(k)); };
  const addLeg = () => {
    if (!player || !kind || !canAddLeg) return;
    const leg = { player, kind: kind as Kind, side, line: lineN, odds: legOddsN };
    const next: ManualLeg[] = [...legs, leg];
    setLegs(next);
    setAdded(`Leg ${next.length} added: ${legText(leg)}. Search for the next player, or fill in the stake below.`);
    setPlayer(null); setKind(""); setLine(""); setLegOdds(""); setAtLeast(false);
    requestAnimationFrame(() => document.getElementById("add-player")?.focus());
    const price = next.length > 1 ? legsPrice(next) : null;
    if (price != null && !parlayOdds) setParlayOdds(americanOdds(price));
  };
  // the leg being filled in counts too, so a single never needs an extra "add" tap
  const allLegs: ManualLeg[] = canAddLeg ? [...legs, { player: player!, kind: kind as Kind, side, line: lineN, odds: legOddsN }] : legs;
  const parlayN = allLegs.length > 1 ? (parseOdds(parlayOdds) ?? legsPrice(allLegs)) : null;
  const propOdds = allLegs.length === 1 ? allLegs[0].odds : parlayN;
  // a single moneyline can carry an early-win token (in its league's default lead)
  const solo = mode === "prop" && allLegs.length === 1 && !allLegs[0].custom ? allLegs[0] : null;
  const moneyline = solo && !solo.custom && solo.kind === "ml" ? legLeague({ kind: "ml", gameId: solo.player.game_id }) : null;
  const otherOdds = parseOdds(odds);
  const startsAt = new Date(`${date}T${time || "00:00"}`);
  const tooOld = startsAt.getTime() < Date.now() - LATE_DAYS * 86_400_000;
  // started already: it's still added, marked late (the server decides; this only says so)
  const late = mode === "prop" ? allLegs.some((l) => !l.custom && l.player.started) : startsAt.getTime() <= Date.now();
  const ready = stakeOk && promoReady(promo, stakeN) && book.trim() !== "" && (mode === "prop" ? allLegs.length > 0 && !!propOdds : description.trim().length >= 3 && !!otherOdds && !tooOld);
  const payOdds = mode === "prop" ? propOdds : otherOdds;
  const pays = stakeOk && payOdds ? promoReturn(stakeN, toWin(payOdds, stakeN), promo) : null;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!ready) return;
    setBusy(true); setProblem("");
    try {
      const s = Math.round(stakeN * 100) / 100, p = promoBody(promo, s);
      if (mode === "prop") await addPropBet(book.trim(), allLegs, s, parlayN ?? 0, tags, p);
      else await addCustomBet({ book: book.trim(), description: description.trim(), startsAt: startsAt.toISOString(), league, odds: otherOdds!, stake: s, tags, promo: p });
      qc.invalidateQueries({ queryKey: ["my-bets"] });
      setDone(true);
    } catch (err) { setProblem((err as Error).message); } finally { setBusy(false); }
  };

  const books = [...new Set([...(known.data?.books ?? []), ...COMMON_BOOKS])];
  // Claude reads any slip when the server has a key; otherwise OCR on this device, for the books whose layouts are known
  const claude = !!known.data?.can_read_screenshots;
  const apply = (d: ScreenDraft) => {
    const legsIn: ManualLeg[] = d.legs.map((l) => (l.graded && l.player
      ? { player: l.player, kind: l.kind as Kind, side: (l.side || "Over") as "Over" | "Under", line: l.line, odds: l.odds }
      : { custom: true as const, text: l.text.slice(0, 80), odds: l.odds }));
    const graded = legsIn.filter((l) => !l.custom);
    if (d.book) setBook(d.book);
    setStake(d.stake ? String(d.stake) : "");   // the slip's, or blank: never the last upload's
    // all of it a bonus bet (part of one is only noted), or a boost or no-sweat bet the reader saw
    setPromo(!!d.stake && !!d.bonus_stake && d.bonus_stake >= d.stake ? { ...NO_PROMO, kind: "bonus" }
      : d.boost ? { ...NO_PROMO, kind: "boost", boost: String(d.boost) } : d.no_sweat ? { ...NO_PROMO, kind: "no_sweat" } : NO_PROMO);
    setPlayer(null); setQ(""); setProblem("");
    if (!graded.length) {
      // nothing the site grades: an other bet, in the slip's words
      setMode("other"); setLeague(d.league ?? "nfl");
      setDescription(d.legs.map((l) => l.text).join("; ").slice(0, 200));
      if (d.odds) setOdds(americanOdds(d.odds));
      setNotes(d.notes);
      return;
    }
    setMode("prop"); if (d.league) setLeague(d.league);
    // a single whose own price wasn't read: the bet's price is the leg's
    setLegs(legsIn.length === 1 && legsIn[0].odds == null ? [{ ...legsIn[0], odds: d.odds }] : legsIn);
    setParlayOdds(d.odds ? americanOdds(d.odds) : "");
    setNotes(d.notes);
  };
  const upload = async (f: File | string | undefined) => {
    if (!f) return;
    setReading(true); setProblem(""); setNotes([]);
    try { apply(typeof f === "string" ? await readSlipText(f) : claude ? await readScreenshot(await screenshotDataUrl(f)) : await readSlipOnDevice(f)); }
    catch (err) { setProblem((err as Error).message); }
    finally { setReading(false); if (file.current) file.current.value = ""; }
  };
  // a screenshot shared to the app (My bets → takeSharedSlip): read it as soon as the sheet knows which reader to use
  const sharedRead = useRef(false);
  useEffect(() => {
    if (!sharedSlip || !known.data || sharedRead.current) return;
    sharedRead.current = true;
    upload(sharedSlip);
  }, [sharedSlip, known.data]); // eslint-disable-line react-hooks/exhaustive-deps
  if (done) return (
    <Sheet label="Add a bet" overlayClass="track-overlay" onClose={onClose}>
      <h2>Added</h2>
      <p className="sub auth-lede">{mode === "prop" && legs.some((l) => l.custom) ? "It's on My bets. Its player legs are graded from the stats; once they're in, mark the result from the bet's menu." :
        mode === "prop" ? "It's on My bets and is graded automatically once the stats are in." :
        "It's on My bets. When it settles, mark the result from the bet's menu."}</p>
      <div className="auth-form"><button className="primary" onClick={onClose}>Done</button></div>
      <p className="auth-links"><button className="link" onClick={() => { setDone(false); setLegs([]); setDescription(""); setOdds(""); setParlayOdds(""); setNotes([]); }}>Add another</button></p>
    </Sheet>
  );

  return (
    <Sheet label="Add a bet" overlayClass="track-overlay" onClose={onClose}>
      <h2>Add a bet</h2>
      <p className="sub auth-lede">Record a bet you placed somewhere else, so everything's in one place. Forgot one? Bets from the last {LATE_DAYS} days can still go in.</p>
      {known.data && (
        <div className="add-upload">
          <button type="button" className="primary outline" disabled={reading} onClick={() => file.current?.click()}>
            <IconPhoto size={16} stroke={1.75} aria-hidden />{reading ? "Reading the slip…" : "Upload a screenshot"}
          </button>
          <span className="mut">{claude ? "Fills in the bet from your slip for you to check. The image isn't kept."
            : "Fills in the bet from a bet365, FanDuel or DraftKings slip for you to check. The image stays on your device."}</span>
          <input ref={file} type="file" accept="image/*" hidden onChange={(e) => upload(e.target.files?.[0])} />
          <form className="add-link" onSubmit={(e) => { e.preventDefault(); if (linkOk) upload(link); }}>
            <label className="sr-only" htmlFor="add-link">FanDuel share link</label>
            <input id="add-link" className="auth-input" value={link} placeholder="Or paste a FanDuel share link" autoComplete="off" inputMode="url"
              aria-invalid={!!link.trim() && !linkOk} aria-describedby={link.trim() && !linkOk ? "add-link-err" : undefined}
              onChange={(e) => setLink(e.target.value)}
              onPaste={(e) => { const t = e.clipboardData.getData("text"); if (/fanduel\.com\/addToBetslip/i.test(t)) { e.preventDefault(); setLink(t.trim()); upload(t); } }} />
            <button type="submit" className="primary outline" disabled={reading || !linkOk}>Read link</button>
          </form>
          {link.trim() && !linkOk && <span id="add-link-err" className="err add-link-err">That isn't a FanDuel share link. It looks like sportsbook.fanduel.com/addToBetslip?shareCode=…</span>}
          {IOS && <span className="mut add-shortcut">On iPhone or iPad, <a href={SHORTCUT} target="_blank" rel="noopener noreferrer">add the Shortcut</a> to share a slip here
            straight from your sportsbook app: a screenshot, or FanDuel's share link.</span>}
        </div>
      )}
      {notes.length > 0 && (
        <div className="banner add-notes" role="status"><b>Check before saving</b><ul>{notes.map((n) => <li key={n}>{n}</li>)}</ul></div>
      )}
      <div className="seg small" role="group" aria-label="Kind of bet">
        <button type="button" className={mode === "prop" ? "on" : ""} aria-pressed={mode === "prop"} onClick={() => { setMode("prop"); if (league === "oth") setLeague(defaultLeague); }}>Player prop</button>
        <button type="button" className={mode === "other" ? "on" : ""} aria-pressed={mode === "other"} onClick={() => setMode("other")}>Other bet</button>
      </div>
      <p className="mut add-mode-note">{mode === "prop" ? "Graded automatically from the box scores." : "A spread, total, another sport or a boost: you mark the result when it settles."}</p>

      <form className="auth-form add-bet" onSubmit={submit}>
        <div className="seg small" role="group" aria-label="League">
          {([["nfl", "NFL"], ["nba", "NBA"], ...(mode === "other" ? [["oth", "Other"]] : [])] as ["nfl" | "nba" | "oth", string][]).map(([l, label]) => (
            <button type="button" key={l} className={league === l ? "on" : ""} aria-pressed={league === l}
              onClick={() => { setLeague(l); setPlayer(null); setLegs([]); setQ(""); }}>{label}</button>
          ))}
        </div>

        {mode === "prop" ? <>
          {legs.length > 0 && <>
            <h3 className="sub-h add-legs-h">Your bet <span className="mut">· {legs.length + (canAddLeg ? 1 : 0)} {legs.length + (canAddLeg ? 1 : 0) === 1 ? "leg" : "legs"}
              {legs.length + (canAddLeg ? 1 : 0) > 1 ? " (parlay)" : ""}</span></h3>
            <ul className="track-legs">
              {legs.map((l, i) => (
                <li key={l.custom ? `custom-${i}` : `${l.player.player_id}-${l.kind}-${i}`}>
                  {l.custom
                    ? <span><b>{l.text}</b> <span className="st">You mark it</span></span>
                    : <span><b>{l.player.name}</b> <SidePill kind={l.kind} side={l.side} line={l.line} />{!namesItself(l.kind) && <span className="mut"> {KIND_LABEL[l.kind]}</span>}</span>}
                  <span className="add-leg-end">{l.odds != null && <span className="mut">{americanOdds(l.odds)}</span>}
                    <button type="button" className="icon-btn add-leg-x" aria-label={`Remove ${l.custom ? l.text : l.player.name}`} onClick={() => setLegs(legs.filter((_, j) => j !== i))}>
                      <IconX size={14} stroke={2} aria-hidden /></button></span>
                </li>
              ))}
            </ul>
          </>}
          <p className="add-added pos" role="status" aria-live="polite">{added}</p>
          <div className={`add-draft${legs.length ? " more" : ""}`}>
          {legs.length > 0 && <p className="add-draft-h">Leg {legs.length + 1} <span className="mut">· optional</span></p>}
          {!player ? <>
            <label className="auth-label" htmlFor="add-player">Player</label>
            <input id="add-player" className="auth-input" value={q} placeholder="Search players by name" autoComplete="off"
              onChange={(e) => setQ(e.target.value)} />
            {query.length >= 2 && (
              <div className="add-results" role="list">
                {found.isLoading ? <p className="mut">Searching…</p> : !found.data?.length ? <p className="mut">No player with a game in the last {LATE_DAYS} days or still to come matches “{query}”.</p> :
                  found.data.map((p) => (
                    <button type="button" role="listitem" key={`${p.player_id}-${p.game_id}`} className="menu-item" onClick={() => pickPlayer(p)}>
                      <span><b>{p.name}</b> <span className="mut">{p.pos} · {p.team} vs {p.opp} · {when(p.kickoff)}</span>
                        {p.started && <span className="bet-pill">Started</span>}</span>
                    </button>
                  ))}
              </div>
            )}
          </> : <>
            <div className="add-player">
              <span><b>{player.name}</b> <span className="mut">{player.team} vs {player.opp} · {when(player.kickoff)}</span>
                {player.started && <span className="bet-pill">Started</span>}</span>
              <button type="button" className="link" onClick={() => setPlayer(null)}>Change</button>
            </div>
            <div className="track-fields">
              <label className="auth-label">Market
                <select value={kind} onChange={(e) => pickKind(e.target.value as Kind)}>
                  {player.kinds.map((k) => <option key={k} value={k}>{KIND_LABEL[k]}</option>)}
                </select>
              </label>
              <div className="auth-label add-side">Side
                <div className="seg small" role="group" aria-label="Side">
                  {yesNo ? (["Over", "Under"] as const).map((s) => (
                    <button type="button" key={s} className={side === s ? "on" : ""} aria-pressed={side === s} onClick={() => setSide(s)}>{s === "Over" ? "Yes" : "No"}</button>
                  )) : ([["least", "At least"], ["Over", "Over"], ["Under", "Under"]] as const).map(([k, label]) => {
                    const on = k === "least" ? atLeast : !atLeast && side === k;
                    return <button type="button" key={k} className={on ? "on" : ""} aria-pressed={on}
                      onClick={() => {
                        const n = Number(line.trim().replace(/\+$/, ""));
                        if (k === "least") { setAtLeast(true); setSide("Over"); if (line.trim() && !atLeast && Number.isFinite(n)) setLine(String(Math.ceil(n))); }
                        else { if (atLeast && Number.isInteger(n) && n >= 1) setLine(String(n - 0.5)); setAtLeast(false); setSide(k); }
                      }}>{label}</button>;
                  })}
                </div>
              </div>
              {!yesNo && (atLeast
                ? <label className="auth-label">How many<span className="promo-input"><input className="auth-input" inputMode="numeric" value={line} placeholder="e.g. 2"
                    aria-invalid={line.trim() !== "" && !countOk} aria-describedby="add-count-hint" onChange={(e) => setLine(e.target.value)} /><span className="mut">+</span></span></label>
                : <label className="auth-label">Line<input className="auth-input" inputMode="decimal" value={line} placeholder="e.g. 64.5"
                    onChange={(e) => typeLine(e.target.value)} /></label>)}
              <label className="auth-label">Odds<input className="auth-input" value={legOdds} placeholder={legs.length ? "If shown" : "e.g. -110"} onChange={(e) => setLegOdds(e.target.value)} aria-invalid={legOdds !== "" && !legOddsN} /></label>
            </div>
            <p className="mut add-leg-preview" id="add-count-hint" aria-live="polite">
              {atLeast && !yesNo
                ? (countOk ? <>{count}+ {KIND_LABEL[kind as Kind].toLowerCase()} is <b>Over {count - 0.5}</b>, as it's graded{legOddsN ? ` · at ${americanOdds(legOddsN)}` : ""}</>
                  : `A whole number: 2+ means ${KIND_LABEL[kind as Kind]?.toLowerCase() ?? "it"} of 2 or more (Over 1.5).`)
                : canAddLeg ? <>{legText({ player, kind: kind as Kind, side, line: lineN })}{legOddsN ? ` at ${americanOdds(legOddsN)}` : ""}</> : "Type the line, or switch to At least for a 2+ style prop."}
            </p>
            <div className="add-leg-actions">
              <button type="button" className="primary outline" disabled={!canAddLeg} onClick={addLeg}
                title={canAddLeg ? undefined : legReady ? "The odds aren't a price yet, like -110 or +150" : "Fill in the line first"}>
                <IconPlus size={16} stroke={1.75} aria-hidden />{legs.length ? "Add this leg" : "Add another leg"}</button>
              {legs.length === 0 && <span className="mut">{canAddLeg ? "Saving now records a single. Add another leg to make it a parlay." : ""}</span>}
              {legs.length > 0 && <button type="button" className="link" onClick={() => { setPlayer(null); setLine(""); setLegOdds(""); }}>Skip this leg</button>}
            </div>
          </>}
          </div>
          {allLegs.length > 1 && (
            <label className="auth-label">Parlay odds{book ? ` at ${book}` : ""}
              <input className="auth-input" value={parlayOdds || (parlayN ? americanOdds(parlayN) : "")} placeholder="From your slip, like +600" onChange={(e) => setParlayOdds(e.target.value)} />
            </label>
          )}
        </> : <>
          <label className="auth-label" htmlFor="add-desc">The bet</label>
          <input id="add-desc" className="auth-input" value={description} maxLength={200} placeholder="e.g. Chiefs -3.5 vs Bills" onChange={(e) => setDescription(e.target.value)} />
          <div className="track-fields">
            <label className="auth-label">Event starts<input className="auth-input" type="date" value={date} onChange={(e) => setDate(e.target.value)} /></label>
            <label className="auth-label">&nbsp;<input className="auth-input" type="time" value={time} aria-label="Start time" onChange={(e) => setTime(e.target.value)} /></label>
            <label className="auth-label">Odds<input className="auth-input" value={odds} placeholder="e.g. -110" onChange={(e) => setOdds(e.target.value)} aria-invalid={odds !== "" && !otherOdds} /></label>
          </div>
          {tooOld && <p className="err">That started more than {LATE_DAYS} days ago, too long ago to add.</p>}
        </>}

        <div className="track-fields">
          <label className="auth-label">Sportsbook
            <input className="auth-input" list="add-books" value={book} placeholder="e.g. FanDuel" onChange={(e) => setBook(e.target.value)} maxLength={40} />
            <datalist id="add-books">{books.map((b) => <option key={b} value={b} />)}</datalist>
          </label>
          <label className="auth-label">Stake<input className="auth-input" inputMode="decimal" value={stake} placeholder="$0" onChange={(e) => setStake(e.target.value)} /></label>
        </div>
        <PromoFields promo={promo} onChange={setPromo} stake={stakeOk ? stakeN : 0} book={book.trim() || undefined} moneyline={moneyline} />
        <label className="auth-label" htmlFor="add-tags">Tags <span className="mut">(optional)</span></label>
        <TagInput id="add-tags" tags={tags} onChange={setTags} suggestions={known.data?.tags} />
        {late && !tooOld && <p className="warn add-late">{mode === "prop" ? "This game has started" : "This has started"}, so it's added
          as <b>late</b>: My bets marks it, since you may have known how it went. It's graded like any other bet.</p>}
        <p className="mut track-pays" aria-live="polite">{!promoReady(promo, stakeN) ? (promo.kind === "boost" ? "Enter the boost, like 50%" : "Enter the refund in dollars")
          : pays != null ? <>Pays <b>{money(pays)}</b> if it wins</> : "Fill in the bet, sportsbook, odds and stake"}</p>
        {problem && <p className="err auth-problem" role="alert">{problem}</p>}
        <button className="primary" disabled={!ready || busy}>{busy ? "Saving…" : mode === "prop" && allLegs.length > 1 ? `Add ${allLegs.length}-leg parlay` : "Add bet"}</button>
      </form>
    </Sheet>
  );
}
