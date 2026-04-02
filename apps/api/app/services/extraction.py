"""ParseGrid — Extraction service (Map-Reduce orchestration).

Handles the core extraction workflow:
1. Download document from S3
2. OCR via PaddleOCR (layout-aware)
3. Schema discovery via LLM
4. Text chunking with overlap
5. Parallel extraction (Map phase via Celery)
6. Programmatic merge (Reduce phase)
"""

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


def chunk_text(
    full_text: str,
    chunk_size: int = 3000,
    overlap: int = 500,
) -> list[dict[str, Any]]:
    """Split document text into overlapping chunks for parallel extraction.

    Uses paragraph boundaries to avoid splitting mid-sentence.
    Each chunk includes metadata for deduplication during merge.

    Args:
        full_text: Complete document text.
        chunk_size: Target characters per chunk.
        overlap: Characters of overlap between consecutive chunks.

    Returns:
        List of dicts with chunk_index, text, start_char, end_char.
    """
    if len(full_text) <= chunk_size:
        return [
            {
                "chunk_index": 0,
                "text": full_text,
                "start_char": 0,
                "end_char": len(full_text),
            }
        ]

    # Split on paragraph boundaries (double newline or page markers)
    paragraphs = re.split(r"\n{2,}|(?=--- Page \d+)", full_text)

    chunks: list[dict[str, Any]] = []
    current_chunk = ""
    current_start = 0
    char_pos = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            char_pos += 2  # account for the newlines
            continue

        if len(current_chunk) + len(para) + 2 > chunk_size and current_chunk:
            # Save current chunk
            chunks.append(
                {
                    "chunk_index": len(chunks),
                    "text": current_chunk.strip(),
                    "start_char": current_start,
                    "end_char": char_pos,
                }
            )

            # Start new chunk with overlap
            overlap_text = current_chunk[-overlap:] if len(current_chunk) > overlap else current_chunk
            current_chunk = overlap_text + "\n\n" + para
            current_start = max(0, char_pos - overlap)
        else:
            if not current_chunk:
                current_start = char_pos
            current_chunk += ("\n\n" if current_chunk else "") + para

        char_pos += len(para) + 2

    # Don't forget the last chunk
    if current_chunk.strip():
        chunks.append(
            {
                "chunk_index": len(chunks),
                "text": current_chunk.strip(),
                "start_char": current_start,
                "end_char": char_pos,
            }
        )

    logger.info(
        f"Split document into {len(chunks)} chunks "
        f"(avg {sum(len(c['text']) for c in chunks) // len(chunks)} chars/chunk)"
    )
    return chunks


def merge_extraction_results(
    chunk_results: list[dict[str, Any]],
    schema: dict,
) -> dict[str, Any]:
    """Merge extracted data from all chunks into a single dataset.

    This is the Reduce phase — purely deterministic, NO LLM involved.
    Handles deduplication of records from overlap regions.

    Args:
        chunk_results: List of extraction results from each chunk.
        schema: The locked JSON schema (used to identify the items array).

    Returns:
        Merged extraction result conforming to the schema.
    """
    # Find the array field in the schema (the main data container)
    items_key = _find_items_key(schema)

    all_records: list[dict] = []
    seen_fingerprints: set[str] = set()

    for chunk in sorted(chunk_results, key=lambda c: c.get("chunk_index", 0)):
        data = chunk.get("data", {})
        if isinstance(data, dict):
            records = data.get(items_key, [])
        elif isinstance(data, list):
            records = data
        else:
            continue

        for record in records:
            if not isinstance(record, dict):
                continue

            # Create fingerprint for deduplication
            fingerprint = _record_fingerprint(record)
            if fingerprint not in seen_fingerprints:
                seen_fingerprints.add(fingerprint)
                all_records.append(record)

    logger.info(
        f"Merged {sum(len(c.get('data', {}).get(items_key, [])) for c in chunk_results if isinstance(c.get('data'), dict))} "
        f"raw records → {len(all_records)} unique records "
        f"({len(seen_fingerprints)} fingerprints)"
    )

    return {items_key: all_records}


def _find_items_key(schema: dict) -> str:
    """Find the main array field in the schema."""
    properties = schema.get("properties", {})
    for key, prop_def in properties.items():
        if prop_def.get("type") == "array":
            return key
    # Fallback: look for common names
    for key in ("items", "records", "data", "rows", "entries"):
        if key in properties:
            return key
    return "items"


def _record_fingerprint(record: dict) -> str:
    """Create a fingerprint for deduplication.

    Uses the first 3 non-null string values concatenated.
    """
    values = []
    for v in record.values():
        if v is not None and isinstance(v, str) and v.strip():
            values.append(v.strip().lower()[:50])
            if len(values) >= 3:
                break
    return "|".join(values) if values else str(hash(frozenset(record.items())))
