import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { IconCheck, IconDots, IconDownload, IconMinus, IconPlus, IconX } from "@tabler/icons-react";
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useEffect, useRef, useState } from "react";
import { useAuth } from "../auth";
import { costOf, csvUrl, deleteBet, editBet, markLeg, voidableLegs, money, myBets, parseOdds, promoBody, promoOf, promoReady, signedMoney, takeSharedSlip, type BetFilters, type BetStatus, type BetSummary, type TrackedBet, type TrackedLeg } from "../bets";
import { namesItself, KIND_SHORT, americanOdds, pct, signedPct } from "../format";
import { useLeague } from "../LeagueContext";
import { leaguePath, goToPath } from "../route";
import AddBetSheet from "./AddBetSheet";
import BookBadge from "./BookBadge";
import BookSelect from "./BookSelect";
import Menu, { MenuItem } from "./Menu";
import PromoFields from "./PromoFields";
import SidePill from "./SidePill";
import { openSignIn } from "./SignIn";
import Skeleton from "./Skeleton";
import TagInput from "./TagInput";

const LEAGUES: [BetFilters["league"], string][] = [[undefined, "All"], ["nfl", "NFL"], ["nba", "NBA"], ["oth", "Other"]];
const PERIODS: [BetFilters["months"], string][] = [[1, "Last month"], [3, "Last 3 months"], [12, "Last 12 months"], [undefined, "All time"]];
const STATUS_TEXT: Record<BetStatus, string> = { open: "Open", won: "Won", lost: "Lost", push: "Push", void: "Void" };
// the site's one icon set (Tabler), as everywhere else, rather than text marks
const LEG_MARK: Record<NonNullable<TrackedLeg["result"]>, [typeof IconCheck, string]> = { win: [IconCheck, "won"], loss: [IconX, "lost"], push: [IconMinus, "push"], void: [IconMinus, "void"] };

// the view (league, book, tag, period) is remembered in this browser, so My bets reopens where it was left
const VIEW_KEY = "my-bets-view";
const storedView = (): BetFilters => { try { return JSON.parse(localStorage.getItem(VIEW_KEY) ?? "{}"); } catch { return {}; } };

function Status({ bet }: { bet: TrackedBet }) {
  // a parlay with legs the bettor marks: once the site's legs are graded, the rest is up to them
  const yours = bet.kind === "custom" || bet.legs.some((l) => l.kind === "custom" && !l.result) && bet.legs.every((l) => l.kind === "custom" || l.result);
  // every leg is in but a dropped leg's price isn't known: it waits on the payout the bettor enters, not on stats
  if (bet.status === "open") return <span className="st">{!bet.started ? "Open" : yours || bet.needs_payout ? "Awaiting your result" : "Awaiting stats"}</span>;
  const cls = bet.status === "won" ? "st st-ok" : bet.status === "lost" ? "st st-error" : "st";
  return <span className={cls}>{STATUS_TEXT[bet.status]}{bet.profit ? ` ${signedMoney(bet.profit)}` : ""}</span>;
}

function LegRow({ leg, bet, onUndo, busy }: { leg: TrackedLeg; bet: TrackedBet; onUndo: () => void; busy: boolean }) {
  const [Mark, word] = leg.result ? LEG_MARK[leg.result] : [null, ""];
  // a custom bet: what the bettor wrote, and when its event starts (they mark the result)
  if (leg.kind === "custom") return (
    <li className="bet-leg custom">
      <span className="bet-leg-main"><b>{bet.description ?? leg.name}</b>
        <span className="mut bet-leg-sub">{new Date(leg.kickoff + "Z").toLocaleString(undefined, { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}
          {bet.status === "open" && " · you mark the result"}</span></span>
    </li>
  );
  return (
    <li className={`bet-leg${leg.result ? ` r-${leg.result}` : ""}`}>
      <span className="bet-leg-mark" role="img" aria-label={word || "not graded yet"}>{Mark && <Mark size={15} stroke={2.25} aria-hidden />}</span>
      <span className="bet-leg-main">
        <b>{leg.name}</b> <SidePill kind={leg.kind} side={leg.side} line={leg.line} />
        {!namesItself(leg.kind) && <span className="mut"> {KIND_SHORT[leg.kind]}</span>}
        {leg.alt && <span className="alt">alt</span>}
        <span className="mut bet-leg-sub">{leg.team} {leg.opp ? `vs ${leg.opp}` : ""} · {new Date(leg.kickoff + "Z").toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" })}
          {leg.kind === "ml" ? (leg.result === "void" ? " · no result" : leg.actual == null ? "" : leg.actual === 0 ? " · tied" : ` · ${leg.actual > 0 ? "won" : "lost"} by ${Math.abs(leg.actual)}`)
            + (bet.early_win && leg.lead != null && leg.actual != null && leg.actual <= 0
              ? (leg.lead >= bet.early_win ? `, but led by ${leg.lead}: won early` : `, led by ${leg.lead} at most`) : "")
            : leg.book_void ? (leg.actual != null ? ` · ${leg.actual}` : "") : leg.result === "void" ? " · didn't play" : leg.actual != null ? ` · ${leg.actual}` : ""}
          {leg.book_void && <> · voided by {bet.book} <button type="button" className="link bet-leg-undo" disabled={busy} onClick={onUndo}
            aria-label={`Undo: ${leg.name} wasn't voided by ${bet.book}`}>Undo</button></>}</span>
      </span>
      <span className="mut">{leg.odds ? americanOdds(leg.odds) : ""}</span>
    </li>
  );
}

function BetCard({ bet, knownTags, onTag }: { bet: TrackedBet; knownTags: string[]; onTag: (t: string) => void }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [stake, setStake] = useState(String(bet.stake));
  const [odds, setOdds] = useState(americanOdds(bet.odds));
  const [tags, setTags] = useState(bet.tags);
  const [promo, setPromo] = useState(promoOf(bet));
  const [problem, setProblem] = useState("");
  const [confirming, setConfirming] = useState(false);
  // picking the parlay leg the book voided though he played (injury protection)
  const [voiding, setVoiding] = useState(false);
  // a parlay whose voided leg has no known price: what the book paid, as typed
  const [paid, setPaid] = useState("");
  const paidN = Number(paid.replace(/[$,\s]/g, ""));
  const paidOk = paid.trim() !== "" && Number.isFinite(paidN) && paidN >= 0;
  const dropped = bet.legs.filter((l) => l.result === "void" || l.result === "push");
  const card = useRef<HTMLElement>(null);
  // closing the edit form or the delete question hands focus back to the card's ⋯ button, rather than to the page
  const backToMenu = () => requestAnimationFrame(() => card.current?.querySelector<HTMLElement>(".bet-head .icon-btn")?.focus());
  const done = () => qc.invalidateQueries({ queryKey: ["my-bets"] });
  const save = useMutation({ mutationFn: (p: Parameters<typeof editBet>[1]) => editBet(bet.id, p), onSuccess: () => { setEditing(false); setProblem(""); done(); },
    onError: (e: Error) => setProblem(e.message) });
  const leg = useMutation({ mutationFn: ({ id, on }: { id: number; on: boolean }) => markLeg(bet.id, id, on),
    onSuccess: () => { setVoiding(false); setProblem(""); backToMenu(); done(); }, onError: (e: Error) => setProblem(e.message) });
  const voidable = voidableLegs(bet);
  const remove = useMutation({ mutationFn: () => deleteBet(bet.id), onSuccess: done, onError: (e: Error) => { setConfirming(false); setProblem(e.message); } });
  const oddsN = parseOdds(odds), stakeN = Number(stake.replace(/[$,\s]/g, ""));
  const title = bet.kind === "parlay" ? `${bet.legs.length}-leg parlay` : bet.kind === "custom" ? "Other bet" : "Single";
  const startEdit = () => { setConfirming(false); setVoiding(false); setStake(String(bet.stake)); setOdds(americanOdds(bet.odds)); setTags(bet.tags); setPromo(promoOf(bet)); setEditing(true); };
  const canSave = stakeN > 0 && !!oddsN && promoReady(promo, stakeN);
  // what a screen reader hears for this card's ⋯ button, so each card's reads differently
  const what = bet.kind === "custom" ? bet.description ?? "" : bet.legs.map((l) => l.name).join(", ");
  const promoPills = [
    bet.bonus && <span key="b" className="bet-pill" title="Placed with a bonus bet: not counted as money you staked">Bonus bet</span>,
    bet.boost && <span key="x" className="bet-pill" title={bet.boost_cap && bet.boost_cap < bet.stake
      ? `A ${bet.boost}% profit boost on the first ${money(bet.boost_cap)} of the stake: that part's winnings pay ${1 + bet.boost / 100}x, the rest the plain price`
      : `A ${bet.boost}% profit boost: a win pays ${1 + bet.boost / 100}x the winnings at these odds`}>
      {bet.boost}% boost{bet.boost_cap && bet.boost_cap < bet.stake ? ` · up to ${money(bet.boost_cap)}` : ""}</span>,
    bet.no_sweat && <span key="n" className="bet-pill" title={`A no-sweat bet: if it loses, ${money(bet.no_sweat)} comes back as a bonus bet`}>No sweat</span>,
    bet.early_win && <span key="e" className="bet-pill" title={`An early-win token: it pays as a win once the team leads by ${bet.early_win}, even if they lose`}>Early win · up {bet.early_win}</span>,
    bet.late && <span key="l" className="bet-pill" title="Added after its game had started, so the result may have been known">Added late</span>,
  ].filter(Boolean);
  const showTags = bet.tags.length > 0 && !editing;
  // a pushed or voided bonus bet: the book hands the bonus bet back, rather than any cash
  const bonusBack = bet.bonus && (bet.status === "push" || bet.status === "void");

  return (
    <article className="card bet-card" ref={card}>
      <header className="bet-head">
        <span className="bet-book"><BookBadge book={bet.book} size={22} /><span className="bet-book-text"><b>{bet.book}</b><span className="mut">{title} · {bet.league === "oth" ? "Other" : bet.league.toUpperCase()}</span></span></span>
        <span className="bet-head-aside">
          <Status bet={bet} />
          <Menu label={<IconDots size={16} stroke={1.75} aria-hidden />} ariaLabel={`Actions for ${title.toLowerCase()} at ${bet.book}${what ? `: ${what}` : ""}`} caret={false} buttonClass="icon-btn">
            {(close) => <>
              <MenuItem onClick={() => { close(); startEdit(); }}>Edit stake, odds, promo and tags</MenuItem>
              {!bet.boost && !bet.no_sweat && <MenuItem onClick={() => { close(); save.mutate({ bonus: !bet.bonus }); }}>{bet.bonus ? "Not a bonus bet" : "Mark as bonus bet"}</MenuItem>}
              {voidable.length > 0 && <MenuItem onClick={() => { close(); setEditing(false); setConfirming(false); setVoiding(true); }}>A leg was voided by {bet.book}…</MenuItem>}
              {(["won", "lost", "push", "void"] as BetStatus[]).filter((s) => !(bet.settled_by === "user" && bet.status === s)).map((s) => (
                <MenuItem key={s} onClick={() => { close(); save.mutate({ status: s }); }}>Mark {STATUS_TEXT[s].toLowerCase()}</MenuItem>
              ))}
              {bet.settled_by === "user" && <MenuItem onClick={() => { close(); save.mutate({ status: "open" }); }}>{bet.kind === "custom" ? "Back to open" : "Back to automatic grading"}</MenuItem>}
              <MenuItem danger onClick={() => { close(); setEditing(false); setVoiding(false); setConfirming(true); }}>Delete</MenuItem>
            </>}
          </Menu>
        </span>
      </header>
      {(promoPills.length > 0 || showTags) && (
        <div className="bet-tags">{promoPills}
          {showTags && bet.tags.map((t) => <button key={t} className="tag-chip" onClick={() => onTag(t)} title={`Show bets tagged ${t}`}>{t}</button>)}</div>
      )}
      <ul className="bet-legs">{bet.legs.map((l) => <LegRow key={l.id} leg={l} bet={bet} busy={leg.isPending} onUndo={() => leg.mutate({ id: l.id, on: false })} />)}</ul>
      {editing ? (
        <form className="bet-edit" onSubmit={(e) => { e.preventDefault(); if (canSave) save.mutate({ stake: stakeN, odds: oddsN!, tags, ...promoBody(promo, stakeN) }); }}>
          <div className="bet-edit-row">
            <label className="auth-label">Stake<input className="auth-input" inputMode="decimal" value={stake} onChange={(e) => setStake(e.target.value)} autoFocus /></label>
            <label className="auth-label">Odds<input className="auth-input" value={odds} onChange={(e) => setOdds(e.target.value)} aria-invalid={!oddsN} /></label>
          </div>
          <PromoFields promo={promo} onChange={setPromo} stake={stakeN > 0 ? stakeN : 0} book={bet.book} compact
            moneyline={bet.kind === "single" && bet.legs[0]?.kind === "ml" && bet.league !== "oth" ? bet.league : null} />
          <label className="auth-label" htmlFor={`tags-${bet.id}`}>Tags</label>
          <TagInput id={`tags-${bet.id}`} tags={tags} onChange={setTags} suggestions={knownTags} />
          <div className="bet-edit-row">
            <button className="primary" disabled={!canSave || save.isPending}>Save</button>
            <button type="button" className="link" onClick={() => { setEditing(false); backToMenu(); }}>Cancel</button>
          </div>
        </form>
      ) : voiding ? (
        <div className="bet-void-pick" role="group" aria-labelledby={`void-q-${bet.id}`}>
          <p id={`void-q-${bet.id}`}>Which leg did {bet.book} void? Use this when the book voided a leg though the player played, for injury
            protection, say. It drops out and the parlay pays on the other legs.</p>
          <div className="bet-void-legs">
            {voidable.map((l, i) => (
              <button key={l.id} type="button" className="chip-btn" autoFocus={i === 0} disabled={leg.isPending} onClick={() => leg.mutate({ id: l.id, on: true })}>
                <b>{l.name}</b> {l.kind !== "custom" && <SidePill kind={l.kind} side={l.side} line={l.line} />}
              </button>
            ))}
          </div>
          <button type="button" className="link" onClick={() => { setVoiding(false); backToMenu(); }}>Cancel</button>
        </div>
      ) : confirming ? (
        <div className="bet-confirm" role="group" aria-label="Delete this bet?">
          <p>Delete this {title.toLowerCase()} at {bet.book}? It comes off your record, and can't be undone.</p>
          <div className="bet-edit-row">
            <button type="button" className="danger-btn" disabled={remove.isPending} onClick={() => remove.mutate()}>{remove.isPending ? "Deleting…" : "Delete bet"}</button>
            <button type="button" className="link" autoFocus onClick={() => { setConfirming(false); backToMenu(); }}>Keep it</button>
          </div>
        </div>
      ) : (
        <p className="bet-foot mut">
          {money(bet.stake)}{bet.bonus && " bonus bet"} at <b>{americanOdds(bet.odds)}</b>{bet.boost && <>, boosted {bet.boost}%{bet.boost_cap && bet.boost_cap < bet.stake && <> on the first {money(bet.boost_cap)}</>}</>}
          {bet.needs_payout ? <> → pays less than <b>{money(bet.to_pay)}</b>, with {dropped.length === 1 ? "a leg" : `${dropped.length} legs`} out</>
            : bet.status === "open" ? <> → pays <b>{money(bet.to_pay)}</b>{bet.no_sweat && <>, or <b>{money(bet.no_sweat)}</b> back as a bonus bet if it loses</>}</>
            : bonusBack ? <> → the bonus bet came back</> : bet.payout != null && <> → returned <b>{money(bet.payout)}</b></>}
          {bet.refund > 0 && <>, plus <b>{money(bet.refund)}</b> back as a bonus bet</>}
          {" · "}placed {new Date(bet.placed_at + "Z").toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })}
          {bet.ev_close != null && <> · <span className={bet.ev_close > 0 ? "good" : "bad"}
            title="What this price was worth by the market's closing line, the best early sign of a good bet">{signedPct(bet.ev_close)} vs the close</span></>}
          {bet.settled_by === "user" && " · settled by you"}
        </p>
      )}
      {bet.needs_payout && !editing && !confirming && (
        <form className="board-note bet-needs-payout" onSubmit={(e) => { e.preventDefault(); if (paidOk) save.mutate({ status: "won", payout: Math.round(paidN * 100) / 100 }); }}>
          <p><b>{dropped.map((l) => l.name).join(" and ")}</b> {dropped.length === 1 ? "was" : "were"} {dropped.every((l) => l.result === "push") ? "a push" : "voided"},
            so {bet.book} pays the rest at a lower price. {dropped.length === 1 ? "That leg's" : "Their"} price wasn't on your slip or in the odds this site
            saved, so the payout can't be worked out. Enter what {bet.book} paid back to settle it.</p>
          <div className="bet-edit-row">
            <label className="auth-label">Paid back<input className="auth-input" inputMode="decimal" autoComplete="off" value={paid} placeholder="$0.00"
              aria-invalid={paid.trim() !== "" && !paidOk} onChange={(e) => setPaid(e.target.value)} /></label>
            <button className="primary" disabled={!paidOk || save.isPending}>{save.isPending ? "Saving…" : "Settle as won"}</button>
          </div>
        </form>
      )}
      {problem && <p className="err" role="alert">{problem}</p>}
    </article>
  );
}

/** Running profit by settled bet, once there are enough to show a trend (10+), drawn like the site's other charts. */
function ProfitChart({ summary }: { summary: BetSummary }) {
  if (summary.curve.length < 10) return null;
  const rows = summary.curve.map(([at, profit], i) => ({ i: i + 1, profit, day: new Date(at + "Z").toLocaleDateString(undefined, { month: "short", day: "numeric" }) }));
  return (
    <section className="card bets-chart">
      <h3 className="sub-h">Profit over time <small className="mut">{rows.length} settled bets, oldest first</small></h3>
      <ResponsiveContainer width="100%" height={180}>
        <LineChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: -8 }}>
          <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="var(--line)" />
          <XAxis dataKey="day" tick={{ fontSize: 10, fill: "var(--mut)" }} minTickGap={24} />
          <YAxis tickFormatter={(v: number) => `$${Math.round(v)}`} tick={{ fontSize: 11, fill: "var(--mut)" }} width={52} />
          <ReferenceLine y={0} stroke="var(--mut)" />
          <Tooltip contentStyle={{ background: "var(--card)", border: "1px solid var(--line)", color: "var(--ink)" }}
            formatter={(v: number) => [signedMoney(v), "Profit"]} labelFormatter={(_, p) => (p?.[0] ? `Bet ${p[0].payload.i} · ${p[0].payload.day}` : "")} />
          <Line dataKey="profit" stroke="var(--acc2)" strokeWidth={2} dot={rows.length <= 40 ? { r: 2.5 } : false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </section>
  );
}

/** Record, profit and ROI for one group of bets (a sportsbook, a tag), as the server's summary works them out. */
function record(bets: TrackedBet[]) {
  const settled = bets.filter((b) => b.status === "won" || b.status === "lost");
  const staked = settled.reduce((s, b) => s + costOf(b), 0), profit = settled.reduce((s, b) => s + (b.profit ?? 0), 0);
  return { n: bets.length, wins: bets.filter((b) => b.status === "won").length, losses: bets.filter((b) => b.status === "lost").length,
    pushes: bets.filter((b) => b.status === "push").length, profit, roi: staked ? profit / staked : null };
}

/** "By sportsbook" / "By tag": each group's record, profit and ROI in the current view; a row opens that group's view. */
function Breakdown({ title, groups, onPick }: { title: string; groups: [string, TrackedBet[]][]; onPick: (key: string) => void }) {
  if (groups.length < 2) return null;
  return (
    <section className="card bets-breakdown">
      <h3 className="sub-h">{title}</h3>
      <div className="bd-rows" role="list">
        {groups.map(([key, bets]) => {
          const r = record(bets);
          return (
            <button key={key} className="bd-row" role="listitem" onClick={() => onPick(key)} title={`Show only ${key}`}>
              <span className="bd-name">
                {title === "By sportsbook" && <BookBadge book={key} size={20} />}
                <span><b>{key}</b><span className="mut bd-sub">{r.n} {r.n === 1 ? "bet" : "bets"} · {r.wins}-{r.losses}{r.pushes ? `-${r.pushes}` : ""}</span></span>
              </span>
              <span className={r.profit < 0 ? "neg" : r.profit > 0 ? "pos" : ""}>{signedMoney(r.profit)}</span>
              <span className="mut">{r.roi == null ? "—" : `${r.roi > 0 ? "+" : ""}${pct(r.roi, 1)}`}</span>
            </button>
          );
        })}
      </div>
    </section>
  );
}

/** My bets (/bets, /nba/bets): the bets this visitor tracked from the slip, graded from the box scores, and their record,
 *  viewed by league, sportsbook, tag and period. Private to them; signed out, a prompt to sign in. */
export default function MyBets() {
  const auth = useAuth();
  const { league: siteLeague } = useLeague();
  const [filters, setFiltersState] = useState<BetFilters>(storedView);
  const [adding, setAdding] = useState(false);
  const [shared, setShared] = useState<File | string>();
  const [sharedWaiting, setSharedWaiting] = useState(false);
  const setFilters = (f: BetFilters) => {
    const clean = Object.fromEntries(Object.entries(f).filter(([, v]) => v != null)) as BetFilters;
    setFiltersState(clean);
    try { localStorage.setItem(VIEW_KEY, JSON.stringify(clean)); } catch { /* private mode: not remembered */ }
  };
  const user = auth?.user;
  // a slip (screenshot or share link) shared to the app from a sportsbook's share menu: opens in Add a bet once they're signed in
  useEffect(() => {
    if (!auth) return;
    if (!user?.age_confirmed) { takeSharedSlip(true).then((f) => setSharedWaiting(!!f)); return; }
    takeSharedSlip().then((f) => { if (f) { setShared(f); setAdding(true); } });
  }, [auth, user?.age_confirmed]); // eslint-disable-line react-hooks/exhaustive-deps
  const q = useQuery({ queryKey: ["my-bets", user?.email, filters], queryFn: () => myBets(filters), enabled: !!user?.age_confirmed, placeholderData: (p) => p });
  // a remembered book or tag that's gone (its bets deleted) shouldn't leave an empty view with no way to pick it again
  const books = q.data?.books, tagList = q.data?.tags;
  useEffect(() => {
    if (!books || !tagList) return;
    if ((filters.book && !books.includes(filters.book)) || (filters.tag && !tagList.some((t) => t.toLowerCase() === filters.tag!.toLowerCase())))
      setFilters({ ...filters, book: filters.book && books.includes(filters.book) ? filters.book : undefined,
        tag: filters.tag && tagList.some((t) => t.toLowerCase() === filters.tag!.toLowerCase()) ? filters.tag : undefined });
  }, [books, tagList]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!auth) return <Skeleton kind="cards" />;
  if (!user) return (
    <div className="card prefs-card bets-intro">
      <h3 className="sub-h">Track the bets you place</h3>
      {sharedWaiting && <p className="banner">Sign in and the slip you shared opens in Add a bet.</p>}
      <p className="mut">Keep a record of every bet you place: track one from the betslip, or add one you placed anywhere (a screenshot of the
        slip fills it in). Player props are graded from the box scores, and bonus bets, profit boosts and no-sweat refunds count the way the
        book pays them. Your record, profit and ROI build up here, by league, sportsbook and your own tags. Only you can see them.</p>
      <div className="prefs-actions"><button className="primary" onClick={() => openSignIn(undefined, "Track your bets and see your record, profit and ROI on every device.")}>Sign in</button></div>
    </div>
  );
  if (!user.age_confirmed) return <div className="card prefs-card"><p className="mut">Confirm you're 21 or older to track bets.</p></div>;
  if (q.isLoading) return <Skeleton kind="cards" />;
  if (q.error || !q.data) return <p className="err">{(q.error as Error)?.message ?? "Couldn't load your bets."}</p>;

  const { bets, summary: s, books: allBooks, tags: allTags } = q.data;
  const open = bets.filter((b) => b.status === "open"), settled = bets.filter((b) => b.status !== "open");
  const narrowed = !!(filters.league || filters.book || filters.tag || filters.months);
  const group = (key: (b: TrackedBet) => string[]) => {
    const m = new Map<string, TrackedBet[]>();
    for (const b of bets) for (const k of key(b)) m.set(k, [...(m.get(k) ?? []), b]);
    return [...m.entries()].sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0]));
  };
  const leagueWord = filters.league ? `${filters.league.toUpperCase()} ` : "";
  const periodWord = PERIODS.find(([m]) => m === filters.months)?.[1].toLowerCase();

  return (
    <div className="bets">
      <div className="bets-views">
        <div className="seg small" role="group" aria-label="League">
          {LEAGUES.filter(([l]) => l !== "oth" || q.data?.leagues.includes("oth") || filters.league === "oth").map(([l, label]) => (
            <button key={label} className={filters.league === l ? "on" : ""} aria-pressed={filters.league === l} onClick={() => setFilters({ ...filters, league: l })}>{label}</button>
          ))}
        </div>
        {allBooks.length > 0 && (
          <BookSelect books={allBooks} book={filters.book ?? "all"} allLabel="Every sportsbook" ariaLabel="Sportsbook"
            onChange={(b) => setFilters({ ...filters, book: b === "all" ? undefined : b })} />
        )}
        <select value={filters.months ?? ""} aria-label="Period" onChange={(e) => setFilters({ ...filters, months: (e.target.value ? Number(e.target.value) : undefined) as BetFilters["months"] })}>
          {PERIODS.map(([m, label]) => <option key={label} value={m ?? ""}>{label}</option>)}
        </select>
        <button className="chip-menu bets-add" onClick={() => setAdding(true)}><IconPlus size={16} stroke={1.75} aria-hidden />Add a bet</button>
        {bets.length > 0 && (
          <a className="chip-menu bets-export" href={csvUrl(filters)} download title="These bets as a spreadsheet, one row per leg">
            <IconDownload size={16} stroke={1.75} aria-hidden />Export CSV
          </a>
        )}
      </div>
      {allTags.length > 0 && (
        <div className="pills bets-tags" role="group" aria-label="Tag">
          <span className="mut">Tags</span>
          {allTags.map((t) => {
            const on = filters.tag?.toLowerCase() === t.toLowerCase();
            return <button key={t} className={`pill${on ? " on" : ""}`} aria-pressed={on} onClick={() => setFilters({ ...filters, tag: on ? undefined : t })}>{t}</button>;
          })}
        </div>
      )}
      {narrowed && (
        <p className="mut bets-showing">
          Showing {leagueWord}bets{filters.book ? <> at <b>{filters.book}</b></> : ""}{filters.tag ? <> tagged <b>{filters.tag}</b></> : ""}
          {periodWord && periodWord !== "all time" ? `, ${periodWord}` : ""}. <button className="link" onClick={() => setFilters({})}>Show all</button>
        </p>
      )}

      <div className="admin-cards">
        <div className="card admin-card"><span className="mut">Record</span><div className="big">{s.wins}-{s.losses}{s.pushes ? `-${s.pushes}` : ""}</div>
          <span className="mut">won-lost{s.pushes ? "-push" : ""}</span></div>
        <div className="card admin-card"><span className="mut">Profit</span><div className={`big${s.profit < 0 ? " under" : s.profit > 0 ? " over" : ""}`}>{signedMoney(s.profit)}</div>
          <span className="mut">on {money(s.staked)} settled{s.bonus_staked > 0 && <>, plus {money(s.bonus_staked)} in bonus bets</>}
            {s.refunds > 0 && <span title="Refunded by no-sweat bets that lost. They count once you track them as bonus bets and they win."> · {money(s.refunds)} back as bonus bets</span>}</span></div>
        <div className="card admin-card"><span className="mut">ROI</span><div className={`big${(s.roi ?? 0) < 0 ? " under" : (s.roi ?? 0) > 0 ? " over" : ""}`}>{s.roi == null ? "—" : `${s.roi > 0 ? "+" : ""}${pct(s.roi, 1)}`}</div>
          <span className="mut">profit ÷ staked</span></div>
        {s.clv_n > 0 && (
          <div className="card admin-card" title="The share of your bets priced better than the market's closing line: over a few dozen bets, a truer sign of skill than wins">
            <span className="mut">Beat the close</span><div className={`big${(s.beat_close ?? 0) >= 0.5 ? " over" : " under"}`}>{pct(s.beat_close ?? 0, 0)}</div>
            <span className="mut">{signedPct(s.ev_close ?? 0)} EV at the close · {s.clv_n} {s.clv_n === 1 ? "bet" : "bets"}</span>
          </div>
        )}
        <div className="card admin-card"><span className="mut">Open</span><div className="big">{s.open}</div><span className="mut">{money(s.open_stake)} at stake</span></div>
      </div>
      <ProfitChart summary={s} />

      {(!filters.book || !filters.tag) && bets.length > 0 && (
        <div className="bets-breakdowns">
          {!filters.book && <Breakdown title="By sportsbook" groups={group((b) => [b.book])} onPick={(book) => setFilters({ ...filters, book })} />}
          {!filters.tag && <Breakdown title="By tag" groups={group((b) => b.tags)} onPick={(tag) => setFilters({ ...filters, tag })} />}
        </div>
      )}

      {!bets.length ? (
        <div className="card prefs-card bets-intro">
          <h3 className="sub-h">{narrowed ? "No bets in this view" : "No tracked bets yet"}</h3>
          {narrowed ? <div className="prefs-actions"><button className="primary outline" onClick={() => setFilters({})}>Show all bets</button></div> : <>
            <p className="mut">Tap <b>Placed it? Track this bet</b> in the betslip after you place a bet, or add one you placed somewhere
              else. Player props are graded once the stats are in; anything else, you mark when it settles.</p>
            <div className="prefs-actions">
              <button className="primary" onClick={() => setAdding(true)}>Add a bet you placed</button>
              <button className="primary outline" onClick={() => goToPath(leaguePath(siteLeague, "props"))}>Go to Best props</button>
            </div>
          </>}
        </div>
      ) : <>
        {open.length > 0 && <><h3 className="sub-h bets-h">Open <small className="mut">{open.length}</small></h3>
          {open.map((b) => <BetCard key={b.id} bet={b} knownTags={allTags} onTag={(tag) => setFilters({ ...filters, tag })} />)}</>}
        {settled.length > 0 && <><h3 className="sub-h bets-h">Settled <small className="mut">{settled.length}</small></h3>
          {settled.map((b) => <BetCard key={b.id} bet={b} knownTags={allTags} onTag={(tag) => setFilters({ ...filters, tag })} />)}</>}
      </>}
      {adding && <AddBetSheet defaultLeague={siteLeague} sharedSlip={shared} onClose={() => { setAdding(false); setShared(undefined); }} />}
      <p className="mut bets-note">NFL bets settle when the week's official stats come out, usually the Tuesday after the games; NBA bets the next
        morning. A player who didn't play is void, as the books settle it. If your book settled a bet differently, mark it from the bet's menu, including a parlay leg it voided though he played (injury protection).</p>
    </div>
  );
}
