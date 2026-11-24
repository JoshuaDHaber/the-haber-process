import type { BaseRate, BaseRates, GameLogEntry, Kind, Projection } from "../api";
import { KIND_LABEL, americanOdds, effText, fmtProj, isDouble, isYesNo, pct, playLabel, signedPct, unit, volLabel } from "../format";
import { hitSplits } from "../gameLog";
import type { Cell } from "./LineLadder";
import Info from "./Info";
import { propRead } from "../read";
import MatchupGrade, { matchupGrade, matchupText } from "./MatchupGrade";

const mult = (x: number) => `${x >= 1 ? "+" : "−"}${Math.abs((x - 1) * 100).toFixed(0)}%`;
const tone = (x: number) => (Math.abs(x - 1) < 0.005 ? "" : x > 1 ? "up" : "down");
const breakEven = (odds: number) => 1 / (odds > 0 ? 1 + odds / 100 : 1 + 100 / -odds);

interface Step { label: string; detail: string; effect?: string; cls?: string }

/** The projection's ingredients, in the order the model multiplies them. NBA rows come from the stored parts; the NFL
 *  shows its volume, efficiency (opponent and game script are already in those), a change at quarterback and weather. */
function buildSteps(kind: Kind, proj: Projection, nba: boolean): Step[] {
  const p = proj.parts;
  if (nba && p && p.rate != null && p.opp != null && p.tm != null) {
    const usage = p.tm / p.tm_min;
    const names = p.out.map((o) => o.name);
    return [
      { label: "Minutes", detail: `${p.min.toFixed(1)} in recent games${Math.abs(p.tm_min - 1) >= 0.005 ? ` → ${proj.vol.toFixed(1)} with who's out` : ""}`,
        effect: Math.abs(p.tm_min - 1) >= 0.005 ? mult(p.tm_min) : undefined, cls: tone(p.tm_min) },
      { label: "Per 36 min", detail: `${(p.rate * 36).toFixed(1)} ${unit(kind)}, steadied toward his position` },
      { label: `vs ${proj.opp}`, detail: Math.abs(p.opp - 1) < 0.005 ? "average defense" : p.opp > 1 ? "allows more than most" : "allows less than most",
        effect: mult(p.opp), cls: tone(p.opp) },
      { label: "Teammates out", detail: names.length ? `${names.join(", ")}` : "full roster: a little less for everyone",
        effect: mult(usage), cls: tone(usage) },
    ];
  }
  if (nba) return [];
  const steps: Step[] = [
    { label: volLabel(kind), detail: `${proj.vol.toFixed(1)}, from recent games, matchup and game script` },
    { label: effText(kind, proj.eff)[0], detail: `${effText(kind, proj.eff)[1]}, steadied toward his position, vs this defense` },
  ];
  if (proj.qb_adj != null && proj.qb_adj <= 0.995) steps.push({ label: "QB change",
    detail: `${proj.qb_name || "A backup"} starting: a downgrade from his recent QBs`, effect: mult(proj.qb_adj), cls: tone(proj.qb_adj) });
  if (proj.wx != null && Math.abs(proj.wx - 1) >= 0.005) steps.push({ label: "Weather", detail: "wind, cold or rain at kickoff", effect: mult(proj.wx), cls: tone(proj.wx) });
  return steps;
}

/** "Why this number": what the projection is built from, then how it becomes a win chance and an EV at this price. */
export function WhyPanel({ kind, proj, play, curveWeight, nba, form }: { kind: Kind; proj: Projection; play: Cell | null; curveWeight: number | null | undefined; nba: boolean;
  /** His record at the play's line over recent games, for the read. */
  form?: { label: string; hit: number; n: number } | null }) {
  const yesNo = isYesNo(kind);
  const steps = yesNo ? [] : buildSteps(kind, proj, nba);
  const q = play?.q;
  if (!steps.length && !q) return null;
  const w = curveWeight ?? null;
  const grade = !yesNo && proj.dvp_rank != null && proj.dvp_n != null
    ? { letter: matchupGrade(proj.dvp_rank, proj.dvp_n), text: matchupText(kind, proj.pos, proj.opp, proj.dvp_rank, proj.dvp_n, proj.dvp, proj.dvp_slot) } : null;
  return (
    <section className="why" aria-label="Why this number">
      <h3>Why {yesNo ? "this chance" : `${fmtProj(kind, proj.mu)} ${unit(kind)}`}</h3>
      <div className="card why-card">
        <p className="why-read" aria-label="The read">{propRead({ kind, proj, form, matchup: grade,
          play: play ? { side: play.side, line: play.row.line, book: play.row.book, q: play.q } : null }).join(" ")}</p>
        {steps.length > 0 && (
          <ol className="why-steps">
            {steps.map((s) => (
              <li key={s.label}><span className="w-l">{s.label}</span><span className="w-d">{s.detail}</span><b className={`w-e ${s.cls ?? ""}`}>{s.effect ?? ""}</b></li>
            ))}
            <li className="w-total"><span className="w-l">Projection</span><span className="w-d">{isDouble(kind) ? "" : `typical miss ±${proj.sd.toFixed(1)}`}</span><b className="w-e">{fmtProj(kind, proj.mu)}</b></li>
          </ol>
        )}
        {q && play && (
          <ol className="why-steps">
            {q.p_model != null && <li><span className="w-l">Model</span><span className="w-d">chance of {playLabel(kind, play.side, play.row.line)}</span><b className="w-e">{pct(q.p_model, 0)}</b></li>}
            {q.p_curve != null && <li><span className="w-l">Market <Info term="curve" /></span><span className="w-d">the books' fair chance</span><b className="w-e">{pct(q.p_curve, 0)}</b></li>}
            {q.p_model != null && q.p_curve != null && w != null && (
              <li><span className="w-l">Blend <Info term="blend" /></span><span className="w-d">{Math.round((1 - w) * 100)}/{Math.round(w * 100)} model/market</span><b className="w-e">{pct(q.prob, 0)}</b></li>
            )}
            <li className="w-total"><span className="w-l">Price</span><span className="w-d">{americanOdds(q.odds)} at {play.row.book}, breaks even at {pct(breakEven(q.odds), 0)}</span>
              <b className={`w-e ${q.ev > 0 ? "pos" : "neg"}`}>{signedPct(q.ev)} EV</b></li>
          </ol>
        )}
        {grade && (
          <p className="why-ctx"><MatchupGrade kind={kind} pos={proj.pos} opp={proj.opp} rank={proj.dvp_rank} n={proj.dvp_n} factor={proj.dvp} slot={proj.dvp_slot} side={play?.side} />
            <span>{grade.text}</span><Info term="matchup" /></p>
        )}
      </div>
    </section>
  );
}

/** The base rate for a posted line in this market: his position's, once it has min_n graded lines, else every position's. */
export function baseRateFor(data: BaseRates | undefined, kind: Kind, pos: string): BaseRate | null {
  const rows = data?.rates.filter((r) => r.kind === kind) ?? [];
  const own = rows.find((r) => r.pos === pos && r.n >= data!.min_n);
  const all = rows.find((r) => r.pos === "all" && r.n >= data!.min_n);
  return own ?? all ?? null;
}

/** In words: "Receiving yds lines for WRs this season: overs hit 48%, the average result +4.9 against the line (292 graded)." */
export function baseRateText(r: BaseRate, side: "Over" | "Under", posLabel?: string) {
  const who = r.pos === "all" ? "" : ` for ${posLabel ?? ({ G: "guards", F: "forwards", C: "centers" }[r.pos] ?? `${r.pos}s`)}`;
  const avg = `${r.avg >= 0 ? "+" : "−"}${Math.abs(r.avg).toFixed(1)}`;
  return `${KIND_LABEL[r.kind]} lines${who} this season: ${side === "Over" ? "overs" : "unders"} hit ${pct(side === "Over" ? r.over : r.under, 0)}, `
    + `the average result ${avg} against the line (${r.n} graded).`;
}

/** Hit rates over slices of his games at the play's line, beside the model's chance and the posted lines' base rate:
 *  context, since raw rates know nothing about minutes, matchups or who's out. */
export function HitSplits({ kind, logs, side, line, opp, home, modelChance, base }: {
  kind: Kind; logs: GameLogEntry[]; side: "Over" | "Under"; line: number; opp: string; home: boolean; modelChance: number | null;
  /** How this market's posted lines did this season (his position's where it has enough), if graded. */
  base?: BaseRate | null;
}) {
  const splits = hitSplits(logs, side, line, { opp, home });
  if (!splits.length && !base) return null;
  return (
    <section className="hit-rates" aria-label="Hit rates">
      <h3>Hit rate · {playLabel(kind, side, line)} <Info term="hitRates" /></h3>
      <div className="hit-splits">
        {modelChance != null && <span className="hs model"><span>Model</span><b>{pct(modelChance, 0)}</b></span>}
        {splits.map((s) => (
          <span key={s.label} className={`hs${s.n < 5 ? " few" : ""}`} title={`${s.hit} of ${s.n} games${s.n < 5 ? " (too few to read much into)" : ""}`}>
            <span>{s.label}</span><b>{pct(s.hit / s.n, 0)}</b><small>{s.hit}/{s.n}</small>
          </span>
        ))}
        {base && (
          <span className="hs base" title={baseRateText(base, side)}>
            <span>{base.pos === "all" ? "All lines" : ({ G: "Guards", F: "Forwards", C: "Centers" }[base.pos] ?? `All ${base.pos}s`)}</span><b>{pct(side === "Over" ? base.over : base.under, 0)}</b><small>{base.n}</small>
          </span>
        )}
      </div>
      {base && <p className="mut base-note">{baseRateText(base, side)} <Info term="baseRate" /></p>}
    </section>
  );
}
