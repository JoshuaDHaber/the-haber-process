import { describe, expect, it } from "vitest";
import { boostPct, costOf, leadOf, legLeague, NO_PROMO, parseOdds, promoBody, promoOf, promoReady, promoReturn, refundOf, signedMoney, type TrackedBet, type TrackedLeg, voidableLegs, winReturn } from "./bets";

describe("bets helpers", () => {
  it("reads American odds typed by hand and refuses anything else", () => {
    expect(parseOdds("+264")).toBe(264);
    expect(parseOdds(" -110 ")).toBe(-110);
    expect(parseOdds("250")).toBe(250);
    for (const bad of ["", "50", "-99", "1.91", "abc", "+"]) expect(parseOdds(bad)).toBeNull();
  });

  it("knows a leg's league from its market, as the server does", () => {
    expect(legLeague({ kind: "pts" })).toBe("nba");
    expect(legLeague({ kind: "rush" })).toBe("nfl");
    expect(legLeague({ kind: "ml", gameId: "2026_20261104_BOS_NYK" })).toBe("nba");
    expect(legLeague({ kind: "ml", gameId: "2026_09_KC_DEN" })).toBe("nfl");
  });

  it("offers a parlay's started, still-counting graded legs to mark voided by the book", () => {
    const at = (h: number) => new Date(Date.UTC(2026, 10, 22, h)).toISOString().slice(0, 19);
    const lg = (id: number, o: Partial<TrackedLeg>) => ({ id, kind: "rush", kickoff: at(18), result: null, ...o }) as TrackedLeg;
    const bet = (legs: TrackedLeg[]) => ({ legs }) as TrackedBet;
    const now = Date.UTC(2026, 10, 22, 21);
    const legs = [lg(1, { result: "loss" }), lg(2, {}), lg(3, { kickoff: at(23) }), lg(4, { result: "void" }), lg(5, { result: "push" }),
      lg(6, { kind: "custom" }), lg(7, { kind: "ml", result: "win" })];
    expect(voidableLegs(bet(legs), now).map((l) => l.id)).toEqual([1, 2, 7]);
    expect(voidableLegs(bet([lg(1, {})]), now)).toEqual([]);   // a single: the whole bet is marked void instead
  });

  it("signs money", () => {
    expect(signedMoney(24)).toBe("+$24.00");
    expect(signedMoney(-5.5)).toBe("−$5.50");
    expect(signedMoney(0)).toBe("$0.00");
  });
});

describe("bonus bets", () => {
  it("pay back only the winnings and cost nothing", () => {
    expect(winReturn(10, 15, false)).toBe(25);
    expect(winReturn(10, 15, true)).toBe(15);
    expect(costOf({ stake: 10, bonus: true })).toBe(0);
    expect(costOf({ stake: 10, bonus: false })).toBe(10);
  });
});

describe("promos", () => {
  const boost = (b: string) => ({ ...NO_PROMO, kind: "boost" as const, boost: b });
  const noSweat = (r: string) => ({ ...NO_PROMO, kind: "no_sweat" as const, refund: r });
  it("boost the winnings, not the stake", () => {
    expect(promoReturn(10, 15, boost("50"))).toBe(32.5);
    expect(promoReturn(10, 15, NO_PROMO)).toBe(25);
    expect(promoReturn(10, 15, { ...NO_PROMO, kind: "bonus" })).toBe(15);
  });
  it("take a whole percent for a boost and a refund up to the stake", () => {
    expect([boostPct(boost("25%")), boostPct(boost("12.5")), boostPct(boost("0")), boostPct(boost("501"))]).toEqual([25, null, null, null]);
    expect([refundOf(noSweat(""), 20), refundOf(noSweat("$10"), 20), refundOf(noSweat("50"), 20), refundOf(noSweat("x"), 20)]).toEqual([20, 10, 20, null]);
    expect(promoReady(boost(""), 10)).toBe(false);
    expect(promoReady(NO_PROMO, 10)).toBe(true);
    expect([promoReady(noSweat(""), 0), promoReady(noSweat("abc"), 10)]).toEqual([true, false]);   // blank is the whole stake
  });
  it("send every field, so switching takes the old promo off", () => {
    expect(promoBody(boost("40"), 10)).toEqual({ bonus: false, boost: 40, boost_cap: 0, no_sweat: 0, early_win: 0 });
    expect(promoBody(noSweat(""), 25)).toEqual({ bonus: false, boost: 0, boost_cap: 0, no_sweat: 25, early_win: 0 });
    expect(promoBody(NO_PROMO, 25)).toEqual({ bonus: false, boost: 0, boost_cap: 0, no_sweat: 0, early_win: 0 });
    expect(promoBody({ ...NO_PROMO, kind: "early_win", lead: "10" }, 25)).toEqual({ bonus: false, boost: 0, boost_cap: 0, no_sweat: 0, early_win: 10 });
  });
  it("read back off a tracked bet", () => {
    const b = { bonus: false, boost: null, no_sweat: null, early_win: null, stake: 10 };
    expect(promoOf({ ...b, boost: 30 })).toEqual({ kind: "boost", boost: "30", refund: "", lead: "", cap: "" });
    expect(promoOf({ ...b, no_sweat: 10 })).toEqual({ kind: "no_sweat", boost: "", refund: "", lead: "", cap: "" });
    expect(promoOf({ ...b, no_sweat: 5 }).refund).toBe("5");
    expect(promoOf({ ...b, early_win: 10 })).toEqual({ kind: "early_win", boost: "", refund: "", lead: "10", cap: "" });
    expect(promoOf({ ...b, boost: 20, boost_cap: 10 }).cap).toBe("10");
  });
  it("a boost capped at part of the stake lifts only that part's winnings", () => {
    const capped = { ...NO_PROMO, kind: "boost" as const, boost: "20", cap: "10" };
    // $15 at +445 wins $66.75; 20% on the first $10's share: 66.75 x (1 + 0.2 x 10/15) + 15 = 90.65
    expect(promoReturn(15, 66.75, capped)).toBeCloseTo(90.65, 2);
    expect(promoReturn(15, 66.75, { ...capped, cap: "" })).toBeCloseTo(95.10, 2);   // no cap: all of it
    expect(promoReturn(15, 66.75, { ...capped, cap: "50" })).toBeCloseTo(95.10, 2); // a cap above the stake: all of it
    expect([promoReady(capped, 15), promoReady({ ...capped, cap: "ten" }, 15)]).toEqual([true, false]);
    expect(promoBody(capped, 15)).toMatchObject({ boost: 20, boost_cap: 10 });
  });
  it("an early win needs whole points, 1 to 40", () => {
    const ew = (lead: string) => ({ ...NO_PROMO, kind: "early_win" as const, lead });
    expect([leadOf(ew("10")), leadOf(ew("17")), leadOf(ew("0")), leadOf(ew("41")), leadOf(ew("9.5")), leadOf(ew(""))]).toEqual([10, 17, null, null, null, null]);
    expect([promoReady(ew("10"), 5), promoReady(ew(""), 5)]).toEqual([true, false]);
  });
});
