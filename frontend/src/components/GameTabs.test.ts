import { describe, expect, it } from "vitest";
import { hashWithView, viewFromHash, type GameView } from "./GameTabs";

const NFL: GameView[] = ["overview", "props", "parlays", "proj", "script", "inj"];
const NBA: GameView[] = ["overview", "props", "parlays", "proj", "inj"];

describe("game page tab in the hash", () => {
  it("reads the tab next to the game and the player drawer", () => {
    expect(viewFromHash(NFL, "#game=2026_12_KC_BUF&view=parlays&player=00-1&kind=rec")).toBe("parlays");
  });
  it("falls back to the Overview without a tab, or with one this league's page doesn't have", () => {
    expect(viewFromHash(NFL, "#game=2026_12_KC_BUF")).toBe("overview");
    expect(viewFromHash(NFL, "#game=x&view=nope")).toBe("overview");
    expect(viewFromHash(NBA, "#game=x&view=script")).toBe("overview");
  });
  it("swaps the tab in, keeping the game and the drawer's keys", () => {
    const h = new URLSearchParams(hashWithView("proj", "#game=g1&player=p&kind=rush").slice(1));
    expect([h.get("game"), h.get("view"), h.get("player"), h.get("kind")]).toEqual(["g1", "proj", "p", "rush"]);
  });
  it("drops the key for the Overview, so a game's plain link is its Overview", () => {
    expect(hashWithView("overview", "#game=g1&view=props")).toBe("#game=g1");
  });
});
