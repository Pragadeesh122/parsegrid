# Foundation: Testing, Security Hardening, and CI

**Date:** 2026-06-10
**Status:** Implemented
**Sub-project:** 0 of 4 (OS-release roadmap: Foundation → Dataset Consolidation → Export → NL Query)

## Context

ParseGrid is heading toward a hybrid open-core model: an open-source self-hosted
edition first, followed by a subscription HIPAA-compliant hosted edition. This
sub-project makes the existing codebase trustworthy enough to be public and to
build the consolidation feature on: a real test suite over the untested core
services, fixes for four concrete security findings, and CI that enforces both.

No new product features. No refactors beyond what testing forces.

**Success criteria:** a stranger can clone the repo, run one command, and see a
green test suite; none of the four security findings below survive; CI blocks
regressions on every push to `main`.

## Roadmap context (decomposition decided 2026-06-10)

| # | Sub-project | Status |
|---|------------|--------|
| 0 | Foundation (this spec) | Approved |
| 1 | Dataset consolidation — multi-file jobs + append-to-locked-schema with compatibility diffing | Not yet designed |
| 2 | Export & download (CSV/JSON/SQL dump) | Not yet designed |
| 3 | NL query layer over provisioned outputs | Not yet designed |

Sequencing rationale: consolidation reshapes the Job data model, so it precedes
export/query which consume job shape; tests and security land before that code
churns. Deferred to the HIPAA edition: rate limiting, encryption of stored
connection strings, audit logging, JWT audience/issuer claims.

## 1. Testing design

All backend, pytest, extending the existing `apps/api/tests/` convention
(currently 6 tests covering the Neo4j and Qdrant output providers). Three tiers:

### Tier 1 — pure-logic unit tests (the bulk)

No DB, no network. The only test double is a fake `openai.OpenAI` client
installed via monkeypatch (`entity_resolution` constructs its client inline
rather than going through the provider factory) — no OpenAI calls in tests,
ever.

- **`services/reconciliation.py`**
  - `normalize_value`: every type coercion — booleans (`"yes"/"n"/"1"`),
    currency and thousands-separator strings (incl. unicode minus), dates via
    dateutil, whitespace/unicode normalization, null-ish values.
  - `needs_resolution` pre-check: fires for any table with two or more rows;
    single-row tables skip the LLM entirely (this gate decides whether the LLM
    is called at all).
  - Entity resolution merge behavior against the fake LLM.
  - `resolve_foreign_keys`: token-set fuzzy matching, incl. near-miss and
    no-match cases.
  - `canonicalize_parents`: composite-natural-key dedupe and merge.
  - `add_provenance`: source_page_numbers / notes population.
- **`services/profiling.py`**: sampling determinism (first-3 / last-2 /
  region-diversity / evenly-spaced fillers), the 15-page cap, region-type
  histogram correctness — all on synthetic OCR JSON fixtures.
- **`services/ddl.py`**: DDL generation from multi-table `DatabaseModel` —
  FK dependency ordering, type mapping, identifier quoting.

### Tier 2 — security-critical units

- **`core/security.py`**: expired token, wrong signature, wrong algorithm,
  missing/empty `sub`, missing `exp` (after Fix 3).
- **`providers/ocr_paddle.py`** page-classification signals: empty text,
  alphanumeric-ratio gibberish detection, image-dominant detection.

### Tier 3 — thin API integration tests

httpx `AsyncClient` against the FastAPI app with a real Postgres (local: the
docker-compose pgvector container; CI: a service container).

- Auth boundary: every job endpoint returns 404/403 for another user's job_id.
- Happy-path job CRUD: create → list → get → delete.
- `/connections/test` blocklist behavior (after Fix 1).

**Explicitly out of scope:** full pipeline e2e (Celery + PaddleOCR + LLM) —
brittle and slow; pipeline logic is covered by Tier 1. Frontend component
tests — deferred until the consolidation UI gives them something new to
protect; CI runs a build/typecheck gate instead.

## 2. Security hardening design

Four findings from the 2026-06-10 code scan, with fixes scoped for a
self-hosted edition where `localhost` is a *legitimate* provisioning target
(so: no naive private-IP blocking).

### Fix 1: `/connections/test` containment

The endpoint keeps accepting arbitrary DSNs — that is the product — with three
guards:

1. **Infrastructure blocklist** (configurable, via Settings): refuse DSNs whose
   host/port match ParseGrid's own metadata Postgres, Redis, or MinIO endpoints
   (parsed from settings). Prevents using "test connection" to probe or
   credential-stuff the internal infrastructure.
2. **Connect timeout** (5s default, configurable) on all provider `test_connection` implementations.
3. **Sanitized responses**: clients receive classified messages ("could not
   reach host", "authentication failed", "unsupported format"); raw exceptions
   go to server logs only. The same sanitation applies to `error_message`
   written on failed jobs.

### Fix 2: Production fail-fast for secrets

`Settings` gains a startup validator: when `fastapi_env=production`, the app
refuses to boot if `auth_secret` equals the shipped dev default or MinIO
credentials are still `minioadmin/minioadmin`. Development experience is
unchanged.

### Fix 3: JWT tightening

Keep HS256 pinned (already done). Add `options={"require": ["exp", "sub"]}` to
`jwt.decode` and reject empty-`sub` tokens explicitly. Audience/issuer claims
deferred to the HIPAA edition (Auth.js does not emit them today; changing token
shape touches the frontend for marginal gain).

### Fix 4: Upload constraints

Uploads use presigned **PUT** (not POST policies), and the frontend currently
uses the `/upload/direct` path. Enforcement: the direct path checks actual
byte length and content type server-side; the presigned path gains a required
`file_size` parameter validated against the cap, with `ContentLength` included
in the signed parameters so the client cannot exceed the declared size.
Defaults: 100 MB cap, content-type allowlist (PDF, PNG, JPEG, TIFF, WebP) —
both configurable. Magic-byte validation is out of scope — OCR already fails
gracefully on garbage files.

## 3. CI design

One GitHub Actions workflow (`.github/workflows/ci.yml`), three parallel jobs
on push/PR to `main`:

| Job | Steps | Blocking |
|-----|-------|----------|
| `api` | `uv sync` → `ruff check` + `ruff format --check` → `pytest` with `pgvector/pgvector:pg16` service container | yes |
| `web` | `pnpm install` → `next build` (typecheck gate) | yes |
| `audit` | `pip-audit` + `pnpm audit` | no — informational |

No deploy stage, no Docker image publishing — release tooling is a separate
later decision.

## Error handling notes

- Sanitized error classes (Fix 1) are produced by a single helper so job-failure
  messages and connection-test responses stay consistent.
- Settings validation errors (Fix 2) must name the offending variable in the
  startup exception — fail loud, fail clear.

## Decisions log

- LLM is always faked in tests; no network in the suite.
- Tier 3 uses real Postgres, not SQLite — the app uses Postgres-specific JSON
  columns, enums, and pgvector.
- Private-IP blocking rejected for connection tests: self-hosters legitimately
  provision to localhost. Blocklist targets only ParseGrid's own services.
- Frontend testing deferred; build gate only.
- Rate limiting deferred to HIPAA edition (edge concern).
