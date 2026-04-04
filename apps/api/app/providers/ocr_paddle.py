"""ParseGrid — PaddleOCR Provider (Local, Air-Gapped).

Uses PaddleOCR's lightweight OCR pipeline for text extraction.
Runs entirely on-device — no SaaS APIs, no network calls.

PDF pages are converted to images via PyMuPDF, then each page
is processed with PaddleOCR for text detection + recognition.
The LLM handles structural understanding from the raw text.
"""

import logging
import os
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
        """Convert PDF pages to images, OCR each page."""
        doc = fitz.open(pdf_path)
        pages: list[OCRPage] = []
        engine = _get_ocr_engine()

        logger.info(f"Processing PDF: {pdf_path} ({len(doc)} pages)")

        with tempfile.TemporaryDirectory() as tmp_dir:
            for page_num in range(len(doc)):
                page = doc[page_num]
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

                pages.append(ocr_page)
                logger.info(
                    f"  Page {page_num + 1}/{len(doc)}: {len(ocr_page.regions)} text lines"
                )

        doc.close()

        return OCRResult(
            pages=pages,
            page_count=len(pages),
            metadata={"source": pdf_path, "dpi": self.dpi},
        )
