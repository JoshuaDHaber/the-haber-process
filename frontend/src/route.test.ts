import { describe, expect, it } from "vitest";
import { DEFAULT_FILTERS, filterParams, filtersNarrowed } from "./route";

describe("filterParams", () => {
  it("leaves the visitor's own default book out of the address, so it never pins it", () => {
    expect(filterParams({ ...DEFAULT_FILTERS, book: "DraftKings" }, true, "DraftKings").book).toBeNull();
    expect(filterParams({ ...DEFAULT_FILTERS, book: "BetMGM" }, true, "DraftKings").book).toBe("BetMGM");
    // switching to every book when the default is one book is a real choice, so it's kept
    expect(filterParams({ ...DEFAULT_FILTERS, book: "all" }, true, "DraftKings").book).toBe("all");
    expect(filterParams({ ...DEFAULT_FILTERS, book: "all" }, true).book).toBeNull();
  });
});

describe("filtersNarrowed", () => {
  it("counts filters that hide rows, not settings", () => {
    expect(filtersNarrowed(DEFAULT_FILTERS)).toBe(false);
    expect(filtersNarrowed({ ...DEFAULT_FILTERS, book: "DraftKings", minEv: 0.1, marketWeight: 0.5 }, true)).toBe(false);
    expect(filtersNarrowed({ ...DEFAULT_FILTERS, kind: "rec" })).toBe(true);
    expect(filtersNarrowed({ ...DEFAULT_FILTERS, q: "  " })).toBe(false);
    expect(filtersNarrowed({ ...DEFAULT_FILTERS, q: "kelce" })).toBe(true);
  });
  it("only counts side and flagged on Best props", () => {
    expect(filtersNarrowed({ ...DEFAULT_FILTERS, side: "Over", flagged: true })).toBe(false);
    expect(filtersNarrowed({ ...DEFAULT_FILTERS, side: "Over" }, true)).toBe(true);
    expect(filtersNarrowed({ ...DEFAULT_FILTERS, flagged: true }, true)).toBe(true);
  });
});
