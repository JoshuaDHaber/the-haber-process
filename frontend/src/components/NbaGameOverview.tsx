import { IconClock } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import BookSelect from "./BookSelect";
import { useMemo, useState } from "react";
import { api, type Game, type Kind, type NbaKind } from "../api";
import type { DrawerPick } from "../playerHash";
import { KIND_SHORT, NBA_MARKET_TABS, SUB_LABEL, groupOf, scrollBehavior } from "../format";
import { useParlayBoard } from "../useParlayBoard";
import GamePlayerSearch from "./GamePlayerSearch";
import { GameInjuries } from "./Injuries";
import { BlowoutCard } from "./Blowout";
import { MoneylineCard } from "./Moneylines";
import ParlayTiles from "./ParlayTiles";
import PicksTable from "./PicksTable";
import ProjectionsTable from "./ProjectionsTable";
import { checkBack } from "../sync";
import EmptyState, { LoadError } from "./EmptyState";
import Skeleton from "./Skeleton";

interface Props {
  game: Game;
  books: string[];
  /** The page's sportsbook ("all" = best price anywhere): props and the moneyline follow it. */
  book: string;
  onBook: (book: string) => void;
  marketWeight: number | null;
  onBack: () => void;
  /** A player picked; `pick` is the play when a priced row was clicked, so the drawer can lead with it. */
  onSelect: (id: string, kind: Kind, pick?: DrawerPick) => void;
}

const SECTIONS: [string, string][] = [["go-props", "Best props"], ["go-parlays", "Parlays"], ["go-proj", "Projections"], ["go-inj", "Injuries"]];
const PROPS_SHOWN = 8;

/** Stat picker; the combo markets fold into one choice, with a pill per combo below. */
function KindSeg({ value, onChange }: { value: NbaKind | "all"; onChange: (k: NbaKind | "all") => void }) {
  const group = groupOf(value);
  const pick = (k: Kind | "all") => onChange(k as NbaKind | "all");
  return (
    <>
      <div className="seg small" role="group" aria-label="Stat">
        {NBA_MARKET_TABS.map((k) => typeof k === "object"
          ? <button key={k.label} className={group === k ? "on" : ""} aria-pressed={group === k} onClick={() => group !== k && pick(k.kinds[0])}>{k.label}</button>
          : <button key={k} className={value === k ? "on" : ""} aria-pressed={value === k} onClick={() => pick(k)}>{k === "all" ? "All" : KIND_SHORT[k]}</button>)}
      </div>
      {group && (
        <div className="seg small sub" role="group" aria-label={`${group.label} market`}>
          {group.kinds.map((k) => <button key={k} className={value === k ? "on" : ""} aria-pressed={value === k} onClick={() => pick(k)}>{SUB_LABEL[k] ?? KIND_SHORT[k]}</button>)}
        </div>
      )}
    </>
  );
}

/** One NBA game on a page: win chances and moneylines, its best props, projections and both teams' injuries. */
export default function NbaGameOverview({ game, books, book, onBook, marketWeight, onBack, onSelect }: Props) {
  const id = game.game_id;
  const [propKind, setPropKind] = useState<NbaKind | "all">("all");
  const [projKind, setProjKind] = useState<NbaKind | "all">("pts");
  const base = { side: "all", game: id, book, q: "", minEv: 0, marketWeight, flagged: false } as const;

  const moneylines = useQuery({ queryKey: ["moneylines", book, marketWeight], queryFn: () => api.moneylines(book, marketWeight) });
  const injuries = useQuery({ queryKey: ["injuries"], queryFn: api.injuries, refetchInterval: 15 * 60_000 });
  const picks = useQuery({ queryKey: ["game-picks", id, propKind, book, marketWeight], queryFn: () => api.picks({ ...base, kind: propKind }, 300), placeholderData: (p) => p });
  const projections = useQuery({ queryKey: ["game-projections", id, projKind], queryFn: () => api.projections({ ...base, kind: projKind }), placeholderData: (p) => p });
  // every stat for the game, for the player search (same cache entry as the projections table on "All")
  const roster = useQuery({ queryKey: ["game-projections", id, "all"], queryFn: () => api.projections({ ...base, kind: "all" }) });

  const chosen = useMemo(() => [id], [id]);
  // parlays need one book; with "all books" they fall back to the board's own default
  const board = useParlayBoard(books, chosen, true, book === "all" ? undefined : book);
  const ml = moneylines.data?.find((g) => g.game_id === id);
  const jump = (el: string) => document.getElementById(el)?.scrollIntoView({ behavior: scrollBehavior(), block: "start" });

  return (
    <div className="stack go">
      <div className="go-head">
        <button className="link go-back" onClick={onBack}>← All games</button>
        <BookSelect books={books} book={book} onChange={onBook} allLabel="All books (best price)" ariaLabel="Sportsbook for this page" />
        <nav className="go-jump" aria-label="Jump to section">
          {SECTIONS.map(([el, label]) => <button key={el} onClick={() => jump(el)}>{label}</button>)}
        </nav>
      </div>

      <GamePlayerSearch rows={roster.data ?? []} onSelect={onSelect} />

      {ml ? (ml.blowout ? <div className="go-top"><MoneylineCard g={ml} records={game.records} /><BlowoutCard g={ml} /></div> : <MoneylineCard g={ml} records={game.records} />)
        : moneylines.isLoading ? <Skeleton kind="card" label="Loading win chances…" /> : moneylines.error && !moneylines.data ? <LoadError compact what="win chances" retry={moneylines.refetch} /> : <EmptyState icon={IconClock} compact title="No win chances for this game yet">{checkBack()}</EmptyState>}

      <section id="go-props" className="go-sec">
        <h2 className="parlay-h">Best props <small>positive EV for this game{book !== "all" ? ` at ${book}` : ""}</small></h2>
        <KindSeg value={propKind} onChange={setPropKind} />
        {picks.isLoading ? <Skeleton kind="rows" /> : picks.error && !picks.data ? <LoadError compact what="this game's props" retry={picks.refetch} /> : <>
          <PicksTable key={propKind} picks={picks.data ?? []} busy={picks.isPlaceholderData} limit={PROPS_SHOWN} empty={{ title: "No positive-EV props for this game yet", body: "Books post NBA props the day of the game; the 10 AM, 2 PM and 6 PM ET syncs pick them up." }}
            onSelect={(p) => onSelect(p.player_id, p.kind, { side: p.side, line: p.line, book: p.book })} />
        </>}
      </section>

      <section id="go-parlays" className="go-sec">
        <h2 className="parlay-h">Parlays <small>built from this game only</small></h2>
        {board.loading ? <Skeleton kind="cards" /> : board.failed ? <LoadError compact what="parlays" retry={board.retry} /> :
          <ParlayTiles book={board.book} games={[game]} selectedGames={chosen} parlays={board.parlays}
            tdParlays={board.tdParlays} ladders={board.ladders} altProps={board.altProps} laddersLoading={board.laddersLoading} starterParlays={board.starterParlays} startersLoading={board.startersLoading} onSelect={onSelect} />}
      </section>

      <section id="go-proj" className="go-sec">
        <h2 className="parlay-h">Projections</h2>
        <KindSeg value={projKind} onChange={setProjKind} />
        {projections.isLoading ? <Skeleton kind="rows" /> : projections.error && !projections.data ? <LoadError compact what="this game's projections" retry={projections.refetch} /> :
          <ProjectionsTable nba rows={projections.data ?? []} busy={projections.isPlaceholderData} onSelect={(p) => onSelect(p.player_id, p.kind)} />}
      </section>

      <section id="go-inj" className="go-sec">
        <h2 className="parlay-h">Injuries <small>from ESPN; Out players aren't projected</small></h2>
        {injuries.isLoading ? <Skeleton kind="cards" count={2} /> : injuries.error || !injuries.data ? <LoadError compact what="the injury report" retry={injuries.refetch} /> :
          <GameInjuries game={game} report={injuries.data} onSelect={onSelect} />}
      </section>
    </div>
  );
}
