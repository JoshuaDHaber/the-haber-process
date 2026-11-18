import { americanOdds, pct } from "./format";
import { toAmerican } from "./slip";

/** "+900": the price at which a chance breaks even. */
export const fair = (p: number) => americanOdds(Math.round(toAmerican(1 / p)));

/** A long-shot chance: one decimal under 10%, whole points from there. Decided on the rounded value, so 9.97% reads "10%"
 *  beside "14%", not "10.0%". */
export const chance = (p: number) => pct(p, p < 0.0995 ? 1 : 0);

/** A meter's fill against the board's leader (full bar), so a slate where the favorite is 34% and one where he's 12% both read. */
export const meterWidth = (p: number, top: number) => `${top > 0 ? Math.min(100, (p / top) * 100) : 0}%`;
