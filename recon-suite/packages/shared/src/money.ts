/**
 * Money — the money value object for the whole platform.
 *
 * NON-NEGOTIABLE: there are no JS `number`s in the money path. `number` is an IEEE-754
 * float and silently loses precision on values as ordinary as 0.1 + 0.2. Every amount is
 * a decimal.js `Decimal`, constructed only from a string or another Decimal. Passing a
 * `number` throws — this is the guardrail the reconciliation engine's correctness rests on.
 *
 * Amounts are stored/serialized as strings and map to Postgres `numeric(38,9)`; keep
 * node-postgres returning `numeric` as string (its default) so values never round-trip
 * through a float.
 */
import { Decimal } from "decimal.js";
import { z } from "zod";
import {
  type CurrencyCode,
  currencyCodeSchema,
  minorUnits,
} from "./currency.js";

// Bankers' rounding (ROUND_HALF_EVEN) is the single documented policy for the money path.
Decimal.set({ rounding: Decimal.ROUND_HALF_EVEN });

export type MoneyInput = string | Decimal;

function toDecimal(amount: MoneyInput): Decimal {
  if (typeof amount === "number") {
    // Defensive: TS types forbid this, but JSON/`any` boundaries can smuggle a float in.
    throw new TypeError(
      "Money does not accept a JS number (float precision loss). Pass a string or Decimal.",
    );
  }
  if (typeof amount === "string") {
    if (amount.trim() === "") throw new TypeError("Money amount string is empty");
    try {
      return new Decimal(amount);
    } catch {
      throw new TypeError(`Money amount is not a valid decimal: ${amount}`);
    }
  }
  if (amount instanceof Decimal) return amount;
  throw new TypeError("Money amount must be a string or Decimal");
}

export class Money {
  private constructor(
    readonly amount: Decimal,
    readonly currency: CurrencyCode,
  ) {}

  /** Construct Money from a string/Decimal amount + ISO currency code. */
  static of(amount: MoneyInput, currency: string): Money {
    const code = currencyCodeSchema.parse(currency);
    return new Money(toDecimal(amount), code);
  }

  /** Zero in the given currency. */
  static zero(currency: string): Money {
    return Money.of("0", currency);
  }

  private assertSameCurrency(other: Money): void {
    if (this.currency !== other.currency) {
      throw new Error(
        `currency mismatch: ${this.currency} vs ${other.currency} — convert via FX first`,
      );
    }
  }

  add(other: Money): Money {
    this.assertSameCurrency(other);
    return new Money(this.amount.plus(other.amount), this.currency);
  }

  subtract(other: Money): Money {
    this.assertSameCurrency(other);
    return new Money(this.amount.minus(other.amount), this.currency);
  }

  negate(): Money {
    return new Money(this.amount.negated(), this.currency);
  }

  abs(): Money {
    return new Money(this.amount.abs(), this.currency);
  }

  /** Round to the currency's minor-unit scale (ROUND_HALF_EVEN). */
  round(): Money {
    return new Money(this.amount.toDecimalPlaces(minorUnits(this.currency)), this.currency);
  }

  isZero(): boolean {
    return this.amount.isZero();
  }

  isNegative(): boolean {
    return this.amount.isNegative();
  }

  /** -1, 0, or 1 (same-currency comparison). */
  compare(other: Money): number {
    this.assertSameCurrency(other);
    return this.amount.comparedTo(other.amount);
  }

  equals(other: Money): boolean {
    return this.currency === other.currency && this.amount.equals(other.amount);
  }

  /** Absolute difference is within `tolerance` (same currency). */
  withinAbs(other: Money, tolerance: MoneyInput): boolean {
    this.assertSameCurrency(other);
    return this.amount.minus(other.amount).abs().lessThanOrEqualTo(toDecimal(tolerance));
  }

  /** Canonical string form at full precision (for hashing, storage, equality). */
  toString(): string {
    return this.amount.toFixed();
  }

  toJSON(): { amount: string; currency: CurrencyCode } {
    return { amount: this.toString(), currency: this.currency };
  }
}

/**
 * Zod schema for the wire/DB shape of Money: `{ amount: string, currency: CODE }`.
 * A JS `number` amount is rejected — floats never enter the money path.
 */
export const moneySchema = z
  .object({
    amount: z.string({ invalid_type_error: "amount must be a string, never a number" }),
    currency: currencyCodeSchema,
  })
  .transform((v, ctx) => {
    try {
      return Money.of(v.amount, v.currency);
    } catch (e) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, message: (e as Error).message });
      return z.NEVER;
    }
  });
