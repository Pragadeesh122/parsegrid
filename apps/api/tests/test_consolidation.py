import pytest

from app.services.consolidation import (
    allocate_sampling_budget,
    build_compat_report,
    union_buckets,
)
from tests.factories import make_column, make_model, make_table

INVOICES = make_table("invoices", [make_column("invoice_number", pk=True), make_column("note")])
LINES = make_table("lines", [make_column("description")])  # no PK
MODEL = make_model([INVOICES, LINES])


class TestAllocateSamplingBudget:
    def test_empty(self):
        assert allocate_sampling_budget({}) == {}

    def test_single_doc_gets_full_budget_capped_by_pages(self):
        assert allocate_sampling_budget({"a": 8}, budget=15) == {"a": 8}
        assert allocate_sampling_budget({"a": 40}, budget=15) == {"a": 15}

    def test_floor_of_three_for_small_docs(self):
        alloc = allocate_sampling_budget({"a": 2, "b": 100}, budget=15)
        assert alloc["a"] == 2  # floor capped by the doc's own page count
        assert alloc["b"] == 13
        assert sum(alloc.values()) == 15

    def test_proportional_split_is_deterministic(self):
        counts = {"a": 50, "b": 50, "c": 10}
        first = allocate_sampling_budget(counts, budget=15)
        assert first == allocate_sampling_budget(counts, budget=15)
        assert sum(first.values()) == 15
        assert all(first[d] >= 3 for d in counts)
        assert first["a"] >= first["c"]

    def test_floors_win_when_budget_too_small(self):
        # 6 docs x floor 3 = 18 > 15: floors are kept, budget overshoots.
        alloc = allocate_sampling_budget({f"d{i}": 30 for i in range(6)}, budget=15)
        assert all(v == 3 for v in alloc.values())


class TestUnionBuckets:
    def test_unions_rows_and_pages_with_string_keys(self):
        doc_a = {
            "tables": {
                "invoices": {
                    "rows": [{"invoice_number": "1", "__chunk_index": "docA:0"}],
                    "chunk_pages": {"docA:0": [1]},
                }
            }
        }
        doc_b = {
            "tables": {
                "invoices": {
                    "rows": [{"invoice_number": "2", "__chunk_index": "docB:0"}],
                    "chunk_pages": {"docB:0": [4]},
                },
                "lines": {
                    "rows": [{"description": "x", "__chunk_index": "docB:1"}],
                    "chunk_pages": {"docB:1": [5]},
                },
            }
        }
        rows, pages = union_buckets([("docA", doc_a), ("docB", doc_b)])
        assert len(rows["invoices"]) == 2
        assert rows["lines"][0]["description"] == "x"
        assert pages["invoices"] == {"docA:0": [1], "docB:0": [4]}

    def test_legacy_integer_keys_are_normalized_to_strings(self):
        # Pre-consolidation merged_buckets.json used bare int chunk indexes.
        legacy = {
            "tables": {
                "invoices": {
                    "rows": [{"invoice_number": "9", "__chunk_index": 0}],
                    "chunk_pages": {"0": [2]},
                }
            }
        }
        rows, pages = union_buckets([("docL", legacy)])
        assert rows["invoices"][0]["__chunk_index"] == "0"
        assert pages["invoices"] == {"0": [2]}


class TestBuildCompatReport:
    def test_healthy_append_auto_continues(self):
        buckets = {
            "invoices": [{"invoice_number": "1"}, {"invoice_number": "2"}],
            "lines": [{"description": "a"}],
        }
        report = build_compat_report(buckets, MODEL, max_pk_null_ratio=0.5)
        assert report["needs_review"] is False
        assert report["total_rows"] == 3
        assert report["rows_per_table"] == {"invoices": 2, "lines": 1}

    def test_zero_rows_needs_review(self):
        report = build_compat_report({}, MODEL, max_pk_null_ratio=0.5)
        assert report["needs_review"] is True
        assert any("no rows" in r for r in report["reasons"])

    def test_pk_null_ratio_over_threshold_needs_review(self):
        buckets = {
            "invoices": [
                {"invoice_number": None},
                {"invoice_number": None},
                {"invoice_number": "ok"},
            ]
        }
        report = build_compat_report(buckets, MODEL, max_pk_null_ratio=0.5)
        assert report["needs_review"] is True
        assert report["pk_null_rows"] == 2

    def test_all_pk_tables_empty_needs_review(self):
        # Rows only in the PK-less table.
        buckets = {"lines": [{"description": "a"}]}
        report = build_compat_report(buckets, MODEL, max_pk_null_ratio=0.5)
        assert report["needs_review"] is True
        assert report["empty_pk_tables"] == ["invoices"]

    def test_histogram_drift_is_flag_only(self):
        buckets = {"invoices": [{"invoice_number": "1"}]}
        report = build_compat_report(
            buckets,
            MODEL,
            max_pk_null_ratio=0.5,
            dataset_histogram={"table": 90, "text": 10},
            document_histogram={"text": 100},
        )
        assert report["needs_review"] is False  # drift alone never blocks
        assert report["profile_drift"] is not None


class _BoomOpenAI:
    """Constructing the client fails → entity_resolution's documented fallback."""

    def __init__(self, *args, **kwargs):
        raise RuntimeError("no network in tests")


class TestUnionThroughReconciliation:
    @pytest.fixture(autouse=True)
    def _no_openai(self, monkeypatch):
        monkeypatch.setattr("openai.OpenAI", _BoomOpenAI)

    def test_same_entity_across_documents_dedupes_to_one_row(self):
        from app.services.reconciliation import reconcile_model

        doc_a = {
            "tables": {
                "invoices": {
                    "rows": [{"invoice_number": "INV-1", "note": None, "__chunk_index": "a:0"}],
                    "chunk_pages": {"a:0": [1]},
                }
            }
        }
        doc_b = {
            "tables": {
                "invoices": {
                    "rows": [{"invoice_number": "inv-1 ", "note": "paid", "__chunk_index": "b:0"}],
                    "chunk_pages": {"b:0": [3]},
                }
            }
        }
        rows, pages = union_buckets([("a", doc_a), ("b", doc_b)])
        finalized, _ = reconcile_model(
            bucketed_rows=rows, chunk_pages_by_index=pages, locked_model=MODEL
        )
        assert len(finalized["invoices"]) == 1
        merged = finalized["invoices"][0]
        assert merged["note"] == "paid"  # null filled from the other document

    def test_provenance_pages_survive_document_prefixed_keys(self):
        from app.services.reconciliation import reconcile_model

        doc = {
            "tables": {
                "invoices": {
                    "rows": [{"invoice_number": "X", "note": "n", "__chunk_index": "docZ:2"}],
                    "chunk_pages": {"docZ:2": [7, 8]},
                }
            }
        }
        rows, pages = union_buckets([("docZ", doc)])
        finalized, _ = reconcile_model(
            bucketed_rows=rows, chunk_pages_by_index=pages, locked_model=MODEL
        )
        assert finalized["invoices"][0]["source_page_numbers"] == [7, 8]
