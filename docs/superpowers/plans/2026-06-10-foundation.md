# Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make ParseGrid trustworthy enough to open-source: a real test suite over the untested core services, fixes for four security findings, and CI that enforces both.

**Architecture:** Three tiers of backend tests (pure-logic units, security-critical units, thin API integration against real Postgres) plus four TDD security fixes (connection-test containment, production secret fail-fast, JWT claim requirements, upload constraints) and one GitHub Actions workflow. Spec: `docs/superpowers/specs/2026-06-10-foundation-design.md`.

**Tech Stack:** pytest + pytest-asyncio (`asyncio_mode = "auto"` already configured), httpx ASGITransport, real Postgres (pgvector image) for integration, monkeypatch for the OpenAI client, GitHub Actions with `astral-sh/setup-uv` and `pnpm/action-setup`.

**Conventions for every task:**
- Working directory for all backend commands: `apps/api/`. Run pytest as `uv run pytest …`.
- All work happens directly on `main` (user preference — no branches, no worktrees).
- Tests of *existing* behavior are characterization tests: they should PASS on first run. If one FAILS, stop — you may have found a real bug; verify against the source before changing either side. (Task 2 contains one *known* bug with an intended fix.)
- Tests of *new* behavior (Tasks 9–12) follow strict TDD: the test must FAIL first.

---

### Task 1: Test factories and green baseline

**Files:**
- Create: `apps/api/tests/factories.py`
- Test: existing suite

- [ ] **Step 1: Create shared model factories**

```python
# apps/api/tests/factories.py
"""Shared builders for extraction-model objects used across the test suite."""

from __future__ import annotations

from app.schemas.extraction_model import (
    ColumnDef,
    DatabaseModel,
    RelationshipDef,
    TableDef,
)


def make_column(name: str, col_type: str = "string", pk: bool = False) -> ColumnDef:
    return ColumnDef(name=name, type=col_type, is_primary_key=pk)


def make_table(name: str, columns: list[ColumnDef], description: str = "") -> TableDef:
    return TableDef(table_name=name, description=description, columns=columns)


def make_rel(
    source_table: str,
    source_column: str,
    references_table: str,
    references_column: str,
    **kwargs,
) -> RelationshipDef:
    kwargs.setdefault("link_basis", "natural_key")
    return RelationshipDef(
        source_table=source_table,
        source_column=source_column,
        references_table=references_table,
        references_column=references_column,
        **kwargs,
    )


def make_model(
    tables: list[TableDef],
    relationships: list[RelationshipDef] | None = None,
    extraction_type: str = "table_graph",
) -> DatabaseModel:
    return DatabaseModel(
        extraction_type=extraction_type,
        tables=tables,
        relationships=relationships or [],
    )
```

- [ ] **Step 2: Confirm green baseline**

Run: `uv run pytest -v`
Expected: 6 tests PASS (3 neo4j + 3 qdrant), 0 failures.

- [ ] **Step 3: Commit**

```bash
git add tests/factories.py
git commit -m "test: add shared extraction-model factories"
```

---

### Task 2: normalize_value / normalize_row tests (+ unicode-minus bug fix)

`_CURRENCY_AND_THOUSANDS_RE` currently *strips* the unicode minus `−`, silently turning negative numbers positive (`"−500"` → `500`). This task's tests expose that bug and fix it.

**Files:**
- Test: `apps/api/tests/test_reconciliation_normalize.py`
- Modify: `apps/api/app/services/reconciliation.py:39` and the integer/float branches

- [ ] **Step 1: Write the tests (the two unicode-minus tests are expected to fail)**

```python
# apps/api/tests/test_reconciliation_normalize.py
from app.services.reconciliation import normalize_row, normalize_value
from tests.factories import make_column, make_table


class TestNullish:
    def test_none_passes_through(self):
        assert normalize_value(None, "string") is None

    def test_blank_string_is_none(self):
        assert normalize_value("   ", "integer") is None

    def test_llm_null_literals_are_none(self):
        for literal in ("null", "None", "N/A", "na", "undefined"):
            assert normalize_value(literal, "string") is None


class TestString:
    def test_strips_and_nfc_normalizes(self):
        assert normalize_value("  Acme Corp  ", "string") == "Acme Corp"

    def test_non_string_coerced(self):
        assert normalize_value(42, "string") == "42"


class TestDate:
    def test_fuzzy_date_parses_to_isoformat(self):
        assert normalize_value("Jan 5, 2024", "date") == "2024-01-05"

    def test_unparseable_date_is_none(self):
        assert normalize_value("not a date at all", "date") is None

    def test_non_string_date_is_none(self):
        assert normalize_value(20240105, "date") is None


class TestInteger:
    def test_bool_to_int(self):
        assert normalize_value(True, "integer") == 1

    def test_float_truncates(self):
        assert normalize_value(3.9, "integer") == 3

    def test_currency_and_thousands_stripped(self):
        assert normalize_value("$1,234", "integer") == 1234

    def test_ascii_negative_preserved(self):
        assert normalize_value("-500", "integer") == -500

    def test_unicode_minus_preserved(self):
        # − is the unicode minus sign LLMs sometimes emit.
        assert normalize_value("−500", "integer") == -500

    def test_garbage_is_none(self):
        assert normalize_value("abc", "integer") is None


class TestFloat:
    def test_currency_string(self):
        assert normalize_value("€1,234.56", "float") == 1234.56

    def test_unicode_minus_preserved(self):
        assert normalize_value("−1,234.50", "float") == -1234.5

    def test_garbage_is_none(self):
        assert normalize_value("n/a-ish", "float") is None


class TestBoolean:
    def test_truthy_strings(self):
        for s in ("true", "T", "yes", "Y", "1"):
            assert normalize_value(s, "boolean") is True

    def test_falsy_strings(self):
        for s in ("false", "F", "no", "N", "0"):
            assert normalize_value(s, "boolean") is False

    def test_numeric_coercion(self):
        assert normalize_value(0, "boolean") is False
        assert normalize_value(1.0, "boolean") is True

    def test_ambiguous_is_none(self):
        assert normalize_value("maybe", "boolean") is None


def test_unknown_type_passes_through():
    assert normalize_value("anything", "jsonb") == "anything"


def test_normalize_row_touches_only_declared_columns():
    table = make_table(
        "items",
        [make_column("qty", col_type="integer"), make_column("name")],
    )
    row = {"qty": "1,000", "name": "  Widget ", "__chunk_index": 3, "extra": " raw "}
    out = normalize_row(row, table)
    assert out["qty"] == 1000
    assert out["name"] == "Widget"
    assert out["__chunk_index"] == 3
    assert out["extra"] == " raw "  # undeclared key untouched
```

- [ ] **Step 2: Run — expect exactly two failures (the unicode-minus tests)**

Run: `uv run pytest tests/test_reconciliation_normalize.py -v`
Expected: all PASS except `test_unicode_minus_preserved` (integer and float variants), which FAIL with `500 == -500` style assertions. If anything *else* fails, stop and investigate before proceeding.

- [ ] **Step 3: Fix the sign-stripping bug**

In `apps/api/app/services/reconciliation.py`, replace line 39 (note: the
source spells the unicode minus as the escape sequence backslash-u2212,
not a literal character — match the file exactly):

```python
_CURRENCY_AND_THOUSANDS_RE = re.compile(r"[\u2212$€£¥,\s]")
```

with:

```python
_CURRENCY_AND_THOUSANDS_RE = re.compile(r"[$€£¥,\s]")


def _clean_numeric(value) -> str:
    """Strip currency symbols / thousands separators, mapping unicode minus to ASCII."""
    return _CURRENCY_AND_THOUSANDS_RE.sub("", str(value).replace("−", "-"))
```

Then in the `integer` branch replace
`cleaned = _CURRENCY_AND_THOUSANDS_RE.sub("", str(value))` with
`cleaned = _clean_numeric(value)`, and identically in the `float` branch.

- [ ] **Step 4: Run again — all pass**

Run: `uv run pytest tests/test_reconciliation_normalize.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_reconciliation_normalize.py app/services/reconciliation.py
git commit -m "test: characterize normalize_value; fix unicode-minus sign loss"
```

---

### Task 3: needs_resolution / entity_resolution tests (fake OpenAI)

`entity_resolution` does `from openai import OpenAI` *inside the function*, so tests monkeypatch `openai.OpenAI` at the module level.

**Files:**
- Test: `apps/api/tests/test_reconciliation_entity.py`

- [ ] **Step 1: Write the tests**

```python
# apps/api/tests/test_reconciliation_entity.py
import json
from types import SimpleNamespace

from app.services.reconciliation import entity_resolution, needs_resolution
from tests.factories import make_column, make_table


def _fake_openai(payload: dict | None = None, raise_exc: Exception | None = None):
    """Build a stand-in for openai.OpenAI returning a canned chat completion."""

    class _Completions:
        @staticmethod
        def create(**kwargs):
            if raise_exc is not None:
                raise raise_exc
            message = SimpleNamespace(content=json.dumps(payload))
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    class _FakeOpenAI:
        def __init__(self, api_key=None):
            self.chat = SimpleNamespace(completions=_Completions())

    return _FakeOpenAI


PEOPLE = make_table(
    "people",
    [make_column("name", pk=True), make_column("email")],
)


def test_needs_resolution_thresholds():
    assert needs_resolution([], ["name"]) is False
    assert needs_resolution([{"name": "a"}], ["name"]) is False
    assert needs_resolution([{"name": "a"}, {"name": "b"}], ["name"]) is True


def test_single_row_never_constructs_client(monkeypatch):
    class _Boom:
        def __init__(self, api_key=None):
            raise AssertionError("OpenAI must not be constructed for single rows")

    monkeypatch.setattr("openai.OpenAI", _Boom)
    rows = [{"name": "Jane Doe", "email": None}]
    out, notes = entity_resolution(rows, PEOPLE, ["name"])
    assert out == rows
    assert notes == []


def test_merges_two_rows_into_one_entity(monkeypatch):
    rows = [
        {"name": "Doe, Jane", "email": None, "__chunk_index": 0},
        {"name": "Jane Doe", "email": "jane@example.com", "__chunk_index": 1},
    ]
    payload = {
        "entities": [
            {
                "row_indices": [0, 1],
                "merged": {"name": "Jane Doe", "email": "jane@example.com"},
            }
        ]
    }
    monkeypatch.setattr("openai.OpenAI", _fake_openai(payload))
    out, notes = entity_resolution(rows, PEOPLE, ["name"])
    assert len(out) == 1
    assert out[0]["email"] == "jane@example.com"
    # Internal markers come from the richest source row (row 1: 2 non-null fields).
    assert out[0]["__chunk_index"] == 1
    assert any("2 rows → 1 entities" in n for n in notes)


def test_llm_failure_returns_rows_unchanged(monkeypatch):
    rows = [{"name": "A"}, {"name": "B"}]
    monkeypatch.setattr(
        "openai.OpenAI", _fake_openai(raise_exc=RuntimeError("api down"))
    )
    out, notes = entity_resolution(rows, PEOPLE, ["name"])
    assert out == rows
    assert any("LLM call failed" in n for n in notes)


def test_unaccounted_rows_pass_through(monkeypatch):
    rows = [{"name": "A"}, {"name": "B"}]
    payload = {"entities": [{"row_indices": [0], "merged": {"name": "A"}}]}
    monkeypatch.setattr("openai.OpenAI", _fake_openai(payload))
    out, notes = entity_resolution(rows, PEOPLE, ["name"])
    assert len(out) == 2
    assert {"name": "B"} in out
    assert any("not in LLM response" in n for n in notes)
```

- [ ] **Step 2: Run — expect PASS (characterization)**

Run: `uv run pytest tests/test_reconciliation_entity.py -v`
Expected: 5 PASS. A failure means the test's reading of the code is wrong or a real bug — verify against `app/services/reconciliation.py:244-375` before changing anything.

- [ ] **Step 3: Commit**

```bash
git add tests/test_reconciliation_entity.py
git commit -m "test: cover entity_resolution gating, merge, and failure fallback"
```

---

### Task 4: resolve_foreign_keys tests

**Files:**
- Test: `apps/api/tests/test_reconciliation_fk.py`

- [ ] **Step 1: Write the tests**

```python
# apps/api/tests/test_reconciliation_fk.py
from app.services.reconciliation import resolve_foreign_keys
from tests.factories import make_column, make_rel, make_table

COMPANIES = make_table("companies", [make_column("company_name", pk=True)])
CONTACTS = make_table(
    "contacts", [make_column("contact_name", pk=True), make_column("company")]
)
TABLE_DEFS = {"companies": COMPANIES, "contacts": CONTACTS}
REL = make_rel("contacts", "company", "companies", "company_name")


def _tables(child_company_value):
    return {
        "companies": [{"company_name": "Acme Corp"}],
        "contacts": [{"contact_name": "Jane", "company": child_company_value}],
    }


def test_exact_match_normalizes_casing():
    tables = _tables("acme corp")
    resolve_foreign_keys(tables, TABLE_DEFS, [REL])
    assert tables["contacts"][0]["company"] == "Acme Corp"
    assert "__notes" not in tables["contacts"][0]


def test_token_set_match_repairs_and_annotates():
    tables = _tables("Corp, Acme")
    resolve_foreign_keys(tables, TABLE_DEFS, [REL])
    child = tables["contacts"][0]
    assert child["company"] == "Acme Corp"
    assert any("token-set match" in n for n in child["__notes"])


def test_no_match_annotates_without_rewriting():
    tables = _tables("Globex")
    resolve_foreign_keys(tables, TABLE_DEFS, [REL])
    child = tables["contacts"][0]
    assert child["company"] == "Globex"
    assert any("no matching" in n for n in child["__notes"])


def test_null_fk_skipped():
    tables = _tables(None)
    resolve_foreign_keys(tables, TABLE_DEFS, [REL])
    assert tables["contacts"][0]["company"] is None
    assert "__notes" not in tables["contacts"][0]


def test_disabled_relationship_is_ignored():
    rel = make_rel("contacts", "company", "companies", "company_name", enabled=False)
    tables = _tables("Corp, Acme")
    resolve_foreign_keys(tables, TABLE_DEFS, [rel])
    assert tables["contacts"][0]["company"] == "Corp, Acme"


def test_non_string_fk_miss_annotates():
    companies = make_table("companies", [make_column("cid", col_type="integer", pk=True)])
    contacts = make_table(
        "contacts", [make_column("contact_name", pk=True), make_column("cid", col_type="integer")]
    )
    rel = make_rel("contacts", "cid", "companies", "cid")
    tables = {
        "companies": [{"cid": 1}],
        "contacts": [{"contact_name": "Jane", "cid": 99}],
    }
    resolve_foreign_keys(tables, {"companies": companies, "contacts": contacts}, [rel])
    assert any("no matching" in n for n in tables["contacts"][0]["__notes"])
```

- [ ] **Step 2: Run — expect PASS**

Run: `uv run pytest tests/test_reconciliation_fk.py -v`
Expected: 6 PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_reconciliation_fk.py
git commit -m "test: cover FK exact/fuzzy repair and miss annotation"
```

---

### Task 5: canonicalize_parents / add_provenance / reconcile_model tests

**Files:**
- Test: `apps/api/tests/test_reconciliation_canonical.py`

- [ ] **Step 1: Write the tests**

```python
# apps/api/tests/test_reconciliation_canonical.py
from app.services.reconciliation import (
    add_provenance,
    canonicalize_parents,
    reconcile_model,
)
from tests.factories import make_column, make_model, make_table

PEOPLE = make_table(
    "people", [make_column("name", pk=True), make_column("city")]
)


def test_case_insensitive_dedupe_fills_nulls():
    rows = [
        {"name": "Acme ", "city": None},
        {"name": "acme", "city": "NYC"},
    ]
    deduped, notes = canonicalize_parents(rows, PEOPLE)
    assert len(deduped) == 1
    assert deduped[0]["city"] == "NYC"
    assert any("merged" in n for n in notes)


def test_missing_pk_component_kept_and_tagged():
    rows = [{"name": None, "city": "NYC"}]
    deduped, _ = canonicalize_parents(rows, PEOPLE)
    assert len(deduped) == 1
    assert any("missing primary-key" in n for n in deduped[0]["__notes"])


def test_no_pk_table_uses_fingerprint_dedupe():
    table = make_table("notes_tbl", [make_column("body")])
    rows = [{"body": "same text"}, {"body": "same text"}, {"body": "other"}]
    deduped, _ = canonicalize_parents(rows, table)
    assert len(deduped) == 2


def test_add_provenance_strips_markers_and_maps_pages():
    rows = [{"a": 1, "__chunk_index": 2, "__notes": ["fixed FK"]}]
    out = add_provenance(rows, {2: [3, 4]})
    assert out[0]["source_page_numbers"] == [3, 4]
    assert out[0]["reconciliation_notes"] == "fixed FK"
    assert out[0]["extraction_confidence"] is None
    assert not any(k.startswith("__") for k in out[0])


def test_add_provenance_unknown_chunk_yields_null_pages():
    out = add_provenance([{"a": 1, "__chunk_index": 9}], {})
    assert out[0]["source_page_numbers"] is None


def test_reconcile_model_end_to_end_drops_orphan_bucket():
    # Single-row tables → needs_resolution is False → no LLM call happens.
    model = make_model([PEOPLE])
    bucketed = {
        "people": [{"name": " Jane ", "city": "nyc", "__chunk_index": 0}],
        "ghost_table": [{"x": 1}],
    }
    finalized, run_notes = reconcile_model(bucketed, {"people": {0: [1]}}, model)
    assert set(finalized) == {"people"}
    assert finalized["people"][0]["name"] == "Jane"
    assert finalized["people"][0]["source_page_numbers"] == [1]
    assert any("orphan extraction bucket" in n for n in run_notes)
```

- [ ] **Step 2: Run — expect PASS**

Run: `uv run pytest tests/test_reconciliation_canonical.py -v`
Expected: 6 PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_reconciliation_canonical.py
git commit -m "test: cover canonicalize/provenance and reconcile_model orchestration"
```

---

### Task 6: profiling tests

**Files:**
- Test: `apps/api/tests/test_profiling.py`

- [ ] **Step 1: Write the tests**

```python
# apps/api/tests/test_profiling.py
from app.services.profiling import (
    MAX_SAMPLED_PAGES,
    build_profile_context,
    profile_document,
)


def _page(n: int, types: list[str]) -> dict:
    return {
        "page_number": n,
        "regions": [{"region_type": t, "text": f"{t} text on page {n}"} for t in types],
    }


def _doc(pages: list[dict]) -> dict:
    return {"page_count": len(pages), "pages": pages}


def test_empty_document():
    assert profile_document({"page_count": 0, "pages": []}) == ([], {})


def test_short_document_samples_every_page():
    doc = _doc([_page(n, ["text"]) for n in range(1, 6)])
    sampled, _ = profile_document(doc)
    assert sampled == [1, 2, 3, 4, 5]


def test_long_document_caps_and_anchors():
    doc = _doc([_page(n, ["text", "table"] if n == 25 else ["text"]) for n in range(1, 51)])
    sampled, _ = profile_document(doc)
    assert len(sampled) <= MAX_SAMPLED_PAGES
    assert {1, 2, 3, 49, 50}.issubset(set(sampled))
    assert sampled == sorted(sampled)


def test_sampling_is_deterministic():
    doc = _doc([_page(n, ["text", "header"]) for n in range(1, 51)])
    assert profile_document(doc) == profile_document(doc)


def test_histogram_counts_all_pages_not_just_sampled():
    doc = _doc([_page(n, ["text", "table"]) for n in range(1, 51)])
    _, histogram = profile_document(doc)
    assert histogram == {"text": 50, "table": 50}


def test_build_profile_context_format():
    doc = _doc([_page(1, ["header", "table"]), _page(2, ["text"])])
    ctx = build_profile_context([1], doc)
    assert "--- Page 1 (types: header, table) ---" in ctx
    assert "header text on page 1" in ctx
    assert "Page 2" not in ctx


def test_build_profile_context_skips_unknown_pages():
    doc = _doc([_page(1, ["text"])])
    assert build_profile_context([1, 99], doc) == build_profile_context([1], doc)
```

- [ ] **Step 2: Run — expect PASS**

Run: `uv run pytest tests/test_profiling.py -v`
Expected: 7 PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_profiling.py
git commit -m "test: cover page sampling determinism and profile context"
```

---

### Task 7: DDL tests

**Files:**
- Test: `apps/api/tests/test_ddl.py`

- [ ] **Step 1: Write the tests**

```python
# apps/api/tests/test_ddl.py
import pytest

from app.services.ddl import build_ddl, build_ddl_with_notes, validate_model
from tests.factories import make_column, make_model, make_rel, make_table


def _invoice_model():
    companies = make_table("Companies", [make_column("Company Name", pk=True)])
    invoices = make_table(
        "Invoices",
        [
            make_column("Invoice Number", pk=True),
            make_column("Company Name"),
            make_column("Total", col_type="float"),
        ],
    )
    rel = make_rel("Invoices", "Company Name", "Companies", "Company Name")
    return make_model([companies, invoices], [rel])


class TestValidateModel:
    def test_snake_cases_identifiers(self):
        result = validate_model(_invoice_model())
        names = [t.table_name for t in result.model.tables]
        assert names == ["companies", "invoices"]
        assert result.model.tables[1].columns[0].name == "invoice_number"

    def test_reserved_word_table_rejected(self):
        model = make_model([make_table("select", [make_column("a")])])
        with pytest.raises(ValueError, match="reserved word"):
            validate_model(model)

    def test_reserved_internal_column_rejected(self):
        model = make_model([make_table("t", [make_column("id")])])
        with pytest.raises(ValueError, match="reserved provenance"):
            validate_model(model)

    def test_duplicate_column_rejected(self):
        model = make_model([make_table("t", [make_column("a"), make_column("A")])])
        with pytest.raises(ValueError, match="duplicate column"):
            validate_model(model)

    def test_relationship_to_non_pk_column_downgraded(self):
        parent = make_table("parent", [make_column("name")])  # NOT a pk
        child = make_table("child", [make_column("cname", pk=True), make_column("name")])
        rel = make_rel("child", "name", "parent", "name")
        result = validate_model(make_model([parent, child], [rel]))
        assert result.model.relationships[0].enabled is False
        assert any("not is_primary_key" in n for n in result.notes)

    def test_relationship_to_missing_table_downgraded(self):
        child = make_table("child", [make_column("cname", pk=True), make_column("pid")])
        rel = make_rel("child", "pid", "nowhere", "pid")
        result = validate_model(make_model([child], [rel]))
        assert result.model.relationships[0].enabled is False


class TestBuildDdl:
    def test_create_table_shape(self):
        stmts = build_ddl(_invoice_model(), "job_abc")
        create_invoices = next(s for s in stmts if '"invoices"' in s and s.startswith("CREATE"))
        assert '"id" BIGSERIAL PRIMARY KEY' in create_invoices
        assert '"total" NUMERIC' in create_invoices
        for prov in ("source_page_numbers", "extraction_confidence", "reconciliation_notes"):
            assert prov in create_invoices
        assert create_invoices.startswith('CREATE TABLE "job_abc".')

    def test_unique_constraints_for_pk_columns(self):
        stmts = build_ddl(_invoice_model(), "job_abc")
        assert any('"uq_companies_company_name" UNIQUE' in s for s in stmts)
        assert any('"uq_invoices_invoice_number" UNIQUE' in s for s in stmts)

    def test_fk_emitted_only_for_enabled_relationships(self):
        stmts = build_ddl(_invoice_model(), "job_abc")
        assert any("FOREIGN KEY" in s for s in stmts)

        model = _invoice_model()
        model.relationships[0].enabled = False
        stmts_disabled = build_ddl(model, "job_abc")
        assert not any("FOREIGN KEY" in s for s in stmts_disabled)

    def test_output_is_byte_deterministic(self):
        assert build_ddl(_invoice_model(), "job_abc") == build_ddl(
            _invoice_model(), "job_abc"
        )

    def test_build_ddl_with_notes_returns_all_artifacts(self):
        stmts, normalized, notes = build_ddl_with_notes(_invoice_model(), "job_abc")
        assert stmts
        assert normalized.tables[0].table_name == "companies"
        assert notes == []
```

- [ ] **Step 2: Run — expect PASS**

Run: `uv run pytest tests/test_ddl.py -v`
Expected: 11 PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_ddl.py
git commit -m "test: cover model validation and deterministic DDL emission"
```

---

### Task 8: OCR page-classification tests

`_is_page_scanned(page, text)` only calls `page.get_images()` — a tiny fake suffices. The text-signal branches are checked before the image branch.

**Files:**
- Test: `apps/api/tests/test_ocr_router.py`

- [ ] **Step 1: Write the tests**

```python
# apps/api/tests/test_ocr_router.py
from app.providers.ocr_paddle import _is_page_scanned


class _FakePage:
    def __init__(self, image_count: int = 0):
        self._image_count = image_count

    def get_images(self):
        return [("xref",)] * self._image_count


NORMAL_TEXT = (
    "Invoice 1042 issued to Acme Corporation on January 5 2024 for services "
    "rendered including consulting and development work across three projects."
)


def test_empty_text_is_scanned():
    assert _is_page_scanned(_FakePage(), "") is True
    assert _is_page_scanned(_FakePage(), "   \n  ") is True


def test_gibberish_ocr_layer_is_scanned():
    # < 40% alphanumeric → hidden garbage layer from a scanner.
    assert _is_page_scanned(_FakePage(), "!!! @@@ ### $$$ %%% ^^^ &&&") is True


def test_image_dominant_short_text_is_scanned():
    assert _is_page_scanned(_FakePage(image_count=2), "short caption") is True


def test_native_text_page_is_not_scanned():
    assert _is_page_scanned(_FakePage(), NORMAL_TEXT) is False


def test_images_with_substantial_text_is_not_scanned():
    assert _is_page_scanned(_FakePage(image_count=2), NORMAL_TEXT) is False
```

- [ ] **Step 2: Run — expect PASS**

Run: `uv run pytest tests/test_ocr_router.py -v`
Expected: 5 PASS. (If importing `app.providers.ocr_paddle` pulls in heavy Paddle deps, they are already installed via `uv sync` — slow first import is normal.)

- [ ] **Step 3: Commit**

```bash
git add tests/test_ocr_router.py
git commit -m "test: cover smart OCR router page-classification signals"
```

---

### Task 9: JWT tightening (Fix 3 — TDD)

**Files:**
- Test: `apps/api/tests/test_security_jwt.py`
- Modify: `apps/api/app/core/security.py:38-56` (the `verify_jwt` body)

- [ ] **Step 1: Write the tests (three must fail)**

```python
# apps/api/tests/test_security_jwt.py
import time
from types import SimpleNamespace

import jwt as pyjwt
import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.core.security import verify_jwt


def _token(claims: dict, secret: str | None = None, alg: str = "HS256") -> str:
    return pyjwt.encode(claims, secret or settings.auth_secret, algorithm=alg)


def _creds(token: str):
    return SimpleNamespace(credentials=token)


def _valid_claims() -> dict:
    return {"sub": "user-1", "exp": int(time.time()) + 300}


def test_valid_token_returns_payload():
    payload = verify_jwt(_creds(_token(_valid_claims())))
    assert payload.sub == "user-1"


def test_expired_token_rejected():
    claims = {"sub": "user-1", "exp": int(time.time()) - 10}
    with pytest.raises(HTTPException) as exc:
        verify_jwt(_creds(_token(claims)))
    assert exc.value.status_code == 401


def test_wrong_secret_rejected():
    with pytest.raises(HTTPException) as exc:
        verify_jwt(_creds(_token(_valid_claims(), secret="x" * 40)))
    assert exc.value.status_code == 401


def test_token_without_exp_rejected():
    with pytest.raises(HTTPException) as exc:
        verify_jwt(_creds(_token({"sub": "user-1"})))
    assert exc.value.status_code == 401


def test_token_without_sub_rejected():
    with pytest.raises(HTTPException) as exc:
        verify_jwt(_creds(_token({"exp": int(time.time()) + 300})))
    assert exc.value.status_code == 401


def test_token_with_empty_sub_rejected():
    with pytest.raises(HTTPException) as exc:
        verify_jwt(_creds(_token({"sub": "", "exp": int(time.time()) + 300})))
    assert exc.value.status_code == 401
```

- [ ] **Step 2: Run — expect 3 failures**

Run: `uv run pytest tests/test_security_jwt.py -v`
Expected: `test_token_without_exp_rejected`, `test_token_without_sub_rejected`, `test_token_with_empty_sub_rejected` FAIL (no exception raised); the other 3 PASS.

- [ ] **Step 3: Implement**

In `apps/api/app/core/security.py`, replace the `try` block of `verify_jwt` with:

```python
    try:
        payload = jwt.decode(
            token,
            settings.auth_secret,
            algorithms=[settings.jwt_algorithm],
            options={
                "verify_aud": False,  # Auth.js may not set audience
                "require": ["exp", "sub"],
            },
        )
        if not payload.get("sub"):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token: empty sub claim",
            )
        return TokenPayload(payload)
```

(The existing `except jwt.ExpiredSignatureError` / `except jwt.InvalidTokenError` handlers stay — `MissingRequiredClaimError` is a subclass of `InvalidTokenError`, and the explicit `HTTPException` is not caught by them.)

- [ ] **Step 4: Run — all pass**

Run: `uv run pytest tests/test_security_jwt.py -v`
Expected: 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_security_jwt.py app/core/security.py
git commit -m "fix(security): require exp and non-empty sub claims on JWTs"
```

---

### Task 10: Production secret fail-fast (Fix 2 — TDD)

**Files:**
- Test: `apps/api/tests/test_config_validation.py`
- Modify: `apps/api/app/core/config.py`

- [ ] **Step 1: Write the tests (two must fail)**

```python
# apps/api/tests/test_config_validation.py
import pytest

from app.core.config import Settings


@pytest.fixture(autouse=True)
def _clear_ambient_env(monkeypatch):
    for var in ("FASTAPI_ENV", "AUTH_SECRET", "S3_ACCESS_KEY", "S3_SECRET_KEY"):
        monkeypatch.delenv(var, raising=False)


def test_development_defaults_boot():
    settings = Settings(_env_file=None)
    assert settings.fastapi_env == "development"


def test_production_rejects_default_auth_secret():
    with pytest.raises(ValueError, match="AUTH_SECRET"):
        Settings(
            _env_file=None,
            fastapi_env="production",
            s3_access_key="prod-key",
            s3_secret_key="prod-secret",
        )


def test_production_rejects_default_minio_credentials():
    with pytest.raises(ValueError, match="S3_ACCESS_KEY"):
        Settings(_env_file=None, fastapi_env="production", auth_secret="x" * 40)


def test_production_with_real_secrets_boots():
    settings = Settings(
        _env_file=None,
        fastapi_env="production",
        auth_secret="x" * 40,
        s3_access_key="prod-key",
        s3_secret_key="prod-secret",
    )
    assert settings.is_production
```

- [ ] **Step 2: Run — expect 2 failures**

Run: `uv run pytest tests/test_config_validation.py -v`
Expected: the two `test_production_rejects_*` tests FAIL (`DID NOT RAISE`); the other 2 PASS.

- [ ] **Step 3: Implement**

In `apps/api/app/core/config.py`: change the import line to
`from pydantic import model_validator` (new import alongside the existing ones)
and add inside the `Settings` class, after the `is_production` property:

```python
    @model_validator(mode="after")
    def _enforce_production_secrets(self) -> "Settings":
        """Refuse to boot in production with shipped development defaults."""
        if self.fastapi_env != "production":
            return self
        problems: list[str] = []
        if self.auth_secret == "parsegrid-dev-secret-minimum-32-characters-long":
            problems.append("AUTH_SECRET is still the shipped development default")
        if self.s3_access_key == "minioadmin" or self.s3_secret_key == "minioadmin":
            problems.append("S3_ACCESS_KEY/S3_SECRET_KEY are still 'minioadmin'")
        if problems:
            raise ValueError(
                "refusing to start in production: " + "; ".join(problems)
            )
        return self
```

- [ ] **Step 4: Run — all pass, suite still green**

Run: `uv run pytest tests/test_config_validation.py -v && uv run pytest -q`
Expected: 4 PASS, full suite green (module-level `settings = Settings()` still constructs fine in development).

- [ ] **Step 5: Commit**

```bash
git add tests/test_config_validation.py app/core/config.py
git commit -m "fix(security): refuse production boot with default secrets"
```

---

### Task 11: Connection-test containment (Fix 1 — TDD)

New module `app/services/safe_errors.py` holds the blocklist and both sanitizers (the spec's "single helper" home). Then wire it into the endpoint and add timeouts to all three providers' `test_connection`.

**Files:**
- Create: `apps/api/app/services/safe_errors.py`
- Test: `apps/api/tests/test_safe_errors.py`
- Modify: `apps/api/app/core/config.py` (one new setting)
- Modify: `apps/api/app/api/v1/connections.py:40-60`
- Modify: `apps/api/app/providers/output_postgres.py:42-49`
- Modify: `apps/api/app/providers/output_neo4j.py:27-37,167-168`
- Modify: `apps/api/app/providers/output_vector_qdrant.py:28-33,127-128`
- Modify: `apps/api/tests/test_output_neo4j.py` and `tests/test_output_vector_qdrant.py` (fake builder signatures)
- Modify: `apps/api/app/worker/callbacks.py:58` and the `error_message=str(exc)` sites in `app/worker/tasks/{profile,extract,merge,reconcile}.py`

- [ ] **Step 1: Add the blocklist setting**

In `app/core/config.py`, under the `# --- Qdrant ... ---` block add:

```python
    # --- Connection-test guard ---
    # Extra DSNs/URLs whose host:port may never be targeted by /connections/test,
    # in addition to the automatically-derived internal endpoints.
    connection_test_blocklist: list[str] = []
```

- [ ] **Step 2: Write the failing tests**

```python
# apps/api/tests/test_safe_errors.py
from app.services.safe_errors import (
    blocked_reason,
    public_error_message,
    sanitize_connection_error,
)


class TestBlockedReason:
    def test_metadata_database_is_blocked(self):
        # settings.database_url default → localhost:5436
        reason = blocked_reason("postgresql://x:y@localhost:5436/parsegrid")
        assert reason is not None
        assert "internal" in reason.lower()

    def test_redis_is_blocked(self):
        assert blocked_reason("redis://localhost:6380/0") is not None

    def test_user_database_on_other_port_is_allowed(self):
        assert blocked_reason("postgresql://u:p@localhost:5999/mydb") is None

    def test_external_host_is_allowed(self):
        assert blocked_reason("postgresql://u:p@db.example.com:5432/prod") is None

    def test_unparseable_string_is_allowed(self):
        # Provider-level parsing will reject it with its own error.
        assert blocked_reason("not a dsn at all") is None


class TestSanitizeConnectionError:
    def test_auth_errors_classified(self):
        exc = Exception('password authentication failed for user "admin"')
        assert sanitize_connection_error(exc) == "Connection failed: authentication failed."

    def test_unreachable_errors_classified(self):
        exc = Exception("connection to server at 10.0.0.5 port 5432 timed out")
        msg = sanitize_connection_error(exc)
        assert msg == "Connection failed: could not reach the database host."

    def test_other_errors_generic_without_detail_leak(self):
        exc = Exception("FATAL: secret internal detail xyz")
        msg = sanitize_connection_error(exc)
        assert "xyz" not in msg
        assert msg.startswith("Connection failed")


class TestPublicErrorMessage:
    def test_scrubs_embedded_dsns(self):
        exc = ValueError("insert failed on postgresql://user:hunter2@db:5432/x")
        msg = public_error_message(exc)
        assert "hunter2" not in msg
        assert "ValueError" in msg

    def test_plain_errors_keep_their_message(self):
        msg = public_error_message(KeyError("missing_table"))
        assert "missing_table" in msg
```

Run: `uv run pytest tests/test_safe_errors.py -v`
Expected: FAIL with `ModuleNotFoundError: app.services.safe_errors`.

- [ ] **Step 3: Implement the module**

```python
# apps/api/app/services/safe_errors.py
"""ParseGrid — error sanitation and connection-target guarding.

Single home for everything that decides what error detail may leave the
server: the /connections/test infrastructure blocklist, the classified
connection-failure messages, and DSN scrubbing for job error_message.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from app.core.config import settings

_DEFAULT_PORTS = {
    "postgresql": 5432,
    "postgres": 5432,
    "redis": 6379,
    "http": 80,
    "https": 443,
    "bolt": 7687,
    "neo4j": 7687,
}

_DSN_RE = re.compile(r"\b[a-z][a-z0-9+]*://\S+", re.IGNORECASE)

_AUTH_MARKERS = ("password", "auth", "permission denied", "access denied", "unauthorized")
_REACH_MARKERS = (
    "timeout",
    "timed out",
    "could not translate",
    "name or service not known",
    "refused",
    "unreachable",
    "no route",
)


def _endpoint(url: str) -> tuple[str, int] | None:
    """(host, port) for a URL/DSN, or None when there is no host.

    Matching is by hostname string — we deliberately do not resolve DNS, so
    "localhost" and "127.0.0.1" are distinct entries. Self-hosters needing
    stricter rules add entries to CONNECTION_TEST_BLOCKLIST.
    """
    parsed = urlparse(url)
    if not parsed.hostname:
        return None
    port = parsed.port or _DEFAULT_PORTS.get((parsed.scheme or "").split("+")[0], 0)
    return (parsed.hostname.lower(), port)


def internal_endpoints() -> set[tuple[str, int]]:
    """ParseGrid's own infrastructure endpoints, derived from settings."""
    candidates = [
        settings.database_url.replace("+asyncpg", ""),
        settings.redis_url,
        settings.s3_endpoint_url or "",
        settings.neo4j_uri,
        settings.qdrant_url,
        *settings.connection_test_blocklist,
    ]
    endpoints: set[tuple[str, int]] = set()
    for url in candidates:
        ep = _endpoint(url)
        if ep:
            endpoints.add(ep)
    return endpoints


def blocked_reason(connection_string: str) -> str | None:
    """Non-None when the DSN targets ParseGrid's internal infrastructure."""
    ep = _endpoint(connection_string)
    if ep is not None and ep in internal_endpoints():
        return (
            "Connection target is ParseGrid's internal infrastructure "
            "and is not allowed."
        )
    return None


def sanitize_connection_error(exc: Exception) -> str:
    """Classified, detail-free message safe to return to the client."""
    text = str(exc).lower()
    if any(marker in text for marker in _AUTH_MARKERS):
        return "Connection failed: authentication failed."
    if any(marker in text for marker in _REACH_MARKERS):
        return "Connection failed: could not reach the database host."
    return "Connection failed: the database rejected the connection."


def public_error_message(exc: Exception) -> str:
    """Job error_message safe for the owning user: keeps the exception type
    and message but scrubs any embedded DSN (which may carry credentials)."""
    msg = _DSN_RE.sub("<connection-string>", str(exc))
    return f"{type(exc).__name__}: {msg[:300]}"
```

Run: `uv run pytest tests/test_safe_errors.py -v`
Expected: 10 PASS.

- [ ] **Step 4: Wire into the endpoint**

In `app/api/v1/connections.py`, add imports at the top:

```python
import logging

from app.services.safe_errors import blocked_reason, sanitize_connection_error

logger = logging.getLogger(__name__)
```

and replace the body of `test_connection` with:

```python
    reason = blocked_reason(body.connection_string)
    if reason:
        return {"success": False, "message": reason}

    from app.providers.factory import get_output_provider

    try:
        provider = get_output_provider(body.output_format)
    except ValueError as e:
        return {"success": False, "message": str(e)}

    try:
        provider.test_connection(body.connection_string)
        return {"success": True, "message": "Connection successful"}
    except Exception as e:
        logger.warning(f"connection test failed for user {user.sub}: {e}")
        return {"success": False, "message": sanitize_connection_error(e)}
```

- [ ] **Step 5: Add connect timeouts to the three providers**

`app/providers/output_postgres.py` — in `test_connection`:

```python
        engine = create_engine(
            connection_string,
            pool_pre_ping=True,
            connect_args={"connect_timeout": 5},
        )
```

`app/providers/output_neo4j.py` — give `_build_driver` an optional timeout and use it from `test_connection`:

```python
    def _build_driver(self, uri: str, user: str, password: str, **driver_kwargs):
        return GraphDatabase.driver(uri, auth=(user, password), **driver_kwargs)
```

and in `test_connection` change the build call to:

```python
        driver = self._build_driver(uri, user, password, connection_timeout=5.0)
```

`app/providers/output_vector_qdrant.py` — same pattern:

```python
    def _build_client(self, url: str, api_key: str | None, **client_kwargs) -> QdrantClient:
        return QdrantClient(url=url, api_key=api_key, **client_kwargs)
```

and in `test_connection`:

```python
        client = self._build_client(url, api_key, timeout=5)
```

Update the existing fakes so the new kwargs don't break them:
in `tests/test_output_neo4j.py` change all three occurrences of
`lambda uri, user, password: _FakeDriver(fake_session)` to
`lambda uri, user, password, **kw: _FakeDriver(fake_session)`;
in `tests/test_output_vector_qdrant.py` change all three occurrences of
`lambda url, api_key: fake_client` to
`lambda url, api_key, **kw: fake_client`.

- [ ] **Step 6: Scrub DSNs from worker failure messages**

In `app/worker/callbacks.py`, change line 58 from
`error_msg = f"Task failed: {type(exception).__name__}: {exception}"` to:

```python
    from app.services.safe_errors import public_error_message

    error_msg = f"Task failed: {public_error_message(exception)}"
```

(put the import at the top of the file with the other imports, not inline).

In each of `app/worker/tasks/profile.py:106-107`, `extract.py:190-191`,
`merge.py:97-98`, `reconcile.py:98-99`, replace `error_message=str(exc)` with
`error_message=public_error_message(exc)` on both the `publish_status` and
`update_job` lines, adding `from app.services.safe_errors import public_error_message`
to each file's imports. (`translate.py` and `rag.py` have no
`error_message=str(exc)` writes — verified by grep — so only those four files
plus `callbacks.py` change.)

- [ ] **Step 7: Full suite green**

Run: `uv run pytest -q`
Expected: all PASS, including the pre-existing neo4j/qdrant tests with the updated fakes.

- [ ] **Step 8: Commit**

```bash
git add app/services/safe_errors.py tests/test_safe_errors.py \
  app/core/config.py app/api/v1/connections.py \
  app/providers/output_postgres.py app/providers/output_neo4j.py \
  app/providers/output_vector_qdrant.py \
  tests/test_output_neo4j.py tests/test_output_vector_qdrant.py \
  app/worker/callbacks.py app/worker/tasks/
git commit -m "fix(security): contain /connections/test and sanitize error output"
```

---

### Task 12: Upload constraints (Fix 4 — TDD)

The frontend uses `/upload/direct` today (`apps/web/src/app/jobs/new/client.tsx:42`); `getPresignedUrl` in `api-client.ts` has **no callers**, so changing its signature is safe.

**Files:**
- Test: `apps/api/tests/test_upload_limits.py`
- Modify: `apps/api/app/core/config.py` (two settings)
- Modify: `apps/api/app/api/v1/upload.py`
- Modify: `apps/api/app/core/storage.py:34-51`
- Modify: `apps/web/src/lib/api-client.ts:212-216`

- [ ] **Step 1: Write the failing tests**

```python
# apps/api/tests/test_upload_limits.py
import io

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_current_user
from app.core.security import TokenPayload
from app.main import app


@pytest.fixture
async def client(monkeypatch):
    app.dependency_overrides[get_current_user] = lambda: TokenPayload(
        {"sub": "user-1", "email": "u@x.com", "name": "U"}
    )
    # Never touch real storage in these tests.
    monkeypatch.setattr(
        "app.api.v1.upload.generate_presigned_upload_url",
        lambda **kwargs: "https://example.com/presigned",
    )
    monkeypatch.setattr(
        "app.api.v1.upload.upload_file_to_s3", lambda **kwargs: None
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def test_presigned_url_happy_path(client):
    res = await client.post(
        "/api/v1/upload/presigned-url",
        params={"filename": "doc.pdf", "file_size": 1024, "content_type": "application/pdf"},
    )
    assert res.status_code == 200
    assert res.json()["upload_url"] == "https://example.com/presigned"


async def test_presigned_url_rejects_oversize(client):
    res = await client.post(
        "/api/v1/upload/presigned-url",
        params={
            "filename": "doc.pdf",
            "file_size": 200 * 1024 * 1024,
            "content_type": "application/pdf",
        },
    )
    assert res.status_code == 413


async def test_presigned_url_rejects_disallowed_content_type(client):
    res = await client.post(
        "/api/v1/upload/presigned-url",
        params={"filename": "x.exe", "file_size": 10, "content_type": "application/x-msdownload"},
    )
    assert res.status_code == 415


async def test_presigned_url_requires_file_size(client):
    res = await client.post(
        "/api/v1/upload/presigned-url", params={"filename": "doc.pdf"}
    )
    assert res.status_code == 422


async def test_direct_upload_rejects_oversize(client, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.max_upload_bytes", 100)
    big = io.BytesIO(b"x" * 200)
    res = await client.post(
        "/api/v1/upload/direct",
        files={"file": ("doc.pdf", big, "application/pdf")},
    )
    assert res.status_code == 413


async def test_direct_upload_rejects_disallowed_content_type(client):
    res = await client.post(
        "/api/v1/upload/direct",
        files={"file": ("x.exe", io.BytesIO(b"MZ"), "application/x-msdownload")},
    )
    assert res.status_code == 415


async def test_direct_upload_happy_path(client):
    res = await client.post(
        "/api/v1/upload/direct",
        files={"file": ("doc.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
    )
    assert res.status_code == 200
    assert res.json()["file_key"].startswith("uploads/user-1/")
```

Run: `uv run pytest tests/test_upload_limits.py -v`
Expected: happy-path tests may PASS; the reject tests and `requires_file_size` FAIL.

- [ ] **Step 2: Add settings**

In `app/core/config.py`, under the upload-relevant section add:

```python
    # --- Upload constraints ---
    max_upload_bytes: int = 100 * 1024 * 1024  # 100 MB
    allowed_upload_content_types: list[str] = [
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/tiff",
        "image/webp",
    ]
```

- [ ] **Step 3: Implement endpoint validation**

Replace `app/api/v1/upload.py` contents with:

```python
"""ParseGrid API — File upload endpoints.

Supports two modes:
1. Presigned URL: Client uploads directly to S3/MinIO (preferred for large files)
2. Direct upload: Client sends file to FastAPI, which forwards to S3 (small files)

Both modes enforce the configured size cap and content-type allowlist.
"""

import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.security import TokenPayload
from app.core.storage import generate_presigned_upload_url, upload_file_to_s3
from app.schemas.job import UploadUrlResponse

router = APIRouter(prefix="/upload", tags=["Upload"])


def _validate_upload(content_type: str, size: int) -> None:
    if content_type not in settings.allowed_upload_content_types:
        raise HTTPException(
            status_code=415, detail=f"Unsupported content type: {content_type}"
        )
    if size <= 0 or size > settings.max_upload_bytes:
        limit_mb = settings.max_upload_bytes // (1024 * 1024)
        raise HTTPException(
            status_code=413, detail=f"File exceeds the {limit_mb} MB upload limit"
        )


@router.post(
    "/presigned-url",
    response_model=UploadUrlResponse,
    summary="Get a presigned URL for direct-to-S3 upload",
)
async def get_upload_url(
    filename: str,
    file_size: int,
    content_type: str = "application/pdf",
    user: TokenPayload = Depends(get_current_user),
) -> dict:
    """Generate a presigned PUT URL for the client to upload directly to
    MinIO/S3. The declared size is part of the signature, so the client
    cannot upload more bytes than it declared.
    """
    _validate_upload(content_type, file_size)
    file_key = f"uploads/{user.sub}/{uuid.uuid4()}/{filename}"
    upload_url = generate_presigned_upload_url(
        object_key=file_key,
        content_type=content_type,
        content_length=file_size,
    )
    return {"upload_url": upload_url, "file_key": file_key}


@router.post(
    "/direct",
    response_model=UploadUrlResponse,
    summary="Upload a file directly through FastAPI",
)
async def direct_upload(
    file: UploadFile = File(...),
    user: TokenPayload = Depends(get_current_user),
) -> dict:
    """Upload a file through FastAPI (for small files < 10MB).
    For larger files, use the presigned URL endpoint instead.
    """
    contents = await file.read()
    _validate_upload(file.content_type or "application/octet-stream", len(contents))
    file_key = f"uploads/{user.sub}/{uuid.uuid4()}/{file.filename}"
    upload_file_to_s3(
        file_bytes=contents,
        object_key=file_key,
        content_type=file.content_type or "application/octet-stream",
    )
    return {"upload_url": "", "file_key": file_key}
```

In `app/core/storage.py`, extend `generate_presigned_upload_url`:

```python
def generate_presigned_upload_url(
    object_key: str,
    content_type: str = "application/octet-stream",
    expires_in: int = 3600,
    content_length: int | None = None,
) -> str:
    """Generate a presigned URL for direct client-to-S3 upload.
    Avoids streaming large files through FastAPI. When `content_length`
    is given it becomes part of the signature, capping the upload size.
    """
    client = get_s3_client()
    params = {
        "Bucket": settings.s3_bucket,
        "Key": object_key,
        "ContentType": content_type,
    }
    if content_length is not None:
        params["ContentLength"] = content_length
    return client.generate_presigned_url(
        "put_object",
        Params=params,
        ExpiresIn=expires_in,
    )
```

- [ ] **Step 4: Run — all pass**

Run: `uv run pytest tests/test_upload_limits.py -v`
Expected: 7 PASS.

Note on `test_direct_upload_rejects_oversize`: it monkeypatches the attribute
on the live `settings` object (`app.core.config.settings.max_upload_bytes`),
which works because `_validate_upload` reads it at call time.

- [ ] **Step 5: Update the (caller-less) frontend client signature**

In `apps/web/src/lib/api-client.ts:212-216` replace:

```ts
  getPresignedUrl: (filename: string, token: string) =>
    request<UploadUrlResponse>(
      `/api/v1/upload/presigned-url?filename=${encodeURIComponent(filename)}`,
      { method: "POST", token },
    ),
```

with:

```ts
  getPresignedUrl: (
    filename: string,
    fileSize: number,
    contentType: string,
    token: string,
  ) =>
    request<UploadUrlResponse>(
      `/api/v1/upload/presigned-url?filename=${encodeURIComponent(filename)}&file_size=${fileSize}&content_type=${encodeURIComponent(contentType)}`,
      { method: "POST", token },
    ),
```

Run: `cd ../../apps/web && pnpm build` (from repo root: `pnpm --dir apps/web build`)
Expected: build succeeds (no callers to update).

- [ ] **Step 6: Commit**

```bash
git add tests/test_upload_limits.py app/api/v1/upload.py \
  app/core/config.py app/core/storage.py ../../apps/web/src/lib/api-client.ts
git commit -m "fix(security): enforce upload size cap and content-type allowlist"
```

---

### Task 13: Tier 3 API integration tests

Real Postgres required. Tests self-skip when it's unreachable, so the suite stays green for contributors without infra; CI always has the service container. httpx's ASGITransport does **not** run the app lifespan, so MinIO is not needed.

**Files:**
- Create: `apps/api/tests/integration/__init__.py` (empty)
- Create: `apps/api/tests/integration/conftest.py`
- Test: `apps/api/tests/integration/test_jobs_api.py`
- Modify: `apps/api/pyproject.toml` (register marker)

- [ ] **Step 1: Register the marker**

In `apps/api/pyproject.toml`, extend `[tool.pytest.ini_options]`:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
markers = [
    "integration: requires a reachable Postgres (TEST_DATABASE_URL)",
]
```

- [ ] **Step 2: Write the integration conftest**

```python
# apps/api/tests/integration/conftest.py
"""Integration fixtures: real Postgres, overridden DB dependency, no Celery/S3."""

import os
import time
from urllib.parse import urlparse

import jwt as pyjwt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.database import get_db
from app.main import app
from app.models.base import Base

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://parsegrid:parsegrid@localhost:5436/parsegrid_test",
)


def _ensure_test_database() -> bool:
    """Create the test database if missing. False when Postgres is down."""
    import psycopg2

    parsed = urlparse(TEST_DATABASE_URL.replace("+asyncpg", ""))
    dbname = parsed.path.lstrip("/")
    try:
        conn = psycopg2.connect(
            host=parsed.hostname,
            port=parsed.port,
            user=parsed.username,
            password=parsed.password,
            dbname="postgres",
            connect_timeout=3,
        )
    except Exception:
        return False
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
        if not cur.fetchone():
            cur.execute(f'CREATE DATABASE "{dbname}"')
    conn.close()
    return True


@pytest.fixture(scope="session")
def database_available():
    if not _ensure_test_database():
        pytest.skip("Postgres not reachable — integration tests skipped")


@pytest.fixture
async def client(database_available, monkeypatch):
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db

    # No real Celery, no real S3.
    from app.worker.tasks import ocr as ocr_tasks

    monkeypatch.setattr(
        ocr_tasks.process_document, "apply_async", lambda *a, **k: None
    )
    from app.api.v1 import jobs as jobs_module

    monkeypatch.setattr(jobs_module, "delete_object_from_s3", lambda *a, **k: None)
    monkeypatch.setattr(jobs_module, "delete_prefix_from_s3", lambda *a, **k: 0)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


def auth_header(user_id: str) -> dict[str, str]:
    token = pyjwt.encode(
        {"sub": user_id, "exp": int(time.time()) + 600},
        settings.auth_secret,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}
```

- [ ] **Step 3: Write the integration tests**

```python
# apps/api/tests/integration/test_jobs_api.py
import pytest

from tests.integration.conftest import auth_header

pytestmark = pytest.mark.integration

PAYLOAD = {"filename": "inv.pdf", "file_key": "uploads/u/x/inv.pdf", "file_size": 123}


async def _create(client, user: str) -> dict:
    res = await client.post("/api/v1/jobs", json=PAYLOAD, headers=auth_header(user))
    assert res.status_code in (200, 201)
    return res.json()


async def test_create_returns_uploaded_job(client):
    job = await _create(client, "user-a")
    assert job["status"] == "UPLOADED"
    assert len(job["id"]) == 36  # string UUID


async def test_get_is_scoped_to_owner(client):
    job = await _create(client, "user-a")
    own = await client.get(f"/api/v1/jobs/{job['id']}", headers=auth_header("user-a"))
    assert own.status_code == 200
    foreign = await client.get(f"/api/v1/jobs/{job['id']}", headers=auth_header("user-b"))
    assert foreign.status_code == 404


async def test_list_is_scoped_to_owner(client):
    await _create(client, "user-a")
    res = await client.get("/api/v1/jobs", headers=auth_header("user-b"))
    assert res.status_code == 200
    body = res.json()
    jobs = body["jobs"] if isinstance(body, dict) and "jobs" in body else body
    assert jobs == []


async def test_delete_is_scoped_to_owner(client):
    job = await _create(client, "user-a")
    res = await client.delete(f"/api/v1/jobs/{job['id']}", headers=auth_header("user-b"))
    assert res.status_code == 404


async def test_unauthenticated_request_rejected(client):
    res = await client.get("/api/v1/jobs")
    assert res.status_code in (401, 403)  # HTTPBearer yields 403 when header absent


async def test_connection_test_blocks_internal_target(client):
    res = await client.post(
        "/api/v1/connections/test",
        json={
            "connection_string": "postgresql://x:y@localhost:5436/parsegrid",
            "output_format": "SQL",
        },
        headers=auth_header("user-a"),
    )
    assert res.status_code == 200
    body = res.json()
    assert body["success"] is False
    assert "internal" in body["message"].lower()
```

- [ ] **Step 4: Run with infra up**

Run: `docker compose -f ../../infrastructure/docker-compose.yml up -d postgres && uv run pytest tests/integration -v`
Expected: 6 PASS (or all SKIP with a clear message if Postgres is down — verify the skip path too by stopping the container once).

Note: `test_list_is_scoped_to_owner` handles both `{"jobs": [...]}` envelope and
bare-list response shapes; check the actual response on first run and, if it is
unambiguous, simplify the assertion to the real shape.

- [ ] **Step 5: Full suite + lint**

Run: `uv run pytest -q && uv run ruff check .`
Expected: everything green.

- [ ] **Step 6: Commit**

```bash
git add tests/integration pyproject.toml
git commit -m "test: add scoped-auth API integration tests against real Postgres"
```

---

### Task 14: GitHub Actions CI

**Files:**
- Create: `.github/workflows/ci.yml` (repo root)

- [ ] **Step 1: Write the workflow**

```yaml
# .github/workflows/ci.yml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  api:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: apps/api
    services:
      postgres:
        image: pgvector/pgvector:pg16
        env:
          POSTGRES_USER: parsegrid
          POSTGRES_PASSWORD: parsegrid
          POSTGRES_DB: parsegrid
        ports:
          - "5436:5432"
        options: >-
          --health-cmd "pg_isready -U parsegrid"
          --health-interval 5s
          --health-timeout 5s
          --health-retries 10
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with:
          enable-cache: true
      - name: Install dependencies
        run: uv sync --extra dev
      - name: Lint
        run: uv run ruff check . && uv run ruff format --check .
      - name: Test
        run: uv run pytest -v
        env:
          TEST_DATABASE_URL: postgresql+asyncpg://parsegrid:parsegrid@localhost:5436/parsegrid_test

  web:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: pnpm/action-setup@v4
        with:
          version: 9
      - uses: actions/setup-node@v4
        with:
          node-version: 22
          cache: pnpm
      - name: Install dependencies
        run: pnpm install --frozen-lockfile
      - name: Build (typecheck gate)
        run: pnpm --dir apps/web build

  audit:
    runs-on: ubuntu-latest
    continue-on-error: true
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - name: pip-audit
        working-directory: apps/api
        run: uvx pip-audit || true
      - uses: pnpm/action-setup@v4
        with:
          version: 9
      - uses: actions/setup-node@v4
        with:
          node-version: 22
      - name: pnpm audit
        run: pnpm audit || true
```

Adjustment note: if `apps/web/package.json` (or the root `package.json`) has a
`packageManager` field, drop the `version: 9` lines and let
`pnpm/action-setup` read it. If `pnpm --dir apps/web build` fails because the
build script needs the workspace context, use `pnpm --filter <web package
name> build` with the `name` from `apps/web/package.json` instead.

- [ ] **Step 2: Lint-check the workflow locally**

Run: `uvx --from yamllint yamllint -d relaxed ../../.github/workflows/ci.yml || true`
(or visually verify indentation). Then validate the web build command from
repo root: `pnpm install --frozen-lockfile && pnpm --dir apps/web build`.
Expected: build succeeds locally exactly as CI will run it.

- [ ] **Step 3: Commit and verify on GitHub**

```bash
git add ../../.github/workflows/ci.yml
git commit -m "ci: lint, test (pgvector service), web build, dependency audit"
git push origin main
gh run watch --exit-status || gh run view --log-failed
```

Expected: all three jobs green (audit may be yellow/informational). Fix any
CI-only failures (path or cache issues) before proceeding.

---

### Task 15: Final verification and spec close-out

**Files:**
- Modify: `docs/superpowers/specs/2026-06-10-foundation-design.md:4` (status line)

- [ ] **Step 1: Full local verification**

Run, from `apps/api/`:
```bash
uv run ruff check . && uv run ruff format --check . && uv run pytest -v
```
Expected: 0 lint errors; full suite green — roughly 60+ tests across 12 files,
integration tests passing (infra up) or cleanly skipping (infra down).

Run, from repo root: `pnpm --dir apps/web build`
Expected: production build succeeds.

- [ ] **Step 2: Update the spec status**

In `docs/superpowers/specs/2026-06-10-foundation-design.md`, change
`**Status:** Approved` to `**Status:** Implemented`.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-06-10-foundation-design.md
git commit -m "docs: mark Foundation spec implemented"
```

- [ ] **Step 4: Verify CI is green on the final push**

```bash
git push origin main
gh run watch --exit-status
```
Expected: CI green. Foundation is done; next sub-project is Dataset Consolidation (needs its own brainstorm + spec).
