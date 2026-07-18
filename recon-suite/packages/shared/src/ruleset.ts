/**
 * The declarative rules model.
 *
 * A RuleSet is versioned and attached to a ReconciliationDefinition. Rules run in
 * priority order; the first rule to claim an item removes it from the pool for later
 * rules (greedy, deterministic). This schema is the authoring + persistence contract —
 * validate on write, pin the version on every run.
 */
import { z } from "zod";
import { cardinalitySchema } from "./canonical.js";

/**
 * Named scoring signals — a fixed, extensible library of pure [0,1] functions.
 * Confidence = Σ(weight · signal).
 */
export const signalNameSchema = z.enum([
  "amount_exact",
  "amount_within_tol",
  "date_proximity",
  "ref_trigram_sim",
  "ref_contains",
  "party_match",
]);
export type SignalName = z.infer<typeof signalNameSchema>;

export const amountToleranceSchema = z
  .object({
    /** Absolute tolerance as a decimal string, e.g. "0.02". */
    abs: z.string().regex(/^\d+(\.\d+)?$/).optional(),
    /** Fractional tolerance, e.g. 0.001 for 0.1%. */
    pct: z.number().min(0).max(1).optional(),
  })
  .refine((t) => t.abs !== undefined || t.pct !== undefined, {
    message: "amount tolerance needs at least one of abs / pct",
  });

export const dateToleranceSchema = z
  .object({
    field: z.enum(["value_date", "posted_date"]).default("value_date"),
    /** Symmetric window in days (used when before/after are omitted). */
    windowDays: z.number().int().min(0).optional(),
    /** Asymmetric windows for settlement lag. */
    windowDaysBefore: z.number().int().min(0).optional(),
    windowDaysAfter: z.number().int().min(0).optional(),
  })
  .refine(
    (d) =>
      d.windowDays !== undefined ||
      d.windowDaysBefore !== undefined ||
      d.windowDaysAfter !== undefined,
    { message: "date tolerance needs windowDays or windowDaysBefore/After" },
  );

export const signalWeightSchema = z.object({
  signal: signalNameSchema,
  weight: z.number().min(0).max(1),
});

/** Grouping/aggregation for N:1 / N:M rules. */
export const groupBySchema = z.object({
  /** Which side is aggregated (the "many" side). */
  manySide: z.string().min(1),
  /** Aggregation applied to the many side. */
  aggregate: z.enum(["sum"]).default("sum"),
  /** Partition keys that form candidate groups (bounds the N:M search). */
  partition: z.array(z.string().min(1)).min(1),
});

export const ruleSchema = z
  .object({
    id: z.string().min(1),
    priority: z.number().int(),
    cardinality: cardinalitySchema,
    /** Equality predicates — the ONLY things eligible for blocking/candidate generation. */
    keys: z.array(z.string().min(1)).default([]),
    tolerances: z
      .object({
        amount: amountToleranceSchema.optional(),
        date: dateToleranceSchema.optional(),
      })
      .default({}),
    /** Required for N:1 / N:M. */
    groupBy: groupBySchema.optional(),
    score: z.array(signalWeightSchema).min(1),
    /** confidence >= autoMatchThreshold -> auto; >= suggestThreshold -> suggested. */
    autoMatchThreshold: z.number().min(0).max(1),
    suggestThreshold: z.number().min(0).max(1),
  })
  .superRefine((rule, ctx) => {
    // Signal weights must sum to ~1.0 so confidence is a bounded [0,1] score.
    const sum = rule.score.reduce((acc, s) => acc + s.weight, 0);
    if (Math.abs(sum - 1) > 1e-6) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["score"],
        message: `signal weights must sum to 1.0 (got ${sum})`,
      });
    }
    if (rule.autoMatchThreshold < rule.suggestThreshold) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["autoMatchThreshold"],
        message: "autoMatchThreshold must be >= suggestThreshold",
      });
    }
    if ((rule.cardinality === "N:1" || rule.cardinality === "N:M") && !rule.groupBy) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["groupBy"],
        message: `cardinality ${rule.cardinality} requires groupBy (partition + aggregate)`,
      });
    }
  });

export type Rule = z.infer<typeof ruleSchema>;

export const ruleSetSchema = z
  .object({
    id: z.string().min(1),
    /** Monotonic version — new version = new row; runs pin this. */
    version: z.number().int().positive(),
    definitionId: z.string().min(1),
    /** The sides being reconciled, e.g. ["bank", "gl"]. */
    sides: z.array(z.string().min(1)).min(2),
    baseCurrency: z.string().regex(/^[A-Z]{3}$/),
    rules: z.array(ruleSchema).min(1),
  })
  .superRefine((rs, ctx) => {
    const ids = new Set<string>();
    for (const r of rs.rules) {
      if (ids.has(r.id)) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["rules"],
          message: `duplicate rule id: ${r.id}`,
        });
      }
      ids.add(r.id);
    }
  });

export type RuleSet = z.infer<typeof ruleSetSchema>;

/** Parse + validate an untrusted ruleset body (throws on invalid). */
export function parseRuleSet(input: unknown): RuleSet {
  return ruleSetSchema.parse(input);
}
