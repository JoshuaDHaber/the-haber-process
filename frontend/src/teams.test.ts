import { setLeague } from "./league";
import { describe, expect, it } from "vitest";
import { luminance, matchupColors, teamLogo } from "./teams";

describe("team colors", () => {
  it("keeps both primaries when they look different", () => {
    const [a, h] = matchupColors("BAL", "CLE");
    expect([a.light, h.light]).toEqual(["#241773", "#FF3C00"]);
  });
  it("switches the home side to its secondary when the primaries clash", () => {
    const [, h] = matchupColors("SEA", "NE"); // navy vs navy
    expect(h.light).toBe("#C60C30");
  });
  it("never puts a near-black color on the dark theme", () => {
    for (const [away, home] of [["CHI", "ATL"], ["LV", "PIT"], ["HOU", "NE"], ["NYJ", "LV"]]) {
      for (const s of matchupColors(away, home)) expect(luminance(s.dark)).toBeGreaterThanOrEqual(0.03);
    }
  });
  it("picks readable text on light colors", () => {
    expect(matchupColors("PIT", "BAL")[0].ink).toBe("#101820");
  });
  it("maps ESPN's logo names and ignores unknown teams", () => {
    expect(teamLogo("LA")).toContain("/lar.png");
    expect(teamLogo("WAS")).toContain("/wsh.png");
    expect(teamLogo("XYZ")).toBeNull();
  });
  it("asks ESPN for a logo scaled to the size shown, snapped to a few sizes, or the original when larger", () => {
    expect(teamLogo("KC", "nfl", 44)).toBe("https://a.espncdn.com/combiner/i?img=/i/teamlogos/nfl/500/kc.png&w=80&h=80");
    expect(teamLogo("KC", "nfl", 24)).toContain("&w=40&h=40");
    expect(teamLogo("KC", "nfl", 400)).toBe("https://a.espncdn.com/i/teamlogos/nfl/500/kc.png");
  });
});

describe("NBA teams", () => {
  it("use NBA logos and colors while the NBA is on screen, NFL ones otherwise", () => {
    setLeague("nba");
    expect(teamLogo("GS")).toBe("https://a.espncdn.com/combiner/i?img=/i/teamlogos/nba/500/gs.png&w=80&h=80");
    const [nbaDal] = matchupColors("DAL", "DEN");
    setLeague("nfl");
    expect(teamLogo("GS")).toBeNull();
    const [nflDal] = matchupColors("DAL", "DEN");
    expect(nbaDal.light).not.toBe(nflDal.light);   // the Mavericks aren't the Cowboys
  });
});
