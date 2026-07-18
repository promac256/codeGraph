-- 0001_core.sql — the platform spine.
--
-- Gets the non-negotiables right up front (impossible to retrofit):
--   * multi-tenancy with Postgres Row-Level Security (RLS)
--   * exact-decimal money (numeric(38,9)) — never float
--   * append-only, hash-chained audit trail
--   * the canonical item + hyperedge match model
--   * versioned, immutable rulesets and runs
--
-- Tenant context: every connection sets `SET app.tenant_id = '<uuid>'` immediately after
-- deriving the tenant from the authenticated principal. RLS policies below make
-- cross-tenant reads impossible even with a buggy query.

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS pg_trgm;     -- fuzzy reference matching (blocking pushdown)

-- Helper: the current tenant from the session GUC (NULL if unset).
CREATE OR REPLACE FUNCTION app_current_tenant() RETURNS uuid
  LANGUAGE sql STABLE AS $$
  SELECT NULLIF(current_setting('app.tenant_id', true), '')::uuid
$$;

-- ---------------------------------------------------------------------------
-- Tenancy
-- ---------------------------------------------------------------------------
CREATE TABLE tenant (
  tenant_id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name        text NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Reconciliation definition + versioned rulesets
-- ---------------------------------------------------------------------------
CREATE TABLE reconciliation_definition (
  definition_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenant(tenant_id),
  name          text NOT NULL,
  sides         text[] NOT NULL,           -- e.g. {bank,gl}
  base_currency char(3) NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now()
);

-- Rulesets are append-only: a new version is a new row. Runs pin (ruleset_id, version).
CREATE TABLE ruleset (
  ruleset_id    text NOT NULL,
  version       integer NOT NULL,
  tenant_id     uuid NOT NULL REFERENCES tenant(tenant_id),
  definition_id uuid NOT NULL REFERENCES reconciliation_definition(definition_id),
  body          jsonb NOT NULL,            -- validated against @recon/shared ruleSetSchema on write
  created_at    timestamptz NOT NULL DEFAULT now(),
  created_by    text NOT NULL,
  PRIMARY KEY (ruleset_id, version)
);

-- ---------------------------------------------------------------------------
-- Ingestion lineage: immutable raw landing -> staging -> canonical
-- ---------------------------------------------------------------------------
CREATE TABLE ingest_batch (
  ingest_batch_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenant(tenant_id),
  source_system   text NOT NULL,           -- 'du:bank-statements' | 'integration:netsuite' | 'rpa:...' | 'upload'
  source_ref      text,                    -- filename / connector run id / robot job id
  raw_uri         text,                    -- pointer to immutable raw copy (object storage)
  row_count       integer,
  content_hash    bytea,                   -- dedupe an identical re-ingest
  created_at      timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Canonical item — the unit the matching engine operates on
-- ---------------------------------------------------------------------------
CREATE TYPE item_status AS ENUM ('open', 'matched', 'archived');

CREATE TABLE recon_item (
  item_id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id          uuid NOT NULL REFERENCES tenant(tenant_id),
  run_scope_id       uuid NOT NULL REFERENCES reconciliation_definition(definition_id),
  side_group         text NOT NULL,

  posted_date        date NOT NULL,
  value_date         date NOT NULL,

  amount             numeric(38,9) NOT NULL,   -- signed, native currency. NEVER float.
  currency           char(3) NOT NULL,
  amount_base        numeric(38,9) NOT NULL,   -- reporting currency (FX applied)
  fx_rate_id         uuid,                     -- versioned FX row used (null when native == base)

  match_key_exact    text,
  match_key_blocks   text[] NOT NULL DEFAULT '{}',
  norm_ref           text,

  attrs              jsonb NOT NULL DEFAULT '{}',

  status             item_status NOT NULL DEFAULT 'open',
  carry_period_id    uuid,                     -- period this item first became open (aging)

  -- lineage
  source_system      text NOT NULL,
  ingest_batch_id    uuid NOT NULL REFERENCES ingest_batch(ingest_batch_id),
  external_id        text NOT NULL,
  row_hash           bytea NOT NULL,
  normalizer_version text NOT NULL,

  created_at         timestamptz NOT NULL DEFAULT now(),

  -- idempotency: re-ingesting the same source row is a no-op
  UNIQUE (tenant_id, source_system, external_id, row_hash)
);

-- Blocking / candidate-generation indexes (see engine/blocking.sql.ts).
CREATE INDEX recon_item_scope_open_idx
  ON recon_item (tenant_id, run_scope_id, side_group, status);
CREATE INDEX recon_item_exact_key_idx
  ON recon_item (tenant_id, run_scope_id, match_key_exact) WHERE match_key_exact IS NOT NULL;
CREATE INDEX recon_item_amount_base_idx
  ON recon_item (tenant_id, run_scope_id, amount_base);
CREATE INDEX recon_item_norm_ref_trgm_idx
  ON recon_item USING gin (norm_ref gin_trgm_ops);
CREATE INDEX recon_item_blocks_idx
  ON recon_item USING gin (match_key_blocks);

-- ---------------------------------------------------------------------------
-- Engine runs (immutable, reproducible)
-- ---------------------------------------------------------------------------
CREATE TABLE recon_run (
  run_id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id        uuid NOT NULL REFERENCES tenant(tenant_id),
  definition_id    uuid NOT NULL REFERENCES reconciliation_definition(definition_id),
  ruleset_id       text NOT NULL,
  ruleset_version  integer NOT NULL,
  period_id        uuid,
  snapshot_ts      timestamptz NOT NULL,
  -- deterministic fingerprint of the frozen candidate set (item_ids + row_hashes)
  input_manifest_hash bytea NOT NULL,
  -- deterministic fingerprint of committed match_groups (equal => identical re-run)
  output_hash      bytea,
  summary          jsonb,                   -- counts, match rate, $ reconciled
  created_at       timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (ruleset_id, ruleset_version) REFERENCES ruleset(ruleset_id, version)
);

-- ---------------------------------------------------------------------------
-- Matches: a match is a hyperedge over items (1:1 .. N:M, one code path)
-- ---------------------------------------------------------------------------
CREATE TYPE match_cardinality AS ENUM ('1:1', '1:N', 'N:1', 'N:M');
CREATE TYPE match_status AS ENUM ('auto', 'suggested', 'confirmed', 'rejected', 'broken');
CREATE TYPE residual_reason AS ENUM ('fee', 'fx', 'rounding', 'unexplained');

CREATE TABLE match_group (
  match_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenant(tenant_id),
  run_id          uuid NOT NULL REFERENCES recon_run(run_id),
  rule_id         text NOT NULL,
  cardinality     match_cardinality NOT NULL,
  status          match_status NOT NULL,
  confidence      numeric(5,4) NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
  score_breakdown jsonb NOT NULL DEFAULT '{}',   -- explainability: per-signal contributions
  residual_amount numeric(38,9) NOT NULL DEFAULT 0,
  residual_reason residual_reason,
  created_at      timestamptz NOT NULL DEFAULT now(),
  created_by      text NOT NULL DEFAULT 'system',
  -- matches are superseded, never mutated
  superseded_by   uuid REFERENCES match_group(match_id),
  -- maker-checker: a confirmed match records both principals; they must differ
  approved_by     text,
  CHECK (approved_by IS NULL OR approved_by <> created_by)
);
CREATE INDEX match_group_run_idx ON match_group (tenant_id, run_id, status);

CREATE TABLE match_member (
  match_id   uuid NOT NULL REFERENCES match_group(match_id) ON DELETE CASCADE,
  item_id    uuid NOT NULL REFERENCES recon_item(item_id),
  side_group text NOT NULL,
  weight     numeric(38,9) NOT NULL DEFAULT 1,   -- partial allocation
  PRIMARY KEY (match_id, item_id)
);
CREATE INDEX match_member_item_idx ON match_member (item_id);

-- An item may belong to at most one *active* (non-superseded) match. Enforced in the
-- engine's COMMIT stage; a partial unique index guards the common case.
CREATE UNIQUE INDEX match_member_active_item_idx
  ON match_member (item_id)
  WHERE weight = 1;

-- ---------------------------------------------------------------------------
-- Audit — append-only, hash-chained per tenant. This IS the SOX evidence engine.
-- ---------------------------------------------------------------------------
CREATE TABLE audit_event (
  event_id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id       uuid NOT NULL REFERENCES tenant(tenant_id),
  actor           text NOT NULL,            -- user email / robot identity / 'system'
  actor_role      text,
  action          text NOT NULL,            -- 'match.confirm', 'ingest.batch', 'ruleset.publish', ...
  entity_type     text NOT NULL,
  entity_id       text NOT NULL,
  before_hash     bytea,
  after_hash      bytea,
  payload         jsonb NOT NULL DEFAULT '{}',
  request_id      text,
  ruleset_version integer,
  occurred_at     timestamptz NOT NULL DEFAULT now(),
  -- tamper-evidence: each event chains to the prior event's hash for the tenant
  prev_event_hash bytea,
  event_hash      bytea NOT NULL
);
CREATE INDEX audit_event_tenant_idx ON audit_event (tenant_id, event_id);

REVOKE UPDATE, DELETE ON audit_event FROM PUBLIC;

-- ---------------------------------------------------------------------------
-- Row-Level Security: enable + FORCE on every tenant-scoped table
-- ---------------------------------------------------------------------------
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'reconciliation_definition','ruleset','ingest_batch','recon_item',
    'recon_run','match_group','match_member','audit_event'
  ] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
  END LOOP;
END $$;

-- match_member has no tenant_id column of its own; scope it via its parent match_group.
CREATE POLICY tenant_isolation ON reconciliation_definition
  USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());
CREATE POLICY tenant_isolation ON ruleset
  USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());
CREATE POLICY tenant_isolation ON ingest_batch
  USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());
CREATE POLICY tenant_isolation ON recon_item
  USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());
CREATE POLICY tenant_isolation ON recon_run
  USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());
CREATE POLICY tenant_isolation ON match_group
  USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());
CREATE POLICY tenant_isolation ON audit_event
  USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());
CREATE POLICY tenant_isolation ON match_member
  USING (EXISTS (
    SELECT 1 FROM match_group g
    WHERE g.match_id = match_member.match_id AND g.tenant_id = app_current_tenant()
  ))
  WITH CHECK (EXISTS (
    SELECT 1 FROM match_group g
    WHERE g.match_id = match_member.match_id AND g.tenant_id = app_current_tenant()
  ));

COMMIT;
