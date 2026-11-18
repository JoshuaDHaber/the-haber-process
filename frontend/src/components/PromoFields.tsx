import { boostPct, money, MAX_BOOST, refundOf, type Promo, type PromoKind } from "../bets";

const KINDS: [PromoKind, string][] = [["none", "None"], ["bonus", "Bonus bet"], ["boost", "Boost"], ["no_sweat", "No sweat"]];

/** The sportsbook promo a bet was placed with, one a bet: a bonus bet, a profit boost (its percent) or a no-sweat bet (what
 *  comes back as a bonus bet if it loses, the whole stake unless the book caps it). Used by every form that records a bet. */
export default function PromoFields({ promo, onChange, stake, book, compact = false }:
  { promo: Promo; onChange: (p: Promo) => void; stake: number; book?: string; /** the bet card's edit form: no explanations */ compact?: boolean }) {
  const from = book ? `from ${book}` : "from the book";
  const boostBad = promo.kind === "boost" && promo.boost.trim() !== "" && boostPct(promo) == null;
  const refundBad = promo.kind === "no_sweat" && promo.refund.trim() !== "" && refundOf(promo, stake) == null;
  return (
    <div className="promo">
      <div className="auth-label">Promo
        <div className="seg small promo-kinds" role="group" aria-label="Promo">
          {KINDS.map(([k, label]) => (
            <button type="button" key={k} className={promo.kind === k ? "on" : ""} aria-pressed={promo.kind === k} onClick={() => onChange({ ...promo, kind: k })}>{label}</button>
          ))}
        </div>
      </div>
      {promo.kind === "boost" && (
        <div className="promo-row">
          <label className="auth-label">Profit boost
            <span className="promo-input"><input className="auth-input" inputMode="numeric" autoComplete="off" value={promo.boost} placeholder="50"
              aria-invalid={boostBad} aria-describedby={boostBad ? "promo-boost-err" : undefined} onChange={(e) => onChange({ ...promo, boost: e.target.value })} /><span className="mut">%</span></span>
          </label>
          {!compact && <p className="mut promo-why">Enter the odds before the boost. A win pays the boosted winnings; the stake comes back as usual.</p>}
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
      {promo.kind === "bonus" && !compact && <p className="mut promo-why">A free bet {from}: not counted as money you staked, and a win pays only the winnings.</p>}
      {boostBad && <p className="err" id="promo-boost-err">A profit boost is a whole percent, 1 to {MAX_BOOST}.</p>}
    </div>
  );
}
