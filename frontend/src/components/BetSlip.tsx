import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { sharedClient } from "../queryClients";
import { useAuth } from "../auth";
import { boostPct, deleteBet, editBet, legLeague, money, NO_PROMO, promoBody, promoReady, promoReturn, refundOf, trackBets, type Promo, type PromoBody } from "../bets";
import PromoFields from "./PromoFields";
import { leaguePath, goToPath } from "../route";
import { useBetSlip } from "../BetSlipContext";
import { detectState } from "../geoState";
import BookBadge from "./BookBadge";
import Sheet from "./Sheet";
import TrackBetSheet from "./TrackBetSheet";
import { openSignIn } from "./SignIn";
import SidePill from "./SidePill";
import { namesItself, KIND_SHORT, americanOdds, pct, playLabel, signedPct, timeAgo } from "../format";
import { gamblyUrl } from "../gambly";
import { alternativesAtBook, bookCoverage, clusterConflicts, conflicts, bookOptions, groupByBook, combinedLink, legLink, slipLinksText, linkStatus, openProgress, parlay, parseStake, priceDrift, rebook, sameGame, slipHitChance, toAmerican, toWin, withLinks, type BookOption, type Leg } from "../slip";

// once per page load: the stored state covers later opens, and "Try again" re-runs it
let locatedOnce = false;

const QUICK_STAKES = [5, 10, 25, 50];

export default function BetSlip() {
  const { legs, remove, replace, clear, lastRemoved, undoRemove, dismissUndo, stakes, setStake, state, setState, open, setOpen, staleSince, clashCount } = useBetSlip();
  // old odds: the bettor ticks that they'll check prices before a Place button hands the slip to a book (once per odds fetch)
  const [checkedStale, setCheckedStale] = useState<string | null>(null);
  const gated = !!staleSince && checkedStale !== staleSince;
  const close = () => setOpen(false);
  const auth = useAuth();
  // the book whose group is being recorded on My bets ("Track this bet")
  const [tracking, setTracking] = useState<string | null>(null);
  // what's in each stake box while it's being typed in ("" or "12."), so the box doesn't fight the bettor
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [copied, setCopied] = useState<"link" | null>(null);
  const [target, setTarget] = useState<string | null>(null);
  const [placeMsg, setPlaceMsg] = useState<string | null>(null);
  const [opened, setOpened] = useState<Record<string, string[]>>({});
  const [linking, setLinking] = useState<string | null>(null);
  const [locating, setLocating] = useState(false);
  const [locError, setLocError] = useState<string | null>(null);
  // each book's promo, as Add a bet and Track this bet take it: a bonus bet (free bet), a profit boost or a no-sweat refund
  const [promos, setPromos] = useState<Record<string, Promo>>({});
  const promoFor = (book: string) => promos[book] ?? NO_PROMO;
  // what "Place bet" records for that promo (a boost or refund still being typed counts once it's complete)
  const bodyFor = (book: string, stake: number): PromoBody => (promoReady(promoFor(book), stake) ? promoBody(promoFor(book), stake) : { bonus: false, boost: 0, boost_cap: 0, no_sweat: 0, early_win: 0 });
  const sameBody = (a: PromoBody, b: PromoBody) => a.bonus === b.bonus && (a.boost ?? 0) === (b.boost ?? 0) && (a.no_sweat ?? 0) === (b.no_sweat ?? 0);
  // what "Place bet" recorded on My bets, per book: `key` is the legs it was for, so clicking Place again (or after tweaking
  // the stake) doesn't record it twice, while a different set of legs is a new bet
  const [tracked, setTracked] = useState<Record<string, { key: string; ids: number[]; asParlay: boolean; odds: number; stake: number; body: PromoBody } | { key: string; error: string }>>({});
  const qc = useQueryClient();
  // some books' links need the bettor's state: find it from the device's location the first time the slip opens
  const locate = async () => {
    setLocating(true);
    const r = await detectState();
    setLocating(false);
    if ("state" in r) { setState(r.state); setLocError(null); } else setLocError(r.error);
  };
  const needsState = legs.some((l) => /\{state\}/.test(`${l.link ?? ""} ${l.eventLink ?? ""}`));
  useEffect(() => {
    if (open && needsState && !locatedOnce) { locatedOnce = true; locate(); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, needsState]);
  const metaFlags = qc.getQueryData<{ has_odds_key: boolean; can_write: boolean }>(["meta"]);
  const hasKey = !!metaFlags?.has_odds_key && !!metaFlags?.can_write;
  // line shopping data: every book's quote for each leg's exact line
  const keys = [...new Map(legs.map((l) => [`${l.playerId}|${l.kind}`, l])).values()];
  const ladders = useQueries({
    queries: keys.map((l) => ({ queryKey: ["ladder", l.playerId, l.kind, 0], queryFn: () => api.lines(l.playerId, l.kind, 0), enabled: open })),
  }, sharedClient);   // legs from either league: kept across a league switch
  const ladderFor = (l: { playerId: string; kind: string }) => ladders[keys.findIndex((k) => k.playerId === l.playerId && k.kind === l.kind)]?.data;
  const options: Record<string, BookOption[]> = Object.fromEntries(legs.map((l) => [l.id, bookOptions(ladderFor(l), l)]));
  const coverage = bookCoverage(options);
  // the leg's own book quoting it at a new price, or no longer offering it, since it was added
  const drift = Object.fromEntries(legs.map((l) => [l.id, priceDrift(ladderFor(l), l)]));
  const movedIn = (ls: Leg[]) => ls.flatMap((l) => { const d = drift[l.id]; return d?.kind === "moved" ? [{ l, now: d.now }] : []; });
  const nMoved = movedIn(legs).length, nGone = legs.filter((l) => drift[l.id]?.kind === "gone").length;
  const priceSummary = [nMoved && `${nMoved} ${nMoved === 1 ? "price has" : "prices have"} changed since you added ${nMoved === 1 ? "it" : "them"}.`,
    nGone && `${nGone} ${nGone === 1 ? "leg is" : "legs are"} no longer offered at ${nGone === 1 ? "its" : "their"} book.`].filter(Boolean).join(" ");
  const moveAll = (book: string) => {
    setTarget(book);
    legs.forEach((l) => {
      const o = options[l.id]?.find((x) => x.book === book);
      if (o && l.book !== book) replace(l.id, rebook(l, o));
    });
  };
  // legs the chosen book can't take as-is, each with the nearest lines that book does offer
  const stranded = target ? legs.filter((l) => l.book !== target) : [];
  const stakeFor = (book: string) => stakes[book] ?? 10;
  // same-game legs: positions (slip legs don't carry them) and the correlations, only once a book's legs share a game
  const needCorr = open && legs.some((l, i) => legs.some((m, j) => j > i && m.gameId === l.gameId && m.book === l.book));
  // both leagues' (a slip can hold either); their market names differ, so one table holds both without clashing. In the
  // cache both leagues share, so switching league doesn't download them again (the NBA's projections are ~200 KB)
  const nflProj = useQuery({ queryKey: ["nfl-projections"], queryFn: api.nflProjections, enabled: needCorr, staleTime: 10 * 60_000 }, sharedClient);
  const nbaProj = useQuery({ queryKey: ["nba-projections"], queryFn: api.nbaProjections, enabled: needCorr, staleTime: 10 * 60_000 }, sharedClient);
  const corrQuery = useQuery({ queryKey: ["correlations"], queryFn: api.correlations, enabled: needCorr, staleTime: 10 * 60_000 }, sharedClient);
  const nbaCorrQuery = useQuery({ queryKey: ["nba-correlations"], queryFn: api.nbaCorrelations, enabled: needCorr, staleTime: 10 * 60_000 }, sharedClient);
  const positions = useMemo(() => Object.fromEntries([...(nflProj.data ?? []), ...(nbaProj.data ?? [])].map((p) => [p.player_id, p.pos])),
    [nflProj.data, nbaProj.data]);
  const corr = useMemo(() => ({ ...corrQuery.data?.pairs, ...nbaCorrQuery.data?.pairs }), [corrQuery.data, nbaCorrQuery.data]);

  const copyText = async (text: string, tag: "link") => {
    try { await navigator.clipboard.writeText(text); setCopied(tag); setTimeout(() => setCopied(null), 1800); } catch { /* clipboard blocked */ }
  };
  // All of a book's linked legs in a single tab (only for books whose multi-selection link format we know)
  const combineFor = (book: string) => {
    const direct = openProgress(legs, book, state, []).direct.flatMap((l) => {
      const url = legLink(l, state);
      return url ? [{ ...l, url }] : [];
    });
    return combinedLink(book, direct);
  };
  // One new tab per click: browsers block extra pop-ups opened from a single click, so we step through the legs.
  const openNext = (book: string) => {
    let p = openProgress(legs, book, state, opened[book] ?? []);
    if (!p.remaining.length) p = openProgress(legs, book, state, []);  // start over
    const leg = p.remaining[0];
    const url = leg && legLink(leg, state);
    if (!url) return;
    window.open(url, "_blank", "noopener");
    autoTrack(book);
    const done = [...(p.done ? opened[book] ?? [] : []).filter((id) => p.direct.some((l) => l.id === id)), leg.id];
    setOpened({ ...opened, [book]: done });
    setPlaceMsg(done.length < p.direct.length ? `Added ${done.length} of ${p.direct.length} at ${book}. Click again for the next leg.`
      : `All ${p.direct.length} legs opened at ${book}. Check the slip there before placing.`);
  };
  const groups = groupByBook(legs);
  const trackKey = (ls: Leg[]) => ls.map((l) => l.id).sort().join(",");
  const trackedNow = (book: string, ls: Leg[]) => { const t = tracked[book]; return t && t.key === trackKey(ls) ? t : null; };
  // "Place bet" records the bet on My bets as it opens the book (signed-in, 21+ members): a parlay at the slip's price, or
  // singles when its legs span both leagues. Once per bet; "Undo" takes it back if it wasn't placed after all.
  const autoTrack = async (book: string) => {
    const ls = groups.find(([b]) => b === book)?.[1] ?? [];
    const stake = stakeFor(book);
    const key = trackKey(ls), body = bodyFor(book, stake);
    if (!ls.length || !stake || !auth?.user?.age_confirmed || (tracked[book]?.key === key && !("error" in tracked[book]))) return;
    const asParlay = ls.length > 1 && new Set(ls.map((l) => legLeague(l))).size === 1;
    const odds = asParlay ? parlay(ls, 1).american : ls[0].odds;
    setTracked((t) => ({ ...t, [book]: { key, ids: [], asParlay, odds, stake, body } }));   // claimed at once, so a second click can't double it
    try {
      const saved = await trackBets(book, ls, asParlay, stake, odds, [], body);
      setTracked((t) => ({ ...t, [book]: { key, ids: saved.map((b) => b.id), asParlay, odds, stake, body } }));
      qc.invalidateQueries({ queryKey: ["my-bets"] });
    } catch (e) { setTracked((t) => ({ ...t, [book]: { key, error: (e as Error).message } })); }
  };
  // the stake or promo changed after "Place bet" recorded it (ticked late, a typo): bring the recorded bet in line
  useEffect(() => {
    const due = Object.entries(tracked).flatMap(([book, t]) => ("ids" in t && t.ids.length && (t.stake !== stakeFor(book) || !sameBody(t.body, bodyFor(book, stakeFor(book)))) && stakeFor(book) > 0
      ? [{ book, t, stake: stakeFor(book), body: bodyFor(book, stakeFor(book)) }] : []));
    if (!due.length) return;
    const timer = setTimeout(async () => {
      for (const { book, t, stake, body } of due) {
        try {
          await Promise.all(t.ids.map((id) => editBet(id, { stake, ...body })));
          setTracked((all) => { const cur = all[book]; return cur && "ids" in cur && cur.ids === t.ids ? { ...all, [book]: { ...cur, stake, body } } : all; });
        } catch { /* left as recorded: My bets still has it to fix by hand */ }
      }
      qc.invalidateQueries({ queryKey: ["my-bets"] });
    }, 600);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stakes, promos, tracked]);
  const untrack = async (book: string) => {
    const t = tracked[book];
    if (!t || !("ids" in t)) return;
    setTracked(({ [book]: _, ...rest }) => rest);
    await Promise.all(t.ids.map((id) => deleteBet(id).catch(() => undefined)));
    qc.invalidateQueries({ queryKey: ["my-bets"] });
  };
  // one player's same market twice at a book: the book won't parlay them, so that book's slip is blocked until one is removed
  const clashes = conflicts(legs);
  const clashBooks = new Set(clashes.map((c) => c[0].book));
  const keepOnly = (keep: Leg, clash: Leg[]) => remove(clash.filter((l) => l.id !== keep.id).map((l) => l.id));
  // "Copy slip link" carries every book that can take its legs; a book with a clash is left out until it's fixed
  const links = slipLinksText(legs.filter((l) => !clashBooks.has(l.book)), state);
  // "Send to Gambly": the whole slip as one parlay, which Gambly prices at every book it covers (books our links don't reach)
  const gambly = gamblyUrl(legs);
  const clashSummary = clashes.length ? `Fix ${clashes.length} ${clashes.length === 1 ? "clash" : "clashes"} at ${[...clashBooks].join(" and ")} to place ${clashBooks.size === 1 ? "that parlay" : "those parlays"}.` : "";
  const showClash = () => {
    const el = document.querySelector<HTMLElement>(".slip-clash");
    el?.scrollIntoView({ behavior: "smooth", block: "center" });
    el?.querySelector<HTMLElement>("button")?.focus({ preventScroll: true });
  };
  // "Place bet": every linked leg in one tab where the book's multi-selection link is known, otherwise one leg per click
  const placeButton = (book: string) => {
    const prog = openProgress(legs, book, state, opened[book] ?? []);
    if (!prog.direct.length) return null;
    const label = <><BookBadge book={book} size={20} />{groups.length > 1 ? `Place at ${book}` : "Place bet"}</>;
    if (clashBooks.has(book)) return <button key={book} className="primary" disabled aria-describedby="slip-clash-msg">{label} ↗</button>;
    if (gated) return <button key={book} className="primary" disabled title="Tick the box above first: these prices are old">{label} ↗</button>;
    const combine = combineFor(book);
    // a real link (not window.open), so phones hand it to the sportsbook app
    if (combine) return (
      <a key={book} className="primary" href={combine.url} target="_blank" rel="noopener noreferrer"
        onClick={() => { setPlaceMsg(`Opened ${book} with ${combine.count} ${combine.count === 1 ? "leg" : "legs"}. If any are missing from the slip there, add them with each leg's button.`); autoTrack(book); }}>
        {label} ↗
      </a>
    );
    const step = prog.direct.length > 1 && prog.remaining.length ? ` · leg ${prog.done + 1} of ${prog.direct.length}` : "";
    return <button key={book} className="primary" onClick={() => openNext(book)}>{label}{step} ↗</button>;
  };

  // legs with no betslip link at all (links only come with a game's alternate-lines fetch); one fetch per game covers them.
  // A player's alternate-lines fetch brings no moneyline links, so moneylines aren't counted
  const missing = legs.filter((l) => l.kind !== "ml" && linkStatus(l, state) === "missing");
  const missingGames = [...new Map(missing.map((l) => [l.gameId, l])).values()];
  const getLinks = async () => {
    const credits = missingGames.length * 2;
    if (!window.confirm(`Fetch betslip links for ${missingGames.length} ${missingGames.length === 1 ? "game" : "games"}?\n\nUses about ${credits}+ Odds API credits.`)) return;
    setLinking("Fetching links…");
    try {
      for (const l of missingGames) await api.fetchAlt(l.playerId);
      await Promise.all([qc.invalidateQueries({ queryKey: ["ladder"] }), sharedClient.invalidateQueries({ queryKey: ["ladder"] })]);
      setLinking(null);
    } catch (e) { setLinking(`Couldn't fetch links: ${(e as Error).message}`); }
  };
  // once fresh quotes arrive, attach their links to legs that had none (same book, line and side); a new price is shown, not swapped in
  useEffect(() => {
    legs.forEach((l) => {
      if (l.link) return;
      const o = bookOptions(ladderFor(l), l).find((x) => x.book === l.book);
      if (o?.link) replace(l.id, withLinks(l, o));
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ladders.map((q) => q.dataUpdatedAt).join(",")]);
  // one leg's row; `clash` is set when it's one of several legs on the same player's market at this book
  const legRow = (l: Leg, ls: Leg[], stake: number, clash?: Leg[]) => {
    // in a parlay the stake rides on every leg together, so a leg's own payout would mislead: that's the group's total below
    const single = ls.length === 1 && stake > 0;
    const url = legLink(l, state);
    const direct = linkStatus(l, state) === "direct";
    const d = drift[l.id];
    const alts = d?.kind === "gone" ? alternativesAtBook(ladderFor(l), l, l.book) : [];
    return (
      <div className="slip-leg" key={l.id}>
        <div>
          <b>{l.name}</b> <span className="mut">{l.team} {l.home ? "vs" : "@"} {l.opp}</span><br />
          <SidePill kind={l.kind} side={l.side} line={l.line} /> {!namesItself(l.kind) && KIND_SHORT[l.kind]}
          {l.alt && <span className="alt">alt</span>}<br />
          <label className="bookpick">
            <BookBadge book={l.book} size={20} />
            <select value={l.book} aria-label={`Book for ${l.name} ${playLabel(l.kind, l.side, l.line)}`}
              onChange={(e) => { const o = options[l.id]?.find((x) => x.book === e.target.value); if (o) replace(l.id, rebook(l, o)); }}>
              {!options[l.id]?.some((o) => o.book === l.book) && <option value={l.book}>{l.book} {americanOdds(l.odds)}</option>}
              {(options[l.id] ?? []).map((o) => (
                <option key={o.book} value={o.book}>{o.book} {americanOdds(o.odds)}</option>
              ))}
            </select>
          </label><br />
          <span className="mut">{pct(l.prob, 0)} model · <span className={l.ev > 0 ? "pos" : ""}>{signedPct(l.ev)} EV</span>{single && <> · to win ${toWin(l.odds, stake).toFixed(2)}</>}</span>
          {d?.kind === "moved" && <><br /><span className={`slip-drift ${d.better ? "pos" : "err"}`}>
            was {americanOdds(d.was)} → now {americanOdds(d.now.odds)} <span className="mut">· {d.better ? "better" : "worse"} for you</span></span></>}
          {d?.kind === "gone" && (
            <div className="slip-gone">
              <span className="err">No longer offered at {l.book}.</span>{" "}
              {alts.length ? <span className="mut">Nearest lines there:</span>
                : <span className="mut">{options[l.id]?.length ? "Pick another book above, or remove it." : "Remove it, or check back after the next odds sync."}</span>}
              {alts.length > 0 && (
                <div className="chips">
                  {alts.map((o) => (
                    <button key={o.line} className="chip-btn" onClick={() => replace(l.id, rebook(l, o))}
                      title={`Swap to ${playLabel(l.kind, l.side, o.line)} at ${l.book}`}>
                      {playLabel(l.kind, l.side, o.line)} <b>{americanOdds(o.odds)}</b>
                      <span className={o.ev > 0 ? "pos" : "mut"}> {signedPct(o.ev)}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
        <div className="slip-actions">
          {/* a book's only leg goes through "Place bet" below */}
          {url && !(ls.length === 1 && direct) && gated && <button className="btn" disabled title="Tick the box below first: these prices are old">{direct ? "Add at book ↗" : "Open book ↗"}</button>}
          {url && !(ls.length === 1 && direct) && !gated && <a href={url} target="_blank" rel="noopener noreferrer" className="btn"
            title={direct ? "Adds this selection to your betslip at the sportsbook" : "Opens the sportsbook's page (no betslip link for this selection yet)"}>
            {direct ? "Add at book ↗" : "Open book ↗"}
          </a>}
          {/* in a pair, removing one is keeping the other: "Keep this" only earns its place from three legs up */}
          {clash && clash.length > 2 && <button className="link" onClick={() => keepOnly(l, clash)}
            aria-label={`Keep only ${playLabel(l.kind, l.side, l.line)}, remove the other ${clash.length - 1}`}>Keep this</button>}
          <button className="link" onClick={() => remove(l.id)} aria-label={`Remove ${l.name} ${playLabel(l.kind, l.side, l.line)}`}>Remove</button>
        </div>
      </div>
    );
  };
  return (
    <>
      <button className="slip-fab" onClick={() => setOpen(true)}
        aria-label={`Open betslip, ${legs.length} ${legs.length === 1 ? "selection" : "selections"}${clashCount ? `, ${clashCount} to fix` : ""}`}>
        Betslip <span className="count">{legs.length}</span>{clashCount > 0 && <span className="slip-alert" aria-hidden>!</span>}
      </button>
      {open && (
        <Sheet label="Betslip" overlayClass="slip-overlay" onClose={close}>
            <h2>Betslip</h2>
            {!legs.length && <p className="mut">Nothing here yet. Open a player and tap + on a line to add it.</p>}

            {needsState && (
              <p className="mut slip-where">
                {locating ? "Finding your state…" : state ? <>Links set for <b>{state}</b>{locError && " (last known)"}</> : null}
                {locError && !locating && <>{state ? " · " : ""}{locError} <button className="link" onClick={locate}>Try again</button></>}
              </p>
            )}

            {groups.map(([book, ls]) => {
              const stake = stakeFor(book);
              const prog = openProgress(legs, book, state, []);
              const p = parlay(ls, stake);
              const moved = movedIn(ls);
              return (
                <section className="slip-group" key={book}>
                  <div className="slip-book">
                    <BookBadge book={book} size={28} /><b>{book}</b><span className="mut">{ls.length} {ls.length === 1 ? "selection" : "selections"}</span>
                  </div>
                  {prog.skipped > 0 && (
                    <p className="warn open-msg">{prog.skipped} {prog.skipped === 1 ? "leg" : "legs"} can't be added: {ls.some((l) => linkStatus(l, state) === "needs-state")
                      ? "needs your state (allow location above)." : "no betslip link yet."}</p>
                  )}
                  {clusterConflicts(ls).map((row) => row.length > 1 ? (
                    <div className="slip-clash" key={row[0].id} role="group" aria-label={`${row[0].name} ${KIND_SHORT[row[0].kind]} clash`}>
                      <p className="err"><b>{row[0].name} {KIND_SHORT[row[0].kind]}</b> is on this slip {row.length} times. Books usually won't
                        parlay {row.some((l) => l.side !== row[0].side) ? "both sides of a prop" : "two lines of the same prop"}, so keep one.</p>
                      {row.map((l) => legRow(l, ls, stake, row))}
                    </div>
                  ) : legRow(row[0], ls, stake))}
                  {moved.length > 0 && (
                    <div className="slip-drift-bar">
                      <span className="warn">{moved.length === ls.length && ls.length > 1 ? "Every price" : `${moved.length} ${moved.length === 1 ? "price" : "prices"}`} at {book} moved
                        since you added {moved.length === 1 ? "it" : "them"}. The payout below still uses the old {moved.length === 1 ? "price" : "prices"}.</span>
                      <button className="btn" onClick={() => moved.forEach(({ l, now }) => replace(l.id, rebook(l, now)))}>
                        Update {moved.length} {moved.length === 1 ? "price" : "prices"}
                      </button>
                    </div>
                  )}
                  <div className="slip-total">
                    <div className="slip-stake">
                      <label>Stake $
                        <input type="text" inputMode="decimal" autoComplete="off" value={drafts[book] ?? (stake ? String(stake) : "")} placeholder="0"
                          aria-label={`Stake at ${book}`}
                          onChange={(e) => { setDrafts({ ...drafts, [book]: e.target.value }); setStake(book, parseStake(e.target.value)); }}
                          onBlur={() => setDrafts(({ [book]: _, ...rest }) => rest)} />
                      </label>
                      <span className="chips" role="group" aria-label={`Quick stakes at ${book}`}>
                        {QUICK_STAKES.map((n) => (
                          <button key={n} type="button" className="chip-btn" aria-pressed={stake === n}
                            onClick={() => { setStake(book, n); setDrafts(({ [book]: _, ...rest }) => rest); }}>${n}</button>
                        ))}
                      </span>
                    </div>
                    <PromoFields promo={promoFor(book)} onChange={(pr) => setPromos({ ...promos, [book]: pr })} stake={stake} book={book} compact
                      moneyline={ls.length === 1 && ls[0].kind === "ml" ? legLeague(ls[0]) : null} />
                    {clashBooks.has(book) ? <span className="err">Not a valid parlay yet</span> : !stake ? (
                      <span className="mut">Enter a stake to see {ls.length > 1 ? `the ${ls.length}-leg payout` : "what it pays"}</span>
                    ) : (() => {
                      const pr = promoFor(book), boost = boostPct(pr), refund = refundOf(pr, stake), isBonus = pr.kind === "bonus";
                      const back = promoReturn(stake, ls.length > 1 ? p.profit : toWin(ls[0].odds, stake), pr);
                      return (
                        <span aria-live="polite">{ls.length > 1 ? `${ls.length}-leg parlay` : "Single bet"} <b>{americanOdds(ls.length > 1 ? p.american : ls[0].odds)}</b>
                          {boost && `, boosted ${boost}%`} → pays <b>{money(back)}</b>
                          {ls.length > 1 && !isBonus && <> (profit {money(back - stake)})</>}
                          {isBonus && <span className="mut"> (a bonus bet's stake isn't returned)</span>}
                          {refund && <>, or <b>{money(refund)}</b> back as a bonus bet if it loses</>}</span>
                      );
                    })()}
                  </div>
                  {ls.length > 1 && !clashBooks.has(book) && (() => {
                    const sg = sameGame(ls);
                    const hit = slipHitChance(ls, positions, sg ? corr : undefined);
                    const ev = hit * p.decimal - 1;
                    return (
                      <p className="mut slip-chance">
                        Model hit chance <b>{pct(hit, hit < 0.1 ? 1 : 0)}</b>
                        {sg ? <> · fair odds <b>{americanOdds(toAmerican(1 / Math.max(hit, 1e-6)))}</b></>
                          : <> · <span className={ev > 0 ? "pos" : ""}>{signedPct(ev)} EV</span></>}
                      </p>
                    );
                  })()}
                  {ls.length > 1 && !clashBooks.has(book) && sameGame(ls) && (
                    <p className="warn">Same-game legs: books price these their own way, so the payout is a guide. Take it if the book's price beats the fair odds.</p>
                  )}
                  {!clashBooks.has(book) && (() => {
                    const t = trackedNow(book, ls);
                    if (t && "error" in t) return (
                      <div className="slip-track" role="status">
                        <span className="err">Couldn't add it to My bets: {t.error}</span>{" "}
                        <button className="link" onClick={() => setTracking(book)}>Track it by hand</button>
                      </div>
                    );
                    if (t) return (
                      <div className="slip-track slip-tracked" role="status">
                        <span><span className="pos" aria-hidden>✓</span> {t.ids.length ? "Added" : "Adding"} to My bets: {t.asParlay ? `${ls.length}-leg parlay at ${americanOdds(t.odds)}`
                          : ls.length > 1 ? `${ls.length} singles` : `single at ${americanOdds(t.odds)}`}, {money(t.stake)}{ls.length > 1 && !t.asParlay ? " each" : ""}{t.body.bonus ? " bonus bet" : t.body.boost ? `, boosted ${t.body.boost}%` : t.body.no_sweat ? ", no sweat" : ""}.
                          {t.asParlay && <span className="mut"> If the price at {book} is different, fix it on My bets.</span>}</span>
                        <span className="slip-tracked-actions">
                          <button className="link" disabled={!t.ids.length} onClick={() => untrack(book)}>Didn't place it? Undo</button>
                          <button className="link" onClick={() => { close(); goToPath(leaguePath(legLeague(ls[0]), "bets")); }}>See My bets</button>
                        </span>
                      </div>
                    );
                    return (
                      <div className="slip-track">
                        <button className="link" onClick={() => setTracking(book)}>Placed it? Track this bet</button>
                        {auth?.user?.age_confirmed && !!stake && <span className="mut"> · Place bet adds it to My bets for you</span>}
                      </div>
                    );
                  })()}
                </section>
              );
            })}

            {/* below the legs, so on a phone the legs come first */}
            {legs.length > 1 && coverage.length > 0 && (
              <label className="statebox">Put every leg at one book
                <select value="" onChange={(e) => e.target.value && moveAll(e.target.value)} aria-label="Move all legs to a book">
                  <option value="">Choose a book…</option>
                  {coverage.map((c) => (
                    <option key={c.book} value={c.book}>{c.book} · {c.count} of {legs.length} legs available</option>
                  ))}
                </select>
              </label>
            )}

            {missing.length > 0 && (
              <section className="suggest">
                <p><b>{missing.length} {missing.length === 1 ? "leg has" : "legs have"} no betslip link yet.</b></p>
                {hasKey ? (
                  <button className="primary" disabled={linking === "Fetching links…"} onClick={getLinks}>
                    {linking === "Fetching links…" ? "Fetching…" : `Get links (~${missingGames.length * 2} credits)`}
                  </button>
                ) : <p className="mut">The next scheduled odds sync usually adds them.</p>}
                {linking && linking !== "Fetching links…" && <p className="err">{linking}</p>}
              </section>
            )}

            {target && stranded.length > 0 && (
              <section className="suggest" aria-live="polite">
                <div className="suggest-head">
                  <b>{target} can't take {stranded.length === legs.length ? "these legs" : `${stranded.length} of ${legs.length} legs`} as they are.</b>
                  <button className="link" onClick={() => setTarget(null)}>Dismiss</button>
                </div>
                <p className="mut">Nearest lines {target} offers for each, so everything can go on one slip:</p>
                {stranded.map((l) => {
                  const alts = alternativesAtBook(ladderFor(l), l, target);
                  return (
                    <div className="suggest-leg" key={l.id}>
                      <div><b>{l.name}</b> <SidePill kind={l.kind} side={l.side} line={l.line} /> {!namesItself(l.kind) && <span className="mut">{KIND_SHORT[l.kind]}</span>}</div>
                      {alts.length ? (
                        <div className="chips">
                          {alts.map((o) => (
                            <button key={o.line} className="chip-btn" onClick={() => replace(l.id, rebook(l, o))}
                              title={`Swap to ${playLabel(l.kind, l.side, o.line)} at ${target}`}>
                              {playLabel(l.kind, l.side, o.line)} <b>{americanOdds(o.odds)}</b>
                              <span className={o.ev > 0 ? "pos" : "mut"}> {signedPct(o.ev)}</span>
                            </button>
                          ))}
                        </div>
                      ) : (
                        <p className="mut">{target} has no {l.side.toLowerCase()} quotes for this player yet. Open the player and load alternate lines to see more.</p>
                      )}
                    </div>
                  );
                })}
              </section>
            )}

            {legs.length > 0 && staleSince && (
              <label className="stale-ack">
                <input type="checkbox" checked={!gated} onChange={(e) => setCheckedStale(e.target.checked ? staleSince : null)} />
                <span><b>These prices are {timeAgo(staleSince).replace(" ago", "")} old</b> and have probably moved. I'll check each one at the book before betting.</span>
              </label>
            )}
            {/* always mounted so a clash appearing (or being fixed) is announced, but opening the slip isn't */}
            <p id="slip-clash-msg" className="err slip-clash-msg" aria-live="polite">
              {clashSummary && <button className="link err" onClick={showClash}>{clashSummary} Show me</button>}
            </p>
            {/* always mounted, like the clash message: a price moving while the slip is open is announced */}
            <p id="slip-price-msg" className="warn slip-price-msg" aria-live="polite">{priceSummary}</p>
            {legs.length > 0 && (
              <div className="slip-footer">
                {groups.map(([book]) => placeButton(book))}
                <button className="primary outline" disabled={!links} onClick={() => copyText(links, "link")}
                  title={!links && clashes.length ? "Keep one leg of each clash first" : links ? "Copies the sportsbook link so you can open the slip on another device" : "No betslip links to copy yet"}>
                  {copied === "link" ? "Link copied" : "Copy slip link"}
                </button>
                {gambly.url && !gated ? (
                  <a className="primary outline" href={gambly.url} target="_blank" rel="noopener noreferrer"
                    title="Builds this slip at every book Gambly covers, so you can compare prices and place it at another book"
                    onClick={() => setPlaceMsg(`Opened Gambly with ${gambly.mapped.length} ${gambly.mapped.length === 1 ? "leg" : "legs"}${groups.length > 1 ? " as one parlay" : ""}. It picks the nearest line when a book lacks yours, so check each line there.`)}>
                    Send to Gambly ↗
                  </a>
                ) : (
                  <button className="primary outline" disabled
                    title={gated ? "Tick the box above first: these prices are old" : "Gambly can't build any of these legs"}>Send to Gambly ↗</button>
                )}
              </div>
            )}
            {legs.length > 0 && gambly.unmapped.length > 0 && (
              <p className="mut slip-gambly-note">
                Not sent to Gambly: {gambly.unmapped.map(({ leg: l, reason }, i) => (
                  <span key={l.id}>{i > 0 && "; "}<b>{l.name} {playLabel(l.kind, l.side, l.line)}</b>{!namesItself(l.kind) && ` ${KIND_SHORT[l.kind]}`} ({l.book}): {reason}</span>
                ))}.
              </p>
            )}
            {placeMsg && <p className="mut slip-place-msg" aria-live="polite">{placeMsg}</p>}
            {legs.length > 1 && (
              <p className="slip-clear-row"><button className="link slip-clear" onClick={clear}>Clear all {legs.length} legs</button></p>
            )}
            {legs.length > 0 && auth && !auth.user && (
              <p className="mut slip-sync-hint">Saved in this browser only. <button className="link" onClick={() => openSignIn()}>Sign in</button> to keep this slip on every device.</p>
            )}
            <p className="mut slip-disclaimer">
              This app doesn't place bets: you sign in and confirm at the sportsbook. Check the line and price first.
            </p>
            {/* always mounted so the removal is announced; sticks to the bottom of the slip so it shows wherever it's scrolled */}
            <div className="slip-undo-wrap" role="status" aria-live="polite">
              {lastRemoved && (
                <div className="slip-undo">
                  <span>Removed {lastRemoved.length === 1 ? `${lastRemoved[0].leg.name} ${playLabel(lastRemoved[0].leg.kind, lastRemoved[0].leg.side, lastRemoved[0].leg.line)}`
                    : `${lastRemoved.length} legs`}</span>
                  <button className="link" onClick={undoRemove}>Undo</button>
                  <button className="slip-undo-x" onClick={dismissUndo} aria-label="Dismiss">×</button>
                </div>
              )}
            </div>
        </Sheet>
      )}
      {open && tracking && groups.some(([b]) => b === tracking) && (
        <TrackBetSheet book={tracking} legs={groups.find(([b]) => b === tracking)![1]} stake={stakeFor(tracking)} promo={promoFor(tracking)} onClose={() => setTracking(null)}
          onLeave={() => { setTracking(null); close(); }} />
      )}
    </>
  );
}
