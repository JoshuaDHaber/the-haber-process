import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { createPortal } from "react-dom";
import { useAuth } from "../auth";
import { legLeague, money, myBets, NO_PROMO, parseOdds, promoBody, promoReady, promoReturn, trackBets } from "../bets";
import { namesItself, KIND_SHORT, americanOdds } from "../format";
import { leaguePath, goToPath } from "../route";
import { parlay, toWin, type Leg } from "../slip";
import BookBadge from "./BookBadge";
import PromoFields from "./PromoFields";
import Sheet from "./Sheet";
import SidePill from "./SidePill";
import TagInput from "./TagInput";
import { openSignIn } from "./SignIn";

/** "Track this bet" from a slip group: record what was placed at the book (a parlay at the book's price, or each leg as a
 *  single) so it's graded and counted on My bets. Prefilled from the slip; the price is editable since the book's may differ.
 *  Rendered on document.body, above the slip it opens from. */
export default function TrackBetSheet({ book, legs, stake: slipStake, bonus: slipBonus = false, onClose, onLeave }:
  { book: string; legs: Leg[]; stake: number; bonus?: boolean; onClose: () => void; /** going to My bets: close the slip under this too */ onLeave: () => void }) {
  const auth = useAuth();
  const qc = useQueryClient();
  const mixed = new Set(legs.map((l) => legLeague(l))).size > 1;   // a parlay can't span leagues
  const [asParlay, setAsParlay] = useState(legs.length > 1 && !mixed);
  const [stake, setStake] = useState(slipStake ? String(slipStake) : "");
  const [odds, setOdds] = useState(americanOdds(parlay(legs, 1).american));
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const [done, setDone] = useState(0);
  const [tags, setTags] = useState<string[]>([]);
  const [promo, setPromo] = useState(slipBonus ? { ...NO_PROMO, kind: "bonus" as const } : NO_PROMO);
  const bonus = promo.kind === "bonus";
  // the tags used before, offered as one-tap suggestions (the same query My bets opens with)
  const ready = !!auth?.user?.age_confirmed;
  const known = useQuery({ queryKey: ["my-bets", auth?.user?.email, {}], queryFn: () => myBets({}), enabled: ready, staleTime: 60_000 });

  const stakeN = Number(stake.replace(/[$,\s]/g, ""));
  const stakeOk = Number.isFinite(stakeN) && stakeN > 0;
  const oddsN = asParlay ? parseOdds(odds) : 0;
  const promoOk = promoReady(promo, stakeN);
  const pays = !stakeOk ? null : asParlay ? (oddsN ? promoReturn(stakeN, toWin(oddsN, stakeN), promo) : null) : null;
  const league = legLeague(legs[0]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!auth?.user) { openSignIn(); return; }
    if (!stakeOk || (asParlay && !oddsN) || !promoOk) return;
    setBusy(true); setProblem("");
    try {
      const s = Math.round(stakeN * 100) / 100;
      const saved = await trackBets(book, legs, asParlay, s, oddsN ?? 0, tags, promoBody(promo, s));
      qc.invalidateQueries({ queryKey: ["my-bets"] });
      setDone(saved.length);
    } catch (err) { setProblem((err as Error).message); }
    finally { setBusy(false); }
  };

  const body = done ? <>
    <h2>Tracked</h2>
    <p className="sub auth-lede">{done === 1 ? "The bet is" : `${done} bets are`} on My bets. {done === 1 ? "It's" : "They're"} graded automatically once
      the stats are in{league === "nfl" ? ", usually the Tuesday after the games" : ", usually the next morning"}.</p>
    <div className="auth-form">
      <button className="primary" onClick={() => { onLeave(); goToPath(leaguePath(league, "bets")); }}>See My bets</button>
    </div>
    <p className="auth-links"><button className="link" onClick={onClose}>Back to the slip</button></p>
  </> : <>
    <h2>Track this bet</h2>
    <p className="sub auth-lede">Record the bet you placed at <b>{book}</b>. It's graded from the box scores and counted on My bets.</p>
    {!auth?.user && <p className="banner">Sign in to track bets: they're saved to your account, and only you can see them.</p>}
    <div className="track-book"><BookBadge book={book} size={24} /><b>{book}</b></div>
    {legs.length > 1 && (
      <div className="seg small" role="group" aria-label="Bet type">
        <button type="button" className={asParlay ? "on" : ""} aria-pressed={asParlay} disabled={mixed} onClick={() => setAsParlay(true)}>One parlay</button>
        <button type="button" className={!asParlay ? "on" : ""} aria-pressed={!asParlay} onClick={() => setAsParlay(false)}>{legs.length} singles</button>
      </div>
    )}
    {mixed && <p className="mut">These legs are from both leagues, so they're tracked as singles.</p>}
    <ul className="track-legs">
      {legs.map((l) => (
        <li key={l.id}>
          <span><b>{l.name}</b> <SidePill kind={l.kind} side={l.side} line={l.line} />{!namesItself(l.kind) && <span className="mut"> {KIND_SHORT[l.kind]}</span>}</span>
          <span className="mut">{americanOdds(l.odds)}</span>
        </li>
      ))}
    </ul>
    <form className="auth-form" onSubmit={submit}>
      <div className="track-fields">
        <label className="auth-label">{asParlay || legs.length === 1 ? "Stake" : "Stake on each"}
          <input className="auth-input" inputMode="decimal" autoComplete="off" value={stake} placeholder="$0" onChange={(e) => setStake(e.target.value)} />
        </label>
        {asParlay && (
          <label className="auth-label">Parlay odds at {book}
            <input className="auth-input" inputMode="text" autoComplete="off" value={odds} onChange={(e) => setOdds(e.target.value)} aria-invalid={!oddsN} />
          </label>
        )}
      </div>
      <PromoFields promo={promo} onChange={setPromo} stake={stakeOk ? stakeN : 0} book={book} />
      <label className="auth-label" htmlFor="track-tags">Tags <span className="mut">(optional, to sort My bets)</span></label>
      <TagInput id="track-tags" tags={tags} onChange={setTags} suggestions={known.data?.tags} />
      <p className="mut track-pays" aria-live="polite">
        {!promoOk ? (promo.kind === "boost" ? "Enter the boost, like 50%" : "Enter the refund in dollars")
          : asParlay ? (pays != null ? <>Pays <b>{money(pays)}</b> if every leg wins</> : !oddsN ? "Enter the price as American odds, like +264" : "Enter a stake")
          : legs.length > 1 ? (stakeOk ? <>{legs.length} bets, <b>{money(stakeN * legs.length)}</b> in {bonus ? "bonus bets" : "all"}, each at its own price</> : "Enter a stake")
          : stakeOk ? <>Pays <b>{money(promoReturn(stakeN, toWin(legs[0].odds, stakeN), promo))}</b> if it wins</> : "Enter a stake"}
      </p>
      {problem && <p className="err auth-problem" role="alert">{problem}</p>}
      <button className="primary" disabled={busy || (auth?.user != null && (!stakeOk || (asParlay && !oddsN) || !promoOk))}>
        {!auth?.user ? "Sign in to track" : busy ? "Saving…" : legs.length > 1 && !asParlay ? `Track ${legs.length} bets` : "Track bet"}
      </button>
    </form>
    <p className="mut auth-fine">The site never places bets. This only records what you placed, for your own record.</p>
  </>;

  return createPortal(<Sheet label="Track this bet" overlayClass="track-overlay" onClose={onClose}>{body}</Sheet>, document.body);
}
