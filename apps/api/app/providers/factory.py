"""ParseGrid — Provider factory.

Creates and returns the configured provider instances.
Reads provider selection from environment variables, defaulting to
the open-core providers (PaddleOCR + OpenAI).
"""

from functools import lru_cache

from app.core.config import settings
from app.providers import BaseLLMProvider, BaseOCRProvider, BaseOutputProvider


@lru_cache(maxsize=1)
def get_ocr_provider() -> BaseOCRProvider:
    """Return the configured OCR provider instance.

    Default: PaddleOCRProvider (local, air-gapped)
    Future: TesseractProvider, LlamaParseProvider, etc.
    """
    from app.providers.ocr_paddle import PaddleOCRProvider

    return PaddleOCRProvider()


@lru_cache(maxsize=1)
def get_llm_provider() -> BaseLLMProvider:
    """Return the configured LLM provider instance.

    Default: OpenAILLMProvider
    Future: OllamaProvider, AnthropicProvider, etc.
    """
    from app.providers.llm_openai import OpenAILLMProvider

    return OpenAILLMProvider(api_key=settings.openai_api_key)


def get_output_provider(output_format: str = "SQL") -> BaseOutputProvider:
    """Return the configured output provider for the given format.

    Default: PostgresOutputProvider (SQL)
    Future: Neo4jOutputProvider (GRAPH), VectorOutputProvider (VECTOR)
    """
    if output_format == "SQL":
        from app.providers.output_postgres import PostgresOutputProvider

        return PostgresOutputProvider()

    # Future providers:
    # if output_format == "GRAPH":
    #     from app.providers.output_neo4j import Neo4jOutputProvider
    #     return Neo4jOutputProvider()
    # if output_format == "VECTOR":
    #     from app.providers.output_vector import VectorOutputProvider
    #     return VectorOutputProvider()

    raise ValueError(f"Unsupported output format: {output_format}. Currently only SQL is supported.")
