import { describe, expect, it } from "vitest";
import type { SoftSpot } from "../api";
import { spotText, spotWho } from "./SoftSpots";

const spot = (o: Partial<SoftSpot>): SoftSpot => ({ defense: "WAS", key: "TE1", kind: "rec", factor: 1.3, rank: 2, n: 32, grade: "A",
  allowed: 64, games: 10, league: 36, players: [], ...o });

describe("soft spots", () => {
  it("names the slot or position", () => {
    expect([spotWho("TE1"), spotWho("QB"), spotWho("G")]).toEqual(["TE1s", "starting QBs", "guards"]);
  });
  it("says what the defense allows against the league, then the grade's read", () => {
    const t = spotText(spot({}));
    expect(t.allowed).toBe("Allows 64 receiving yds a game to TE1s (league 36, +78%) over 10 games.");
    expect(t.vsUsual).toBe("TE1s do +30% vs their usual here, 2nd-softest of 32.");
    expect(spotText(spot({ allowed: null, league: null, kind: "pts", key: "G", rank: 29, n: 30, factor: 0.9 }))).toEqual(
      { allowed: null, vsUsual: "Guards do −10% vs their usual here, 2nd-toughest of 30." });
  });
});
