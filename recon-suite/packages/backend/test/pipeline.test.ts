import { describe, it, expect } from "vitest";
import type { Rule } from "@recon/shared";
import { scoreCandidate, decide, scoreAndDecide } from "../src/engine/pipeline.js";

const rule: Rule = {
  id: "r2_amount_date_fuzzyref",
  priority: 20,
  cardinality: "1:1",
  keys: [],
  tolerances: { amount: { abs: "0.02" }, date: { field: "value_date", windowDays: 3 } },
  score: [
    { signal: "amount_within_tol", weight: 0.35 },
    { signal: "date_proximity", weight: 0.15 },
    { signal: "ref_trigram_sim", weight: 0.5 },
  ],
  autoMatchThreshold: 0.9,
  suggestThreshold: 0.65,
};

describe("engine scoring — deterministic confidence", () => {
  it("computes Σ(weight·signal) and a per-signal breakdown", () => {
    const { confidence, breakdown } = scoreCandidate(rule, {
      amount_within_tol: 1,
      date_proximity: 1,
      ref_trigram_sim: 1,
    });
    expect(confidence).toBe(1);
    expect(breakdown).toEqual({
      amount_within_tol: 0.35,
      date_proximity: 0.15,
      ref_trigram_sim: 0.5,
    });
  });

  it("treats a missing signal as zero contribution", () => {
    const { confidence } = scoreCandidate(rule, { amount_within_tol: 1, date_proximity: 1 });
    expect(confidence).toBeCloseTo(0.5, 6);
  });

  it("is deterministic — identical inputs give identical output", () => {
    const s = { amount_within_tol: 1, date_proximity: 0.5, ref_trigram_sim: 0.97 };
    expect(scoreCandidate(rule, s)).toEqual(scoreCandidate(rule, s));
  });

  it("maps confidence to auto / suggested / open via the two thresholds", () => {
    expect(decide(rule, 0.95)).toBe("auto");
    expect(decide(rule, 0.7)).toBe("suggested");
    expect(decide(rule, 0.4)).toBe("open");
    // boundaries are inclusive
    expect(decide(rule, 0.9)).toBe("auto");
    expect(decide(rule, 0.65)).toBe("suggested");
  });

  it("scoreAndDecide carries the breakdown and decision", () => {
    const out = scoreAndDecide(rule, {
      members: [
        { itemId: "a", sideGroup: "bank" },
        { itemId: "b", sideGroup: "gl" },
      ],
      signals: { amount_within_tol: 1, date_proximity: 1, ref_trigram_sim: 0.95 },
      residualAmount: "0.00",
    });
    expect(out.decision).toBe("auto");
    expect(out.confidence).toBeCloseTo(0.975, 6);
    expect(out.scoreBreakdown.ref_trigram_sim).toBeCloseTo(0.475, 6);
  });
});
