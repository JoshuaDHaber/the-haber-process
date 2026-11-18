import { describe, expect, it } from "vitest";
import { boostPct, costOf, legLeague, NO_PROMO, parseOdds, promoBody, promoOf, promoReady, promoReturn, refundOf, signedMoney, winReturn } from "./bets";

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
    expect(promoBody(boost("40"), 10)).toEqual({ bonus: false, boost: 40, no_sweat: 0 });
    expect(promoBody(noSweat(""), 25)).toEqual({ bonus: false, boost: 0, no_sweat: 25 });
    expect(promoBody(NO_PROMO, 25)).toEqual({ bonus: false, boost: 0, no_sweat: 0 });
  });
  it("read back off a tracked bet", () => {
    expect(promoOf({ bonus: false, boost: 30, no_sweat: null, stake: 10 })).toEqual({ kind: "boost", boost: "30", refund: "" });
    expect(promoOf({ bonus: false, boost: null, no_sweat: 10, stake: 10 })).toEqual({ kind: "no_sweat", boost: "", refund: "" });
    expect(promoOf({ bonus: false, boost: null, no_sweat: 5, stake: 10 }).refund).toBe("5");
  });
});
