import { describe, expect, it } from "vitest";
import { chance, fair, meterWidth } from "./promo";

describe("promo formatting", () => {
  it("shows one decimal only under 10%, decided on the rounded value", () => {
    expect(chance(0.144)).toBe("14%");
    expect(chance(0.0998)).toBe("10%");
    expect(chance(0.088)).toBe("8.8%");
  });
  it("prices a chance at break-even", () => {
    expect(fair(0.5)).toBe("+100");
    expect(fair(0.1)).toBe("+900");
  });
  it("fills meters against the leader", () => {
    expect(meterWidth(0.34, 0.34)).toBe("100%");
    expect(meterWidth(0.085, 0.34)).toBe("25%");
    expect(meterWidth(0.1, 0)).toBe("0%");
  });
});
