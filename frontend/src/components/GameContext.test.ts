import { describe, expect, it } from "vitest";
import type { TeamContext } from "../api";
import { impliedLine } from "./GameContext";

const t = (team: string, implied: number | null): TeamContext => ({ team, implied, opp_implied: null, spread: null, total: null, script: [], players: [] });

describe("game context", () => {
  it("lists implied points favorite first", () => {
    expect(impliedLine([t("GB", 21), t("LA", 23.5)])).toBe("LA 23.5 · GB 21.0");
    expect(impliedLine([t("GB", null), t("LA", null)])).toBeNull();
  });
});
