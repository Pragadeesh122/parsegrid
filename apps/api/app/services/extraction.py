"""ParseGrid — Extraction service helpers (Phase 7).

The chunker splits OCR text on paragraph and page boundaries and tracks
which `--- Page N ---` markers each chunk covers, so reconciliation can
attach `source_page_numbers` provenance to extracted rows.

The legacy single-table merge helper has been removed — Phase 7 buckets
by table inside `app/worker/tasks/merge.py`.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_PAGE_MARKER_RE = re.compile(r"--- Page (\d+) ---")


def _pages_in(text: str) -> list[int]:
    """Return the unique page numbers referenced in `text` (in order)."""
    seen: set[int] = set()
    out: list[int] = []
    for m in _PAGE_MARKER_RE.finditer(text):
        n = int(m.group(1))
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def chunk_text(
    full_text: str,
    chunk_size: int = 3000,
    overlap: int = 500,
) -> list[dict[str, Any]]:
    """Split document text into overlapping chunks for parallel extraction.

    Each returned dict has:
        - chunk_index: int
        - text: str
        - start_char / end_char: int
        - pages: list[int]  (page numbers referenced inside the chunk)

    The overlap is preserved verbatim so the LLM has context for records
    that straddle a chunk boundary.
    """
    if len(full_text) <= chunk_size:
        return [
            {
                "chunk_index": 0,
                "text": full_text,
                "start_char": 0,
                "end_char": len(full_text),
                "pages": _pages_in(full_text),
            }
        ]

    paragraphs = re.split(r"\n{2,}|(?=--- Page \d+)", full_text)

    chunks: list[dict[str, Any]] = []
    current_chunk = ""
    current_start = 0
    char_pos = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            char_pos += 2
            continue

        if len(current_chunk) + len(para) + 2 > chunk_size and current_chunk:
            chunks.append(
                {
                    "chunk_index": len(chunks),
                    "text": current_chunk.strip(),
                    "start_char": current_start,
                    "end_char": char_pos,
                    "pages": _pages_in(current_chunk),
                }
            )
            overlap_text = (
                current_chunk[-overlap:] if len(current_chunk) > overlap else current_chunk
            )
            current_chunk = overlap_text + "\n\n" + para
            current_start = max(0, char_pos - overlap)
        else:
            if not current_chunk:
                current_start = char_pos
            current_chunk += ("\n\n" if current_chunk else "") + para

        char_pos += len(para) + 2

    if current_chunk.strip():
        chunks.append(
            {
                "chunk_index": len(chunks),
                "text": current_chunk.strip(),
                "start_char": current_start,
                "end_char": char_pos,
                "pages": _pages_in(current_chunk),
            }
        )

    if chunks:
        logger.info(
            f"Split document into {len(chunks)} chunks "
            f"(avg {sum(len(c['text']) for c in chunks) // len(chunks)} chars/chunk)"
        )
    return chunks
