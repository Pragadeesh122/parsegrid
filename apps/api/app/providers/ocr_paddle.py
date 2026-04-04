"""ParseGrid — PaddleOCR Provider (Local, Air-Gapped).

Uses PaddleOCR's lightweight OCR pipeline for text extraction.
Runs entirely on-device — no SaaS APIs, no network calls.

PDF pages are converted to images via PyMuPDF, then each page
is processed with PaddleOCR for text detection + recognition.
The LLM handles structural understanding from the raw text.
"""

import logging
import os
import re
import tempfile
from pathlib import Path

import fitz  # PyMuPDF

from app.providers import BaseOCRProvider, OCRPage, OCRRegion, OCRResult

logger = logging.getLogger(__name__)

_ocr_engine = None


def _get_ocr_engine():
    """Lazy-initialize PaddleOCR engine (singleton).

    Disables doc orientation, unwarping, and textline orientation
    to keep model loading fast and memory usage low.
    """
    global _ocr_engine
    if _ocr_engine is None:
        from paddleocr import PaddleOCR

        _ocr_engine = PaddleOCR(
            text_detection_model_name="PP-OCRv5_mobile_det",
            text_recognition_model_name="en_PP-OCRv5_mobile_rec",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )
        logger.info("PaddleOCR engine initialized")
    return _ocr_engine


def _parse_ocr_page(page_result, page_number: int, width: int, height: int) -> OCRPage:
    """Convert a PaddleOCR v3 result object into an OCRPage."""
    texts = page_result.get("rec_texts", [])
    polys = page_result.get("dt_polys", [])
    scores = page_result.get("rec_scores", [])

    regions: list[OCRRegion] = []
    for i, text in enumerate(texts):
        if not text.strip():
            continue

        # Convert polygon [[x1,y1],[x2,y2],[x3,y3],[x4,y4]] to bbox (x1,y1,x2,y2)
        if i < len(polys):
            poly = polys[i]
            xs = [p[0] for p in poly]
            ys = [p[1] for p in poly]
            bbox = (int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys)))
        else:
            bbox = (0, 0, 0, 0)

        confidence = float(scores[i]) if i < len(scores) else 0.0

        regions.append(
            OCRRegion(
                region_type="text",
                bbox=bbox,
                text=text.strip(),
                confidence=confidence,
            )
        )

    regions.sort(key=lambda r: (r.bbox[1], r.bbox[0]))
    return OCRPage(page_number=page_number, width=width, height=height, regions=regions)


def _is_page_scanned(page: fitz.Page, text: str) -> bool:
    """Determine if a PDF page needs OCR based on page composition.

    Checks three signals:
    1. No embedded text at all -> definitely scanned
    2. Low alphanumeric ratio -> hidden garbage OCR layer from scanner
    3. Images present with minimal text -> data locked in images
    """
    stripped = text.strip()

    if not stripped:
        return True

    alphanumeric_count = len(re.findall(r"[a-zA-Z0-9]", stripped))
    total_chars = len(stripped)
    if total_chars > 0 and (alphanumeric_count / total_chars) < 0.4:
        return True

    images = page.get_images()
    if len(images) > 0 and total_chars < 100:
        return True

    return False


def _extract_native_regions(page: fitz.Page, page_number: int) -> OCRPage:
    """Extract text from a native digital PDF page via PyMuPDF.

    Uses page.get_text("blocks") for block-level bounding boxes, producing
    OCRRegion objects structurally identical to PaddleOCR output.
    """
    rect = page.rect
    width = int(rect.width)
    height = int(rect.height)

    blocks = page.get_text("blocks")
    regions: list[OCRRegion] = []
    for block in blocks:
        # block = (x0, y0, x1, y1, text, block_no, block_type)
        # block_type: 0 = text, 1 = image
        if block[6] != 0:
            continue
        text = block[4].strip()
        if not text:
            continue
        bbox = (int(block[0]), int(block[1]), int(block[2]), int(block[3]))
        regions.append(
            OCRRegion(
                region_type="text",
                bbox=bbox,
                text=text,
                confidence=1.0,
            )
        )

    regions.sort(key=lambda r: (r.bbox[1], r.bbox[0]))
    return OCRPage(page_number=page_number, width=width, height=height, regions=regions)


class PaddleOCRProvider(BaseOCRProvider):
    """Local OCR provider using PaddleOCR (text detection + recognition).

    Process flow:
    1. PDF → images at 200 DPI via PyMuPDF
    2. Each image → PaddleOCR text detection + recognition
    3. Lines sorted by reading order (top-to-bottom, left-to-right)
    """

    def __init__(self, dpi: int = 200):
        self.dpi = dpi

    def process_document(self, file_path: str) -> OCRResult:
        """Process a PDF or image file into structured OCR output."""
        path = Path(file_path)

        if path.suffix.lower() == ".pdf":
            return self._process_pdf(file_path)
        elif path.suffix.lower() in (".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp"):
            page = self.process_image(file_path)
            return OCRResult(pages=[page], page_count=1)
        else:
            raise ValueError(f"Unsupported file format: {path.suffix}")

    def process_image(self, image_path: str) -> OCRPage:
        """Process a single image with OCR."""
        engine = _get_ocr_engine()
        results = engine.predict(image_path)
        if results:
            return _parse_ocr_page(results[0], page_number=1, width=0, height=0)
        return OCRPage(page_number=1, width=0, height=0, regions=[])

    def _process_pdf(self, pdf_path: str) -> OCRResult:
        """Process PDF with smart routing: native text extraction or PaddleOCR fallback.

        For each page, checks if it's a native digital page or a scanned image.
        Digital pages are extracted instantly via PyMuPDF; scanned pages fall back
        to PaddleOCR. Output format is identical regardless of path taken.
        """
        doc = fitz.open(pdf_path)
        pages: list[OCRPage] = []
        engine = None  # lazy-init only if a scanned page is found
        fast_count = 0
        slow_count = 0

        logger.info(f"Processing PDF: {pdf_path} ({len(doc)} pages)")

        with tempfile.TemporaryDirectory() as tmp_dir:
            for page_num in range(len(doc)):
                page = doc[page_num]
                raw_text = page.get_text("text")

                if not _is_page_scanned(page, raw_text):
                    # FAST PATH: native digital text
                    ocr_page = _extract_native_regions(page, page_number=page_num + 1)
                    fast_count += 1
                    logger.info(
                        f"  Page {page_num + 1}/{len(doc)}: "
                        f"{len(ocr_page.regions)} text blocks [native]"
                    )
                else:
                    # SLOW PATH: scanned/image page — fall back to PaddleOCR
                    if engine is None:
                        engine = _get_ocr_engine()

                    mat = fitz.Matrix(self.dpi / 72, self.dpi / 72)
                    pix = page.get_pixmap(matrix=mat)
                    img_path = os.path.join(tmp_dir, f"page_{page_num + 1}.png")
                    pix.save(img_path)

                    results = engine.predict(img_path)
                    if results:
                        ocr_page = _parse_ocr_page(
                            results[0],
                            page_number=page_num + 1,
                            width=pix.width,
                            height=pix.height,
                        )
                    else:
                        ocr_page = OCRPage(
                            page_number=page_num + 1,
                            width=pix.width,
                            height=pix.height,
                        )

                    slow_count += 1
                    logger.info(
                        f"  Page {page_num + 1}/{len(doc)}: "
                        f"{len(ocr_page.regions)} text lines [paddle-ocr]"
                    )

                pages.append(ocr_page)

        doc.close()

        logger.info(
            f"Smart router: {fast_count} native + {slow_count} OCR "
            f"= {len(pages)} total pages"
        )

        return OCRResult(
            pages=pages,
            page_count=len(pages),
            metadata={
                "source": pdf_path,
                "dpi": self.dpi,
                "native_pages": fast_count,
                "ocr_pages": slow_count,
            },
        )
