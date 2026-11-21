import { describe, expect, it } from "vitest";
import type { GameWeather } from "./api";
import { compassPoint, easternToday, slatePhrase, groupOf, isYesNo, skyIcon, americanOdds, fmtEdge, fmtProj, fmtSd, kickoff, marketLabel, unit, notableWeather, pct, playLabel, signed, signedPct, timeAgo, weatherShort, weatherText, wxEffect } from "./format";

describe("format", () => {
  it("formats percentages and signs", () => {
    expect(pct(0.1234)).toBe("12.3%");
    expect(signedPct(0.05)).toBe("+5.0%");
    expect(signedPct(-0.021)).toBe("-2.1%");
    expect(signed(3.14159)).toBe("+3.1");
  });
  it("formats American odds", () => {
    expect(americanOdds(120)).toBe("+120");
    expect(americanOdds(-110)).toBe("-110");
  });
  it("labels kickoff by weekday", () => {
    const k = (tz: string) => kickoff("2026-10-04", "13:00", tz).replace(/\s/g, " ");
    expect(k("America/New_York")).toMatch(/^Sun,? 1:00 PM$/);
    expect(k("America/Los_Angeles")).toMatch(/^Sun,? 10:00 AM$/);
    expect(k("Europe/London")).toMatch(/^Sun,? 6:00 PM$/);
    // late games roll into the next day, and the winter offset is applied after DST ends
    expect(kickoff("2026-12-06", "20:20", "Europe/London").replace(/\s/g, " ")).toMatch(/^Mon,? 1:20 AM$/);
    expect(kickoff("2026-10-04", "")).toBe("Sun");
  });
  it("renders relative times", () => {
    const now = Date.parse("2026-10-04T12:00:00Z");
    expect(timeAgo("2026-10-04T11:30:00", now)).toBe("30 min ago");
    expect(timeAgo("2026-10-04T08:00:00", now)).toBe("4 h ago");
  });
});

describe("anytime TD formatting", () => {
  it("shows a touchdown projection as a chance, and yardage as yards", () => {
    expect(fmtProj("td", 0.412)).toBe("41%");
    expect(fmtProj("rush", 54.6)).toBe("55");
  });
  it("names the plays: Anytime TD / No TD, but Over 54.5 for yardage", () => {
    expect(playLabel("td", "Over", 0.5)).toBe("Anytime TD");
    expect(playLabel("td", "Under", 0.5)).toBe("No TD");
    expect(playLabel("rec", "Under", 54.5)).toBe("Under 54.5");
  });
  it("gives the edge in yards, or percentage points for touchdowns", () => {
    expect(fmtEdge("pass", 7.74)).toBe("+7.7");
    expect(fmtEdge("td", -3.26)).toBe("-3.3 pts");
  });
});


describe("double-double formatting", () => {
  it("reads as a yes/no chance under the Combos tab", () => {
    expect(isYesNo("dd") && isYesNo("tpl")).toBe(true);
    expect(fmtProj("dd", 0.437)).toBe("44%");
    expect(playLabel("dd", "Over", 0.5)).toBe("Double-double");
    expect(playLabel("tpl", "Under", 0.5)).toBe("No triple-double");
    expect(groupOf("tpl")?.label).toBe("Combos");
  });
});

describe("weather", () => {
  const w = (o: Partial<GameWeather>): GameWeather => ({ stadium: "Lambeau Field", roof: "outdoors", indoor: false, source: "forecast", temp: 38,
    wind: 19.4, gust: 27, precip_prob: 10, precip: 0, snow: 0, fetched_at: null, factors: {}, ...o });
  it("describes the kickoff forecast, and indoor games by their roof", () => {
    expect(weatherText(w({}))).toBe("38°F · wind 19 mph (gusts 27)");
    expect(weatherText(w({ precip_prob: 70, gust: 20 }))).toBe("38°F · wind 19 mph · 70% rain");
    expect(weatherText(w({ indoor: true, roof: "dome", source: "indoor" }))).toBe("Dome");
    expect(weatherText(w({ source: "none", temp: null, wind: null }))).toBeNull();
  });
  it("flags only weather that matters, with the condition that stands out", () => {
    expect(notableWeather(w({ temp: 70, wind: 6 }))).toBe(false);
    expect(weatherShort(w({}))).toEqual({ icon: "wind", text: "19 mph" });
    expect(weatherShort(w({ wind: 5, snow: 0.4 }))).toEqual({ icon: "snow", text: "snow" });
    expect(weatherShort(w({ wind: 5 }))).toEqual({ icon: "cold", text: "38°F" });
  });
  it("shows the adjustment as a percentage, and nothing when there is none", () => {
    expect(wxEffect(0.782)).toBe("−22%");
    expect(wxEffect(1.03)).toBe("+3%");
    expect(wxEffect(1)).toBeNull();
    expect(wxEffect(null)).toBeNull();
  });
});

describe("game weather panel", () => {
  it("names compass points", () => {
    expect(compassPoint(0)).toBe("N");
    expect(compassPoint(300)).toBe("WNW");
    expect(compassPoint(359)).toBe("N");
    expect(compassPoint(-90)).toBe("W");
  });
  it("picks a sky icon, with a moon for clear night games", () => {
    expect(skyIcon("Rain showers")).toBe("rain");
    expect(skyIcon("Partly cloudy")).toBe("partly");
    expect(skyIcon("Mostly Cloudy")).toBe("cloud");
    expect(skyIcon("Clear", "20:20")).toBe("moon");
    expect(skyIcon("Mostly Sunny", "13:00")).toBe("sun");
  });
});

describe("receptions and moneyline labels", () => {
  it("shows receptions with one decimal and catches as the unit", () => {
    expect(fmtProj("recs", 4.26)).toBe("4.3");
    expect(fmtProj("rec", 54.6)).toBe("55");
    expect(fmtSd("recs", 1.84)).toBe("±1.8");
    expect(unit("recs")).toBe("rec");
    expect(unit("rush")).toBe("yds");
  });
  it("names the moneyline market alongside the player props", () => {
    expect(marketLabel("ml")).toBe("Moneyline");
    expect(marketLabel("recs")).toBe("Receptions");
  });
});

describe("first TD scorer and longest rush formatting", () => {
  it("shows first TD like anytime TD: a chance, First TD / Not first TD, edges in points", () => {
    expect(fmtProj("ftd", 0.118)).toBe("12%");
    expect(playLabel("ftd", "Over", 0.5)).toBe("First TD");
    expect(playLabel("ftd", "Under", 0.5)).toBe("Not first TD");
    expect(fmtEdge("ftd", 1.24)).toBe("+1.2 pts");
  });
  it("shows longest rush in yards", () => {
    expect(fmtProj("rlng", 17.36)).toBe("17");
    expect(playLabel("rlng", "Over", 15.5)).toBe("Over 15.5");
  });
});

describe("easternToday", () => {
  it("uses the Eastern date, not UTC's", () => {
    expect(easternToday(new Date("2026-10-22T02:30:00Z"))).toBe("2026-10-21");   // 10:30 PM EDT
    expect(easternToday(new Date("2026-10-22T05:00:00Z"))).toBe("2026-10-22");
  });
});

describe("slatePhrase", () => {
  it("names a single day's games", () => {
    expect(slatePhrase(["2026-11-21", "2026-11-21"], "2026-11-20")).toBe("Saturday's 2 games");
    expect(slatePhrase(["2026-11-21"], "2026-11-20")).toBe("Saturday's game");
    expect(slatePhrase(["2026-11-20", "2026-11-20", "2026-11-20"], "2026-11-20")).toBe("Today's 3 games");
  });
  it("falls back to a count across dates or with none", () => {
    expect(slatePhrase(["2026-11-20", "2026-11-21"], "2026-11-20")).toBe("2 games");
    expect(slatePhrase([], "2026-11-20")).toBe("0 games");
  });
});
