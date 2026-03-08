"""Catalog service with embedding-based search via pgvector."""

import logging
import os
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

import numpy as np
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from yes_chef.catalog.provider import CatalogProvider, PriceResult
from yes_chef.db.models import CatalogItem

logger = logging.getLogger(__name__)

# Type alias for the async embedding function
EmbedFn = Callable[[list[str]], Coroutine[Any, Any, list[np.ndarray]]]

_EMBEDDING_DIM = 1536
_DEFAULT_BATCH_SIZE = 100
_EMBEDDING_MODEL = "text-embedding-3-small"


@dataclass
class CatalogCandidate:
    source_item_id: str
    description: str
    unit_of_measure: str
    cost_per_case: float
    provider: str
    similarity_score: float
    category: str | None = None
    brand: str | None = None


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
    """Catalog service: embeds items, stores in Postgres via pgvector, searches by similarity."""  # noqa: E501

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
        """Return True if at least one active embedding row exists in the DB."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(func.count())
                .select_from(CatalogItem)
                .where(CatalogItem.is_active == True)  # noqa: E712
            )
            return result.scalar_one() > 0

    async def ingest(self, provider_name: str) -> None:
        """ETL: load provider, embed descriptions, upsert into catalog_items with soft deletes."""  # noqa: E501
        if provider_name not in self._providers:
            raise ValueError(f"Unknown provider {provider_name!r}")

        provider = self._providers[provider_name]
        records = provider.load_catalog()

        logger.info("Ingesting %d items from provider %r…", len(records), provider_name)

        # Embed descriptions in batches
        descriptions = [r.description for r in records]
        all_embeddings: list[np.ndarray] = []
        for i in range(0, len(descriptions), _DEFAULT_BATCH_SIZE):
            batch = descriptions[i : i + _DEFAULT_BATCH_SIZE]
            batch_embeddings = await self._get_embed_fn()(batch)
            all_embeddings.extend(batch_embeddings)

        async with self._session_factory() as sess:
            async with sess.begin():
                # Soft-delete existing rows for this provider
                await sess.execute(
                    update(CatalogItem)
                    .where(CatalogItem.provider == provider_name)
                    .where(CatalogItem.is_active == True)  # noqa: E712
                    .values(is_active=False)
                )

                # Upsert new rows
                for record, embedding in zip(records, all_embeddings, strict=True):
                    stmt = (
                        pg_insert(CatalogItem)
                        .values(
                            source_item_id=record.source_item_id,
                            provider=record.provider,
                            description=record.description,
                            unit_of_measure=record.unit_of_measure,
                            cost_per_case=record.cost_per_case,
                            category=record.category,
                            brand=record.brand,
                            source_metadata=record.source_metadata,
                            embedding=embedding.astype(np.float32).tolist(),
                            is_active=True,
                        )
                        .on_conflict_do_update(
                            index_elements=["provider", "source_item_id"],
                            set_={
                                "description": record.description,
                                "unit_of_measure": record.unit_of_measure,
                                "cost_per_case": record.cost_per_case,
                                "category": record.category,
                                "brand": record.brand,
                                "source_metadata": record.source_metadata,
                                "embedding": embedding.astype(np.float32).tolist(),
                                "is_active": True,
                            },
                        )
                    )
                    await sess.execute(stmt)

        logger.info("Ingested %d items for provider %r.", len(records), provider_name)

    async def search(self, query: str, top_k: int = 5) -> list[CatalogCandidate]:
        """Embed the query and return top-k candidates by cosine similarity via pgvector."""  # noqa: E501
        embed_fn = self._get_embed_fn()
        query_vectors = await embed_fn([query])
        query_vec = query_vectors[0].tolist()

        async with self._session_factory() as session:
            stmt = (
                select(
                    CatalogItem.source_item_id,
                    CatalogItem.description,
                    CatalogItem.unit_of_measure,
                    CatalogItem.cost_per_case,
                    CatalogItem.provider,
                    CatalogItem.category,
                    CatalogItem.brand,
                    (1 - CatalogItem.embedding.cosine_distance(query_vec)).label(
                        "similarity"
                    ),
                )
                .where(CatalogItem.is_active == True)  # noqa: E712
                .order_by(CatalogItem.embedding.cosine_distance(query_vec))
                .limit(top_k)
            )
            result = await session.execute(stmt)
            rows = result.all()

        return [
            CatalogCandidate(
                source_item_id=row.source_item_id,
                description=row.description,
                unit_of_measure=row.unit_of_measure,
                cost_per_case=float(row.cost_per_case),
                provider=row.provider,
                similarity_score=float(row.similarity),
                category=row.category,
                brand=row.brand,
            )
            for row in rows
        ]

    def get_price(self, source_item_id: str, provider: str) -> PriceResult:
        """Return pricing from the named provider.

        Raises ValueError for unknown providers and ItemNotFoundError for
        unknown item numbers (propagated from the provider).
        """
        if provider not in self._providers:
            raise ValueError(
                f"Unknown provider {provider!r}. "
                f"Available: {list(self._providers.keys())}"
            )
        return self._providers[provider].get_price(source_item_id)
