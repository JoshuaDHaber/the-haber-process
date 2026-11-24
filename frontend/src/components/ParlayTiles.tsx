import { useState } from "react";
import type { Game, Kind } from "../api";
import { useBetSlip } from "../BetSlipContext";
import { americanOdds, namesItself, pct, scrollBehavior, signedPct, KIND_LABEL } from "../format";
import { getLeague } from "../league";
import { pickToLeg, type Parlay, type ParlayLeg } from "../parlays";
import type { DrawerPick } from "../playerHash";
import { toAmerican } from "../slip";
import Avatar from "./Avatar";
import GamePills from "./GamePills";
import { Star } from "./icons";
import Info from "./Info";
import SidePill from "./SidePill";
import { PickAvatar } from "./TeamLogo";
import EmptyState from "./EmptyState";
import Skeleton from "./Skeleton";

const STAKE = 10;
/** Whether the comparison table was left open, remembered per browser. */
const COMPARE_KEY = "parlayCompareOpen";
/** A parlay EV above this is mostly small leg edges compounding, so the tile says to read it as an upper bound. */
const BIG_EV = 0.3;

type OnSelect = (id: string, kind: Kind, pick?: DrawerPick) => void;

/** The odds that break even on the hit chance: what a same-game parlay has to pay to be worth it. */
const fairOdds = (p: Parlay) => toAmerican(1 / Math.max(p.prob, 1e-6));
/** "about 1 in 3" */
const oneIn = (prob: number) => `about 1 in ${Math.max(1, Math.round(1 / Math.max(prob, 1e-6)))}`;
/** A tile the model expects to lose money on (a same-game parlay is priced by the book, so it has no EV of its own here). */
const losing = (p: Parlay) => !p.sameGame && p.ev < 0;
const tileId = (p: Parlay) => `parlay-${p.key}`;
const scrollTo = (id: string) => document.getElementById(id)?.scrollIntoView({ behavior: scrollBehavior(), block: "start" });

/** A player's name in a leg row: opens the player drawer on that exact play, when the page can open one. */
function LegName({ l, onSelect }: { l: ParlayLeg; onSelect?: OnSelect }) {
  if (!onSelect || l.kind === "ml") return <b>{l.name}</b>;   // a team has no player drawer
  return <button type="button" className="link leg-name" onClick={() => onSelect(l.player_id, l.kind, { side: l.side, line: l.line, book: l.book })}
    title={`Open ${l.name}: why this play`}>{l.name}</button>;
}

/** Add the whole parlay to the slip, or take it off again. With other legs already on the slip it says they'd be combined,
 *  and offers to start a fresh slip with just these (the slip can undo that). */
function SlipButton({ p, quiet }: { p: Parlay; quiet: boolean }) {
  const slip = useBetSlip();
  const legs = p.legs.map(pickToLeg);
  const ids = new Set(legs.map((l) => l.id));
  const missing = legs.filter((l) => !slip.has(l.id));
  const others = slip.legs.filter((l) => !ids.has(l.id)).length;
  const clashes = missing.filter((l) => slip.wouldClash(l)).length;
  const add = () => { missing.forEach(slip.toggle); slip.setOpen(true); };
  const fresh = () => { slip.remove(slip.legs.filter((l) => !ids.has(l.id)).map((l) => l.id)); missing.forEach(slip.toggle); slip.setOpen(true); };
  if (!missing.length) return (
    <div className="parlay-act">
      <button type="button" className="parlay-off" onClick={() => slip.remove([...ids])}>✓ On your slip · Remove</button>
    </div>
  );
  return (
    <div className="parlay-act">
      <button type="button" className={quiet ? "parlay-off" : "primary"} onClick={add}>
        {others ? `Add ${missing.length} to your ${others}-leg slip` : quiet ? "Add anyway" : "Add all to slip"}
      </button>
      {others > 0 && <button type="button" className="link" onClick={fresh}>Start a new slip with {missing.length === legs.length ? "these" : "just these"}</button>}
      {clashes > 0 && <p className="parlay-clash">{clashes === 1 ? "1 leg clashes" : `${clashes} legs clash`} with your slip at {p.book}: books won't take another line of the same prop.</p>}
    </div>
  );
}

function ParlayCard({ p, lead, onSelect }: { p: Parlay; lead: boolean; onSelect?: OnSelect }) {
  const lose = losing(p);
  const big = !p.sameGame && p.ev > BIG_EV;
  const mixedAlt = p.legs.some((l) => l.alt) && p.legs.some((l) => !l.alt);
  return (
    <article id={tileId(p)} className={`parlay${lead ? " lead" : ""}${lose ? " losing" : ""}`}>
      <header>
        <div>
          <span className="tile-label">{lead && <Star />}{p.title}</span>
          <p className="parlay-sub mut">{p.blurb}</p>
        </div>
        <span className="parlay-meta mut">
          {p.sameGame && <span className="sg-tag">Same game <Info term="sameGame" /></span>}
          {p.legs.length} legs
        </span>
      </header>
      {p.sameGame ? (
        // the book prices a same-game parlay its own way, usually below the legs multiplied: what matters is the price that breaks even
        <div className="parlay-odds">{americanOdds(fairOdds(p))}<small>or better is worth it. Check {p.book}'s same-game price.</small></div>
      ) : (
        <div className="parlay-odds">{americanOdds(p.american)}<small>combined odds</small></div>
      )}
      <dl className="parlay-stats">
        <div><dt>Hit chance<Info term="hit" /></dt><dd>{pct(p.prob, 0)}<small>{oneIn(p.prob)}</small></dd></div>
        {p.sameGame
          ? <div><dt>Legs multiplied</dt><dd>{americanOdds(p.american)}<small>not what the book pays</small></dd></div>
          : <div><dt>Model EV<Info term="modelEv" /></dt><dd className={p.ev >= 0 ? "pos" : "under"}>{signedPct(p.ev)}</dd></div>}
        <div><dt>${STAKE} pays</dt><dd>${(STAKE * (p.sameGame ? 1 / Math.max(p.prob, 1e-6) : p.decimal)).toFixed(2)}{p.sameGame && <small>at {americanOdds(fairOdds(p))}</small>}</dd></div>
      </dl>
      {lose && <p className="parlay-flag neg">The model expects this to lose money ({signedPct(p.ev)}). It's here for fun, not value.</p>}
      {big && <p className="parlay-flag">Each leg's edge multiplies into a big EV, and so does any miss in the model. Read it as an upper bound.</p>}
      <ul className="parlay-legs">
        {p.legs.map((l) => (
          <li key={`${l.player_id}-${l.kind}-${l.side}-${l.line}`}>
            <PickAvatar name={l.name} kind={l.kind} team={l.team} gameId={l.game_id} />
            <span className="leg-main"><LegName l={l} onSelect={onSelect} /> <SidePill kind={l.kind} side={l.side} line={l.line} />
              {mixedAlt && l.alt && <span className="alt">alt</span>}
              <small>{namesItself(l.kind) ? "" : `${KIND_LABEL[l.kind]} · `}{pct(l.prob, 0)} · {l.team} {l.home ? "vs" : "@"} {l.opp}</small></span>
            <span className="leg-odds">{americanOdds(l.odds)}</span>
          </li>
        ))}
      </ul>
      <SlipButton p={p} quiet={lose} />
    </article>
  );
}

/** The likeliest single alternate-line overs, each with its main line, EV and a button to put just that prop on the slip. */
function AltProps({ props, onSelect }: { props: ParlayLeg[]; onSelect?: OnSelect }) {
  const slip = useBetSlip();
  return (
    <section className="card altprops" aria-label="Likely alternate props">
      <p className="mut altprops-note">Lower lines than the book's main one, each at least 65% likely. Short prices pay little alone; use them to build your own parlay.</p>
      <ul className="parlay-legs">
        {props.map((l) => {
          const leg = pickToLeg(l);
          const on = slip.has(leg.id);
          const clash = !on && slip.wouldClash(leg);
          return (
            <li key={leg.id}>
              <Avatar name={l.name} size={26} />
              <span className="leg-main"><LegName l={l} onSelect={onSelect} /> <SidePill kind={l.kind} side={l.side} line={l.line} />
                <small>{KIND_LABEL[l.kind]}{l.mainLine != null && l.mainLine !== l.line ? ` · main line ${l.mainLine}` : ""} · {l.team} {l.home ? "vs" : "@"} {l.opp}</small></span>
              <span className="leg-odds">{americanOdds(l.odds)}<small>{pct(l.prob, 0)} · <span className={l.ev > 0 ? "pos ev-text" : "ev-text"}>{signedPct(l.ev)} EV</span></small></span>
              <button className={`add ${on ? "on" : ""}${clash ? " clash" : ""}`} onClick={() => slip.toggle(leg)} aria-pressed={on}
                title={clash ? `Another line of this prop is already on your slip at ${leg.book}` : undefined}
                aria-label={on ? "Remove from slip" : clash ? `Add to slip. Another line of this prop is already on your slip at ${leg.book}` : "Add to slip"}>{on ? "✓" : "+"}</button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/** Ladder tiles split into rows by type (mixed markets, threes only, both teams), in the order they came. */
function ladderRows(ladders: Parlay[], nba: boolean): { key: string; label: string; tiles: Parlay[] }[] {
  const type = (k: string) => (k.startsWith("threes-") ? "threes" : k.startsWith("both-") ? "both" : "mixed");
  const labels: Record<string, string> = { mixed: nba ? "Mixed markets" : "Rushing and receiving", threes: "Threes only", both: "Both teams" };
  const rows = new Map<string, Parlay[]>();
  for (const p of ladders) rows.set(type(p.key), [...(rows.get(type(p.key)) ?? []), p]);
  return [...rows].map(([key, tiles]) => ({ key, label: labels[key], tiles }));
}

/** The first tile of a section worth a star: one the model doesn't expect to lose on. */
const leadOf = (ps: Parlay[]) => ps.find((p) => !losing(p))?.key;

/** A game Overview's one parlay: the first tile worth a star, taking likely plays first, then touchdowns, ladders and starters. */
export function topParlay(sections: Parlay[][]): Parlay | null {
  for (const ps of sections) { const k = leadOf(ps); if (k) return ps.find((p) => p.key === k)!; }
  return null;
}

/** One parlay tile on its own (a game's Overview), sized as it is in a row. */
export function ParlayTile({ p, onSelect }: { p: Parlay; onSelect?: OnSelect }) {
  return <div className="parlay-row single"><ParlayCard p={p} lead onSelect={onSelect} /></div>;
}

/** "a, b and c" */
const listOf = (xs: string[]) => xs.length < 2 ? xs.join("") : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`;

/** Every tile on one screen to compare: price, hit chance and EV, each row jumping to its tile. Folded to one line until opened. */
function Compare({ sections }: { sections: { id: string; label: string; tiles: Parlay[] }[] }) {
  // folded by default so the tiles lead the page; opening it is remembered in this browser
  const [open, setOpen] = useState(() => { try { return localStorage.getItem(COMPARE_KEY) === "1"; } catch { return false; } });
  const toggle = (o: boolean) => { setOpen(o); try { localStorage.setItem(COMPARE_KEY, o ? "1" : "0"); } catch { /* private mode: not remembered */ } };
  const shown = sections.filter((s) => s.tiles.length);
  const tiles = shown.flatMap((s) => s.tiles);
  if (tiles.length < 3) return null;
  const bestEv = tiles.filter((p) => !p.sameGame).reduce<number | null>((b, p) => (b == null || p.ev > b ? p.ev : b), null);
  const likeliest = Math.max(...tiles.map((p) => p.prob));
  return (
    <details className="card parlay-compare" open={open} onToggle={(e) => toggle(e.currentTarget.open)}>
      <summary>
        <b>Compare all {tiles.length} parlays</b>
        <span className="mut">likeliest hits {pct(likeliest, 0)}{bestEv != null && <> · best EV <span className={`ev-text ${bestEv >= 0 ? "pos" : "under"}`}>{signedPct(bestEv)}</span></>}</span>
      </summary>
      <table>
        <thead><tr><th>Parlay</th><th>Odds</th><th>Hits</th><th>EV</th></tr></thead>
        {shown.map((s) => (
          <tbody key={s.id}>
            <tr className="pc-sec"><th colSpan={4} scope="colgroup"><button type="button" className="link" onClick={() => scrollTo(s.id)}>{s.label}</button></th></tr>
            {s.tiles.map((p) => (
              <tr key={p.key} className={losing(p) ? "losing" : ""} onClick={() => scrollTo(tileId(p))}>
                <td><button type="button" className="link" onClick={(e) => { e.stopPropagation(); scrollTo(tileId(p)); }}>{p.title}</button>
                  <small className="mut pc-meta">{p.legs.length} legs{p.sameGame ? " · same game" : ""}</small></td>
                <td>{p.sameGame ? <>{americanOdds(fairOdds(p))}<small className="mut pc-meta">fair</small></> : americanOdds(p.american)}</td>
                <td>{pct(p.prob, 0)}</td>
                <td>{p.sameGame ? <span className="mut" title="Books price same-game parlays their own way">n/a</span> : <span className={`ev-text ${p.ev >= 0 ? "pos" : "under"}`}>{signedPct(p.ev)}</span>}</td>
              </tr>
            ))}
          </tbody>
        ))}
      </table>
    </details>
  );
}

interface Props {
  book: string | null;
  games: Game[]; selectedGames: string[];
  /** Omit to hide the game picker (a single game's page builds from that game only). */
  onGames?: (ids: string[]) => void;
  parlays: Parlay[]; tdParlays: Parlay[]; ladders: Parlay[]; altProps: ParlayLeg[]; laddersLoading: boolean;
  /** Longer-priced parlays of the biggest roles' overs (parlays.ts buildStarterParlays), and whether their ladders are still loading. */
  starterParlays: Parlay[]; startersLoading: boolean;
  /** Likely-play parlays per book, and a way to switch book, so an empty section can point at a book that has some. */
  parlayCounts?: Record<string, number>;
  onBook?: (b: string) => void;
  /** Open a leg's player drawer on that play. */
  onSelect?: OnSelect;
  /** A game page: sections with nothing to show fold into one line at the end instead of an empty state each. */
  quietEmpty?: boolean;
}

/** "Try DraftKings (3 parlays)": the book with the most likely-play parlays, as a button, when it isn't the one on screen. */
function TryBook({ book, counts, onBook }: { book: string | null; counts?: Record<string, number>; onBook?: (b: string) => void }) {
  const best = Object.entries(counts ?? {}).filter(([b, n]) => b !== book && n > 0).sort((a, b) => b[1] - a[1])[0];
  if (!best || !onBook) return null;
  return <button type="button" className="chip-btn" onClick={() => onBook(best[0])}>Try {best[0]} ({best[1]} parlay{best[1] === 1 ? "" : "s"})</button>;
}

/** A section's tiles in one sideways row, so a section is one screen tall on a phone and never leaves a lone tile on a desktop. */
const Row = ({ tiles, onSelect, label }: { tiles: Parlay[]; onSelect?: OnSelect; label: string }) => {
  const lead = leadOf(tiles);
  return <div className="parlay-row" role="region" aria-label={label}>{tiles.map((p) => <ParlayCard key={p.key} p={p} lead={p.key === lead} onSelect={onSelect} />)}</div>;
};

/** Recommended parlays at one sportsbook: tiles of high-probability plays, then ladder-style tiles, each with the combined odds. */
export default function ParlayTiles({ book, games, selectedGames, onGames, parlays, tdParlays, ladders, altProps, laddersLoading, starterParlays, startersLoading, parlayCounts, onBook, onSelect, quietEmpty = false }: Props) {
  const nba = getLeague() === "nba";
  const all = [...parlays, ...tdParlays, ...ladders, ...starterParlays];
  const likeliest = all.reduce<Parlay | null>((b, p) => (!b || p.prob > b.prob ? p : b), null);
  const rows = ladderRows(ladders, nba);
  const empties = quietEmpty ? ([
    !parlays.length && "likely plays", !nba && !tdParlays.length && "touchdown parlays",
    !ladders.length && !laddersLoading && "ladders", !starterParlays.length && !startersLoading && "starter parlays",
  ].filter(Boolean) as string[]) : [];
  const shows = (label: string) => !empties.includes(label);
  const at = book ?? "this sportsbook";
  if (quietEmpty && !all.length && !laddersLoading && !startersLoading && !altProps.length) return (
    <EmptyState compact title={`No parlays at ${at} for this game yet`}>
      Likely plays need three high-probability props from different players, and ladders and starters need alternate lines {at} links to.
      Books post more as kickoff nears{book ? ", and another book may have some" : ""}.
    </EmptyState>
  );
  return (
    <>
      {onGames && <GamePills games={games} selected={selectedGames} onChange={onGames} multi label="Games to build from" />}
      {book && all.length > 0 && (
        <p className="mut parlay-book">
          Every leg is priced at {book}, so the odds are what you'd get there.
          {onGames && selectedGames.length > 0 && <> Built from <b>{selectedGames.length} of {games.length}</b> games.</>}
        </p>
      )}
      {likeliest && <p className="parlay-warn">Parlays multiply misses: the likeliest one here hits {oneIn(likeliest.prob)}. Expect most to lose.</p>}

      <Compare sections={[
        { id: "parlays-likely", label: "Likely plays", tiles: parlays },
        { id: "parlays-td", label: "Touchdowns", tiles: nba ? [] : tdParlays },
        { id: "parlays-ladders", label: "Ladders", tiles: ladders },
        { id: "parlays-starters", label: "Starters", tiles: starterParlays },
      ]} />

      {shows("likely plays") && <>
      <h2 className="parlay-h" id="parlays-likely">Likely plays <Info term="likely" /></h2>
      {parlays.length
        ? <Row tiles={parlays} onSelect={onSelect} label="Recommended parlays" />
        : <EmptyState compact title={`Not enough likely plays at ${book ?? "one sportsbook"} to build one`} action={<TryBook book={book} counts={parlayCounts} onBook={onBook} />}>
            These need at least three high-probability plays from different players{selectedGames.length === 1 ? " in this game" : selectedGames.length ? " in the selected games" : ""}.
          </EmptyState>}
      </>}

      {!nba && shows("touchdown parlays") && <>
      <h2 className="parlay-h" id="parlays-td">Touchdowns <Info term="tdParlay" /></h2>
      {tdParlays.length
        ? <Row tiles={tdParlays} onSelect={onSelect} label="Touchdown parlays" />
        : <EmptyState compact title={`No anytime-TD prices at ${book ?? "this sportsbook"} to parlay${selectedGames.length === 1 ? " for this game" : ""} yet`}>
            Books usually post them a day or two before kickoff.
          </EmptyState>}
      </>}

      {shows("ladders") && <>
      <h2 className="parlay-h" id="parlays-ladders">Ladders <Info term="ladder" /></h2>
      {ladders.length
        ? rows.map((row) => (
          <section key={row.key} aria-label={`${row.label} ladders`}>
            {rows.length > 1 && <h3 className="ladder-row-h">{row.label} <small>{row.tiles.length}</small></h3>}
            <Row tiles={row.tiles} onSelect={onSelect} label={`${row.label} ladders`} />
          </section>))
        : laddersLoading ? <Skeleton kind="tiles" label="Loading alternate lines…" />
        : <EmptyState compact title={`No ladders at ${book ?? "this sportsbook"} between +100 and +300`}>
            Ladders need alternate lines with betslip links. {nba ? "NBA alternate lines come with the day-of props syncs, and not" : "Not"} every book links them, so another book may have some.
          </EmptyState>}
      </>}

      {shows("starter parlays") && <>
      <h2 className="parlay-h" id="parlays-starters">Starters <Info term="starters" /></h2>
      {starterParlays.length
        ? <Row tiles={starterParlays} onSelect={onSelect} label="Starter parlays" />
        : startersLoading ? <Skeleton kind="tiles" label="Loading alternate lines…" />
        : <EmptyState compact title={`Not enough starters' lines at ${book ?? "this sportsbook"} to build one${selectedGames.length === 1 ? " for this game" : ""}`}>
            These need at least four players with an over priced -200 or longer that {book ?? "the book"} links to. {selectedGames.length ? "Adding games, or another book, may" : "Another book may"} turn some up.
          </EmptyState>}
      </>}

      {empties.length > 0 && (
        <p className="mut board-note">No {listOf(empties)} at {at} for this game yet. Books add props and alternate lines as kickoff nears{book ? ", and another book may have some" : ""}.</p>
      )}

      {altProps.length > 0 && <>
        <h2 className="parlay-h" id="parlays-alt">Likely alternate props <Info term="altProps" /></h2>
        <AltProps props={altProps} onSelect={onSelect} />
      </>}

      <p className="td-note">Hit chance multiplies each leg's win probability (the model blended with the book's price){nba ? "" : ", except legs in the same game, which are priced with how they moved together in past games"}. Models miss context and parlays compound that. Not betting advice.</p>
    </>
  );
}
