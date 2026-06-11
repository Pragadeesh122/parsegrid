from app.services.reconciliation import (
    add_provenance,
    canonicalize_parents,
    reconcile_model,
)
from tests.factories import make_column, make_model, make_table

PEOPLE = make_table("people", [make_column("name", pk=True), make_column("city")])


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
