import { describe, it, expect } from "vitest";
import { Decimal } from "decimal.js";
import { Money, moneySchema, registerCurrency } from "../src/index.js";

describe("Money — no floats in the money path", () => {
  it("rejects a JS number amount (the core guardrail)", () => {
    // @ts-expect-error — number is not assignable to MoneyInput; also throws at runtime.
    expect(() => Money.of(0.1, "USD")).toThrow(/does not accept a JS number/);
  });

  it("rejects a number amount smuggled through the Zod schema", () => {
    const parsed = moneySchema.safeParse({ amount: 12.34, currency: "USD" });
    expect(parsed.success).toBe(false);
  });

  it("preserves precision that a float would lose", () => {
    const sum = Money.of("0.1", "USD").add(Money.of("0.2", "USD"));
    expect(sum.toString()).toBe("0.3");
    // sanity: the float version is famously wrong
    expect(0.1 + 0.2).not.toBe(0.3);
  });

  it("round-trips through JSON as a string", () => {
    const m = Money.of("1234.567", "USD");
    const json = m.toJSON();
    expect(json).toEqual({ amount: "1234.567", currency: "USD" });
    expect(typeof json.amount).toBe("string");
    const back = moneySchema.parse(json);
    expect(back.equals(m)).toBe(true);
  });

  it("accepts a Decimal amount", () => {
    const m = Money.of(new Decimal("99.99"), "EUR");
    expect(m.toString()).toBe("99.99");
    expect(m.currency).toBe("EUR");
  });

  it("blocks cross-currency arithmetic", () => {
    expect(() => Money.of("1", "USD").add(Money.of("1", "EUR"))).toThrow(
      /currency mismatch/,
    );
  });

  it("does exact add/subtract/negate/abs", () => {
    const a = Money.of("100.00", "USD");
    const b = Money.of("30.25", "USD");
    expect(a.subtract(b).toString()).toBe("69.75");
    expect(b.negate().toString()).toBe("-30.25");
    expect(b.negate().abs().toString()).toBe("30.25");
  });

  it("compares within an absolute tolerance", () => {
    const a = Money.of("100.00", "USD");
    const b = Money.of("100.01", "USD");
    expect(a.withinAbs(b, "0.02")).toBe(true);
    expect(a.withinAbs(b, "0.005")).toBe(false);
  });

  it("rounds per-currency with ROUND_HALF_EVEN", () => {
    // JPY has 0 minor units
    expect(Money.of("1234.5", "JPY").round().toString()).toBe("1234");
    // banker's rounding: .5 rounds to even
    expect(Money.of("2.5", "JPY").round().toString()).toBe("2");
    expect(Money.of("3.5", "JPY").round().toString()).toBe("4");
    // USD default 2 dp — banker's rounding on the dropped 5 depends on the preceding digit
    expect(Money.of("2.675", "USD").round().toString()).toBe("2.68"); // preceding 7 (odd) -> up
    expect(Money.of("2.665", "USD").round().toString()).toBe("2.66"); // preceding 6 (even) -> stays
    // toString() is canonical minimal form (no trailing zeros) for stable hashing/equality
    expect(Money.of("1.005", "USD").round().toString()).toBe("1"); // 1.00 == 1
  });

  it("supports registering a high-precision (crypto) currency", () => {
    registerCurrency("BTC", 8);
    expect(Money.of("0.123456785", "BTC").round().toString()).toBe("0.12345678");
  });

  it("rejects an invalid currency code", () => {
    expect(() => Money.of("1", "usd")).toThrow();
    expect(() => Money.of("1", "DOLLAR")).toThrow();
  });

  it("rejects a non-numeric amount string", () => {
    expect(() => Money.of("abc", "USD")).toThrow(/not a valid decimal/);
    expect(() => Money.of("", "USD")).toThrow(/empty/);
  });
});
