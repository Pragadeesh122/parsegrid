# Dataset Consolidation: Multi-File Jobs + Append-to-Locked-Schema

**Date:** 2026-06-11
**Status:** Approved
**Sub-project:** 1 of 4 (OS-release roadmap: Foundation ✅ → **Dataset Consolidation** → Export → NL Query)

## Context

Today a Job is hard-wired to exactly one file: `filename`/`file_key`/`file_size`
are scalar columns on `Job`, and the pipeline runs once per job into a
freshly created output schema. Users cannot upload several related files as
one dataset, and cannot add more data to a completed dataset later.

This sub-project makes a Job a **dataset**: created from one or more files,
extendable after completion with new files of the same document type, always
provisioned as a single deduplicated output database against the one locked
`DatabaseModel`.

**Scope: FULL jobs only.** TARGETED (RAG) jobs keep their single-file flow;
multi-document TARGETED querying is a separate future feature. Schema
evolution (extending a locked model to fit new columns) is explicitly
deferred to its own future sub-project.

## Decisions log (brainstorm 2026-06-11)

- **Append UX:** auto-run when compatible; pull the user in only on trouble
  (no mandatory review per append).
- **On incompatibility:** user chooses **force** (extract what maps, drops
  itemized in a report) or **cancel** (file rejected, dataset untouched).
  No schema evolution in v1.
- **Multi-file creation:** one unified model discovery across all uploaded
  files (profiling budget spread over all documents), one review, one lock.
- **Rebuild over incremental insert:** every (re)provision drops and
  recreates the output schema from the union of all documents' stored
  extraction buckets. The output DB is a cache; per-document buckets are the
  source of truth. This avoids per-provider incremental-insert machinery,
  makes appends idempotent, and gets cross-file dedupe for free from the
  existing `canonicalize_parents` natural-key logic.

## Architecture: Job = dataset, Document = file

### Chosen approach (vs. alternatives)

**Chosen — new `documents` child table under `Job`.** Job keeps dataset-level
state (locked model, output schema, connection string, lifecycle); per-file
state moves to Document rows.

Rejected: *parent/child jobs* (incremental insert ×3 providers, cross-batch
dedupe requires reading provisioned rows back, confusing job list);
*top-level Dataset entity above jobs* (same outcome as chosen approach with
roughly double the API/UI relocation work).

### New `documents` table

| Column | Type | Notes |
|---|---|---|
| `id` | String(36) PK | UUID, consistent with the codebase |
| `job_id` | String(36) FK → jobs.id | `ondelete="CASCADE"`, indexed |
| `filename` | String(512) | moved from Job |
| `file_key` | String(1024) | moved from Job |
| `file_size` | Integer | moved from Job |
| `page_count` | Integer, nullable | moved from Job, per-file |
| `status` | Enum `DocumentStatus` | see below |
| `extracted_buckets` | JSON, nullable | this file's `{table: [rows]}` — permanent raw material for rebuilds |
| `compat_report` | JSON, nullable | mapping stats from the append compatibility check; null for founding documents |
| `error_message` | Text, nullable | per-file failure detail, scrubbed via `public_error_message` |
| `created_at`/`updated_at` | TimestampMixin | |

`DocumentStatus`: `PENDING → OCR_PROCESSING → OCR_DONE → EXTRACTING →
EXTRACTED`; terminal failures `FAILED` and `REJECTED` (user cancelled an
incompatible append).

OCR artifacts move from `parsed/{job_id}/...` to
`parsed/{job_id}/{document_id}/...` in S3.

### Job changes

- **Dropped columns:** `filename`, `file_key`, `file_size`, `page_count`.
  The Alembic migration backfills one Document per existing job (copying the
  four values) **before** dropping the columns. Backfilled document status:
  `FAILED` for FAILED jobs, `EXTRACTED` for jobs whose `extracted_data` is
  non-null, otherwise `OCR_DONE` when `page_count` is set, else `PENDING`.
  Existing jobs become single-document datasets with zero behavior change.
- `extracted_data` stays, redefined as the **reconciled union** — recomputed
  from all `EXTRACTED` documents' buckets on every (re)build.
- All other columns unchanged (`locked_model`, `document_profile`,
  `output_schema_name`, `connection_string`, `target_ddl`, ...).

### JobStatus additions

Two values added via `ALTER TYPE job_status ADD VALUE` (same precedent as
the dead `SCHEMA_*` members):

- `APPENDING` — an append's OCR/extract/compat phase is running; the dataset
  remains live (output DB untouched) during this state.
- `AWAITING_APPEND_REVIEW` — compatibility check failed; waiting for the
  user's force/cancel decision.

Append tail reuses existing states:
`APPENDING → (AWAITING_APPEND_REVIEW?) → RECONCILING → TRANSLATING →
PROVISIONING → COMPLETED`. The creation flow's state sequence is unchanged.

## Pipeline

### Creation (N files, N ≥ 1)

1. `POST /jobs` accepts `files: [{filename, file_key, file_size}]`; creates
   Job + N Documents. The legacy single-file body remains accepted and is
   treated as a one-element list.
2. `ocr.process_document` becomes per-document; a Celery **group** runs all
   documents, a chord callback advances the job when all reach `OCR_DONE`.
   Any document FAILED during creation → job FAILED (all-or-nothing, as
   today).
3. `profile_and_propose` gains multi-document sampling: the existing
   deterministic sampler runs per document with the ~15-page budget split
   proportionally by page count (floor of 3 pages per document so small
   files keep front/back anchors). The LLM receives one combined context
   with per-document page markers and proposes one `DatabaseModel`.
   Review/lock flow unchanged.
4. Extraction fans out per document × chunk; each document's rows land in
   its own `extracted_buckets`. Chunk-provenance keys are prefixed with the
   document id so `source_page_numbers` stays traceable to a file.
5. Reconciliation runs over the union of all buckets; translate/provision
   unchanged (drop + recreate `job_{uuid}`).

### Append (`POST /jobs/{id}/documents`, requires job status COMPLETED)

1. Create Document, job → `APPENDING`. OCR runs for the single new file.
2. **Compatibility gate — two layers, no extra LLM call:**
   - *Pre-extraction sanity (flag only, never blocks alone):* compare the
     new document's region-type histogram against the dataset's stored
     `document_profile`.
   - *Post-extraction stats (the real test):* extract against the locked
     model, compute the `compat_report` — total rows, rows per table, share
     of rows missing PK components, PK tables with zero rows.
   - **Pull-in rule:** zero total rows, OR > 50% of rows missing primary-key
     components, OR every PK-bearing table received zero rows →
     `AWAITING_APPEND_REVIEW`. Otherwise auto-continue. Thresholds are
     `Settings` fields (self-hosters can tune).
3. Resolution endpoint: **force** → continue with what mapped (drops already
   itemized in `compat_report`); **cancel** → document `REJECTED`, job back
   to `COMPLETED`, output DB untouched.
4. Continue = re-reconcile the union of all `EXTRACTED` documents → rebuild
   DDL → re-provision (drop + recreate). Extraction *is* the compatibility
   check: fitness is measured by what actually mapped, never guessed from
   structure alone.

## API & UI surface

**FastAPI:**
- `POST /jobs` — multi-file body (backward compatible).
- `POST /jobs/{id}/documents` — append; reuses `_validate_upload`; `409`
  when job is not COMPLETED; `400` for TARGETED jobs.
- `POST /jobs/{id}/documents/{doc_id}/resolve` — `{action: "force"|"cancel"}`;
  only valid in `AWAITING_APPEND_REVIEW`.
- `DELETE /jobs/{id}/documents/{doc_id}` — allowed when COMPLETED; removes
  row + S3 artifacts, triggers re-reconcile + rebuild. Deleting the last
  document → `400` (delete the job instead).
- `POST /jobs/{id}/rebuild` — re-runs the deterministic tail (reconcile →
  translate → provision) from stored buckets; recovery path for rebuild
  failures.
- `GET /jobs/{id}` — gains `documents: [...]` (id, filename, status,
  page_count, compat_report) and derived `document_count`/`total_pages`;
  job-level `filename` removed from the response (frontend updated).
- All document routes user-scoped identically to job routes.
- SSE: same transport; events gain optional `document_id` for per-file
  progress.

**Next.js:**
- New-job page: multi-file picker with per-file progress rows.
- Job detail: documents list with status chips; **"Add data"** button when
  COMPLETED.
- Append review screen (pull-in only): renders `compat_report` (what mapped,
  what drops) with Force / Cancel actions; reuses model-review layout
  patterns.

## Error handling

- **Append failure isolation:** OCR/extraction failure of an appended file →
  document `FAILED`, job returns to `COMPLETED`, output DB untouched.
- **Rebuild window:** if drop succeeded but recreate failed, the output DB
  is temporarily gone but all buckets are intact; job `FAILED` with scrubbed
  error; `POST /jobs/{id}/rebuild` re-runs the tail. No data loss is
  possible — the output DB is always reproducible.
- **Concurrency:** one pipeline run per job; append/delete/rebuild return
  `409` unless the job is `COMPLETED`. No locking beyond the status check.
- **Worker crash safety:** the existing `task_failure` signal additionally
  marks the in-flight document `FAILED` so none strand in `EXTRACTING`.
- **Job deletion:** unchanged; cascade removes documents, existing S3 prefix
  delete covers the per-document artifact paths.

## Testing

- **Tier 1 (pure logic):** proportional sampling-budget split + 3-page
  floor; union reconciliation with cross-document dedupe (same entity in two
  files → one row); compat-report computation; each pull-in threshold gets a
  triggering and a non-triggering case; document-prefixed provenance keys.
- **Tier 2:** append state-machine transitions (auto-continue, force,
  cancel, failure isolation) with faked Celery/S3.
- **Tier 3 (real Postgres):** multi-file create lists documents; append
  guards (409 non-COMPLETED, 400 TARGETED, 400 last-document delete);
  document-level user scoping (foreign user → 404); migration backfill
  (seeded pre-migration job → exactly one document with copied fields).
- Alembic migration gets an explicit upgrade test.

## Deferred (recorded, not in scope)

- Schema evolution on append (extend locked model + migrate output DB).
- Multi-document TARGETED (RAG) jobs.
- Incremental insert for very large datasets (re-provision cost currently
  acceptable; the data model already supports adding it later).
- Per-dataset auto-approve toggles or additional append policies.
