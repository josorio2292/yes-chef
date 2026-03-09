import os
import uuid

import numpy as np
import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from yes_chef.db.models import Base, CatalogItem, IngredientCache, Quote, MenuItem

TEST_DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/yeschef_test",
)


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def engine():
    eng = create_async_engine(TEST_DB_URL, echo=False)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()


@pytest_asyncio.fixture(loop_scope="session", scope="session")
def session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture(loop_scope="session")
async def session(session_factory):
    async with session_factory() as sess:
        yield sess
        await sess.rollback()


async def test_create_quote(session: AsyncSession):
    quote = Quote(
        event="Wedding Reception",
        date="2026-06-15",
        venue="The Grand Ballroom",
        guest_count_estimate=150,
        notes="No peanuts",
        status="pending",
        menu_spec={"courses": ["appetizer", "main", "dessert"]},
    )
    session.add(quote)
    await session.flush()

    result = await session.execute(select(Quote).where(Quote.id == quote.id))
    fetched = result.scalar_one()

    assert fetched.event == "Wedding Reception"
    assert fetched.date == "2026-06-15"
    assert fetched.venue == "The Grand Ballroom"
    assert fetched.guest_count_estimate == 150
    assert fetched.notes == "No peanuts"
    assert fetched.status == "pending"
    assert fetched.menu_spec == {"courses": ["appetizer", "main", "dessert"]}
    assert fetched.id is not None
    assert fetched.created_at is not None


async def test_menu_item_status_transitions(session: AsyncSession):
    quote = Quote(event="Corporate Dinner", status="pending", menu_spec={})
    session.add(quote)
    await session.flush()

    item = MenuItem(
        quote_id=quote.id,
        item_name="Beef Wellington",
        category="main",
        status="pending",
    )
    session.add(item)
    await session.flush()

    transitions = ["decomposing", "decomposed", "resolving", "completed"]
    for new_status in transitions:
        item.status = new_status
        await session.flush()

        result = await session.execute(select(MenuItem).where(MenuItem.id == item.id))
        fetched = result.scalar_one()
        assert fetched.status == new_status, (
            f"Expected {new_status}, got {fetched.status}"
        )


async def test_menu_item_failure(session: AsyncSession):
    quote = Quote(event="Birthday Party", status="pending", menu_spec={})
    session.add(quote)
    await session.flush()

    item = MenuItem(
        quote_id=quote.id,
        item_name="Mystery Ingredient",
        category="unknown",
        status="pending",
    )
    session.add(item)
    await session.flush()

    item.status = "failed"
    item.error = "Could not resolve ingredient: supplier unavailable"
    await session.flush()

    result = await session.execute(select(MenuItem).where(MenuItem.id == item.id))
    fetched = result.scalar_one()
    assert fetched.status == "failed"
    assert fetched.error == "Could not resolve ingredient: supplier unavailable"


async def test_cache_upsert(session: AsyncSession):
    entry = IngredientCache(
        ingredient_name="chicken breast",
        source_item_id="SYS-001",
        source="sysco_catalog",
        provider="sysco",
    )
    session.add(entry)
    await session.flush()

    original_id = entry.id

    await session.execute(
        text(
            """
            INSERT INTO ingredient_cache
                (id, ingredient_name, source_item_id, source, provider)
            VALUES (:id, :name, :source_id, :source, :provider)
            ON CONFLICT (ingredient_name) DO UPDATE
            SET source_item_id = EXCLUDED.source_item_id,
                source = EXCLUDED.source,
                provider = EXCLUDED.provider
            """
        ),
        {
            "id": str(uuid.uuid4()),
            "name": "chicken breast",
            "source_id": "SYS-002-UPDATED",
            "source": "estimated",
            "provider": "internal",
        },
    )
    await session.flush()

    session.expire(entry)
    result = await session.execute(
        select(IngredientCache).where(
            IngredientCache.ingredient_name == "chicken breast"
        )
    )
    fetched = result.scalar_one()
    assert fetched.id == original_id
    assert fetched.source_item_id == "SYS-002-UPDATED"
    assert fetched.source == "estimated"
    assert fetched.provider == "internal"


async def test_cache_normalized_key(session: AsyncSession):
    raw_name = "  Beef Tenderloin  "
    normalized = raw_name.strip().lower()

    entry = IngredientCache(
        ingredient_name=normalized,
        source_item_id="SYS-100",
        source="sysco_catalog",
        provider="sysco",
    )
    session.add(entry)
    await session.flush()

    result = await session.execute(
        select(IngredientCache).where(
            IngredientCache.ingredient_name == "beef tenderloin"
        )
    )
    fetched = result.scalar_one()
    assert fetched.ingredient_name == "beef tenderloin"
    assert fetched.source_item_id == "SYS-100"


async def test_catalog_item(session: AsyncSession):
    arr = np.random.rand(1536).astype(np.float32).tolist()

    record = CatalogItem(
        source_item_id="CAT-9999",
        description="Premium Wagyu Beef",
        provider="sysco",
        embedding=arr,
        unit_of_measure="6/1 GAL",
        cost_per_case=45.99,
        category="dairy",
        brand="Land O Lakes",
        source_metadata={"contract_number": "ABC-123", "aasis_number": "12345"},
    )
    session.add(record)
    await session.flush()

    result = await session.execute(
        select(CatalogItem).where(CatalogItem.source_item_id == "CAT-9999")
    )
    fetched = result.scalar_one()
    assert fetched.source_item_id == "CAT-9999"
    assert fetched.description == "Premium Wagyu Beef"
    assert fetched.provider == "sysco"
    assert fetched.unit_of_measure == "6/1 GAL"
    assert fetched.cost_per_case == 45.99
    assert fetched.category == "dairy"
    assert fetched.brand == "Land O Lakes"
    assert fetched.source_metadata == {
        "contract_number": "ABC-123",
        "aasis_number": "12345",
    }
    assert fetched.is_active is True
    assert fetched.ingested_at is not None

    recovered = np.array(fetched.embedding, dtype=np.float32)
    np.testing.assert_array_almost_equal(recovered, arr, decimal=5)


async def test_catalog_item_composite_unique(session: AsyncSession):
    """Composite unique on (provider, source_item_id) prevents duplicates."""
    arr = np.random.rand(1536).astype(np.float32).tolist()

    item1 = CatalogItem(
        source_item_id="DUP-001",
        description="First item",
        provider="sysco",
        embedding=arr,
        unit_of_measure="1/EA",
        cost_per_case=10.0,
    )
    session.add(item1)
    await session.flush()

    item2 = CatalogItem(
        source_item_id="DUP-001",
        description="Duplicate",
        provider="sysco",
        embedding=arr,
        unit_of_measure="1/EA",
        cost_per_case=20.0,
    )
    session.add(item2)
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()
