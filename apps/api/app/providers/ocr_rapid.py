"""ParseGrid — RapidOCR Provider (Local, Air-Gapped).

Runs the PP-OCR detection/recognition models through ONNX Runtime via
RapidOCR. This avoids the PaddlePaddle native framework, which segfaults on
linux/arm64 (e.g. Docker on Apple Silicon); ONNX Runtime is stable and fast
on both arm64 and amd64, and the bundled models need no network download.

Smart OCR Router: native digital text is read instantly with PyMuPDF; only
scanned/image pages fall back to RapidOCR. Output is identical regardless of
the path taken.
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
    """Lazy-initialize the RapidOCR engine (singleton).

    RapidOCR bundles the PP-OCR ONNX models, so the first call only loads
    them from disk — no model download, no network.
    """
    global _ocr_engine
    if _ocr_engine is None:
        from rapidocr_onnxruntime import RapidOCR

        _ocr_engine = RapidOCR()
        logger.info("RapidOCR engine initialized")
    return _ocr_engine


def _ocr_image_to_regions(image_path: str) -> list[OCRRegion]:
    """Run RapidOCR on an image and map its output to OCRRegion objects.

    RapidOCR returns `(result, elapse)` where `result` is None or a list of
    `[box, text, score]`; `box` is four [x, y] points. We reduce each box to
    an (x1, y1, x2, y2) bbox to match the PyMuPDF native path.
    """
    engine = _get_ocr_engine()
    result, _elapse = engine(image_path)

    regions: list[OCRRegion] = []
    for item in result or []:
        box, text, score = item[0], item[1], item[2]
        clean = (text or "").strip()
        if not clean:
            continue
        xs = [pt[0] for pt in box]
        ys = [pt[1] for pt in box]
        bbox = (int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys)))
        regions.append(
            OCRRegion(
                region_type="text",
                bbox=bbox,
                text=clean,
                confidence=float(score),
            )
        )

    regions.sort(key=lambda r: (r.bbox[1], r.bbox[0]))
    return regions


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
    OCRRegion objects structurally identical to the OCR output.
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


class RapidOCRProvider(BaseOCRProvider):
    """Local OCR provider using RapidOCR (PP-OCR models via ONNX Runtime).

    Process flow:
    1. PDF → per-page smart routing (native text vs scanned image)
    2. Scanned pages → rendered to images at `dpi` via PyMuPDF → RapidOCR
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
        regions = _ocr_image_to_regions(image_path)
        return OCRPage(page_number=1, width=0, height=0, regions=regions)

    def _process_pdf(self, pdf_path: str) -> OCRResult:
        """Process PDF with smart routing: native text extraction or RapidOCR fallback.

        For each page, checks if it's a native digital page or a scanned image.
        Digital pages are extracted instantly via PyMuPDF; scanned pages fall back
        to RapidOCR. Output format is identical regardless of path taken.
        """
        doc = fitz.open(pdf_path)
        pages: list[OCRPage] = []
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
                    # SLOW PATH: scanned/image page — fall back to RapidOCR
                    mat = fitz.Matrix(self.dpi / 72, self.dpi / 72)
                    pix = page.get_pixmap(matrix=mat)
                    img_path = os.path.join(tmp_dir, f"page_{page_num + 1}.png")
                    pix.save(img_path)

                    regions = _ocr_image_to_regions(img_path)
                    ocr_page = OCRPage(
                        page_number=page_num + 1,
                        width=pix.width,
                        height=pix.height,
                        regions=regions,
                    )

                    slow_count += 1
                    logger.info(
                        f"  Page {page_num + 1}/{len(doc)}: "
                        f"{len(ocr_page.regions)} text lines [rapid-ocr]"
                    )

                pages.append(ocr_page)

        doc.close()

        logger.info(
            f"Smart router: {fast_count} native + {slow_count} OCR = {len(pages)} total pages"
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
