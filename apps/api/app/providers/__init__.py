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


# ============================================================================
# Embedding Provider Interface
# ============================================================================


class BaseEmbeddingProvider(ABC):
    """Abstract interface for embedding providers.

    Implementations:
    - OpenAIEmbeddingProvider (default, cloud, text-embedding-3-small)
    - Future: FastEmbedProvider (local, air-gapped)
    """

    @abstractmethod
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of text strings into vectors.

        Args:
            texts: List of text strings to embed.

        Returns:
            List of embedding vectors (each a list of floats).
        """
        ...

    @abstractmethod
    def embed_query(self, query: str) -> list[float]:
        """Embed a single query string.

        Args:
            query: The query text to embed.

        Returns:
            Embedding vector as a list of floats.
        """
        ...

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Return the dimensionality of the embedding vectors."""
        ...


# ============================================================================
# Output Provider Interface
# ============================================================================


@dataclass
class ProvisionResult:
    """Result of a provisioning operation."""

    connection_string: str
    rows_inserted: int
    schema_name: str
    ddl_executed: str


class BaseOutputProvider(ABC):
    """Abstract interface for output database providers.

    Implementations:
    - PostgresOutputProvider (default, SQL)
    - Future: Neo4jOutputProvider (GRAPH), VectorOutputProvider (VECTOR)
    """

    @abstractmethod
    def test_connection(self, connection_string: str) -> bool:
        """Test whether a connection string is valid and reachable.

        Args:
            connection_string: Database connection string to test.

        Returns:
            True if connection succeeds.

        Raises:
            Exception with details if connection fails.
        """
        ...

    @abstractmethod
    def provision(
        self,
        schema_name: str,
        ddl: str,
        data: dict | list,
        json_schema: dict,
    ) -> ProvisionResult:
        """Create schema, execute DDL, and bulk insert data.

        Args:
            schema_name: Isolated schema/namespace name (e.g., job_{uuid}).
            ddl: DDL statements to execute.
            data: The merged extraction data.
            json_schema: The locked JSON schema (for table name inference).

        Returns:
            ProvisionResult with connection string, row count, etc.
        """
        ...
