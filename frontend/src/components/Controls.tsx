import type { Filters, Game, Kind } from "../api";
import { IconAdjustmentsHorizontal, IconX } from "@tabler/icons-react";
import { isDouble, isYesNo, kickoff, playLabel, KIND_LABEL, MARKET_TABS, NBA_MARKET_TABS, pct, signedPct } from "../format";
import { getLeague } from "../league";
import { DEFAULT_FILTERS } from "../route";
import { MarketIcon } from "./icons";
import Info from "./Info";
import Menu, { MenuItem } from "./Menu";

interface Props {
  filters: Filters;
  onChange: (f: Filters) => void;
  games: Game[];
  showPickControls: boolean;
  /** The market menu's "every market" entry; by default "All markets" on Best props, "All projections" elsewhere. */
  allMarkets?: string;
}

/** The market's name in the bar and its chip: "All markets", "Receiving yds". */
const marketName = (k: Kind | "all", picks: boolean, all?: string) => (k === "all" ? all ?? (picks ? "All markets" : "All projections") : KIND_LABEL[k]);

/** Best props and Projections filters in one bar (market, side, game, search, and the finer settings behind Filters), with
 *  whatever differs from the defaults repeated below as chips that clear it. */
export default function Controls({ filters, onChange, games, showPickControls: picks, allMarkets }: Props) {
  const set = (patch: Partial<Filters>) => onChange({ ...filters, ...patch });
  const nba = getLeague() === "nba";
  const sideName = (s: Filters["side"]) => (s === "all" ? "Overs and unders" : isYesNo(filters.kind) ? playLabel(filters.kind as Kind, s, 0.5) : `${s}s`);
  const game = games.find((g) => g.game_id === filters.game);
  const finer = picks ? [filters.minEv !== DEFAULT_FILTERS.minEv, filters.marketWeight !== DEFAULT_FILTERS.marketWeight, filters.flagged].filter(Boolean).length : 0;
  const chips: [string, Partial<Filters>][] = [
    ...(filters.kind !== "all" ? [[marketName(filters.kind, picks, allMarkets), { kind: "all" }] as [string, Partial<Filters>]] : []),
    ...(picks && filters.side !== "all" ? [[sideName(filters.side), { side: "all" }] as [string, Partial<Filters>]] : []),
    ...(game ? [[game.label, { game: "all" }] as [string, Partial<Filters>]] : []),
    ...(filters.q.trim() ? [[`"${filters.q.trim()}"`, { q: "" }] as [string, Partial<Filters>]] : []),
    ...(picks && filters.minEv !== DEFAULT_FILTERS.minEv ? [[`Min EV ${signedPct(filters.minEv)}`, { minEv: DEFAULT_FILTERS.minEv }] as [string, Partial<Filters>]] : []),
    ...(picks && filters.marketWeight !== null ? [[`Trust market ${pct(filters.marketWeight, 0)}`, { marketWeight: null }] as [string, Partial<Filters>]] : []),
    ...(picks && filters.flagged ? [["Flagged shown", { flagged: false }] as [string, Partial<Filters>]] : []),
  ];
  return (
    <section className="controls" aria-label="Filters">
      <div className="filter-bar">
        <Menu align="start" ariaLabel={`Market: ${marketName(filters.kind, picks, allMarkets)}`} label={<><MarketIcon k={filters.kind} />{marketName(filters.kind, picks, allMarkets)}</>}>
          {(close) => (nba ? NBA_MARKET_TABS : MARKET_TABS).map((k) => typeof k === "object" ? (
            <div key={k.label} className="menu-group">
              <div className="menu-group-h">{k.label}</div>
              {k.kinds.map((kk) => (
                <MenuItem key={kk} on={filters.kind === kk} onClick={() => { close(); set({ kind: kk }); }}>
                  <span className="with-icon"><MarketIcon k={kk} />{KIND_LABEL[kk]}</span>
                </MenuItem>
              ))}
            </div>
          ) : (
            <MenuItem key={k} on={filters.kind === k} onClick={() => { close(); set({ kind: k }); }}>
              <span className="with-icon"><MarketIcon k={k} />{marketName(k, picks, allMarkets)}</span>
            </MenuItem>
          ))}
        </Menu>
        {picks && (
          <Menu align="start" ariaLabel={`Side: ${sideName(filters.side)}`} label={sideName(filters.side)}>
            {(close) => (["all", "Over", "Under"] as const).map((s) => (
              <MenuItem key={s} on={filters.side === s} onClick={() => { close(); set({ side: s }); }}>{sideName(s)}</MenuItem>
            ))}
          </Menu>
        )}
        <Menu align="start" ariaLabel={`Game: ${game?.label ?? "all games"}`} label={game?.label ?? <>All games <span className="pill-count">{games.length}</span></>}>
          {(close) => <>
            <MenuItem on={!game} onClick={() => { close(); set({ game: "all" }); }}>All games <span className="mut">{games.length} {nba ? "on the slate" : "this week"}</span></MenuItem>
            {games.map((g) => (
              <MenuItem key={g.game_id} on={filters.game === g.game_id} onClick={() => { close(); set({ game: g.game_id }); }}>
                {g.label} <span className="mut">{kickoff(g.gameday, g.gametime)}</span>
              </MenuItem>
            ))}
          </>}
        </Menu>
        <input type="search" placeholder="Search player" value={filters.q} onChange={(e) => set({ q: e.target.value })} aria-label="Search player" />
        {picks && (
          <Menu align="end" ariaLabel="More filters" label={<><IconAdjustmentsHorizontal size={15} stroke={1.75} aria-hidden className="icon" />Filters{finer > 0 && <span className="count-badge">{finer}</span>}</>}>
            {() => (
              <div className="filter-pop">
                <label className="slider">
                  <span>Min EV <b>{signedPct(filters.minEv)}</b> <Info term="minEv" /></span>
                  <input type="range" min={-0.1} max={0.2} step={0.01} value={filters.minEv} onChange={(e) => set({ minEv: Number(e.target.value) })} />
                </label>
                <label className="slider">
                  <span>Trust market <b>{filters.marketWeight === null ? "Fitted" : pct(filters.marketWeight, 0)}</b> <Info term="trust" /></span>
                  <input type="range" min={0} max={1} step={0.05} value={filters.marketWeight ?? 0.35} aria-valuetext={filters.marketWeight === null ? "Fitted per market" : undefined}
                    onChange={(e) => set({ marketWeight: Number(e.target.value) })} />
                </label>
                <label className="check">
                  <input type="checkbox" checked={filters.marketWeight === null} onChange={(e) => set({ marketWeight: e.target.checked ? null : 0.35 })} />
                  Fitted per market and time to kickoff <Info term="fitted" />
                </label>
                <label className="check">
                  <input type="checkbox" checked={filters.flagged} onChange={(e) => set({ flagged: e.target.checked })} />
                  Show flagged plays <Info term="gaps" />
                </label>
              </div>
            )}
          </Menu>
        )}
      </div>
      {chips.length > 0 && (
        <div className="active-filters" aria-label="Active filters">
          {chips.map(([label, reset]) => (
            <button key={label} className="chip-x" onClick={() => set(reset)} aria-label={`Clear ${label}`}>{label}<IconX size={13} stroke={2} aria-hidden /></button>
          ))}
          {chips.length > 1 && <button className="link" onClick={() => onChange({ ...DEFAULT_FILTERS, book: filters.book })}>Clear all</button>}
        </div>
      )}
      {filters.kind === "td" && (
        <p className="td-note">
          <b>Anytime TD:</b> books post a single “Yes” price with a large margin, and this model doesn't see red-zone usage, so it
          always leans at least 60% on the market's chance. Treat these edges with caution; big ones usually mean the model is missing something.
        </p>
      )}
      {isDouble(filters.kind) && (
        <p className="td-note">
          <b>{filters.kind === "dd" ? "Double-double" : "Triple-double"}:</b> the chance that {filters.kind === "dd" ? "two" : "three"} of points, rebounds,
          assists, steals and blocks reach 10, from each stat's projection and its backtested misses, calibrated on past games
          (stats rise and fall together with minutes). Shown as a percentage; plays read Yes / No.
        </p>
      )}
      {filters.kind === "ftd" && (
        <p className="td-note">
          <b>First TD scorer:</b> each player's share of the game's expected touchdowns (defense and returns included), from the
          anytime-TD model and the game total. Books price 30+ players for one outcome, so the margin is far bigger than anytime TD;
          like anytime TD these lean at least 60% on the market. Who gets the ball first isn't modeled.
        </p>
      )}
    </section>
  );
}
