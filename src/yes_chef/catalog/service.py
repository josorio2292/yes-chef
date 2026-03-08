"""Catalog service with embedding-based search via pgvector."""

import logging
import os
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

import numpy as np
from sqlalchemy import delete, func, select

from yes_chef.catalog.provider import CatalogProvider, PriceResult
from yes_chef.db.models import CatalogEmbedding

logger = logging.getLogger(__name__)

# Type alias for the async embedding function
EmbedFn = Callable[[list[str]], Coroutine[Any, Any, list[np.ndarray]]]

_EMBEDDING_DIM = 1536
_DEFAULT_BATCH_SIZE = 100
_EMBEDDING_MODEL = "text-embedding-3-small"


@dataclass
class CatalogCandidate:
    item_number: str
    description: str
    unit_of_measure: str
    provider: str
    similarity_score: float


def _make_openai_embed_fn() -> EmbedFn:
    """Build an async embedding function backed by the OpenAI API.

    Respects EMBEDDING_API_BASE > OPENAI_API_BASE > default OpenAI endpoint.
    Respects OPENROUTER_API_KEY > OPENAI_API_KEY for authentication.
    """
    from openai import AsyncOpenAI

    api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY")
    base_url = os.environ.get("EMBEDDING_API_BASE") or os.environ.get("OPENAI_API_BASE")

    # OpenRouter uses a different model name prefix
    if os.environ.get("OPENROUTER_API_KEY") and not os.environ.get(
        "EMBEDDING_API_BASE"
    ):
        base_url = "https://openrouter.ai/api/v1"
        model = "openai/text-embedding-3-small"
    else:
        model = _EMBEDDING_MODEL

    client_kwargs: dict[str, Any] = {}
    if api_key:
        client_kwargs["api_key"] = api_key
    if base_url:
        client_kwargs["base_url"] = base_url

    client = AsyncOpenAI(**client_kwargs)

    async def _embed(texts: list[str]) -> list[np.ndarray]:
        response = await client.embeddings.create(model=model, input=texts)
        ordered = sorted(response.data, key=lambda e: e.index)
        return [np.array(e.embedding, dtype=np.float32) for e in ordered]

    return _embed


class CatalogService:
    """Catalog service: embeds items, stores in Postgres via pgvector, searches by similarity."""

    def __init__(
        self,
        providers: dict[str, CatalogProvider],
        session_factory: Any,
        embed_fn: EmbedFn | None = None,
    ) -> None:
        self._providers = providers
        self._session_factory = session_factory
        # Stored as-is; lazily resolved on first use so no API key is required
        # at construction time.
        self._embed_fn: EmbedFn | None = embed_fn

    def _get_embed_fn(self) -> EmbedFn:
        """Return the embed function, building the OpenAI default on first use."""
        if self._embed_fn is None:
            self._embed_fn = _make_openai_embed_fn()
        return self._embed_fn

    async def has_embeddings(self) -> bool:
        """Return True if at least one embedding row exists in the DB."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(func.count()).select_from(CatalogEmbedding)
            )
            return result.scalar_one() > 0

    async def embed_catalog(self) -> None:
        """Embed all catalog items and store in the DB.

        Existing rows are deleted and replaced so this is idempotent.
        """
        # Collect all items from all providers
        items: list[tuple[str, str, str, str]] = []  # (number, desc, uom, provider)
        for provider_name, provider in self._providers.items():
            catalog = provider.load_catalog()
            for item in catalog:
                items.append(
                    (
                        item.item_number,
                        item.description,
                        item.unit_of_measure,
                        provider_name,
                    )
                )

        logger.info("Embedding %d catalog items…", len(items))

        descriptions = [desc for _, desc, _, _ in items]

        # Embed in batches
        all_embeddings: list[np.ndarray] = []
        for i in range(0, len(descriptions), _DEFAULT_BATCH_SIZE):
            batch = descriptions[i : i + _DEFAULT_BATCH_SIZE]
            batch_embeddings = await self._get_embed_fn()(batch)
            all_embeddings.extend(batch_embeddings)
            logger.debug(
                "Embedded batch %d/%d",
                min(i + _DEFAULT_BATCH_SIZE, len(descriptions)),
                len(descriptions),
            )

        # Persist to DB (replace all existing rows)
        async with self._session_factory() as sess:
            async with sess.begin():
                await sess.execute(delete(CatalogEmbedding))

                for (item_number, description, _uom, provider_name), embedding in zip(
                    items, all_embeddings, strict=True
                ):
                    record = CatalogEmbedding(
                        item_number=item_number,
                        description=description,
                        provider=provider_name,
                        embedding=embedding.astype(np.float32).tolist(),
                    )
                    sess.add(record)

        logger.info("Stored %d embeddings in DB.", len(items))

    async def search(self, query: str, top_k: int = 5) -> list[CatalogCandidate]:
        """Embed the query and return top-k candidates by cosine similarity via pgvector."""
        embed_fn = self._get_embed_fn()
        query_vectors = await embed_fn([query])
        query_vec = query_vectors[0].tolist()

        async with self._session_factory() as session:
            # pgvector cosine distance operator: <=>
            # similarity = 1 - distance
            stmt = (
                select(
                    CatalogEmbedding.item_number,
                    CatalogEmbedding.description,
                    CatalogEmbedding.provider,
                    (1 - CatalogEmbedding.embedding.cosine_distance(query_vec)).label(
                        "similarity"
                    ),
                )
                .order_by(CatalogEmbedding.embedding.cosine_distance(query_vec))
                .limit(top_k)
            )
            result = await session.execute(stmt)
            rows = result.all()

        candidates = []
        for row in rows:
            provider = self._providers.get(row.provider)
            uom = ""
            if provider:
                try:
                    price_result = provider.get_price(row.item_number)
                    uom = price_result.unit_of_measure
                except Exception:
                    pass

            candidates.append(
                CatalogCandidate(
                    item_number=row.item_number,
                    description=row.description,
                    unit_of_measure=uom,
                    provider=row.provider,
                    similarity_score=float(row.similarity),
                )
            )

        return candidates

    def get_price(self, item_number: str, provider: str) -> PriceResult:
        """Return pricing from the named provider.

        Raises ValueError for unknown providers and ItemNotFoundError for
        unknown item numbers (propagated from the provider).
        """
        if provider not in self._providers:
            raise ValueError(
                f"Unknown provider {provider!r}. "
                f"Available: {list(self._providers.keys())}"
            )
        return self._providers[provider].get_price(item_number)
