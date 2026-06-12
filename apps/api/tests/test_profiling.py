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


def test_budget_overrides_default_cap():
    doc = _doc([_page(n, ["text"]) for n in range(1, 51)])
    sampled, _ = profile_document(doc, budget=6)
    assert len(sampled) == 6
    assert sampled == sorted(sampled)
    assert 1 in sampled  # front anchor survives trimming


def test_budget_short_doc_still_takes_everything_under_budget():
    doc = _doc([_page(n, ["text"]) for n in range(1, 5)])
    sampled, _ = profile_document(doc, budget=6)
    assert sampled == [1, 2, 3, 4]


def test_default_budget_unchanged():
    doc = _doc([_page(n, ["text"]) for n in range(1, 51)])
    assert profile_document(doc) == profile_document(doc, budget=MAX_SAMPLED_PAGES)
