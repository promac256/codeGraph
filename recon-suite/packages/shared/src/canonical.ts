/**
 * The canonical transaction model.
 *
 * Every source (bank file, DU extraction, Integration Service pull, RPA drop, manual
 * upload) normalizes into `ReconItem`. Nothing downstream of normalization is
 * domain-specific — the matching engine only ever sees `ReconItem`s. Domain fields that
 * the engine core doesn't use live in `attrs`.
 */
import { z } from "zod";
import { currencyCodeSchema } from "./currency.js";

/** ISO calendar date, `YYYY-MM-DD`. */
export const isoDateSchema = z
  .string()
  .regex(/^\d{4}-\d{2}-\d{2}$/, "date must be YYYY-MM-DD");

/** Decimal amount as a string (never a JS number — see Money). */
export const decimalStringSchema = z
  .string()
  .regex(/^-?\d+(\.\d+)?$/, "amount must be a decimal string, e.g. '1234.56'");

/** A side of a reconciliation — supports >2 sides (bank / gl / processor / ...). */
export const sideGroupSchema = z.string().min(1).max(64);

export const itemStatusSchema = z.enum(["open", "matched", "archived"]);
export type ItemStatus = z.infer<typeof itemStatusSchema>;

/**
 * Lineage back to the immutable raw copy — how we answer "where did this row come from?"
 * and replay deterministically.
 */
export const lineageSchema = z.object({
  sourceSystem: z.string(),
  ingestBatchId: z.string().uuid(),
  /** Stable id from the source system; unique within (tenant, source). */
  externalId: z.string(),
  /** Hash of the normalized fields — dedupe + idempotency key. */
  rowHash: z.string(),
  /** Version of the normalizer that produced this item (drift detection). */
  normalizerVersion: z.string(),
});
export type Lineage = z.infer<typeof lineageSchema>;

export const reconItemSchema = z.object({
  itemId: z.string().uuid(),
  tenantId: z.string().uuid(),
  /** Which reconciliation definition this item belongs to. */
  runScopeId: z.string().uuid(),
  sideGroup: sideGroupSchema,

  postedDate: isoDateSchema,
  valueDate: isoDateSchema,

  /** Signed native amount + its currency. */
  amount: decimalStringSchema,
  currency: currencyCodeSchema,
  /** Amount converted to the reporting/base currency (FX applied). */
  amountBase: decimalStringSchema,
  /** The FX rate row used to derive amountBase (versioned; null when native == base). */
  fxRateId: z.string().uuid().nullable(),

  /** Precomputed canonical key for exact-match blocking. */
  matchKeyExact: z.string().nullable(),
  /** Precomputed coarse blocking keys (amount/date buckets etc.). */
  matchKeyBlocks: z.array(z.string()).default([]),
  /** Normalized reference/description for fuzzy (pg_trgm) matching. */
  normRef: z.string().nullable(),

  /** Source-specific fields the engine core does not use. */
  attrs: z.record(z.unknown()).default({}),

  status: itemStatusSchema.default("open"),
  /** Period in which this item first became open — drives carry-forward + aging. */
  carryPeriodId: z.string().uuid().nullable(),

  lineage: lineageSchema,
});

export type ReconItem = z.infer<typeof reconItemSchema>;

// --- Matches (a match is a hyperedge over items) ---

export const cardinalitySchema = z.enum(["1:1", "1:N", "N:1", "N:M"]);
export type Cardinality = z.infer<typeof cardinalitySchema>;

export const matchStatusSchema = z.enum([
  "auto",
  "suggested",
  "confirmed",
  "rejected",
  "broken",
]);
export type MatchStatus = z.infer<typeof matchStatusSchema>;

export const residualReasonSchema = z.enum(["fee", "fx", "rounding", "unexplained"]);
export type ResidualReason = z.infer<typeof residualReasonSchema>;

export const matchMemberSchema = z.object({
  itemId: z.string().uuid(),
  sideGroup: sideGroupSchema,
  /** Allocation weight — models partial matches (one item split across groups). */
  weight: decimalStringSchema.default("1"),
});
export type MatchMember = z.infer<typeof matchMemberSchema>;

export const matchGroupSchema = z.object({
  matchId: z.string().uuid(),
  tenantId: z.string().uuid(),
  runId: z.string().uuid(),
  ruleId: z.string(),
  cardinality: cardinalitySchema,
  status: matchStatusSchema,
  confidence: z.number().min(0).max(1),
  /** Per-signal contributions — the "why did this match?" record. */
  scoreBreakdown: z.record(z.number()).default({}),
  residualAmount: decimalStringSchema,
  residualReason: residualReasonSchema.nullable(),
  members: z.array(matchMemberSchema).min(2),
  supersededBy: z.string().uuid().nullable().default(null),
});
export type MatchGroup = z.infer<typeof matchGroupSchema>;
