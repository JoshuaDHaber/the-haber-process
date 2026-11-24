import type { Kind } from "../api";
import { KIND_LABEL, isYesNo } from "../format";

const GRADES = "ABCDF";
const GROUP: Record<string, string> = { G: "guards", F: "forwards", C: "centers", QB: "quarterbacks", RB: "running backs", FB: "running backs", WR: "wide receivers", TE: "tight ends" };

/** A for the softest fifth of the league's defenses against this position (as engine/dvp.py grades it), F for the toughest. */
export const matchupGrade = (rank: number, n: number) => GRADES[Math.min(Math.floor(((rank - 1) * GRADES.length) / Math.max(n, 1)), GRADES.length - 1)];

export const ordinal = (n: number) => `${n}${n % 100 >= 11 && n % 100 <= 13 ? "th" : ["th", "st", "nd", "rd"][n % 10] ?? "th"}`;

/** Who a grade is about: a depth slot ("WR2s") where the NFL graded one, else the position group. */
export const slotGroup = (pos: string, slot?: string | null) => (slot && /\d$/.test(slot) ? `${slot}s` : GROUP[pos] ?? "his position");

/** What a grade means, in words, for its tooltip and the drawer. */
export function matchupText(kind: Kind, pos: string, opp: string, rank: number, n: number, factor?: number | null, slot?: string | null) {
  const group = slotGroup(pos, slot);
  const where = rank <= n / 2 ? `${ordinal(rank)}-softest` : `${ordinal(n + 1 - rank)}-toughest`;
  const stat = KIND_LABEL[kind].toLowerCase();
  const off = factor != null ? Math.round((factor - 1) * 100) : null;
  const how = off != null ? ` (${group} ${off >= 0 ? "+" : "−"}${Math.abs(off)}% vs their usual)` : "";
  return `${opp}: ${where} of ${n} vs ${group} for ${stat}${how}.`;
}

/** The letter grade as a chip like the weather chip, toned for the side: a soft matchup helps an Over and hurts an
 *  Under. `quiet` leaves out a middling C (the board, where every row would otherwise carry one). */
export default function MatchupGrade({ kind, pos, opp, rank, n, factor, slot, side, quiet }: {
  kind: Kind; pos: string; opp: string; rank?: number | null; n?: number | null; factor?: number | null; slot?: string | null; side?: "Over" | "Under"; quiet?: boolean;
}) {
  if (rank == null || n == null || isYesNo(kind)) return null;
  const g = matchupGrade(rank, n);
  if (quiet && g === "C") return null;
  const soft = g === "A" || g === "B", tough = g === "D" || g === "F";
  const tone = side === "Under" ? (tough ? " up" : soft ? " down" : "") : side ? (soft ? " up" : tough ? " down" : "") : "";
  return (
    <span className={`wx-chip mgrade${tone}`} title={`${matchupText(kind, pos, opp, rank, n, factor, slot)} Context, not an edge: the projection already uses this defense.`}>
      Matchup {g}
    </span>
  );
}
