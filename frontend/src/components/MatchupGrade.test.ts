import { describe, expect, it } from "vitest";
import { matchupGrade, matchupText } from "./MatchupGrade";

describe("matchup grades", () => {
  it("grades fifths of the league, softest first, as the backend does", () => {
    expect([1, 6, 7, 15, 24, 25, 30].map((r) => matchupGrade(r, 30))).toEqual(["A", "A", "B", "C", "D", "F", "F"]);
    expect([1, 8, 32].map((r) => matchupGrade(r, 32))).toEqual(["A", "B", "F"]);
  });
  it("says it in words from the nearer end", () => {
    expect(matchupText("pts", "G", "BOS", 3, 30, 1.06)).toBe("BOS: 3rd-softest of 30 vs guards for points (guards +6% vs their usual).");
    expect(matchupText("rec", "WR", "SF", 31, 32, 0.9)).toBe("SF: 2nd-toughest of 32 vs wide receivers for receiving yds (wide receivers −10% vs their usual).");
  });
  it("names the NFL depth slot when the grade is for one, and the position past the depth tracked", () => {
    expect(matchupText("rec", "WR", "WAS", 1, 32, 1.31, "WR2")).toBe("WAS: 1st-softest of 32 vs WR2s for receiving yds (WR2s +31% vs their usual).");
    expect(matchupText("rec", "WR", "WAS", 1, 32, 1.1, "WR")).toBe("WAS: 1st-softest of 32 vs wide receivers for receiving yds (wide receivers +10% vs their usual).");
  });
});
