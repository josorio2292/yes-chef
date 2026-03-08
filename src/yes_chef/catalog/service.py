"""Catalog service with embedding-based search."""

import logging
import os
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

import numpy as np
from sqlalchemy import delete, select

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


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Compute cosine similarity between two unit vectors."""
    dot = float(np.dot(a, b))
    norm_a = float(np.linalg.norm(a))
    norm_b = float(np.linalg.norm(b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class CatalogService:
    """Catalog service: embeds items, stores in Postgres, searches by similarity."""

    def __init__(
        self,
        providers: dict[str, CatalogProvider],
        session_factory: Any,
        embed_fn: EmbedFn | None = None,
    ) -> None:
        self._providers = providers
        self._session_factory = session_factory
        # Stored as-is; lazily resolved on first use so no API key is required
        # at construction time (e.g. when only load_embeddings() will be called).
        self._embed_fn: EmbedFn | None = embed_fn

        # In-memory index: item_number → (embedding, description, uom, provider)
        self._index: dict[str, tuple[np.ndarray, str, str, str]] = {}

    @property
    def has_embeddings(self) -> bool:
        """Return True if embeddings have been loaded into memory."""
        return len(self._index) > 0

    def _get_embed_fn(self) -> EmbedFn:
        """Return the embed function, building the OpenAI default on first use."""
        if self._embed_fn is None:
            self._embed_fn = _make_openai_embed_fn()
        return self._embed_fn

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
                        embedding=embedding.astype(np.float32).tobytes(),
                    )
                    sess.add(record)

        # Also load into in-memory index
        self._index = {}
        for (item_number, description, uom, provider_name), embedding in zip(
            items, all_embeddings, strict=True
        ):
            self._index[item_number] = (embedding, description, uom, provider_name)

        logger.info("Stored %d embeddings in DB and memory.", len(items))

    async def load_embeddings(self) -> None:
        """Load embeddings from Postgres into memory. No API calls."""
        self._index = {}

        async with self._session_factory() as sess:
            result = await sess.execute(select(CatalogEmbedding))
            rows: list[CatalogEmbedding] = list(result.scalars().all())

        # We need the unit_of_measure from the provider; look it up
        uom_map: dict[str, str] = {}
        for provider in self._providers.values():
            try:
                catalog = provider.load_catalog()
                for item in catalog:
                    uom_map[item.item_number] = item.unit_of_measure
            except Exception:
                pass

        for row in rows:
            embedding = np.frombuffer(row.embedding, dtype=np.float32).copy()
            uom = uom_map.get(row.item_number, "")
            self._index[row.item_number] = (
                embedding,
                row.description,
                uom,
                row.provider,
            )

        logger.info("Loaded %d embeddings from DB into memory.", len(self._index))

    async def search(self, query: str, top_k: int = 5) -> list[CatalogCandidate]:
        """Embed the query and return top-k candidates by cosine similarity."""
        if not self._index:
            raise RuntimeError(
                "No embeddings loaded. Call embed_catalog() or load_embeddings() first."
            )

        # Embed the query (single item)
        query_embeddings = await self._get_embed_fn()([query])
        query_vec = query_embeddings[0]

        # Build matrix of all stored embeddings for vectorized similarity
        item_numbers = list(self._index.keys())
        embeddings_matrix = np.stack(
            [self._index[n][0] for n in item_numbers], axis=0
        )  # shape: (N, dim)

        # Cosine similarity: (N,) = (N, dim) · (dim,) / (||rows|| * ||query||)
        norms = np.linalg.norm(embeddings_matrix, axis=1)  # (N,)
        query_norm = float(np.linalg.norm(query_vec))
        if query_norm == 0:
            scores = np.zeros(len(item_numbers), dtype=np.float32)
        else:
            dots = embeddings_matrix @ query_vec  # (N,)
            # Avoid division by zero
            safe_norms = np.where(norms == 0, 1.0, norms)
            scores = dots / (safe_norms * query_norm)

        # Top-k indices by score descending
        if top_k >= len(scores):
            top_indices = np.argsort(scores)[::-1]
        else:
            # argpartition is faster for large arrays
            top_indices = np.argpartition(scores, -top_k)[-top_k:]
            top_indices = top_indices[np.argsort(scores[top_indices])[::-1]]

        candidates: list[CatalogCandidate] = []
        for idx in top_indices[:top_k]:
            item_number = item_numbers[idx]
            embedding, description, uom, provider_name = self._index[item_number]
            candidates.append(
                CatalogCandidate(
                    item_number=item_number,
                    description=description,
                    unit_of_measure=uom,
                    provider=provider_name,
                    similarity_score=float(scores[idx]),
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
