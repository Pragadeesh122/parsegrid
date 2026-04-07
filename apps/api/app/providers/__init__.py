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

    Phase 7 contract: model discovery returns a typed `DatabaseModel`,
    extraction is per-table, and DDL generation is gone (deterministic).

    Implementations:
    - OpenAILLMProvider (default, cloud)
    - Future: OllamaProvider, AnthropicProvider, etc.
    """

    @abstractmethod
    def generate_model(
        self,
        document_text: str,
        profile: "DocumentProfile | None",
        num_pages: int,
    ) -> "DatabaseModel":
        """Analyze document text and propose a typed DatabaseModel.

        Uses OpenAI strict structured outputs so the LLM cannot emit
        arbitrary keys — the response is guaranteed to validate against
        the meta-schema.

        Args:
            document_text: Sampled or retrieved document text.
            profile: Optional DocumentProfile for FULL jobs (None for TARGETED).
            num_pages: Total pages in the source document.

        Returns:
            A validated DatabaseModel.
        """
        ...

    @abstractmethod
    def extract_table(
        self,
        text: str,
        table: "TableDef",
        link_targets: "list[RelationshipDef]",
    ) -> LLMResponse:
        """Extract structured rows for a single table from a chunk of text.

        Uses Structured Outputs (strict: true) with a per-table Pydantic
        model built at runtime. Each row may also carry symbolic link-key
        columns for any relationships where this table is the source.

        Args:
            text: The text chunk to extract data from.
            table: The TableDef describing the columns to extract.
            link_targets: Relationships where source_table == table.table_name.

        Returns:
            LLMResponse whose `data` is `{"rows": [...]}`.
        """
        ...


# Forward references for the type hints above. Imported lazily to avoid
# a circular import (extraction_model imports nothing from providers).
from app.schemas.extraction_model import (  # noqa: E402
    DatabaseModel,
    DocumentProfile,
    RelationshipDef,
    TableDef,
)


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
        ddl_statements: list[str],
        data: "dict[str, list[dict]]",
        model: "DatabaseModel",
    ) -> ProvisionResult:
        """Create schema, execute DDL, and bulk insert multi-table data.

        Args:
            schema_name: Isolated schema/namespace name (e.g., job_{uuid}).
            ddl_statements: Ordered DDL statements from `services.ddl.build_ddl`.
            data: Reconciled rows keyed by table name.
            model: The (already validated) locked DatabaseModel.

        Returns:
            ProvisionResult with connection string, total row count, etc.
        """
        ...

    @abstractmethod
    def delete_output(self, schema_name: str) -> None:
        """Delete all provisioned output for a job."""
        ...
