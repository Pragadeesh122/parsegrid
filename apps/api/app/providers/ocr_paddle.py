"""ParseGrid — PaddleOCR Provider (Local, Air-Gapped).

Uses PaddleOCR's PPStructure for layout-aware document parsing.
Runs entirely on-device — no SaaS APIs, no network calls.

Capabilities:
- Layout analysis (text, title, table, figure detection)
- Reading order preservation
- PDF-to-image conversion via PyMuPDF
- Structure-preserving text extraction
"""

import logging
import os
import tempfile
from pathlib import Path

import fitz  # PyMuPDF
import numpy as np

from app.providers import BaseOCRProvider, OCRPage, OCRRegion, OCRResult

logger = logging.getLogger(__name__)

# Lazy-load PaddleOCR to avoid import overhead on every worker start
_ocr_engine = None
_structure_engine = None


def _get_ocr_engine():
    """Lazy-initialize PaddleOCR engine (singleton)."""
    global _ocr_engine
    if _ocr_engine is None:
        from paddleocr import PaddleOCR

        _ocr_engine = PaddleOCR(
            use_angle_cls=True,
            lang="en",
            show_log=False,
            use_gpu=False,  # CPU for Community Edition; GPU via env override
        )
    return _ocr_engine


def _get_structure_engine():
    """Lazy-initialize PPStructure engine for layout analysis (singleton)."""
    global _structure_engine
    if _structure_engine is None:
        from paddleocr import PPStructure

        _structure_engine = PPStructure(
            show_log=False,
            image_orientation=True,
            layout=True,
            table=True,
            ocr=True,
            use_gpu=False,
        )
    return _structure_engine


class PaddleOCRProvider(BaseOCRProvider):
    """Local OCR provider using PaddleOCR with layout analysis.

    Process flow:
    1. PDF → images (via PyMuPDF at 300 DPI)
    2. Each image → PPStructure for layout analysis
    3. Regions sorted by reading order (top-to-bottom, left-to-right)
    4. Text extracted per-region with type labels
    """

    def __init__(self, dpi: int = 300, use_layout: bool = True):
        self.dpi = dpi
        self.use_layout = use_layout

    def process_document(self, file_path: str) -> OCRResult:
        """Process a PDF or image file into structured OCR output."""
        path = Path(file_path)

        if path.suffix.lower() in (".pdf",):
            return self._process_pdf(file_path)
        elif path.suffix.lower() in (".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp"):
            page = self.process_image(file_path)
            return OCRResult(pages=[page], page_count=1)
        else:
            raise ValueError(f"Unsupported file format: {path.suffix}")

    def process_image(self, image_path: str) -> OCRPage:
        """Process a single image with layout analysis."""
        import cv2

        img = cv2.imread(image_path)
        if img is None:
            raise ValueError(f"Could not read image: {image_path}")

        h, w = img.shape[:2]

        if self.use_layout:
            return self._process_with_layout(img, page_number=1, width=w, height=h)
        else:
            return self._process_ocr_only(img, page_number=1, width=w, height=h)

    def _process_pdf(self, pdf_path: str) -> OCRResult:
        """Convert PDF pages to images and process each with layout analysis."""
        doc = fitz.open(pdf_path)
        pages: list[OCRPage] = []

        logger.info(f"Processing PDF: {pdf_path} ({len(doc)} pages)")

        with tempfile.TemporaryDirectory() as tmp_dir:
            for page_num in range(len(doc)):
                page = doc[page_num]
                # Render page to image at specified DPI
                mat = fitz.Matrix(self.dpi / 72, self.dpi / 72)
                pix = page.get_pixmap(matrix=mat)

                img_path = os.path.join(tmp_dir, f"page_{page_num + 1}.png")
                pix.save(img_path)

                # Process the page image
                import cv2

                img = cv2.imread(img_path)
                h, w = img.shape[:2]

                if self.use_layout:
                    ocr_page = self._process_with_layout(
                        img, page_number=page_num + 1, width=w, height=h
                    )
                else:
                    ocr_page = self._process_ocr_only(
                        img, page_number=page_num + 1, width=w, height=h
                    )

                pages.append(ocr_page)
                logger.info(
                    f"  Page {page_num + 1}/{len(doc)}: "
                    f"{len(ocr_page.regions)} regions detected"
                )

        doc.close()

        return OCRResult(
            pages=pages,
            page_count=len(pages),
            metadata={"source": pdf_path, "dpi": self.dpi},
        )

    def _process_with_layout(
        self,
        img: np.ndarray,
        page_number: int,
        width: int,
        height: int,
    ) -> OCRPage:
        """Process an image using PPStructure for layout-aware OCR."""
        engine = _get_structure_engine()
        result = engine(img)

        regions: list[OCRRegion] = []
        for block in result:
            region_type = block.get("type", "text").lower()
            bbox_raw = block.get("bbox", [0, 0, 0, 0])
            bbox = (int(bbox_raw[0]), int(bbox_raw[1]), int(bbox_raw[2]), int(bbox_raw[3]))

            # Extract text from the block
            text = ""
            if "res" in block:
                res = block["res"]
                if isinstance(res, list):
                    # OCR result: list of (text, confidence) pairs
                    text_parts = []
                    for item in res:
                        if isinstance(item, dict) and "text" in item:
                            text_parts.append(item["text"])
                        elif isinstance(item, (list, tuple)) and len(item) >= 2:
                            text_parts.append(str(item[1][0]) if isinstance(item[1], tuple) else str(item[0]))
                    text = " ".join(text_parts)
                elif isinstance(res, dict):
                    # Table or other structured result
                    text = res.get("html", res.get("text", str(res)))
                elif isinstance(res, str):
                    text = res

            if not text.strip() and "text" in block:
                text = block["text"]

            confidence = float(block.get("score", 0.0))

            regions.append(
                OCRRegion(
                    region_type=region_type,
                    bbox=bbox,
                    text=text.strip(),
                    confidence=confidence,
                )
            )

        # Sort regions by reading order: top-to-bottom, then left-to-right
        regions.sort(key=lambda r: (r.bbox[1], r.bbox[0]))

        return OCRPage(
            page_number=page_number,
            width=width,
            height=height,
            regions=regions,
        )

    def _process_ocr_only(
        self,
        img: np.ndarray,
        page_number: int,
        width: int,
        height: int,
    ) -> OCRPage:
        """Process an image with basic OCR (no layout analysis)."""
        engine = _get_ocr_engine()
        result = engine.ocr(img, cls=True)

        regions: list[OCRRegion] = []
        if result and result[0]:
            for line in result[0]:
                points = line[0]  # [[x1,y1], [x2,y2], [x3,y3], [x4,y4]]
                text_info = line[1]  # (text, confidence)

                # Convert polygon to bounding box
                xs = [p[0] for p in points]
                ys = [p[1] for p in points]
                bbox = (int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys)))

                regions.append(
                    OCRRegion(
                        region_type="text",
                        bbox=bbox,
                        text=str(text_info[0]),
                        confidence=float(text_info[1]),
                    )
                )

        regions.sort(key=lambda r: (r.bbox[1], r.bbox[0]))

        return OCRPage(
            page_number=page_number,
            width=width,
            height=height,
            regions=regions,
        )
