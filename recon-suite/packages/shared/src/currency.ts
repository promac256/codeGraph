/**
 * Currency metadata: ISO 4217 codes and their minor-unit scale.
 *
 * Rounding in the money path is explicit and per-currency. Most currencies use
 * 2 minor units, JPY/KRW use 0, and some (BHD/KWD) use 3. Crypto and high-precision
 * assets can be registered with a larger scale via `registerCurrency`.
 */
import { z } from "zod";

/** ISO 4217 alphabetic currency code (3 uppercase letters). */
export const currencyCodeSchema = z
  .string()
  .regex(/^[A-Z]{3}$/, "currency must be a 3-letter ISO 4217 code");

export type CurrencyCode = z.infer<typeof currencyCodeSchema>;

/** Minor-unit scale (number of fractional digits) per currency. */
const MINOR_UNITS: Record<string, number> = {
  // 0-decimal currencies
  JPY: 0,
  KRW: 0,
  CLP: 0,
  VND: 0,
  // 3-decimal currencies
  BHD: 3,
  KWD: 3,
  OMR: 3,
  TND: 3,
};

const DEFAULT_MINOR_UNITS = 2;

/** The maximum scale we persist (`numeric(38,9)` in Postgres). */
export const MAX_SCALE = 9;

/**
 * Register (or override) a currency's minor-unit scale — e.g. a crypto asset.
 * Scale must be between 0 and MAX_SCALE inclusive.
 */
export function registerCurrency(code: string, minorUnits: number): void {
  const parsed = currencyCodeSchema.safeParse(code);
  if (!parsed.success) throw new Error(`invalid currency code: ${code}`);
  if (!Number.isInteger(minorUnits) || minorUnits < 0 || minorUnits > MAX_SCALE) {
    throw new Error(`minorUnits for ${code} must be an integer in [0, ${MAX_SCALE}]`);
  }
  MINOR_UNITS[code] = minorUnits;
}

/** Minor-unit scale for a currency (defaults to 2 if unregistered). */
export function minorUnits(code: CurrencyCode): number {
  return MINOR_UNITS[code] ?? DEFAULT_MINOR_UNITS;
}
