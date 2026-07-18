/**
 * The matching-engine run pipeline — a deterministic, checkpointed DAG.
 *
 *   SNAPSHOT -> NORMALIZE-CHECK -> (per rule: BLOCK -> GENERATE -> SCORE -> FILTER ->
 *   RESOLVE -> COMMIT) -> RESIDUALS -> FINALIZE
 *
 * Determinism is sacred: same input manifest + same ruleset version => byte-identical
 * output. This module owns the orchestration and the *pure* scoring/decision logic; all
 * set-based candidate generation is pushed to Postgres via an `EngineStore` port (so the
 * engine is unit-testable without a database and the heavy joins stay in the DB).
 */
import type { Rule, RuleSet, SignalName } from "@recon/shared";

/** Raw signal values for one candidate pair/group, each in [0,1]. */
export type SignalValues = Partial<Record<SignalName, number>>;

export interface Candidate {
  /** Item ids on each side (a hyperedge). */
  members: { itemId: string; sideGroup: string; weight?: string }[];
  signals: SignalValues;
  residualAmount: string;
}

export interface ScoredCandidate extends Candidate {
  confidence: number;
  scoreBreakdown: Record<string, number>;
  decision: "auto" | "suggested" | "open";
}

/**
 * Pure, deterministic confidence: Σ(weight · signal). Missing signal => 0 contribution.
 * Weights are validated to sum to 1.0 at ruleset parse time, so confidence ∈ [0,1].
 */
export function scoreCandidate(rule: Rule, signals: SignalValues): {
  confidence: number;
  breakdown: Record<string, number>;
} {
  const breakdown: Record<string, number> = {};
  let confidence = 0;
  for (const { signal, weight } of rule.score) {
    const value = signals[signal] ?? 0;
    const contribution = weight * value;
    breakdown[signal] = contribution;
    confidence += contribution;
  }
  // Guard against fp drift so equal inputs hash equally.
  confidence = Math.min(1, Math.max(0, Number(confidence.toFixed(6))));
  return { confidence, breakdown };
}

/** Apply the rule's two thresholds -> auto / suggested / open. */
export function decide(rule: Rule, confidence: number): ScoredCandidate["decision"] {
  if (confidence >= rule.autoMatchThreshold) return "auto";
  if (confidence >= rule.suggestThreshold) return "suggested";
  return "open";
}

export function scoreAndDecide(rule: Rule, candidate: Candidate): ScoredCandidate {
  const { confidence, breakdown } = scoreCandidate(rule, candidate.signals);
  return {
    ...candidate,
    confidence,
    scoreBreakdown: breakdown,
    decision: decide(rule, confidence),
  };
}

// --- Store port: the DB-facing side the engine delegates set-based work to ---

export interface RunContext {
  tenantId: string;
  runId: string;
  runScopeId: string;
  snapshotTs: string;
}

export interface EngineStore {
  /** Freeze the candidate set as of snapshotTs; return a deterministic manifest hash. */
  snapshot(ctx: RunContext): Promise<{ inputManifestHash: string; openItemCount: number }>;
  /** Generate + score candidates for a rule (blocking pushed to SQL). */
  generateCandidates(ctx: RunContext, rule: Rule): Promise<Candidate[]>;
  /** Persist committed matches and mark their items matched (removed from the pool). */
  commit(ctx: RunContext, rule: Rule, matches: ScoredCandidate[]): Promise<void>;
  /** Write run summary + output hash after all rules. */
  finalize(ctx: RunContext, summary: RunSummary): Promise<void>;
}

export interface RunSummary {
  autoCount: number;
  suggestedCount: number;
  openCount: number;
  matchRate: number;
  outputHash: string;
}

/**
 * Orchestrate a full run. Rules execute in priority order; the store enforces that a
 * committed item leaves the pool so later rules cannot re-claim it (greedy resolution).
 * Actual conflict-free assignment (greedy vs optimal per-partition) lives in the store's
 * `generateCandidates`/`commit` for the N:M case; this loop owns ordering + accounting.
 */
export async function runPipeline(
  ctx: RunContext,
  ruleSet: RuleSet,
  store: EngineStore,
): Promise<RunSummary> {
  await store.snapshot(ctx);

  let autoCount = 0;
  let suggestedCount = 0;

  const rules = [...ruleSet.rules].sort((a, b) => a.priority - b.priority);
  for (const rule of rules) {
    const candidates = await store.generateCandidates(ctx, rule);
    const scored = candidates.map((c) => scoreAndDecide(rule, c));
    const committed = scored.filter((s) => s.decision === "auto" || s.decision === "suggested");
    await store.commit(ctx, rule, committed);
    autoCount += committed.filter((s) => s.decision === "auto").length;
    suggestedCount += committed.filter((s) => s.decision === "suggested").length;
  }

  const { openItemCount } = await store.snapshot(ctx); // remaining open after commits
  const matched = autoCount + suggestedCount;
  const summary: RunSummary = {
    autoCount,
    suggestedCount,
    openCount: openItemCount,
    matchRate: matched + openItemCount === 0 ? 0 : matched / (matched + openItemCount),
    outputHash: "", // store.finalize computes the deterministic content hash
  };
  await store.finalize(ctx, summary);
  return summary;
}
