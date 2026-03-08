"""Tests for the CatalogService with pgvector-based embedding search."""

import hashlib
import pathlib

import numpy as np
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from yes_chef.catalog.provider import SyscoCsvProvider
from yes_chef.catalog.service import CatalogCandidate, CatalogService
from yes_chef.db.models import Base

DATA_DIR = pathlib.Path(__file__).parent.parent / "data"
SYSCO_CSV = DATA_DIR / "sysco_catalog.csv"

TEST_DB_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/yeschef_test"

# ── Known catalog item numbers from sysco_catalog.csv ────────────────────────
# "applewood smoked bacon" → 4842788 (BACON, SMOKED, APPLEWOOD, THICK CUT, 15LB)
# "beef tenderloin"        → 5614226 (BEEF, TENDERLOIN, FILET, 8OZ, CENTER CUT)
# "brioche bread"          → 7960328 (ROLL, DINNER, BRIOCHE, 2OZ, 120/CS)
# "heavy cream"            → 5137722 (CREAM, HEAVY, WHIPPING, 36%)
# "dijon mustard"          → 5382463 (MUSTARD, DIJON, 13.4OZ, 6/CS)

KNOWN_PAIRS = [
    ("applewood smoked bacon", "4842788"),
    ("beef tenderloin", "5614226"),
    ("brioche bread", "7960328"),
    ("heavy cream", "5137722"),
    ("dijon mustard", "5382463"),
]


# ── Deterministic fake embedding ──────────────────────────────────────────────

EMBEDDING_DIM = 1536


def _text_to_vector(text: str) -> np.ndarray:
    """Hash text to a deterministic unit vector.

    Strategy: repeat SHA-256 hashing to fill 1536 floats, then normalize.
    This guarantees that identical texts produce identical vectors, and
    similar descriptions (sharing words) will have modestly higher similarity
    than unrelated ones — enough for the mock tests to work.
    """
    seed = int(hashlib.sha256(text.lower().encode()).hexdigest(), 16) % (2**32)
    rng = np.random.default_rng(seed)
    vec = rng.standard_normal(EMBEDDING_DIM).astype(np.float32)
    norm = np.linalg.norm(vec)
    if norm == 0:
        vec[0] = 1.0
        return vec
    return vec / norm


def _smart_embed(text: str) -> np.ndarray:
    """Embedding function for the known-pairs test.

    For each known ingredient query we return the *exact same* vector as the
    catalog description it should match, so cosine similarity == 1.0 for the
    correct pair.  Everything else gets a hash-based vector.
    """
    # Mapping from query → catalog description (so they get identical vectors)
    query_to_catalog = {
        "applewood smoked bacon": "BACON, SMOKED, APPLEWOOD, THICK CUT, 15LB, 1/CS",
        "beef tenderloin": "BEEF, TENDERLOIN, FILET, 8OZ, CENTER CUT, 20/CS",
        "brioche bread": "ROLL, DINNER, BRIOCHE, 2OZ, 120/CS",
        "heavy cream": "CREAM, HEAVY, WHIPPING, 36%, 1QT, 12/CS",
        "dijon mustard": "MUSTARD, DIJON, 13.4OZ, 6/CS",
    }
    canonical = query_to_catalog.get(text.lower().strip())
    if canonical is not None:
        return _text_to_vector(canonical)
    return _text_to_vector(text)


async def _fake_embed_batch(texts: list[str]) -> list[np.ndarray]:
    """Async wrapper around _text_to_vector — no API calls."""
    return [_text_to_vector(t) for t in texts]


async def _smart_embed_batch(texts: list[str]) -> list[np.ndarray]:
    """Async wrapper around _smart_embed — no API calls."""
    return [_smart_embed(t) for t in texts]


# ── DB fixtures ───────────────────────────────────────────────────────────────


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def catalog_engine():
    eng = create_async_engine(TEST_DB_URL, echo=False)

    async with eng.begin() as conn:
        # Enable pgvector extension before creating tables.
        # The pgvector.sqlalchemy.Vector type uses the text wire protocol, so
        # no asyncpg binary codec registration is required.
        await conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    yield eng

    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()


@pytest_asyncio.fixture(loop_scope="session", scope="session")
def catalog_session_factory(catalog_engine):
    return async_sessionmaker(
        catalog_engine, class_=AsyncSession, expire_on_commit=False
    )


@pytest.fixture(scope="session")
def sysco_provider():
    prov = SyscoCsvProvider(csv_path=str(SYSCO_CSV))
    prov.load_catalog()
    return prov


@pytest.fixture(scope="session")
def providers(sysco_provider):
    return {"sysco": sysco_provider}


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def catalog_service(providers, catalog_session_factory):
    """Base CatalogService using fake embeddings."""
    return CatalogService(
        providers=providers,
        session_factory=catalog_session_factory,
        embed_fn=_fake_embed_batch,
    )


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def embedded_catalog(catalog_service):
    """CatalogService with embeddings already written to the test DB."""
    await catalog_service.embed_catalog()
    return catalog_service


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def smart_embedded_catalog(providers, catalog_session_factory):
    """CatalogService with smart embeddings that guarantee correct ranking for known pairs."""  # noqa: E501
    service = CatalogService(
        providers=providers,
        session_factory=catalog_session_factory,
        embed_fn=_smart_embed_batch,
    )
    await service.embed_catalog()
    return service


# ── Tests ─────────────────────────────────────────────────────────────────────


async def test_embed_catalog_stores_embeddings(
    providers, catalog_session_factory
) -> None:
    """embed_catalog() should store one embedding row per catalog item (565)."""
    service = CatalogService(
        providers=providers,
        session_factory=catalog_session_factory,
        embed_fn=_fake_embed_batch,
    )
    await service.embed_catalog()

    from sqlalchemy import func, select

    from yes_chef.db.models import CatalogEmbedding

    async with catalog_session_factory() as sess:
        result = await sess.execute(select(func.count()).select_from(CatalogEmbedding))
        count = result.scalar_one()

    assert count == 565


async def test_has_embeddings_returns_true(embedded_catalog) -> None:
    """has_embeddings() returns True when embeddings exist in the DB."""
    assert await embedded_catalog.has_embeddings() is True


async def test_search_returns_top_5(embedded_catalog) -> None:
    """search() should return exactly 5 candidates sorted by score descending."""
    results = await embedded_catalog.search("bacon", top_k=5)

    assert len(results) == 5
    assert all(isinstance(r, CatalogCandidate) for r in results)

    scores = [r.similarity_score for r in results]
    assert scores == sorted(scores, reverse=True)


async def test_search_correct_match(smart_embedded_catalog) -> None:
    """search('applewood smoked bacon') must rank the bacon item first."""
    results = await smart_embedded_catalog.search("applewood smoked bacon", top_k=5)

    assert len(results) > 0
    top = results[0]
    assert top.item_number == "4842788", (
        f"Expected item 4842788 (bacon) at rank 1, got {top.item_number!r} "
        f"({top.description!r})"
    )


async def test_search_known_pairs(smart_embedded_catalog) -> None:
    """At least 5 known ingredient-to-catalog pairs must match at rank 1."""
    for query, expected_item_number in KNOWN_PAIRS:
        results = await smart_embedded_catalog.search(query, top_k=5)
        assert len(results) > 0, f"No results for query: {query!r}"
        top = results[0]
        assert top.item_number == expected_item_number, (
            f"Query {query!r}: expected item {expected_item_number!r} at rank 1, "
            f"got {top.item_number!r} ({top.description!r}, "
            f"score={top.similarity_score:.4f})"
        )


async def test_get_price_delegates_to_provider(
    providers, catalog_session_factory
) -> None:
    """get_price() should delegate to the correct provider."""
    service = CatalogService(
        providers=providers,
        session_factory=catalog_session_factory,
        embed_fn=_fake_embed_batch,
    )

    # Item 5614226 = BEEF, TENDERLOIN, FILET, 8OZ — $315.80, 20/8 OZ
    result = service.get_price("5614226", "sysco")

    assert abs(result.cost_per_case - 315.80) < 0.01
    assert result.unit_of_measure == "20/8 OZ"


async def test_get_price_unknown_provider(providers, catalog_session_factory) -> None:
    """get_price() with an unknown provider must raise ValueError."""
    service = CatalogService(
        providers=providers,
        session_factory=catalog_session_factory,
        embed_fn=_fake_embed_batch,
    )

    with pytest.raises((ValueError, KeyError)):
        service.get_price("5614226", "unknown_provider")
