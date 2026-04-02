"""ParseGrid — Provider factory.

Creates and returns the configured provider instances.
Reads provider selection from environment variables, defaulting to
the open-core providers (PaddleOCR + OpenAI).
"""

from functools import lru_cache

from app.core.config import settings
from app.providers import BaseLLMProvider, BaseOCRProvider


@lru_cache(maxsize=1)
def get_ocr_provider() -> BaseOCRProvider:
    """Return the configured OCR provider instance.

    Default: PaddleOCRProvider (local, air-gapped)
    Future: TesseractProvider, LlamaParseProvider, etc.
    """
    from app.providers.ocr_paddle import PaddleOCRProvider

    return PaddleOCRProvider(dpi=300, use_layout=True)


@lru_cache(maxsize=1)
def get_llm_provider() -> BaseLLMProvider:
    """Return the configured LLM provider instance.

    Default: OpenAILLMProvider
    Future: OllamaProvider, AnthropicProvider, etc.
    """
    from app.providers.llm_openai import OpenAILLMProvider

    return OpenAILLMProvider(api_key=settings.openai_api_key)
