import { useEffect } from "react";
import { boostPct, capOf, EARLY_WIN, leadOf, money, MAX_BOOST, MAX_EARLY_WIN, refundOf, type Promo, type PromoKind } from "../bets";

const KINDS: [PromoKind, string][] = [["none", "None"], ["bonus", "Bonus bet"], ["boost", "Boost"], ["no_sweat", "No sweat"], ["early_win", "Early win"]];

/** The sportsbook promo a bet was placed with, one a bet: a bonus bet, a profit boost (its percent), a no-sweat bet (what
 *  comes back as a bonus bet if it loses, the whole stake unless the book caps it) or an early-win token (a single
 *  moneyline paid as a win once its team leads by enough). Used by every form that records a bet. */
export default function PromoFields({ promo, onChange, stake, book, moneyline, compact = false }:
  { promo: Promo; onChange: (p: Promo) => void; stake: number; book?: string;
    /** the bet is a single moneyline in this league: an early-win token can go on it (only then is it offered) */ moneyline?: "nfl" | "nba" | null;
    /** the bet card's edit form: no explanations */ compact?: boolean }) {
  const from = book ? `from ${book}` : "from the book";
  const boostBad = promo.kind === "boost" && promo.boost.trim() !== "" && boostPct(promo) == null;
  const refundBad = promo.kind === "no_sweat" && promo.refund.trim() !== "" && refundOf(promo, stake) == null;
  const capBad = promo.kind === "boost" && promo.cap.trim() !== "" && capOf(promo) == null;
  const capPart = capOf(promo) != null && stake > 0 && capOf(promo)! < stake ? capOf(promo)! : null;
  const leadBad = promo.kind === "early_win" && promo.lead.trim() !== "" && leadOf(promo) == null;
  // the bet stopped being a single moneyline (a leg added): an early win can't stay on it
  useEffect(() => { if (!moneyline && promo.kind === "early_win") onChange({ ...promo, kind: "none" }); }, [moneyline, promo.kind]); // eslint-disable-line react-hooks/exhaustive-deps
  const pick = (k: PromoKind) => onChange({ ...promo, kind: k, lead: k === "early_win" && !promo.lead.trim() && moneyline ? String(EARLY_WIN[moneyline]) : promo.lead });
  return (
    <div className="promo">
      <div className="auth-label">Promo
        <div className="seg small promo-kinds" role="group" aria-label="Promo">
          {KINDS.filter(([k]) => k !== "early_win" || moneyline).map(([k, label]) => (
            <button type="button" key={k} className={promo.kind === k ? "on" : ""} aria-pressed={promo.kind === k} onClick={() => pick(k)}>{label}</button>
          ))}
        </div>
      </div>
      {promo.kind === "boost" && (
        <div className="promo-row">
          <label className="auth-label">Profit boost
            <span className="promo-input"><input className="auth-input" inputMode="numeric" autoComplete="off" value={promo.boost} placeholder="50"
              aria-invalid={boostBad} aria-describedby={boostBad ? "promo-boost-err" : undefined} onChange={(e) => onChange({ ...promo, boost: e.target.value })} /><span className="mut">%</span></span>
          </label>
          <label className="auth-label">On up to
            <span className="promo-input promo-cap"><span className="mut">$</span><input className="auth-input" inputMode="decimal" autoComplete="off" value={promo.cap}
              placeholder="Whole stake" aria-invalid={capBad} aria-describedby="promo-cap-why" onChange={(e) => onChange({ ...promo, cap: e.target.value })} /></span>
          </label>
          {(!compact || capPart != null) && <p className="mut promo-why" id="promo-cap-why" aria-live="polite">
            {capPart != null ? `Only the first ${money(capPart)} of the ${money(stake)} stake is boosted; the rest pays the plain price.`
              : "Enter the odds before the boost. A win pays the boosted winnings; the stake comes back as usual. If the boost covers only part of the wager (\"up to max $10\"), put that amount in On up to."}</p>}
        </div>
      )}
      {promo.kind === "no_sweat" && (
        <div className="promo-row">
          <label className="auth-label">Refund if it loses
            <input className="auth-input" inputMode="decimal" autoComplete="off" value={promo.refund} placeholder={stake > 0 ? money(stake) : "The stake"}
              aria-invalid={refundBad} onChange={(e) => onChange({ ...promo, refund: e.target.value })} />
          </label>
          {!compact && <p className="mut promo-why">Paid back as a bonus bet, up to the stake. A loss still counts against your profit; the bonus bet counts when you track it.</p>}
        </div>
      )}
      {promo.kind === "early_win" && moneyline && (
        <div className="promo-row">
          <label className="auth-label">Wins if they lead by
            <span className="promo-input"><input className="auth-input" inputMode="numeric" autoComplete="off" value={promo.lead} placeholder={String(EARLY_WIN[moneyline])}
              aria-invalid={leadBad} aria-describedby={leadBad ? "promo-lead-err" : undefined} onChange={(e) => onChange({ ...promo, lead: e.target.value })} /><span className="mut">pts</span></span>
          </label>
          {!compact && <p className="mut promo-why">An early-win token {from}: the bet pays as a win once the team is up by this much at any point, even if they lose. It's graded from the game's largest lead.</p>}
        </div>
      )}
      {promo.kind === "bonus" && !compact && <p className="mut promo-why">A free bet {from}: not counted as money you staked, and a win pays only the winnings.</p>}
      {boostBad && <p className="err" id="promo-boost-err">A profit boost is a whole percent, 1 to {MAX_BOOST}.</p>}
      {capBad && <p className="err">On up to is a dollar amount, like 10.</p>}
      {leadBad && <p className="err" id="promo-lead-err">An early win's lead is whole points, 1 to {MAX_EARLY_WIN}.</p>}
    </div>
  );
}
