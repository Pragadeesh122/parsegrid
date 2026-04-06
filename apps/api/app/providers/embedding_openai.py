"""ParseGrid — OpenAI Embedding Provider.

Uses text-embedding-3-large (3072 dimensions) for document chunk
embeddings and query embeddings in the targeted RAG pipeline.
"""

import logging

from openai import OpenAI

from app.providers import BaseEmbeddingProvider

logger = logging.getLogger(__name__)

# OpenAI embedding API supports up to 2048 inputs per request,
# but we batch conservatively to stay within token limits.
_BATCH_SIZE = 100


class OpenAIEmbeddingProvider(BaseEmbeddingProvider):
    """OpenAI text-embedding-3-large provider.

    Native 3072 dimensions — full quality, no truncation.
    """

    MODEL = "text-embedding-3-large"
    DIMENSION = 3072

    def __init__(self, api_key: str) -> None:
        self._client = OpenAI(api_key=api_key)

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts, auto-batching if needed."""
        all_embeddings: list[list[float]] = []

        for i in range(0, len(texts), _BATCH_SIZE):
            batch = texts[i : i + _BATCH_SIZE]
            response = self._client.embeddings.create(
                model=self.MODEL,
                input=batch,
            )
            batch_embeddings = [item.embedding for item in response.data]
            all_embeddings.extend(batch_embeddings)

            logger.info(
                f"Embedded batch {i // _BATCH_SIZE + 1}: "
                f"{len(batch)} texts, "
                f"{response.usage.total_tokens} tokens"
            )

        return all_embeddings

    def embed_query(self, query: str) -> list[float]:
        """Embed a single query string."""
        response = self._client.embeddings.create(
            model=self.MODEL,
            input=query,
        )
        return response.data[0].embedding

    @property
    def dimension(self) -> int:
        return self.DIMENSION
