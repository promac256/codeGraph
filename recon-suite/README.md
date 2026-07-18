# recon-suite

A UiPath-native financial **reconciliation platform** — a multi-tenant SaaS that reconciles
*anything to anything* (bank ↔ books, processor ↔ bank ↔ orders, subledger ↔ GL, entity ↔
entity) on one domain-agnostic matching engine.

> Full product + architecture blueprint: `../.claude/plans/i-want-to-build-zippy-hennessy.md`.

## Architecture at a glance — pragmatic UiPath-native hybrid

UiPath is the **deployment + capability fabric**; a custom TypeScript engine + your own
managed Postgres do the **heavy deterministic core**. This split is forced by two facts:
UiPath **Coded Apps** host frontend static bundles only (no long-lived Node server), and
UiPath **Data Service** caps at ~1k rows/query — unusable for millions of matching rows.

| Layer | Tech | Notes |
|---|---|---|
| Frontend | React + Vite + TS → **UiPath Coded App** | `*.uipath.host`, in-app multi-tenancy |
| Backend API | Node 18+ + TS (NestJS/Fastify) | your infra, registered as a UiPath **External Application** |
| Shared types | **Zod** | one source of truth across frontend + backend |
| Data + match compute | **PostgreSQL 16** | `numeric`, `pg_trgm`, RLS, `SKIP LOCKED` |
| Money | **decimal.js** | zero floats in the money path |
| UiPath access | `@uipath/uipath-typescript` + DU REST | Orchestrator, Action Center, Data Service, Agents |
| Ingestion | Document Understanding · Integration Service · RPA robots | via the UiPath fabric |
| Human tasks / AI | Action Center · Agents/Autopilot | advisory only — never the match decision |

## Packages

- **`packages/shared`** — canonical transaction model, `Money` value object, currency/FX,
  and the versioned declarative **ruleset** schema (all Zod). Framework-free; shared by
  frontend + backend.
- **`packages/backend`** — the matching **engine** (deterministic pipeline + SQL blocking),
  the **UiPath** External-App client (OAuth token cache + Orchestrator/Action Center helpers),
  and the core DB migration (`drizzle/0001_core.sql`: items, hyperedge matches, hash-chained
  audit, tenancy + RLS).
- **`apps/web`** — the React/Vite **Coded App** (to be scaffolded via UiPath Studio Web).

## Develop

```bash
pnpm install
pnpm -r typecheck
pnpm -r test
```

## What's built so far (Phase 0 spine)

- ✅ `Money` value object on decimal.js — rejects JS `number`, ROUND_HALF_EVEN, per-currency scale.
- ✅ Canonical `ReconItem` + hyperedge `MatchGroup`/`MatchMember` (Zod).
- ✅ Versioned declarative **ruleset** schema (keys, tolerances, signals, thresholds, N:M grouping).
- ✅ Core DB migration: tenancy + **RLS**, exact-decimal money, append-only **hash-chained audit**,
  blocking indexes (`pg_trgm`, amount/exact-key), immutable rulesets + runs.
- ✅ Deterministic scoring pipeline (Σ weight·signal → auto/suggested/open) + SQL blocking builders.
- ✅ UiPath External-App client: OAuth2 client-credentials token cache (1-hr, no refresh) +
  Orchestrator queue / Action Center task helpers.

See the blueprint's roadmap for Phases 1–6 (thin end-to-end slice → scale/N:M → bank rec →
close/SOX → payments → Insights + agents).
