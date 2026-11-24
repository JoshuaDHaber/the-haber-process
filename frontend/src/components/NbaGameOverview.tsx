import { IconClock } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { api, type Game, type Kind, type NbaKind } from "../api";
import type { DrawerPick } from "../playerHash";
import { KIND_SHORT, NBA_MARKET_TABS, SUB_LABEL, groupOf } from "../format";
import { useParlayBoard } from "../useParlayBoard";
import GameContext from "./GameContext";
import GamePlayerSearch from "./GamePlayerSearch";
import { GameInjuries, KeyInjuries } from "./Injuries";
import { BlowoutCard } from "./Blowout";
import { MoneylineCard } from "./Moneylines";
import ParlayTiles, { ParlayTile, topParlay } from "./ParlayTiles";
import PicksTable from "./PicksTable";
import ProjectionsTable from "./ProjectionsTable";
import { checkBack } from "../sync";
import EmptyState, { LoadError } from "./EmptyState";
import Skeleton from "./Skeleton";
import { GameHeader, GamePanel, OverviewBlock, useGameView, type GameView } from "./GameTabs";

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

// the NBA game script is a few lines (no roles table), so it sits on the Overview instead of a tab of its own
const VIEWS: readonly GameView[] = ["overview", "props", "parlays", "proj", "inj"];
const PROPS_SHOWN = 8;
const OVERVIEW_PROPS = 3;
const PROJ_SHOWN = 10;

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

/** One NBA game's page, in tabs under a sticky header: an Overview (win chances, blowout odds, the game script and rundown,
 *  the top props and parlay, key injuries), then its props, parlays, projections and both injury reports in full. */
export default function NbaGameOverview({ game, books, book, onBook, marketWeight, onBack, onSelect }: Props) {
  const id = game.game_id;
  const [view, setView] = useGameView(VIEWS);
  const [propKind, setPropKind] = useState<NbaKind | "all">("all");
  const [projKind, setProjKind] = useState<NbaKind | "all">("pts");
  const base = { side: "all", game: id, book, q: "", minEv: 0, marketWeight, flagged: false } as const;

  const moneylines = useQuery({ queryKey: ["moneylines", book, marketWeight], queryFn: () => api.moneylines(book, marketWeight) });
  const injuries = useQuery({ queryKey: ["injuries"], queryFn: api.injuries, refetchInterval: 15 * 60_000 });
  const picks = useQuery({ queryKey: ["game-picks", id, propKind, book, marketWeight], queryFn: () => api.picks({ ...base, kind: propKind }, 300), placeholderData: (p) => p });
  // every priced play for the game, for the Overview and the rundown (the same cache entry as the props table on "All")
  const allPicks = useQuery({ queryKey: ["game-picks", id, "all", book, marketWeight], queryFn: () => api.picks({ ...base, kind: "all" }, 300), enabled: propKind !== "all" });
  const every = propKind === "all" ? picks : allPicks;
  const projections = useQuery({ queryKey: ["game-projections", id, projKind], queryFn: () => api.projections({ ...base, kind: projKind }), placeholderData: (p) => p });
  // every stat for the game, for the player search (same cache entry as the projections table on "All")
  const roster = useQuery({ queryKey: ["game-projections", id, "all"], queryFn: () => api.projections({ ...base, kind: "all" }) });

  const chosen = useMemo(() => [id], [id]);
  // parlays need one book; with "all books" they fall back to the board's own default
  const board = useParlayBoard(books, chosen, true, book === "all" ? undefined : book);
  const parlayCount = board.parlays.length + board.ladders.length + board.starterParlays.length;
  const top = topParlay([board.parlays, board.ladders, board.starterParlays]);
  const ml = moneylines.data?.find((g) => g.game_id === id);
  const atBook = book !== "all" ? ` at ${book}` : "";
  const pickRow = (p: { player_id: string; kind: Kind; side: "Over" | "Under"; line: number; book: string }) => onSelect(p.player_id, p.kind, { side: p.side, line: p.line, book: p.book });
  const noProps = { title: "No positive-EV props for this game yet", body: "Books post NBA props the day of the game; the 10 AM, 2 PM and 6 PM ET syncs pick them up." };

  return (
    <div className="stack go">
      <GameHeader game={game} books={books} book={book} onBook={onBook} onBack={onBack} views={VIEWS} view={view} onView={setView}
        counts={{ props: every.data?.length, parlays: board.loading ? undefined : parlayCount }} />

      <GamePanel view={view}>
      {view === "overview" && <>
        <GamePlayerSearch rows={roster.data ?? []} onSelect={onSelect} />
        {ml ? (ml.blowout ? <div className="go-top"><MoneylineCard g={ml} records={game.records} /><BlowoutCard g={ml} /></div> : <MoneylineCard g={ml} records={game.records} />)
          : moneylines.isLoading ? <Skeleton kind="card" label="Loading win chances…" /> : moneylines.error && !moneylines.data ? <LoadError compact what="win chances" retry={moneylines.refetch} /> : <EmptyState icon={IconClock} compact title="No win chances for this game yet">{checkBack()}</EmptyState>}

        <GameContext gameId={id} league="nba" onSelect={onSelect} ml={ml} picks={every.data} />

        <OverviewBlock title="Top props" sub={`best EV for this game${atBook}`} more="All props" onMore={() => setView("props")}>
          {every.isLoading ? <Skeleton kind="rows" count={OVERVIEW_PROPS} label="Loading this game's props…" /> : every.error && !every.data ? <LoadError compact what="this game's props" retry={every.refetch} /> :
            <PicksTable picks={every.data ?? []} busy={every.isPlaceholderData} limit={OVERVIEW_PROPS} onMore={() => setView("props")} empty={noProps} onSelect={pickRow} />}
        </OverviewBlock>

        <div className="go-ov-pair">
        <OverviewBlock title="Top parlay" sub={board.book ? `from this game, at ${board.book}` : "from this game"} more={parlayCount > 1 ? `All ${parlayCount} parlays` : "Parlays"} onMore={() => setView("parlays")}>
          {board.loading ? <Skeleton kind="tiles" count={1} label="Building this game's parlays…" /> : board.failed ? <LoadError compact what="parlays" retry={board.retry} />
            : top ? <ParlayTile p={top} onSelect={onSelect} />
            : board.laddersLoading || board.startersLoading ? <Skeleton kind="tiles" count={1} label="Loading alternate lines…" />
            : <EmptyState compact title={`No parlays worth a look${board.book ? ` at ${board.book}` : ""} yet`}>{parlayCount ? "The ones built from this game all expect to lose money; they're on the Parlays tab." : "Books post NBA props and alternate lines the day of the game."}</EmptyState>}
        </OverviewBlock>

        <OverviewBlock title="Key injuries" sub="players with props who are out or in doubt" more="Full report" onMore={() => setView("inj")}>
          {injuries.isLoading ? <Skeleton kind="cards" count={1} label="Loading the injury report…" /> : injuries.error || !injuries.data ? <LoadError compact what="the injury report" retry={injuries.refetch} /> :
            <KeyInjuries game={game} report={injuries.data} onSelect={onSelect} />}
        </OverviewBlock>
        </div>
      </>}

      {view === "props" && (
        <section className="go-sec">
          <h2 className="parlay-h">Best props <small>positive EV for this game{atBook}</small></h2>
          <KindSeg value={propKind} onChange={setPropKind} />
          {picks.isLoading ? <Skeleton kind="rows" /> : picks.error && !picks.data ? <LoadError compact what="this game's props" retry={picks.refetch} /> :
            <PicksTable key={propKind} picks={picks.data ?? []} busy={picks.isPlaceholderData} limit={PROPS_SHOWN} empty={noProps} onSelect={pickRow} />}
        </section>
      )}

      {view === "parlays" && (
        <section className="go-sec">
          <h2 className="parlay-h">Parlays <small>built from this game only</small></h2>
          {board.loading ? <Skeleton kind="cards" /> : board.failed ? <LoadError compact what="parlays" retry={board.retry} /> :
            <ParlayTiles book={board.book} games={[game]} selectedGames={chosen} parlays={board.parlays} quietEmpty
              tdParlays={board.tdParlays} ladders={board.ladders} altProps={board.altProps} laddersLoading={board.laddersLoading} starterParlays={board.starterParlays} startersLoading={board.startersLoading} onSelect={onSelect} />}
        </section>
      )}

      {view === "proj" && (
        <section className="go-sec">
          <h2 className="parlay-h">Projections</h2>
          <KindSeg value={projKind} onChange={setProjKind} />
          {projections.isLoading ? <Skeleton kind="rows" /> : projections.error && !projections.data ? <LoadError compact what="this game's projections" retry={projections.refetch} /> :
            <ProjectionsTable key={projKind} nba rows={projections.data ?? []} busy={projections.isPlaceholderData} limit={PROJ_SHOWN} onSelect={(p) => onSelect(p.player_id, p.kind)} />}
        </section>
      )}

      {view === "inj" && (
        <section className="go-sec">
          <h2 className="parlay-h">Injuries <small>from ESPN; Out players aren't projected</small></h2>
          {injuries.isLoading ? <Skeleton kind="cards" count={2} /> : injuries.error || !injuries.data ? <LoadError compact what="the injury report" retry={injuries.refetch} /> :
            <GameInjuries game={game} report={injuries.data} onSelect={onSelect} />}
        </section>
      )}
      </GamePanel>
    </div>
  );
}
