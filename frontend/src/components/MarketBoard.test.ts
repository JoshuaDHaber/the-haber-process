import { describe, expect, it } from "vitest";
import type { MarketMove } from "../api";
import { viewRows } from "./MarketBoard";

const mv = (o: Partial<MarketMove>): MarketMove => ({ game_id: "g", kind: "rec", player_id: "p", name: "P", team: "KC", pos: "WR", line: 50.5, books: 5,
  p_over: 0.5, fetches: 3, open_line: 50.5, move: 0, steam: 0, ...o });

describe("market board views", () => {
  const rows = [mv({ player_id: "big", move: -0.09, steam: -1 }), mv({ player_id: "small", move: 0.02 }), mv({ player_id: "flat", move: 0.01, fetches: 4 }),
    mv({ player_id: "moved-line", move: 0.01, open_line: 47.5 }), mv({ player_id: "two", move: 0.0, fetches: 2 }), mv({ player_id: "new", move: null, open_line: null })];
  it("movers: 4+ points either way", () => expect(viewRows(rows, "movers").map((r) => r.player_id)).toEqual(["big"]));
  it("steam: books moved together", () => expect(viewRows(rows, "steam").map((r) => r.player_id)).toEqual(["big"]));
  it("flat: same line, under 1.5 points, 3+ fetches, most quoted first", () =>
    expect(viewRows(rows, "flat").map((r) => r.player_id)).toEqual(["flat"]));
});
