import { useQuery } from "@tanstack/react-query";
import { api, type Kind, type VsDefense as VsData } from "../api";
import { KIND_LABEL, KIND_SHORT, fmtProj } from "../format";
import EmptyState, { LoadError } from "./EmptyState";
import Info from "./Info";
import { slotGroup } from "./MatchupGrade";
import Skeleton from "./Skeleton";

/** The NFL markets the matchup grades cover (backend model.DVP_KINDS), so the only ones with a same-slot table. */
export const VS_KINDS: ReadonlySet<Kind> = new Set<Kind>(["rush", "rec", "recs", "pass", "rr"]);

const RESULT: Record<string, string> = { over: "Over", under: "Under", push: "Push" };

/** One line on how the slot did against this defense: their average beside their usual, and the logged lines cleared. */
export function vsSummary(d: VsData): string {
  const withUsual = d.rows.filter((r) => r.usual != null);
  const parts: string[] = [];
  if (withUsual.length) {
    const act = withUsual.reduce((s, r) => s + r.actual, 0) / withUsual.length;
    const usual = withUsual.reduce((s, r) => s + r.usual!, 0) / withUsual.length;
    const off = usual > 0 ? Math.round((act / usual - 1) * 100) : null;
    parts.push(`Averaged ${fmtProj(d.kind, act)} against a usual ${fmtProj(d.kind, usual)}${off != null ? ` (${off >= 0 ? "+" : "−"}${Math.abs(off)}%)` : ""}`);
  }
  const lined = d.rows.filter((r) => r.result != null);
  if (lined.length) parts.push(`went over the line in ${lined.filter((r) => r.result === "over").length} of ${lined.length} with one logged`);
  return parts.length ? `${parts.join(", ")}.` : "";
}

/** "Recent WR2s vs DAL": what players in his depth slot did against this week's defense in its last few games, with the
 *  line the board logged for each where it had one. Context for the matchup grade, not an edge. */
export default function VsDefense({ playerId, kind, pos, opp, slot }: { playerId: string; kind: Kind; pos: string; opp: string; slot?: string | null }) {
  const q = useQuery({ queryKey: ["vs-defense", playerId, kind], queryFn: () => api.vsDefense(playerId, kind), enabled: VS_KINDS.has(kind) });
  if (!VS_KINDS.has(kind)) return null;
  const d = q.data;
  const key = d?.slot ?? slot ?? pos;
  const who = slotGroup(pos, key);
  const head = (n?: number) => (
    <h3>Recent {who} vs {opp}{n != null && <span className="mut"> · {n}</span>} <Info term="vsDefense" /></h3>
  );
  if (q.isPending) return <section className="vs-def" aria-label={`Recent games vs ${opp}`}>{head()}<Skeleton kind="rows" count={3} label={`Loading recent games vs ${opp}…`} /></section>;
  if (q.isError) return <section className="vs-def">{head()}<LoadError compact what={`recent games vs ${opp}`} retry={q.refetch} /></section>;
  if (!d || !d.rows.length) return (
    <section className="vs-def">{head(0)}
      <EmptyState compact title={`No recent ${key} games vs ${opp}`}>
        {opp} hasn't faced one with a role in {KIND_LABEL[kind].toLowerCase()} in its recent games. It fills in after their next game.
      </EmptyState>
    </section>
  );
  const missing = d.rows.filter((r) => r.line == null).length;
  return (
    <section className="vs-def" aria-label={`Recent ${who} vs ${opp}`}>
      {head(d.rows.length)}
      <div className="card vs-card">
        <table>
          <thead><tr><th scope="col">Game</th><th scope="col">{KIND_SHORT[kind]}</th><th scope="col">Usual</th><th scope="col">Line</th></tr></thead>
          <tbody>
            {d.rows.map((r) => {
              const diff = r.usual != null ? r.actual - r.usual : null;
              return (
                <tr key={`${r.player_id}-${r.season}-${r.week}`}>
                  <td><b>{r.name}</b><small className="mut vs-meta">{r.team} · Week {r.week}{r.season !== d.rows[0].season ? `, ${r.season}` : ""}</small></td>
                  <td className={diff == null || Math.abs(diff) < 0.5 ? "" : diff > 0 ? "over" : "under"}>{fmtProj(kind, r.actual)}</td>
                  <td className="mut">{r.usual != null ? fmtProj(kind, r.usual) : "–"}</td>
                  <td>{r.line != null
                    ? <span className={r.result === "over" ? "over" : r.result === "under" ? "under" : ""}>{r.line}<small className="vs-meta">{RESULT[r.result ?? ""] ?? ""}</small></span>
                    : <span className="mut" title="No line logged for him that week">–</span>}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="mut board-note">
        {vsSummary(d)} {missing > 0 && <>A dash: no line logged for him that week (not posted, or before the board began logging lines in October 2026). </>}
        Context, not an edge: a handful of games, and the projection already uses this defense.
      </p>
    </section>
  );
}
