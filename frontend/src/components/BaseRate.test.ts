import { describe, expect, it } from "vitest";
import type { BaseRates } from "../api";
import { baseRateFor, baseRateText } from "./Why";

const data: BaseRates = { season: 2026, min_n: 30, rates: [
  { kind: "rec", pos: "all", n: 493, over: 0.481, under: 0.51, push: 0.009, avg: 4.51 },
  { kind: "rec", pos: "WR", n: 292, over: 0.483, under: 0.517, push: 0, avg: 4.89 },
  { kind: "rec", pos: "QB", n: 4, over: 0.5, under: 0.5, push: 0, avg: 0 },
  { kind: "pts", pos: "G", n: 1211, over: 0.437, under: 0.55, push: 0.013, avg: -0.36 },
] };

describe("base rates", () => {
  it("uses his position once it has enough graded lines, else every position", () => {
    expect(baseRateFor(data, "rec", "WR")?.pos).toBe("WR");
    expect(baseRateFor(data, "rec", "QB")?.pos).toBe("all");
    expect(baseRateFor(data, "rush", "RB")).toBeNull();
    expect(baseRateFor(undefined, "rec", "WR")).toBeNull();
  });
  it("says it for the play's side", () => {
    expect(baseRateText(data.rates[1], "Over")).toBe("Receiving yds lines for WRs this season: overs hit 48%, the average result +4.9 against the line (292 graded).");
    expect(baseRateText(data.rates[3], "Under")).toBe("Points lines for guards this season: unders hit 55%, the average result −0.4 against the line (1211 graded).");
  });
});
