import { useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { Bar, BarChart, CartesianGrid, Cell as BarCell, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, type Kind } from "../api";
import { useAuth } from "../auth";
import { useBetSlip } from "../BetSlipContext";
import { MAX_FAV_PLAYERS, toggleFavPlayer, useFavorites } from "../favorites";
import { getLeague } from "../league";
import { DOUBLE_NEED, isDouble, isYesNo, KIND_LABEL, KIND_SHORT, americanOdds, effText, fmtEdge, fmtProj, fmtSd, isCount, isNba, matchup, pct, playLabel, signedPct,
  timeAgo, unit, volLabel, weatherText, wxEffect } from "../format";
import { hitRate, hits, seasonStarts, withGaps, type LogBar } from "../gameLog";
import { useOverflowFade } from "../useOverflowFade";
import type { DrawerPick } from "../playerHash";
import Avatar from "./Avatar";
import { BookName } from "./BookBadge";
import LineLadder, { ODDS_RANGE, bestOf, legFor, type Cell, type PlayerCtx } from "./LineLadder";
import LineMovement from "./LineMovement";
import Sheet from "./Sheet";
import Skeleton from "./Skeleton";
import TrackBetSheet from "./TrackBetSheet";
import VsDefense from "./VsDefense";
import { FavStar } from "./icons";
import { openSignIn } from "./SignIn";
import { HitSplits, WhyPanel } from "./Why";

interface Props {
  playerId: string; initialKind: Kind;
  /** The play the drawer was opened from, if a priced row was clicked: the drawer leads with it. */
  pick?: DrawerPick;
  marketWeight: number | null; book: string; onBookChange: (b: string) => void;
  /** A market picked from the tabs (kept in the link). */
  onKindChange?: (k: Kind) => void;
  onClose: () => void;
}

const coarse = () => typeof window !== "undefined" && window.matchMedia("(pointer: coarse)").matches;
const narrow = () => typeof window !== "undefined" && window.matchMedia("(max-width: 480px)").matches;

export default function PlayerDrawer({ playerId, initialKind, pick, marketWeight, book, onBookChange, onKindChange, onClose }: Props) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["player", playerId, marketWeight], queryFn: () => api.player(playerId, marketWeight),
  });
  const [kind, setKindState] = useState<Kind>(initialKind);
  useEffect(() => setKindState(initialKind), [initialKind, playerId]);
  const setKind = (k: Kind) => { setKindState(k); onKindChange?.(k); };
  const slip = useBetSlip();

  const proj = data?.projections.find((p) => p.kind === kind);
  const meta = useQuery({ queryKey: ["meta"], queryFn: api.meta });   // already cached by the page
  const weather = meta.data?.games.find((g) => g.game_id === proj?.game_id)?.weather;
  const wxText = weatherText(weather);
  const wxAdj = !isYesNo(kind) ? wxEffect(proj?.wx) : null;

  // market tabs: the one opened first, then the rest by their best price's EV (a + marks a market with a +EV price somewhere)
  const bestEv = (k: Kind) => Math.max(-1, ...(data?.picks ?? []).filter((p) => p.kind === k).map((p) => p.ev));
  const kinds = useMemo(() => {
    const all = (data?.projections ?? []).map((p) => p.kind);
    const rest = all.filter((k) => k !== initialKind).sort((a, b) => bestEv(b) - bestEv(a));
    return all.includes(initialKind) ? [initialKind, ...rest] : rest;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, initialKind]);

  // the same ladder the table shows (so it follows the sportsbook filter) gives the chart its main lines and the drawer its verdict
  const ladder = useQuery({ queryKey: ["ladder", playerId, kind, ODDS_RANGE, marketWeight], queryFn: () => api.lines(playerId, kind, ODDS_RANGE, marketWeight) });
  const yesNo = isYesNo(kind);
  const quotes = ladder.data?.quotes ?? [];
  const atBook = quotes.filter((q) => book === "all" || q.book === book);
  const lines = yesNo ? [] : [...new Set(atBook.filter((q) => !q.alt).map((q) => q.line))].sort((a, b) => a - b);
  const cellsOf = (rows: typeof quotes) => rows.flatMap((r) => [bestOf([r], "over"), bestOf([r], "under")]).filter((c): c is Cell => !!c);
  // the play: the one clicked to get here, else the best-EV main line at the chosen book
  const ladderPick = pick && kind === initialKind
    ? cellsOf(quotes).find((c) => c.side === pick.side && c.row.line === pick.line && c.row.book === pick.book) : undefined;
  // opened from a board row: show that row's own win prob and EV, so the same play reads the same on the card and here (the
  // board blends the model with the books' consensus; the ladder with a curve fitted to every rung, which can differ a little)
  const qc = useQueryClient();
  const boardRow = ladderPick ? boardRowFor(qc, playerId, kind, ladderPick) : undefined;
  const picked = ladderPick && boardRow
    ? { ...ladderPick, q: { ...ladderPick.q, prob: boardRow.prob, ev: boardRow.ev, p_model: boardRow.p_model ?? ladderPick.q.p_model, p_curve: boardRow.p_mkt ?? ladderPick.q.p_curve } }
    : ladderPick;
  const best = cellsOf(atBook.filter((q) => !q.alt)).reduce<Cell | null>((b, c) => (!b || c.q.ev > b.q.ev ? c : b), null);
  const play = picked ?? best;

  const mainLine = play && !play.row.alt ? play.row.line : lines[0] ?? null;
  const side = play?.side ?? "Over";
  // what each logged game is measured against: the play's line, or for yes/no markets whether it happened
  const hitLine = isDouble(kind) ? DOUBLE_NEED[kind]! - 0.5 : yesNo ? 0.5 : play?.row.line ?? mainLine;
  // the server sends this season's and last season's games: the chart shows the last 12, the hit-rate splits use them all
  const allLogs = data?.logs[kind] ?? [];
  const bars = withGaps(allLogs.slice(-12));
  const rate = hitLine != null ? hitRate(bars, side, hitLine) : null;
  const byLabel = new Map(bars.map((b) => [b.label, b]));

  // the big header scrolls away; a compact one takes its place above the market tabs
  const headRef = useRef<HTMLDivElement>(null);
  const [stuck, setStuck] = useState(false);
  useEffect(() => {
    const el = headRef.current;
    if (!el) return;
    const io = new IntersectionObserver(([e]) => setStuck(!e.isIntersecting), { root: el.closest(".drawer"), rootMargin: "-56px 0px 0px 0px" });
    io.observe(el);
    return () => io.disconnect();
  }, [data, proj]);

  // on touch the chart's tooltip shows for a moment after a tap, instead of staying stuck until the next one
  const [tip, setTip] = useState(false);
  const tipTimer = useRef<number>();
  useEffect(() => () => window.clearTimeout(tipTimer.current), []);
  const tapChart = () => { if (!coarse()) return; setTip(true); window.clearTimeout(tipTimer.current); tipTimer.current = window.setTimeout(() => setTip(false), 2500); };

  const ctx: PlayerCtx | null = data && proj ? { playerId: data.player_id, name: data.name, team: data.team, opp: proj.opp, home: proj.home, gameId: proj.game_id } : null;
  const kindPicks = (data?.picks ?? []).filter((p) => p.kind === kind);
  const overPick = kindPicks.find((p) => p.side === "Over"), underPick = kindPicks.find((p) => p.side === "Under");
  const marketYes = overPick ? overPick.p_mkt : underPick ? 1 - underPick.p_mkt : null;

  return (
    <Sheet label={data ? `${data.name}: player detail` : "Player detail"} overlayClass={slip.staleSince ? "odds-stale" : ""} onClose={onClose}>
        {isLoading && <Skeleton kind="panel" />}
        {error && <p className="err">Couldn't load this player.</p>}
        {data && proj && ctx && (
          <>
            <div className="player-head" ref={headRef}>
              <Avatar name={data.name} src={data.headshot} size={84} />
              <div>
                <div className="pd-name"><h2>{data.name}</h2><FavButton player={data} /></div>
                <div className="mut"><span className="pos-pill">{data.pos}</span> {data.team} {matchup(proj.opp, proj.home)}{proj.thin && <> <span className="thin-pill">Thin history</span></>}</div>
              </div>
            </div>
            {wxText && (
              <p className="wx-line mut">
                {weather?.stadium ? `${weather.stadium} · ` : ""}{wxText}
                {wxAdj ? <> · <b className={proj.wx! < 1 ? "under" : "over"}>{wxAdj} {KIND_SHORT[kind].toLowerCase()} for weather</b></>
                  : !weather?.indoor && !yesNo ? " · no weather adjustment" : ""}
              </p>
            )}
            <div className="pd-sticky">
              <div className={`pd-mini${stuck ? " on" : ""}`} aria-hidden={!stuck}>
                <Avatar name={data.name} src={data.headshot} size={28} />
                <b>{data.name}</b><span className="mut">{data.pos} · {data.team} {matchup(proj.opp, proj.home)}</span>
              </div>
              <MarketTabs kinds={kinds} kind={kind} hasEv={(k) => bestEv(k) > 0} onPick={setKind} />
            </div>

            <div role="tabpanel" aria-label={KIND_LABEL[kind]}>
            {!proj.thin && <Verdict kind={kind} mu={proj.mu} play={play} picked={!!picked} book={book} ctx={ctx} loading={ladder.isLoading} stale={slip.staleSince} onLeave={onClose} />}

            <div className="facts">
              {yesNo ? (
                <>
                  <div><span className="mut">{kind === "ftd" ? "Model: scores first" : isDouble(kind) ? "Model chance" : "Model: scores"}</span><b>{fmtProj(kind, proj.mu)}</b></div>
                  <div><span className="mut">Market chance</span><b>{marketYes != null ? pct(marketYes, 0) : "–"}</b></div>
                  <div><span className="mut">Edge</span><b className={marketYes == null ? "" : proj.mu > marketYes ? "pos" : "neg"}>{marketYes != null ? fmtEdge(kind, (proj.mu - marketYes) * 100) : "–"}</b></div>
                  {isDouble(kind)
                    ? <div><span className="mut">Needs</span><b>{DOUBLE_NEED[kind]} stats at 10+</b></div>
                    : <div><span className="mut">Expected TDs</span><b>{proj.eff.toFixed(2)}</b></div>}
                </>
              ) : (
                <>
                  <div><span className="mut">Projection</span><b>{fmtProj(kind, proj.mu)} {unit(kind)}</b></div>
                  <div><span className="mut">Line{book !== "all" ? ` · ${book}` : ""}</span><b>{mainLine ?? "–"}</b></div>
                  <div><span className="mut">Market median</span><b>{ladder.data?.curve ? fmtProj(kind, ladder.data.curve.median) : "–"}</b></div>
                  <div><span className="mut">Edge vs line</span>
                    <b className={mainLine == null ? "" : (proj.mu - mainLine) * (side === "Over" ? 1 : -1) > 0 ? "pos" : "neg"}>{mainLine != null ? `${fmtEdge(kind, proj.mu - mainLine)} ${unit(kind)}` : "–"}</b></div>
                </>
              )}
            </div>
            {(yesNo || proj.thin) && <p className="model-details mut">
              {isDouble(kind) ? <>Expected stats at 10+ <b>{proj.eff.toFixed(2)}</b> · Minutes <b>{proj.vol.toFixed(1)}</b></>
                : yesNo ? <>Touches <b>{proj.vol.toFixed(1)}</b> · TDs per touch <b>{(proj.eff / Math.max(proj.vol, 0.1)).toFixed(3)}</b></>
                : <>Std dev <b>{fmtSd(kind, proj.sd)}</b> · {volLabel(kind)} <b>{proj.vol.toFixed(1)}</b> · {effText(kind, proj.eff)[0]} <b>{effText(kind, proj.eff)[1]}</b></>}
            </p>}
            {!proj.thin && <WhyPanel kind={kind} proj={proj} play={play} curveWeight={boardRow && picked === play ? boardRow.w ?? ladder.data?.w : ladder.data?.w} nba={isNba(kind)} />}

            {hitLine != null && (
              <HitSplits kind={kind} logs={allLogs} side={side} line={hitLine} opp={proj.opp} home={proj.home} modelChance={play ? play.q.p_model ?? play.q.prob : null} />
            )}
            {!isNba(kind) && <VsDefense playerId={data.player_id} kind={kind} pos={proj.pos} opp={proj.opp} slot={proj.dvp_slot} />}
            <h3 className="log-h">
              Last {rate?.n ?? bars.filter((b) => !b.missed).length} games · {isDouble(kind) ? "stats in double figures" : kind === "ftd" ? "scored the first TD" : yesNo ? "touchdowns scored" : KIND_LABEL[kind]}
              {rate && rate.n > 0 && <span className="hit-sum">{playLabel(kind, side, hitLine!)} in <b>{rate.hit} of {rate.n}</b></span>}
            </h3>
            <div className="chart-key mut" aria-hidden>
              {hitLine != null && <><span><i className="k-hit" />{playLabel(kind, side, hitLine)}</span><span><i className="k-miss" />didn't hit</span></>}
              {!yesNo && <span><i className="k-proj" />proj {fmtProj(kind, proj.mu)}</span>}
              {kind === "td" && <span><i className="k-proj" />expected {proj.eff.toFixed(2)}</span>}
              {lines.length > 0 && <span><i className="k-line" />line {lines.join(" / ")}</span>}
            </div>
            <div className="chart" onClick={tapChart}>
              <ResponsiveContainer width="100%" height={220}>
                <BarChart data={bars} margin={{ top: 8, right: 8, bottom: 0, left: -16 }}>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="var(--line)" />
                  <XAxis dataKey="label" interval={0} height={bars.some((b) => b.season != null) ? 34 : 50} tickLine={false}
                    tick={(p: { x: number; y: number; payload: { value: string } }) => <LogTick {...p} bar={byLabel.get(p.payload.value)} compact={narrow() && bars.length > 10} />} />
                  <YAxis tick={{ fontSize: 11, fill: "var(--mut)" }} allowDecimals={!isCount(kind) && !yesNo} />
                  <Tooltip active={coarse() ? tip : undefined} cursor={{ fill: "var(--chip)" }}
                    formatter={(v: number) => (isDouble(kind) ? [`${v}`, "Stats at 10+"] : isNba(kind) ? [`${v}`, KIND_LABEL[kind]] : kind === "ftd" ? [v ? "Yes" : "No", "First TD"] : yesNo ? [`${v}`, "TDs"] : kind === "ptd" ? [`${v}`, "Passing TDs"] : isCount(kind) ? [`${v}`, "Receptions"] : [`${v} yds`, kind === "lng" || kind === "rlng" ? "Longest" : "Yards"])}
                    labelFormatter={(l: string) => { const b = byLabel.get(l); return b?.missed ? `${l}: no game (bye, injury or inactive)` : `${l}${b?.opp ? ` vs ${b.opp}` : ""}`; }}
                    contentStyle={{ background: "var(--card)", border: "1px solid var(--line)", color: "var(--ink)" }} />
                  {seasonStarts(bars).map((b) => (
                    <ReferenceLine key={b.label} x={b.label} position="start" stroke="var(--mut)" strokeDasharray="2 2"
                      label={{ value: String(b.season), position: "insideTopLeft", fill: "var(--mut)", fontSize: 10 }} />
                  ))}
                  <Bar dataKey="yards" radius={[3, 3, 0, 0]} isAnimationActive={false}>
                    {bars.map((b) => {
                      const hit = hitLine != null && b.yards != null && hits(b.yards, side, hitLine);
                      return <BarCell key={b.label} fill={hitLine == null ? "var(--acc)" : hit ? "var(--good)" : "var(--mut)"} fillOpacity={hitLine == null || hit ? 1 : 0.4} />;
                    })}
                  </Bar>
                  {!yesNo && <ReferenceLine y={proj.mu} stroke="var(--acc2)" strokeDasharray="5 3" />}
                  {kind === "td" && <ReferenceLine y={proj.eff} stroke="var(--acc2)" strokeDasharray="5 3" />}
                  {lines.map((l) => <ReferenceLine key={l} y={l} stroke="var(--bad)" strokeDasharray="2 3" />)}
                </BarChart>
              </ResponsiveContainer>
            </div>
            {proj.thin ? (
              <p className="thin-note mut">
                <span className="thin-pill">Thin history</span> {data.name} has only a game or two of history (a rookie, or back from IR),
                so this projection is a rough guess. It isn't priced against sportsbook lines and stays out of Best props and parlays.
              </p>
            ) : (
              <>
                <LineLadder kind={kind} mu={proj.mu} book={book} onBookChange={onBookChange} marketWeight={marketWeight} ctx={ctx} />
                <LineMovement playerId={data.player_id} kind={kind} />
              </>
            )}
            </div>
          </>
        )}
    </Sheet>
  );
}

/** An axis label under each bar: the week and the opponent (a dash for a week without a game); NBA dates as they come.
 *  Compact (a phone with a long log): the week number alone, in a smaller size, so neighbours don't run together. */
function LogTick({ x, y, bar, compact }: { x: number; y: number; bar?: LogBar; compact?: boolean }) {
  if (!bar) return null;
  if (bar.season == null) return <text x={x} y={y + 4} transform={`rotate(-35 ${x} ${y + 4})`} textAnchor="end" fontSize={10} fill="var(--mut)">{bar.tick}</text>;
  return (
    <text x={x} y={y + 10} textAnchor="middle" fontSize={compact ? 9 : 10} fill="var(--mut)">
      <tspan x={x}>{compact ? bar.tick.slice(1) : bar.tick}</tspan>
      <tspan x={x} dy={12} fill={bar.missed ? "var(--mut)" : "var(--ink)"}>{bar.missed ? "—" : bar.opp}</tspan>
    </text>
  );
}

/** The markets as tabs: arrow keys move between them, and a fade at either edge says there are more to scroll to. */
function MarketTabs({ kinds, kind, hasEv, onPick }: { kinds: Kind[]; kind: Kind; hasEv: (k: Kind) => boolean; onPick: (k: Kind) => void }) {
  const { ref, onScroll, className: fade } = useOverflowFade<HTMLDivElement>("[aria-selected=true]", [kinds, kind]);
  const keys = (e: KeyboardEvent) => {
    const step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (!step) return;
    e.preventDefault();
    const next = kinds[(kinds.indexOf(kind) + step + kinds.length) % kinds.length];
    onPick(next);
    requestAnimationFrame(() => ref.current?.querySelector<HTMLElement>("[aria-selected=true]")?.focus());
  };
  return (
    <div ref={ref} className={`seg small market-tabs${fade}`} role="tablist" aria-label="Markets" onScroll={onScroll} onKeyDown={keys}>
      {kinds.map((k) => (
        <button key={k} role="tab" aria-selected={k === kind} tabIndex={k === kind ? 0 : -1} className={k === kind ? "on" : ""} onClick={() => onPick(k)}>
          {KIND_SHORT[k]}{hasEv(k) && <i className="ev-dot" title="A +EV price is posted" aria-label="(+EV price posted)" />}
        </button>
      ))}
    </div>
  );
}

/** The drawer's answer up top: the play it was opened from (or the best-EV main line at the chosen book), its price and
 *  chances, and a button to slip it. With old odds it says the price needs checking. */
function Verdict({ kind, mu, play, picked, book, ctx, loading, stale, onLeave }: {
  kind: Kind; mu: number; play: Cell | null; picked: boolean; book: string; ctx: PlayerCtx; loading: boolean; stale: string | null;
  /** going to My bets from the Track sheet: close the drawer under it */
  onLeave: () => void;
}) {
  const slip = useBetSlip();
  const [tracking, setTracking] = useState(false);
  if (loading) return <div className="verdict"><Skeleton kind="rows" count={1} /></div>;
  const at = book !== "all" ? ` at ${book}` : "";
  if (!play) return <div className="verdict none mut">No {KIND_LABEL[kind].toLowerCase()} lines posted{at} yet. Books post props mid-week; the scheduled syncs pick them up.</div>;
  const leg = legFor(ctx, kind, play);
  const inSlip = slip.has(leg.id);
  const clash = !inSlip && slip.wouldClash(leg);
  const plus = play.q.ev > 0;
  return (
    <section className={`verdict${plus ? " plus" : ""}`} aria-label="The play">
      <div className="v-label">{picked ? "The play you opened" : plus ? `Best play${at}` : `No +EV price${at} · closest`}</div>
      <div className="v-main">
        <div className="v-play">
          <b>{playLabel(kind, play.side, play.row.line)}</b>
          {!isYesNo(kind) && <span className="mut"> {KIND_LABEL[kind].toLowerCase()}</span>}
          {play.row.alt && <span className="alt">alt</span>}
        </div>
        <div className="v-price"><span className="odds">{americanOdds(play.q.odds)}</span><BookName book={play.row.book} size={16} /></div>
      </div>
      <div className="v-stats">
        <span>Win prob <b>{pct(play.q.prob)}</b></span>
        <span>EV <b className={`ev-text ${plus ? "pos" : ""}`}>{signedPct(play.q.ev)}</b></span>
        {!isYesNo(kind) && <span>Model <b>{fmtProj(kind, mu)}</b> vs line <b>{play.row.line}</b></span>}
      </div>
      {stale && <p className="v-stale">This price is from {timeAgo(stale)}. Check it at the book before betting.</p>}
      <button type="button" className={`v-add${inSlip ? " on" : plus ? " primary" : ""}${clash ? " clash" : ""}`} onClick={() => slip.toggle(leg)}
        title={clash ? `Another ${KIND_LABEL[kind]} line is already on your slip at ${play.row.book}: books usually won't parlay both` : undefined}>
        {inSlip ? "✓ On your slip" : clash ? "Add to slip (clashes with a leg)" : "Add to slip"}
      </button>
      {/* a one-off bet placed straight from here, without building a slip */}
      <p className="v-track"><button type="button" className="link" onClick={() => setTracking(true)}>Placed it? Track this bet</button></p>
      {tracking && <TrackBetSheet book={leg.book} legs={[leg]} stake={slip.stakes[leg.book] ?? 10} onClose={() => setTracking(false)}
        onLeave={() => { setTracking(false); onLeave(); }} />}
    </section>
  );
}

/** Star a player: the compare page lists favorites to pick from. Signed out, it opens sign-in. */
function FavButton({ player }: { player: { player_id: string; name: string; pos: string; team: string; headshot?: string | null } }) {
  const auth = useAuth();
  const league = getLeague();
  const favs = useFavorites(league);
  const [problem, setProblem] = useState("");
  const on = favs.players.some((p) => p.player_id === player.player_id);
  const full = !on && favs.players.length >= MAX_FAV_PLAYERS;
  const click = () => {
    if (!auth?.user) return openSignIn();
    if (!favs.ready) return setProblem("Confirm you're 21 or older to save favorites.");
    if (full) return setProblem(`You have ${MAX_FAV_PLAYERS} favorites. Remove one on the Compare page first.`);
    setProblem("");
    toggleFavPlayer(league, favs.players, player).catch((err) => setProblem((err as Error).message));
  };
  const label = on ? `Remove ${player.name} from favorites` : `Add ${player.name} to favorites`;
  return (
    <>
      <button type="button" className={`fav-btn${on ? " on" : ""}`} onClick={click} aria-pressed={on} aria-label={label} title={problem || label} disabled={favs.loading}>
        <FavStar on={on} size={20} />
      </button>
      {problem && <small className="fav-msg" role="status">{problem}</small>}
    </>
  );
}

interface BoardRow { player_id: string; kind: string; side: string; line: number; book: string; odds: number; prob: number; ev: number; p_model?: number | null; p_mkt?: number | null; w?: number | null }
/** The board row (Best props, the games' top props, Who's Hot) this play was opened from, if a board on this page has it
 *  loaded at the same price. */
function boardRowFor(qc: QueryClient, playerId: string, kind: Kind, c: Cell): BoardRow | undefined {
  for (const q of qc.getQueryCache().getAll()) {
    const data = q.state.data;
    if (!Array.isArray(data)) continue;
    const hit = (data as Partial<BoardRow>[]).find((r) => r && r.player_id === playerId && r.kind === kind && r.side === c.side && r.line === c.row.line
      && r.book === c.row.book && r.odds === c.q.odds && typeof r.prob === "number" && typeof r.ev === "number");
    if (hit) return hit as BoardRow;
  }
  return undefined;
}
