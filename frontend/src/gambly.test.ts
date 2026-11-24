import { describe, expect, it } from "vitest";
import type { Leg } from "./slip";
import { gamblyLeg, gamblyPrompt, gamblyUrl } from "./gambly";

const leg = (o: Partial<Leg> = {}): Leg => ({
  id: "x", playerId: "p1", name: "James Cook", team: "BUF", opp: "KC", home: true, kind: "rush", side: "Over", line: 69.5,
  odds: -110, book: "DraftKings", link: null, eventLink: null, alt: false, prob: 0.5, ev: 0, gameId: "2026_12_KC_BUF", ...o,
});

describe("gamblyLeg", () => {
  it("writes over/under markets as player, side, line and market, tagged with the league", () => {
    expect(gamblyLeg(leg())).toBe("James Cook Over 69.5 rushing yards (NFL)");
    expect(gamblyLeg(leg({ name: "Josh Allen", kind: "pass", line: 249.5, side: "Under" }))).toBe("Josh Allen Under 249.5 passing yards (NFL)");
    expect(gamblyLeg(leg({ name: "Khalil Shakir", kind: "lng", line: 22.5 }))).toBe("Khalil Shakir Over 22.5 longest reception (NFL)");
    expect(gamblyLeg(leg({ kind: "recs", line: 2.5 }))).toBe("James Cook Over 2.5 receptions (NFL)");
  });
  it("tags NBA markets as NBA", () => {
    expect(gamblyLeg(leg({ name: "Jalen Brunson", kind: "threes", line: 2.5 }))).toBe("Jalen Brunson Over 2.5 threes made (NBA)");
    expect(gamblyLeg(leg({ name: "Jalen Brunson", kind: "pra", line: 38.5 }))).toBe("Jalen Brunson Over 38.5 points + rebounds + assists (NBA)");
  });
  it("writes the yes side of yes/no markets as a phrase", () => {
    expect(gamblyLeg(leg({ kind: "td", line: 0.5 }))).toBe("James Cook anytime touchdown (NFL)");
    expect(gamblyLeg(leg({ kind: "ftd", line: 0.5 }))).toBe("James Cook first touchdown scorer (NFL)");
    expect(gamblyLeg(leg({ name: "Nikola Jokic", kind: "tpl", line: 0.5 }))).toBe("Nikola Jokic to record a triple-double (NBA)");
  });
  it("can't send the no side of a yes/no market", () => {
    for (const kind of ["td", "ftd", "dd", "tpl"] as const)
      expect(gamblyLeg(leg({ kind, side: "Under", line: 0.5 }))).toEqual({ reason: expect.stringContaining("\"no\" side") });
  });
  it("writes moneylines by team, with the league from the game id", () => {
    expect(gamblyLeg(leg({ kind: "ml", name: "Buffalo Bills", playerId: "BUF@2026_12_KC_BUF" }))).toBe("Buffalo Bills moneyline (NFL)");
    expect(gamblyLeg(leg({ kind: "ml", name: "New York Knicks", gameId: "2026_20261124_NYK_BOS" }))).toBe("New York Knicks moneyline (NBA)");
  });
});

describe("gamblyPrompt", () => {
  it("asks for a betslip for one leg and a numbered parlay for several", () => {
    expect(gamblyPrompt([leg()]).prompt).toBe("Build me a betslip: James Cook Over 69.5 rushing yards (NFL)");
    const two = gamblyPrompt([leg(), leg({ playerId: "p2", name: "Josh Allen", kind: "pass", line: 249.5, book: "Caesars" })]);
    expect(two.prompt).toBe("Build me a 2-leg parlay:\n1. James Cook Over 69.5 rushing yards (NFL)\n2. Josh Allen Over 249.5 passing yards (NFL)");
    expect(two.mapped).toHaveLength(2);
    expect(two.unmapped).toEqual([]);
  });
  it("lists the legs it couldn't send, with why, and numbers only the ones it did", () => {
    const noTd = leg({ id: "notd", kind: "td", side: "Under", line: 0.5 });
    const r = gamblyPrompt([noTd, leg()]);
    expect(r.prompt).toBe("Build me a betslip: James Cook Over 69.5 rushing yards (NFL)");
    expect(r.unmapped.map((u) => u.leg.id)).toEqual(["notd"]);
  });
  it("keeps one of a player's market when the slip has it twice (two books, or both sides)", () => {
    const a = leg({ id: "a", book: "DraftKings" });
    const b = leg({ id: "b", book: "FanDuel", line: 74.5 });
    const r = gamblyPrompt([a, b]);
    expect(r.mapped.map((l) => l.id)).toEqual(["a"]);
    expect(r.unmapped).toEqual([{ leg: b, reason: expect.stringContaining("already in the parlay") }]);
  });
  it("keeps one side of a game's moneyline", () => {
    const home = leg({ id: "h", kind: "ml", name: "Buffalo Bills", playerId: "BUF@g" });
    const away = leg({ id: "a", kind: "ml", name: "Kansas City Chiefs", playerId: "KC@g" });
    expect(gamblyPrompt([home, away]).mapped.map((l) => l.id)).toEqual(["h"]);
  });
  it("is empty when nothing can go", () => {
    expect(gamblyPrompt([]).prompt).toBe("");
    expect(gamblyPrompt([leg({ kind: "dd", side: "Under", line: 0.5 })]).prompt).toBe("");
  });
});

describe("gamblyUrl", () => {
  it("opens Ask Gambly with the prompt submitted on load", () => {
    const { url } = gamblyUrl([leg(), leg({ playerId: "p2", name: "Josh Allen", kind: "pass", line: 249.5 })]);
    const u = new URL(url!);
    expect(u.origin + u.pathname).toBe("https://gambly.com/chat");
    expect(u.searchParams.get("entry")).toBe("ask-gambly");
    expect(u.searchParams.get("autoSubmit")).toBe("1");
    expect(u.searchParams.get("prompt")).toBe("Build me a 2-leg parlay:\n1. James Cook Over 69.5 rushing yards (NFL)\n2. Josh Allen Over 249.5 passing yards (NFL)");
  });
  it("has no link when no leg can go", () => {
    expect(gamblyUrl([leg({ kind: "td", side: "Under", line: 0.5 })]).url).toBeNull();
  });
});
