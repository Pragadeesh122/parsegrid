"""ParseGrid — Abstract provider interfaces.

Open-Core pattern: all AI/OCR capabilities are behind abstract interfaces
so the community can swap providers (e.g., OpenAI → Ollama, PaddleOCR → Tesseract).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


# ============================================================================
# OCR Provider Interface
# ============================================================================


@dataclass
class OCRRegion:
    """A detected region in a document page."""

    region_type: str  # "text", "title", "table", "figure", "header", "footer"
    bbox: tuple[int, int, int, int]  # (x1, y1, x2, y2)
    text: str
    confidence: float = 0.0


@dataclass
class OCRPage:
    """OCR result for a single page."""

    page_number: int
    width: int
    height: int
    regions: list[OCRRegion] = field(default_factory=list)

    @property
    def full_text(self) -> str:
        """Concatenate all text regions in reading order."""
        return "\n".join(r.text for r in self.regions if r.text.strip())


@dataclass
class OCRResult:
    """Complete OCR result for a document."""

    pages: list[OCRPage] = field(default_factory=list)
    page_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def full_text(self) -> str:
        """Full document text across all pages."""
        return "\n\n".join(
            f"--- Page {p.page_number} ---\n{p.full_text}" for p in self.pages
        )


class BaseOCRProvider(ABC):
    """Abstract interface for OCR providers.

    Implementations:
    - PaddleOCRProvider (default, local, air-gapped)
    - Future: TesseractProvider, LlamaParseProvider, etc.
    """

    @abstractmethod
    def process_document(self, file_path: str) -> OCRResult:
        """Process a document file (PDF, image) and return structured OCR result.

        Args:
            file_path: Path to the document file on disk.

        Returns:
            OCRResult with pages, regions, and extracted text.
        """
        ...

    @abstractmethod
    def process_image(self, image_path: str) -> OCRPage:
        """Process a single image and return OCR result for that page.

        Args:
            image_path: Path to the image file.

        Returns:
            OCRPage with detected regions and text.
        """
        ...


# ============================================================================
# LLM Provider Interface
# ============================================================================


@dataclass
class LLMResponse:
    """Structured response from an LLM provider."""

    data: dict[str, Any] | list[dict[str, Any]]
    model: str = ""
    usage: dict[str, int] = field(default_factory=dict)  # tokens used
    raw_response: Any = None


class BaseLLMProvider(ABC):
    """Abstract interface for LLM providers.

    Implementations:
    - OpenAILLMProvider (default, cloud)
    - Future: OllamaProvider, AnthropicProvider, etc.
    """

    @abstractmethod
    def generate_schema(self, sample_text: str, num_pages: int) -> dict:
        """Analyze sample text and propose a JSON schema for extraction.

        Args:
            sample_text: Text from the first few pages of the document.
            num_pages: Total number of pages in the document.

        Returns:
            A JSON schema dict describing the data structure found.
        """
        ...

    @abstractmethod
    def extract_structured(
        self,
        text: str,
        schema: dict,
    ) -> LLMResponse:
        """Extract structured data from text according to the given schema.

        Uses Structured Outputs (strict: true) to guarantee schema compliance.

        Args:
            text: The text chunk to extract data from.
            schema: The locked JSON schema to enforce.

        Returns:
            LLMResponse with extracted data conforming to the schema.
        """
        ...

    @abstractmethod
    def generate_ddl(
        self,
        schema: dict,
        target_format: str,
    ) -> str:
        """Generate DDL statements from a JSON schema for the target database.

        Args:
            schema: The locked JSON schema.
            target_format: "SQL", "GRAPH", or "VECTOR".

        Returns:
            DDL string (SQL CREATE TABLE, Cypher CREATE, etc.)
        """
        ...
