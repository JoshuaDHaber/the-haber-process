import { IconInfoCircle } from "@tabler/icons-react";
import { useEffect, useRef, useState, type MouseEvent } from "react";

/** One sentence for each number a casual bettor might not know. */
export const GLOSSARY = {
  ev: ["Expected value (EV)", "What a $1 bet returns on average if the model's chance is right: +5% means about 5¢ profit per dollar over many bets. Positive is good."],
  kelly: ["¼ Kelly", "A suggested stake as a share of your bankroll, a quarter of the Kelly formula's amount to keep swings small. 2% means $2 of every $100."],
  minEv: ["Min EV", "Hide plays below this expected value. Slide below 0 to see the closest plays that aren't positive yet."],
  trust: ["Trust market", "How much of the sportsbooks' own (no-vig) chance to blend into the model's. Higher leans on the market, lower on the model."],
  fitted: ["Fitted weights", "By default each market leans on the books as much as past graded picks say it should, and more on prices posted close to kickoff, when the market is sharpest."],
  move: ["Market move", "How far the books' fair chance of this side has moved since the week's first odds fetch, at this line. A pick the market is moving away from is a warning sign."],
  steam: ["Steam", "Three or more books moved this line the same way between the last two odds fetches, with none moving against it."],
  clv: ["Closing line value", "Whether the market moved toward a pick after it was posted. Beating the closing price is the quickest honest test of an edge: results take hundreds of bets to say anything."],
  curve: ["Market curve", "The books' whole distribution for this player, read off every main and alternate line. A rung marked 'beats ladder' is priced better than the books' own ladder implies, whatever the model says."],
  gaps: ["Flagged plays", "Hidden by default. Large gap: the model and the market disagree by 40% or more (25% for rushing, receiving and passing yards), which usually means the model is missing something. Long shot: a double- or triple-double Yes longer than +400 (these have hit well below their price). Edge too big (NBA): EV over 20%, which has been a model miss far more often than a real edge."],
  winProb: ["Win prob", "The chance this side hits: the model's chance blended with the market's (see Trust market)."],
  blend: ["Model and market", "Win prob mixes two chances: the model's, and the sportsbooks' own with their margin taken out. The blend is how much each counts: 67/33 means two parts model to one part market. Markets the model reads less well lean on the books more."],
  edge: ["Edge", "How far the projection is from the line, in yards (catches for receptions). For yes/no markets, the model's chance minus the market's, in points."],
  proj: ["Projection", "The model's expected number for this player, ± its typical miss."],
  hit: ["Hit chance", "The chance every leg wins. Legs in the same game are priced together, using how they moved together in past games."],
  modelEv: ["Model EV", "Expected return of the whole parlay at the book's combined odds, if the hit chance is right."],
  likely: ["Likely plays", "Parlays of three to five of the board's likeliest plays, each leg from a different player, at one book."],
  tdParlay: ["Touchdown parlays", "The likeliest anytime touchdown scorers, parlayed. Each leg wins if that player scores a touchdown at any point in the game."],
  altProps: ["Likely alternate props", "Each player's likeliest over on an alternate line the book links to, to bet on its own rather than in a parlay."],
  ladder: ["Ladder", "Overs on alternate lines, stacked until the parlay pays +100 to +300. Each leg is a lower, likelier line than the main one."],
  sameGame: ["Same-game parlay", "Some legs share a game, and those legs move together, so the hit chance counts how they did in past games instead of multiplying. Books price same-game parlays their own way, usually below the legs multiplied: take it if the book's price beats the fair odds."],
  matchup: ["Matchup grade", "How this defense does against his position in this market, A (softest fifth of the league) to F (toughest), measured against what the players it faced usually do. Context, not an edge: the projection already uses the defense, and in past seasons the position split added little or nothing."],
  hitRates: ["Hit rate", "How often this exact play would have cashed in his past games. Useful context, but it knows nothing about minutes, the matchup or who's out; the model's chance does."],
  fair: ["Fair odds", "The odds that would make this parlay break even. If the book offers better, the model likes it."],
} as const;
export type Term = keyof typeof GLOSSARY;

/** A small ⓘ that explains a term: on hover with a mouse, on tap on a phone. */
export default function Info({ term }: { term: Term }) {
  const [at, setAt] = useState<{ x: number; y: number } | null>(null);
  const ref = useRef<HTMLButtonElement>(null);
  const [title, body] = GLOSSARY[term];
  const show = () => {
    const r = ref.current!.getBoundingClientRect();
    setAt({ x: Math.min(Math.max(r.left + r.width / 2, 150), window.innerWidth - 150), y: r.bottom + 6 });
  };
  useEffect(() => {
    if (!at) return;
    const hide = (e: Event) => { if (e.target !== ref.current && !ref.current?.contains(e.target as Node)) setAt(null); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setAt(null); };
    const scroll = () => setAt(null);
    document.addEventListener("pointerdown", hide);
    document.addEventListener("keydown", esc);
    window.addEventListener("scroll", scroll, { capture: true });
    return () => {
      document.removeEventListener("pointerdown", hide);
      document.removeEventListener("keydown", esc);
      window.removeEventListener("scroll", scroll, { capture: true });
    };
  }, [at]);
  // inside a <label> a click would also toggle its input; it only ever opens the note (a tap elsewhere closes it)
  const open = (e: MouseEvent) => { e.preventDefault(); e.stopPropagation(); show(); };
  return (
    <>
      <button ref={ref} type="button" className="info-btn" aria-label={`What is ${title}?`} aria-expanded={!!at}
        onClick={open} onPointerEnter={(e) => { if (e.pointerType === "mouse") show(); }} onPointerLeave={(e) => { if (e.pointerType === "mouse") setAt(null); }}>
        <IconInfoCircle size={14} stroke={1.75} aria-hidden />
      </button>
      {at && <span className="info-pop" role="tooltip" style={{ left: at.x, top: at.y }}><b>{title}</b>{body}</span>}
    </>
  );
}
