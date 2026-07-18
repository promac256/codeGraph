/**
 * Candidate generation (blocking) — the whole game for O(n) instead of O(n²).
 *
 * These builders return parameterized SQL that runs *in Postgres* over the run snapshot.
 * The engine never materializes a cross product in Node — it pulls back only candidate
 * pairs/groups that already passed the blocking predicate.
 *
 * Two-sided helpers assume a `recon_item` snapshot filtered to one run scope + period.
 * `$1` = tenantId, `$2` = runScopeId, plus per-builder params documented inline.
 */

export interface Sql {
  text: string;
  params: unknown[];
}

/**
 * Exact-key blocking: candidate pairs come from an indexed equi-join on `match_key_exact`.
 * Uses `recon_item_exact_key_idx`.
 */
export function exactKeyCandidates(
  tenantId: string,
  runScopeId: string,
  sideA: string,
  sideB: string,
): Sql {
  return {
    text: `
      SELECT a.item_id AS a_id, b.item_id AS b_id
      FROM recon_item a
      JOIN recon_item b
        ON a.tenant_id = b.tenant_id
       AND a.run_scope_id = b.run_scope_id
       AND a.match_key_exact = b.match_key_exact
      WHERE a.tenant_id = $1
        AND a.run_scope_id = $2
        AND a.status = 'open' AND b.status = 'open'
        AND a.side_group = $3 AND b.side_group = $4
        AND a.match_key_exact IS NOT NULL
    `,
    params: [tenantId, runScopeId, sideA, sideB],
  };
}

/**
 * Amount+date fuzzy blocking: block on a coarsened amount bucket (rounded base amount)
 * and a date window, then let the SCORE stage apply the fine tolerance. Bucketing turns
 * the quadratic comparison into "pairs within a small bucket".
 *
 * @param amountBucket rounding unit for the base amount, e.g. 1 (whole units)
 * @param windowDays   +/- day window on value_date
 */
export function amountDateCandidates(
  tenantId: string,
  runScopeId: string,
  sideA: string,
  sideB: string,
  amountBucket: number,
  windowDays: number,
): Sql {
  return {
    text: `
      SELECT a.item_id AS a_id, b.item_id AS b_id
      FROM recon_item a
      JOIN recon_item b
        ON a.tenant_id = b.tenant_id
       AND a.run_scope_id = b.run_scope_id
       AND round(a.amount_base / $5) = round(b.amount_base / $5)
       AND b.value_date BETWEEN a.value_date - ($6 || ' days')::interval
                            AND a.value_date + ($6 || ' days')::interval
      WHERE a.tenant_id = $1
        AND a.run_scope_id = $2
        AND a.status = 'open' AND b.status = 'open'
        AND a.side_group = $3 AND b.side_group = $4
    `,
    params: [tenantId, runScopeId, sideA, sideB, amountBucket, windowDays],
  };
}

/**
 * Fuzzy reference blocking within an amount/date block: pg_trgm similarity on norm_ref,
 * gated by the `%` similarity operator so the GIN index prunes at the source. Never run
 * as a global cross join — always combined with an amount/date predicate.
 *
 * @param minSimilarity pg_trgm threshold (set via `SET pg_trgm.similarity_threshold`)
 */
export function refSimilarityCandidates(
  tenantId: string,
  runScopeId: string,
  sideA: string,
  sideB: string,
  windowDays: number,
): Sql {
  return {
    text: `
      SELECT a.item_id AS a_id, b.item_id AS b_id,
             similarity(a.norm_ref, b.norm_ref) AS ref_sim
      FROM recon_item a
      JOIN recon_item b
        ON a.tenant_id = b.tenant_id
       AND a.run_scope_id = b.run_scope_id
       AND a.norm_ref % b.norm_ref
       AND b.value_date BETWEEN a.value_date - ($5 || ' days')::interval
                            AND a.value_date + ($5 || ' days')::interval
      WHERE a.tenant_id = $1
        AND a.run_scope_id = $2
        AND a.status = 'open' AND b.status = 'open'
        AND a.side_group = $3 AND b.side_group = $4
        AND a.norm_ref IS NOT NULL AND b.norm_ref IS NOT NULL
    `,
    params: [tenantId, runScopeId, sideA, sideB, windowDays],
  };
}

/**
 * N:1 / N:M candidate groups: aggregate the "many" side within a partition key and
 * compare the sum against the "one" side. Each partition is solved independently, which
 * bounds the combinatorial resolution to a small graph per partition.
 *
 * @param partitionExpr a whitelisted column/expression on the many side, e.g.
 *                       "attrs->>'deposit_batch_ref'"
 */
export function aggregatedGroupCandidates(
  tenantId: string,
  runScopeId: string,
  manySide: string,
  oneSide: string,
  partitionExpr: string,
  amountAbsTolerance: string,
  windowDays: number,
): Sql {
  // NOTE: partitionExpr is caller-controlled and must come from a validated allow-list,
  // never raw user input (it is interpolated, not parameterized).
  return {
    text: `
      WITH grp AS (
        SELECT ${partitionExpr} AS part_key,
               sum(amount_base) AS agg_amount,
               min(value_date) AS min_date,
               max(value_date) AS max_date,
               array_agg(item_id) AS member_ids
        FROM recon_item
        WHERE tenant_id = $1 AND run_scope_id = $2
          AND status = 'open' AND side_group = $3
          AND ${partitionExpr} IS NOT NULL
        GROUP BY ${partitionExpr}
      )
      SELECT g.part_key, g.agg_amount, g.member_ids, o.item_id AS one_id
      FROM grp g
      JOIN recon_item o
        ON o.tenant_id = $1 AND o.run_scope_id = $2
       AND o.status = 'open' AND o.side_group = $4
       AND abs(o.amount_base - g.agg_amount) <= $5::numeric
       AND o.value_date BETWEEN g.min_date - ($6 || ' days')::interval
                            AND g.max_date + ($6 || ' days')::interval
    `,
    params: [tenantId, runScopeId, manySide, oneSide, amountAbsTolerance, windowDays],
  };
}
