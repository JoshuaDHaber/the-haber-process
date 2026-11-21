import { IconUsers } from "@tabler/icons-react";
import EmptyState from "./EmptyState";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, type GameWeather, type Kind, type PlayerHit, type Projection } from "../api";
import { isDouble, isYesNo, KIND_LABEL, NBA_KINDS, effText, fmtProj, fmtSd, kickoff, matchup, volLabel } from "../format";
import { useAuth } from "../auth";
import { MAX_FAV_PLAYERS, toggleFavPlayer, useFavorites } from "../favorites";
import { getLeague } from "../league";
import { useDebounced } from "../useDebounced";
import Avatar from "./Avatar";
import { FavStar } from "./icons";
import { openSignIn } from "./SignIn";
import Spark from "./Spark";
import WeatherChip from "./WeatherChip";

/** Up to this many players side by side. */
export const MAX_COMPARE = 4;
const ORDER: Kind[] = ["pass", "ptd", "rush", "rlng", "rec", "recs", "lng", "rr", "td", "ftd", ...NBA_KINDS];
/** The chosen players, remembered in this browser per league. */
const storeKey = (nba: boolean) => (nba ? "comparePlayersNba" : "comparePlayers");
const stored = (nba: boolean): PlayerHit[] => {
  try { const v = JSON.parse(localStorage.getItem(storeKey(nba)) ?? "[]"); return Array.isArray(v) ? v.slice(0, MAX_COMPARE) : []; } catch { return []; }
};
/** Earliest game first: an NBA player can play twice on the slate, and the compare shows his next game. */
const byStart = (a: Projection, b: Projection) => `${a.gameday} ${a.gametime}`.localeCompare(`${b.gameday} ${b.gametime}`);

export default function PlayerCompare({ onSelect, weather = {} }: { onSelect: (id: string, kind: Kind) => void; weather?: Record<string, GameWeather | null | undefined> }) {
  const nba = getLeague() === "nba";
  const noGame = nba ? "Not playing on the slate" : "No projection this week";
  const [players, setPlayersState] = useState<PlayerHit[]>(() => stored(nba));
  const setPlayers = (p: PlayerHit[]) => { setPlayersState(p); try { localStorage.setItem(storeKey(nba), JSON.stringify(p)); } catch { /* private mode: not remembered */ } };
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const query = useDebounced(q.trim(), 200);
  const auth = useAuth();
  const favs = useFavorites();
  const [favProblem, setFavProblem] = useState("");
  const box = useRef<HTMLDivElement>(null);

  const hits = useQuery({ queryKey: ["player-search", nba, query], queryFn: () => api.searchPlayers(query), enabled: query.length >= 2, placeholderData: (p) => p });
  const projections = useQuery({ queryKey: ["all-projections", nba], queryFn: api.allProjections });
  const byPlayer = useMemo(() => {
    const m = new Map<string, Map<Kind, Projection>>();
    for (const p of [...(projections.data ?? [])].sort(byStart)) {
      if (!m.has(p.player_id)) m.set(p.player_id, new Map());
      const kinds = m.get(p.player_id)!;
      if (!kinds.has(p.kind) && [...kinds.values()].every((o) => o.game_id === p.game_id)) kinds.set(p.kind, p);
    }
    return m;
  }, [projections.data]);

  useEffect(() => {
    const close = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  const chosen = new Set(players.map((p) => p.player_id));
  const options = (query.length >= 2 ? hits.data ?? [] : []).filter((h) => !chosen.has(h.player_id));
  const full = players.length >= MAX_COMPARE;
  // still typing, or the new search hasn't come back: say so rather than show the last search's (empty) result
  const pending = q.trim() !== query || hits.isFetching;
  const add = (h: PlayerHit) => { if (full || chosen.has(h.player_id)) return; setPlayers([...players, h]); setQ(""); setActive(0); };
  const remove = (id: string) => setPlayers(players.filter((p) => p.player_id !== id));
  const isFav = (id: string) => favs.players.some((f) => f.player_id === id);
  const favHit = (f: (typeof favs.players)[number]): PlayerHit => ({ ...f, projected: byPlayer.has(f.player_id) });
  // a favorite chip adds the player, or takes them back out; "Compare favorites" fills the table with the first few
  const pickFav = (f: (typeof favs.players)[number]) => (chosen.has(f.player_id) ? remove(f.player_id) : add(favHit(f)));
  const starIt = (h: PlayerHit) => {
    if (!favs.ready) return void (auth?.user ? setFavProblem("Confirm you're 21 or older to save favorites.") : openSignIn());
    if (!isFav(h.player_id) && favs.players.length >= MAX_FAV_PLAYERS) return setFavProblem(`You have ${MAX_FAV_PLAYERS} favorites. Unstar one first.`);
    setFavProblem("");
    toggleFavPlayer(getLeague(), favs.players, h).catch((err) => setFavProblem((err as Error).message));
  };
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setOpen(true); setActive((a) => Math.min(a + 1, options.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
    else if (e.key === "Enter" && options[active]) { e.preventDefault(); add(options[active]); }
    else if (e.key === "Escape") setOpen(false);
  };

  const kinds = ORDER.filter((k) => players.some((p) => byPlayer.get(p.player_id)?.has(k)));
  const first = (id: string) => byPlayer.get(id)?.values().next().value as Projection | undefined;
  // the best projection in each market, when at least two players have one
  const leader = (k: Kind) => {
    const rows = players.map((p) => byPlayer.get(p.player_id)?.get(k)).filter(Boolean) as Projection[];
    return rows.length >= 2 ? rows.reduce((a, b) => (b.mu > a.mu ? b : a)).player_id : null;
  };

  return (
    <section className="compare">
      <div className="cmp-search" ref={box}>
        <input type="search" placeholder={full ? "Remove a player to add another" : "Search active players to compare"}
          value={q} disabled={full} onChange={(e) => { setQ(e.target.value); setOpen(true); setActive(0); }} onFocus={() => setOpen(true)} onKeyDown={onKey}
          aria-label="Search players" role="combobox" aria-expanded={open && options.length > 0} aria-controls="cmp-hits" autoComplete="off" />
        {open && q.trim().length >= 2 && (
          <ul id="cmp-hits" className="cmp-hits card" role="listbox">
            {!options.length && pending ? <li className="mut cmp-none">Searching…</li>
              : !options.length ? <li className="mut cmp-none">No active players match “{q.trim()}”.</li>
              : options.map((h, i) => (
                <li key={h.player_id} role="option" aria-selected={i === active} className={i === active ? "on" : ""}
                  onMouseEnter={() => setActive(i)} onMouseDown={(e) => { e.preventDefault(); add(h); }}>
                  <Avatar name={h.name} src={h.headshot} size={30} />
                  <span><b>{h.name}</b> <span className="pos-pill">{h.pos}</span> <span className="mut">{h.team}</span></span>
                  {!h.projected && <small className="mut cmp-tag">{noGame}</small>}
                </li>
              ))}
          </ul>
        )}
      </div>

      {favs.ready && favs.players.length > 0 ? (
        <div className="cmp-favs" aria-label="Your favorite players">
          <span className="tile-label">Favorites</span>
          {favs.players.map((f) => {
            const on = chosen.has(f.player_id);
            return (
              <button key={f.player_id} type="button" className="chip-btn cmp-fav" aria-pressed={on} disabled={!on && full}
                onClick={() => pickFav(f)} title={on ? `Take ${f.name} out of the comparison` : `Compare ${f.name}`}>
                <Avatar name={f.name} src={f.headshot} size={22} />{f.name}{!byPlayer.has(f.player_id) && !projections.isLoading && <small className="mut"> · no projection</small>}
              </button>
            );
          })}
          {favs.players.length >= 2 && (
            <button type="button" className="link cmp-fav-all" onClick={() => setPlayers(favs.players.slice(0, MAX_COMPARE).map(favHit))}>
              Compare {favs.players.length > MAX_COMPARE ? `first ${MAX_COMPARE}` : "all"}
            </button>
          )}
        </div>
      ) : !favs.loading && (
        <p className="mut cmp-fav-hint">
          {favs.ready ? "Star a player here, or beside their name when you open them, to keep them one tap away."
            : <><button type="button" className="link" onClick={() => openSignIn()}>Sign in</button> to save favorite players and compare them in one tap.</>}
        </p>
      )}
      {favProblem && <p className="err">{favProblem}</p>}

      {!players.length ? (
        <EmptyState icon={IconUsers} title="Pick players to compare">Search for 2 to {MAX_COMPARE} players above to see this week's projections side by side.</EmptyState>
      ) : (
        <div className="card scroll">
          <table className="t-cmp" style={{ "--n": players.length } as React.CSSProperties}>
            <thead>
              <tr>
                <th scope="col" className="cmp-mkt">Market</th>
                {players.map((h) => {
                  const p = first(h.player_id);
                  return (
                    <th key={h.player_id} scope="col" className="cmp-player">
                      <button className="cmp-x" onClick={() => remove(h.player_id)} aria-label={`Remove ${h.name}`} title="Remove">×</button>
                      <button className={`cmp-star fav-btn${isFav(h.player_id) ? " on" : ""}`} onClick={() => starIt(h)} aria-pressed={isFav(h.player_id)}
                        aria-label={isFav(h.player_id) ? `Remove ${h.name} from favorites` : `Add ${h.name} to favorites`} title={isFav(h.player_id) ? "Unstar" : "Star as a favorite"}>
                        <FavStar on={isFav(h.player_id)} size={16} />
                      </button>
                      <Avatar name={h.name} src={h.headshot} size={48} />
                      <b>{h.name}</b>
                      <span><span className="pos-pill">{h.pos}</span> {p?.team ?? h.team}</span>
                      <small className="mut">{p ? <>{matchup(p.opp, p.home)}{nba ? ` · ${kickoff(p.gameday, p.gametime)}` : <WeatherChip weather={weather[p.game_id]} wx={p.wx} />}</> : projections.isLoading ? "…" : noGame}</small>
                      {p?.status && <small className="q">{p.status}</small>}
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              {kinds.map((k) => {
                const best = leader(k);
                return (
                  <tr key={k}>
                    <th scope="row" className="cmp-mkt">{KIND_LABEL[k]}</th>
                    {players.map((h) => {
                      const p = byPlayer.get(h.player_id)?.get(k);
                      if (!p) return <td key={h.player_id} className="cmp-cell mut">–</td>;
                      const [effLabel, eff] = effText(k, p.eff);
                      return (
                        <td key={h.player_id} className={`cmp-cell clickable${best === h.player_id ? " lead" : ""}`} tabIndex={0}
                          onClick={() => onSelect(h.player_id, k)} onKeyDown={(e) => e.key === "Enter" && onSelect(h.player_id, k)}
                          title={`Open ${h.name}'s ${KIND_LABEL[k].toLowerCase()}`}>
                          <div className="cmp-val"><b>{fmtProj(k, p.mu)}</b>{!isYesNo(k) && <small className="mut"> {fmtSd(k, p.sd)}</small>}</div>
                          {isYesNo(k)
                            ? <small className="mut">{p.eff.toFixed(2)} {isDouble(k) ? "stats expected in double figures" : "expected TDs"}</small>
                            : <small className="mut">{volLabel(k)} {p.vol.toFixed(1)} · {effLabel} {eff}</small>}
                          {!isYesNo(k) && <Spark values={p.last5} />}
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
              {!kinds.length && !projections.isLoading && (
                <tr><td colSpan={players.length + 1} className="mut cmp-none">{nba ? "None of these players is projected on the slate (not playing, ruled out or too few minutes)." : "None of these players has a projection this week (on bye, injured or not projected)."}</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}
      {kinds.length > 0 && <p className="mut cmp-foot">Highlighted: the higher projection in each market. Tap a number for that player's chart, lines and plays.</p>}
    </section>
  );
}
