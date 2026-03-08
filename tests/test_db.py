import os
import uuid

import numpy as np
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from yes_chef.db.models import Base, CatalogEmbedding, IngredientCache, Job, WorkItem

TEST_DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/yeschef_test",
)


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def engine():
    eng = create_async_engine(TEST_DB_URL, echo=False)
    async with eng.begin() as conn:
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


async def test_create_job(session: AsyncSession):
    job = Job(
        event="Wedding Reception",
        date="2026-06-15",
        venue="The Grand Ballroom",
        guest_count_estimate=150,
        notes="No peanuts",
        status="pending",
        menu_spec={"courses": ["appetizer", "main", "dessert"]},
    )
    session.add(job)
    await session.flush()

    result = await session.execute(select(Job).where(Job.id == job.id))
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


async def test_work_item_status_transitions(session: AsyncSession):
    job = Job(event="Corporate Dinner", status="pending", menu_spec={})
    session.add(job)
    await session.flush()

    item = WorkItem(
        job_id=job.id,
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

        result = await session.execute(select(WorkItem).where(WorkItem.id == item.id))
        fetched = result.scalar_one()
        assert fetched.status == new_status, (
            f"Expected {new_status}, got {fetched.status}"
        )


async def test_work_item_failure(session: AsyncSession):
    job = Job(event="Birthday Party", status="pending", menu_spec={})
    session.add(job)
    await session.flush()

    item = WorkItem(
        job_id=job.id,
        item_name="Mystery Ingredient",
        category="unknown",
        status="pending",
    )
    session.add(item)
    await session.flush()

    item.status = "failed"
    item.error = "Could not resolve ingredient: supplier unavailable"
    await session.flush()

    result = await session.execute(select(WorkItem).where(WorkItem.id == item.id))
    fetched = result.scalar_one()
    assert fetched.status == "failed"
    assert fetched.error == "Could not resolve ingredient: supplier unavailable"


async def test_cache_upsert(session: AsyncSession):
    entry = IngredientCache(
        ingredient_name="chicken breast",
        sysco_item_number="SYS-001",
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
                (id, ingredient_name, sysco_item_number, source, provider)
            VALUES (:id, :name, :sysco, :source, :provider)
            ON CONFLICT (ingredient_name) DO UPDATE
            SET sysco_item_number = EXCLUDED.sysco_item_number,
                source = EXCLUDED.source,
                provider = EXCLUDED.provider
            """
        ),
        {
            "id": str(uuid.uuid4()),
            "name": "chicken breast",
            "sysco": "SYS-002-UPDATED",
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
    assert fetched.sysco_item_number == "SYS-002-UPDATED"
    assert fetched.source == "estimated"
    assert fetched.provider == "internal"


async def test_cache_normalized_key(session: AsyncSession):
    raw_name = "  Beef Tenderloin  "
    normalized = raw_name.strip().lower()

    entry = IngredientCache(
        ingredient_name=normalized,
        sysco_item_number="SYS-100",
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
    assert fetched.sysco_item_number == "SYS-100"


async def test_catalog_embedding(session: AsyncSession):
    arr = np.array([0.1, 0.2, 0.3, 0.4, 0.5], dtype=np.float32)
    embedding_bytes = arr.tobytes()

    record = CatalogEmbedding(
        item_number="CAT-9999",
        description="Premium Wagyu Beef",
        provider="sysco",
        embedding=embedding_bytes,
    )
    session.add(record)
    await session.flush()

    result = await session.execute(
        select(CatalogEmbedding).where(CatalogEmbedding.item_number == "CAT-9999")
    )
    fetched = result.scalar_one()
    assert fetched.item_number == "CAT-9999"
    assert fetched.description == "Premium Wagyu Beef"
    assert fetched.provider == "sysco"

    recovered = np.frombuffer(fetched.embedding, dtype=np.float32)
    np.testing.assert_array_almost_equal(recovered, arr)
