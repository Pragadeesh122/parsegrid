"""ParseGrid — Whole-document profiling (Phase 7).

For FULL jobs, after OCR completes we need to give the LLM enough signal to
decide whether the document is one repeated entity or a heterogeneous packet
of multiple entities. The MVP heuristic uses cheap deterministic sampling
based on the OCR layout JSON — no embeddings, no extra LLM calls.

Sampling strategy (capped at ~15 pages):
    1. First 3 pages         (front matter / cover)
    2. Last 2 pages          (appendices)
    3. Up to 3 "diversity"   (pages with the most distinct OCR region_types)
    4. Evenly-spaced fillers through the middle to reach the cap

Output:
    - The sorted, deduplicated list of sampled page numbers
    - A whole-document histogram of region_types
    - Concatenated page text annotated with region-type markers
"""

from __future__ import annotations

from collections import Counter
from typing import Any

# Cap on the number of pages we feed to the LLM. ~15 pages of OCR text fits
# comfortably under the gpt-4o context budget while leaving headroom for the
# system prompt and meta-schema definition.
MAX_SAMPLED_PAGES = 15

# How many "high-region-diversity" pages to surface in addition to the
# fixed front/back samples.
DIVERSITY_PAGE_COUNT = 3


def profile_document(
    ocr_json: dict[str, Any],
) -> tuple[list[int], dict[str, int]]:
    """Pick representative pages and build a region-type histogram.

    Args:
        ocr_json: Parsed contents of `parsed/{job_id}/ocr_result.json` —
            shape `{page_count: int, pages: [{page_number, regions: [...]}]}`.

    Returns:
        (sampled_page_numbers, region_type_histogram)
    """
    pages = ocr_json.get("pages") or []
    total_pages = ocr_json.get("page_count") or len(pages)
    if total_pages == 0:
        return [], {}

    # Build the global region-type histogram and a per-page diversity score.
    histogram: Counter[str] = Counter()
    diversity_scores: list[tuple[int, int]] = []  # (page_number, distinct_region_types)
    for page in pages:
        page_number = page.get("page_number") or 0
        regions = page.get("regions") or []
        types_on_page: set[str] = set()
        for region in regions:
            rtype = region.get("region_type") or "unknown"
            histogram[rtype] += 1
            types_on_page.add(rtype)
        diversity_scores.append((page_number, len(types_on_page)))

    selected: set[int] = set()

    # 1. First 3 pages
    for p in range(1, min(3, total_pages) + 1):
        selected.add(p)

    # 2. Last 2 pages
    for p in range(max(1, total_pages - 1), total_pages + 1):
        selected.add(p)

    # 3. Diversity pages — highest distinct-region-type counts, ties broken by
    #    page number ascending so the result is deterministic.
    diversity_sorted = sorted(diversity_scores, key=lambda t: (-t[1], t[0]))
    for page_number, _ in diversity_sorted:
        if len(selected) >= MAX_SAMPLED_PAGES:
            break
        if (
            len([p for p in selected if p not in range(1, 4) and p < total_pages - 1])
            >= DIVERSITY_PAGE_COUNT
        ):
            break
        selected.add(page_number)

    # 4. Evenly-spaced fillers through the middle.
    if len(selected) < MAX_SAMPLED_PAGES and total_pages > MAX_SAMPLED_PAGES:
        remaining_slots = MAX_SAMPLED_PAGES - len(selected)
        # Use a stride that walks the middle of the doc.
        stride = max(1, total_pages // (remaining_slots + 1))
        for offset in range(stride, total_pages, stride):
            if len(selected) >= MAX_SAMPLED_PAGES:
                break
            selected.add(offset)

    # If the doc is short, just take everything.
    if total_pages <= MAX_SAMPLED_PAGES:
        selected = set(range(1, total_pages + 1))

    sampled = sorted(p for p in selected if 1 <= p <= total_pages)
    return sampled, dict(histogram)


def build_profile_context(
    sampled_pages: list[int], ocr_json: dict[str, Any]
) -> str:
    """Concatenate sampled-page text with region-type markers for the LLM.

    Each page is prefixed with `--- Page N (types: ...) ---` so the LLM can
    see structural variation between pages without us shipping the whole
    layout JSON.
    """
    pages_by_number = {
        p.get("page_number"): p for p in (ocr_json.get("pages") or [])
    }
    sampled_set = set(sampled_pages)

    blocks: list[str] = []
    for page_number in sorted(sampled_set):
        page = pages_by_number.get(page_number)
        if not page:
            continue
        regions = page.get("regions") or []
        types = sorted({r.get("region_type") or "unknown" for r in regions})
        text = "\n".join(
            (r.get("text") or "").strip()
            for r in regions
            if (r.get("text") or "").strip()
        )
        header = f"--- Page {page_number} (types: {', '.join(types) or 'none'}) ---"
        blocks.append(f"{header}\n{text}")

    return "\n\n".join(blocks)
