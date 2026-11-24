import { describe, expect, it } from "vitest";
import type { GameContext, MoneylineGame, Pick, Projection } from "./api";
import { gameRundown, propRead } from "./read";

const proj = (o: Partial<Projection> = {}) => ({ player_id: "p", name: "Puka Nacua", pos: "WR", team: "LA", opp: "GB", home: true, kind: "rec", mu: 68.2, sd: 25,
  vol: 8, eff: 8.5, spread: 2.5, status: "", game_id: "g", gameday: "2026-11-29", gametime: "16:25", last5: [], ...o } as Projection);
const q = (o = {}) => ({ odds: -110, prob: 0.58, ev: 0.107, link: null, p_model: 0.61, p_curve: 0.53, ...o });

describe("the prop read", () => {
  it("says the projection against the line, the price against the chances, then context and form", () => {
    const s = propRead({ kind: "rec", proj: proj({ wx: 0.92 }), play: { side: "Over", line: 62.5, book: "FanDuel", q: q() },
      form: { label: "Last 10", hit: 6, n: 10 }, matchup: { letter: "B", text: "GB: 9th-softest of 32 vs WR1s for receiving yds (WR1s +8% vs their usual)." } });
    expect(s[0]).toBe("The model projects 68 receiving yds, 6 above the 62.5 line.");
    expect(s[1]).toBe("Over 62.5 at -110 (FanDuel) breaks even at 52%; the model 61%, the market 53%, blended 58%: +10.7% EV.");
    expect(s[2]).toBe("Matchup B: GB is the 9th-softest of 32 vs WR1s for receiving yds (WR1s +8% vs their usual), which helps the Over; the weather takes 8% off.");
    expect(s[3]).toBe("Over 62.5 hit in 6 of his last 10 (60%).");
  });
  it("warns when the model and the market are far apart, and says a losing price plainly", () => {
    const s = propRead({ kind: "rec", proj: proj(), play: { side: "Under", line: 62.5, book: "X", q: q({ ev: -0.04, p_model: 0.30, p_curve: 0.50 }) } });
    expect(s[1]).toContain("not enough to beat the price (-4.0% EV)");
    expect(s[2]).toContain("20 points apart");
  });
  it("reads a yes/no market as a chance", () => {
    expect(propRead({ kind: "td", proj: proj({ kind: "td", mu: 0.43 }), play: null })[0]).toBe("The model gives Puka Nacua a 43% chance to score a touchdown.");
  });
});

const side = (team: string, prob: number, p_model = prob, p_mkt: number | null = prob) => ({ team, name: team, home: false, p_model, p_mkt, prob, books: 5,
  odds: null, book: null, ev: null, kelly: null, link: null, event_link: null });

describe("the game rundown", () => {
  const ml = { game_id: "g", label: "GB @ LA", gameday: "", gametime: "", margin: 2.5, spread: -2.5, sides: [side("GB", 0.43), side("LA", 0.57, 0.64, 0.56)] } as MoneylineGame;
  const ctx: GameContext = { game_id: "g", teams: [
    { team: "GB", implied: 21, opp_implied: 23.5, spread: -2.5, total: 44.5, script: [], players: [] },
    { team: "LA", implied: 23.5, opp_implied: 21, spread: 2.5, total: 44.5, script: [], players: [
      { player_id: "k", name: "Kyren Williams", pos: "RB", team: "LA", games: 5, played: 5, tgt_l5: 0.12, tgt_prior: 0.16, tgt_trend: -1, car_l5: 0.43, car_prior: 0.5,
        car_trend: -1, rz_share: 0.3, rz: 32, rz_team: 108, p_td: 0.46, exp_td: 0.6, p_td2: 0.13 }] }] };
  const pick = { name: "Tyler Higbee", kind: "recs", side: "Under", line: 3.5, odds: -136, book: "FanDuel", ev: 0.047, kelly: 0.02, flagged: false } as Pick;
  it("names the favorite, the implied split, the roles and the best play", () => {
    const s = gameRundown({ league: "nfl", ml, ctx, picks: [pick, { ...pick, name: "B", ev: 0.01, kelly: 0.001 }] });
    expect(s[0]).toBe("LA is a 2.5-point favorite over GB: 57% to win, and the total of 44.5 splits LA 23.5, GB 21.0.");
    expect(s[1]).toBe("The model is higher on LA than the books (64% vs 56%).");
    expect(s[2]).toBe("Kyren Williams gets 30% of LA's touches inside the 20, with a 13% chance of 2+ TDs.");
    expect(s[3]).toBe("The board's best play here: Tyler Higbee Under 3.5 receptions at -136 (FanDuel), +4.7% EV, one of 2 +EV props for this game.");
  });
  it("still says something with no context and no props", () => {
    expect(gameRundown({ league: "nba", ml, picks: [] })).toEqual(["LA is a 2.5-point favorite over GB: 57% to win.", "The model is higher on LA than the books (64% vs 56%)."]);
  });
});
