import { useQuery } from "@tanstack/react-query";
import { IconShieldOff } from "@tabler/icons-react";
import { useMemo, useState } from "react";
import { api, type Kind, type SoftSpot } from "../api";
import { KIND_LABEL, KIND_SHORT, fmtProj } from "../format";
import type { League } from "../league";
import EmptyState, { LoadError } from "./EmptyState";
import { ordinal, slotGroup } from "./MatchupGrade";
import Skeleton from "./Skeleton";
import { useOverflowFade } from "../useOverflowFade";
import TeamLogo from "./TeamLogo";

const MARKETS: Record<League, Kind[]> = { nfl: ["rec", "rush", "recs", "rr", "pass"], nba: ["pts", "reb", "ast", "threes", "pra"] };
const GROUPS: Record<League, string[]> = { nfl: ["QB", "RB", "WR", "TE"], nba: ["G", "F", "C"] };
/** Spots shown before "Show all": the softest of what's filtered. */
const FIRST = 12;

/** Who a spot is about, in words: "TE1s", "starting QBs", "guards". */
export const spotWho = (key: string) => (key === "QB" ? "starting QBs" : slotGroup(key, key));
const signed = (x: number) => `${x >= 0 ? "+" : "−"}${Math.abs(Math.round(x))}%`;

/** The spot's numbers in a sentence: what the defense gives up a game against the league's average (NFL), then the grade's
 *  read, which compares those players with their own usual (so a defense that faced stars isn't called soft for it). */
export function spotText(s: SoftSpot): { allowed: string | null; vsUsual: string } {
  const who = spotWho(s.key), stat = KIND_LABEL[s.kind].toLowerCase();
  const allowed = s.allowed != null && s.league != null && s.league > 0
    ? `Allows ${fmtProj(s.kind, s.allowed)} ${stat} a game to ${who} (league ${fmtProj(s.kind, s.league)}, ${signed((s.allowed / s.league - 1) * 100)}) over ${s.games} game${s.games === 1 ? "" : "s"}.`
    : null;
  const where = s.rank <= s.n / 2 ? `${ordinal(s.rank)}-softest` : `${ordinal(s.n + 1 - s.rank)}-toughest`;
  return { allowed, vsUsual: `${who[0].toUpperCase()}${who.slice(1)} do ${signed((s.factor - 1) * 100)} vs their usual here, ${where} of ${s.n}.` };
}

/** "Soft spots": the slate's defenses ranked by how much they give up to each depth slot (NFL) or position (NBA) in a
 *  market, against what those players usually do, with the slate's players who line up there. Context, not an edge. */
export default function SoftSpots({ league, onSelect, gameIds }: { league: League; onSelect: (id: string, kind: Kind) => void;
  /** NBA: the games of the date picked at the top of the page (all of them when omitted). */
  gameIds?: string[] }) {
  const q = useQuery({ queryKey: ["soft-spots", league], queryFn: api.softSpots });
  const [kind, setKind] = useState<Kind>(MARKETS[league][0]);
  const [group, setGroup] = useState<string>("all");
  const [all, setAll] = useState(false);
  const keep = gameIds?.join();
  const spots = useMemo(() => {
    const ids = keep != null ? new Set(keep.split(",")) : null;
    return ids ? (q.data ?? []).map((s) => ({ ...s, players: s.players.filter((p) => ids.has(p.game_id)) })).filter((s) => s.players.length) : q.data ?? [];
  }, [q.data, keep]);
  const rows = useMemo(() => spots.filter((s) => s.kind === kind && (group === "all" || s.key.replace(/\d$/, "") === group)), [spots, kind, group]);
  const { ref, onScroll, className: fade } = useOverflowFade<HTMLDivElement>("button.on", [kind, !!q.data]);
  const groups = GROUPS[league].filter((g) => spots.some((s) => s.kind === kind && s.key.replace(/\d$/, "") === g));
  if (q.isPending) return <Skeleton kind="cards" label="Loading soft spots…" />;
  if (q.isError && !q.data) return <LoadError what="soft spots" retry={q.refetch} />;
  if (!q.data?.length) return (
    <EmptyState icon={IconShieldOff} title="No matchup grades yet">
      Soft spots come from this {league === "nba" ? "slate" : "week"}'s projections. They show here after the next model run.
    </EmptyState>
  );
  const shown = all ? rows : rows.slice(0, FIRST);
  return (
    <section className={`spots${q.isRefetching ? " refreshing" : ""}`} aria-busy={q.isRefetching}>
      <div className="spots-controls">
        <div ref={ref} onScroll={onScroll} className={`seg small${fade}`} role="group" aria-label="Market">
          {MARKETS[league].map((k) => (
            <button type="button" key={k} className={kind === k ? "on" : ""} aria-pressed={kind === k} onClick={() => { setKind(k); setAll(false); if (group !== "all" && !spots.some((s) => s.kind === k && s.key.replace(/\d$/, "") === group)) setGroup("all"); }}>
              {KIND_SHORT[k]}
            </button>
          ))}
        </div>
        {groups.length > 1 && <div className="chips" role="group" aria-label="Position">
          {["all", ...groups].map((g) => (
            <button type="button" key={g} className="chip-btn" aria-pressed={group === g} onClick={() => { setGroup(g); setAll(false); }}>
              {g === "all" ? "All" : g}
            </button>
          ))}
        </div>}
      </div>
      <h3 className="sub-h">Softest for {KIND_LABEL[kind].toLowerCase()} <span className="mut">· {rows.length}</span></h3>
      {rows.length === 0 ? (
        <EmptyState compact title={`No graded ${group} spots for ${KIND_LABEL[kind].toLowerCase()}`}>Try another market or position.</EmptyState>
      ) : <>
        <p className="mut board-note spots-note">
          Ranked by how {league === "nfl" ? "each depth slot (a team's WR1, WR2... by role over its last 4 games)" : "guards, forwards and centers"} did
          against this defense compared with their own usual, shrunk toward average. Context, not an edge: the projections already use each defense.
        </p>
        <ol className="spots-grid" aria-label={`Soft spots for ${KIND_LABEL[kind].toLowerCase()}`}>
          {shown.map((s) => {
            const t = spotText(s);
            const tone = s.grade === "A" || s.grade === "B" ? " up" : s.grade === "D" || s.grade === "F" ? " down" : "";
            return (
              <li key={`${s.defense}-${s.key}`} className="card spot">
                <div className="spot-h">
                  <TeamLogo team={s.defense} league={league} size={24} />
                  <b>{s.defense}</b><span className="mut">vs {spotWho(s.key)}</span>
                  <span className={`wx-chip mgrade${tone}`}>Matchup {s.grade}</span>
                </div>
                {t.allowed && <p className="spot-allowed">{t.allowed}</p>}
                <p className="mut spot-usual">{t.vsUsual}</p>
                <ul className="spot-players" aria-label={`Facing ${s.defense} this ${league === "nba" ? "slate" : "week"}`}>
                  {s.players.map((p) => (
                    <li key={p.player_id}>
                      <button type="button" className="link spot-link" onClick={() => onSelect(p.player_id, s.kind)}>
                        <b>{p.name}</b> <span className="mut">{p.team} · {league === "nfl" ? `${s.key} · ` : ""}proj {fmtProj(s.kind, p.mu)}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              </li>
            );
          })}
        </ol>
        {rows.length > FIRST && (
          <p className="spots-more"><button type="button" className="chip-btn" onClick={() => setAll(!all)}>{all ? `Show the softest ${FIRST}` : `Show all ${rows.length}, toughest last`}</button></p>
        )}
      </>}
    </section>
  );
}
