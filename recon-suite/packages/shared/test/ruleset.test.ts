import { describe, it, expect } from "vitest";
import { parseRuleSet, type RuleSet } from "../src/index.js";

const bankRuleSet: RuleSet = {
  id: "rs_bank_std",
  version: 7,
  definitionId: "def_chase_gl",
  sides: ["bank", "gl"],
  baseCurrency: "USD",
  rules: [
    {
      id: "r1_exact_ref",
      priority: 10,
      cardinality: "1:1",
      keys: ["bank.norm_ref == gl.norm_ref"],
      tolerances: { amount: { abs: "0" }, date: { field: "value_date", windowDays: 0 } },
      score: [
        { signal: "amount_exact", weight: 0.4 },
        { signal: "ref_exact" as never, weight: 0 }, // placeholder replaced below
      ],
      autoMatchThreshold: 1,
      suggestThreshold: 0,
    },
  ],
};

// Fix the placeholder to a real signal set summing to 1.
bankRuleSet.rules[0]!.score = [
  { signal: "amount_exact", weight: 0.4 },
  { signal: "ref_trigram_sim", weight: 0.6 },
];

describe("RuleSet validation", () => {
  it("accepts a well-formed bank ruleset", () => {
    const rs = parseRuleSet(bankRuleSet);
    expect(rs.rules).toHaveLength(1);
    expect(rs.rules[0]!.id).toBe("r1_exact_ref");
  });

  it("rejects signal weights that do not sum to 1.0", () => {
    const bad = structuredClone(bankRuleSet);
    bad.rules[0]!.score = [
      { signal: "amount_exact", weight: 0.4 },
      { signal: "ref_trigram_sim", weight: 0.4 },
    ];
    expect(() => parseRuleSet(bad)).toThrow(/sum to 1\.0/);
  });

  it("rejects autoMatchThreshold below suggestThreshold", () => {
    const bad = structuredClone(bankRuleSet);
    bad.rules[0]!.autoMatchThreshold = 0.5;
    bad.rules[0]!.suggestThreshold = 0.9;
    expect(() => parseRuleSet(bad)).toThrow(/autoMatchThreshold must be >= suggestThreshold/);
  });

  it("requires groupBy for N:1 / N:M cardinality", () => {
    const bad = structuredClone(bankRuleSet);
    bad.rules[0]!.cardinality = "N:1";
    expect(() => parseRuleSet(bad)).toThrow(/requires groupBy/);
  });

  it("accepts an N:1 rule with groupBy", () => {
    const ok = structuredClone(bankRuleSet);
    ok.rules[0]!.cardinality = "N:1";
    ok.rules[0]!.groupBy = {
      manySide: "bank",
      aggregate: "sum",
      partition: ["bank.deposit_batch_ref"],
    };
    expect(() => parseRuleSet(ok)).not.toThrow();
  });

  it("rejects duplicate rule ids", () => {
    const bad = structuredClone(bankRuleSet);
    bad.rules.push(structuredClone(bankRuleSet.rules[0]!));
    expect(() => parseRuleSet(bad)).toThrow(/duplicate rule id/);
  });

  it("rejects an amount tolerance with neither abs nor pct", () => {
    const bad = structuredClone(bankRuleSet);
    bad.rules[0]!.tolerances = { amount: {} };
    expect(() => parseRuleSet(bad)).toThrow();
  });
});
