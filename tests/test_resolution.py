"""Tests for the resolution engine (cache fast path + matching agent)."""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest_asyncio
from pydantic_ai.models.test import TestModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from yes_chef.catalog.provider import ItemNotFoundError, PriceResult
from yes_chef.catalog.service import CatalogCandidate, CatalogService
from yes_chef.db.models import Base, IngredientCache, Job, WorkItem
from yes_chef.decomposition.engine import Ingredient

TEST_DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/yeschef_test",
)


# ---------------------------------------------------------------------------
# DB fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def resolution_engine():
    eng = create_async_engine(TEST_DB_URL, echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()


@pytest_asyncio.fixture(loop_scope="session", scope="session")
def resolution_session_factory(resolution_engine):
    return async_sessionmaker(
        resolution_engine, class_=AsyncSession, expire_on_commit=False
    )


# ---------------------------------------------------------------------------
# Catalog service mock helpers
# ---------------------------------------------------------------------------


def make_mock_catalog_service(
    search_results: list[CatalogCandidate] | None = None,
    price_result: PriceResult | None = None,
    price_raises: Exception | None = None,
) -> CatalogService:
    """Build a MagicMock CatalogService with configurable search/get_price."""
    svc = MagicMock(spec=CatalogService)

    if search_results is None:
        search_results = []
    svc.search = AsyncMock(return_value=search_results)

    if price_raises is not None:
        svc.get_price = MagicMock(side_effect=price_raises)
    elif price_result is not None:
        svc.get_price = MagicMock(return_value=price_result)
    else:
        svc.get_price = MagicMock(
            return_value=PriceResult(cost_per_case=50.0, unit_of_measure="10 LB")
        )

    return svc


# ---------------------------------------------------------------------------
# test_cache_hit_fast_path
# ---------------------------------------------------------------------------


async def test_cache_hit_fast_path(resolution_session_factory):
    """Cache hit: returns IngredientMatch with correct fields, zero LLM calls."""
    from yes_chef.resolution.engine import IngredientMatch, resolve_from_cache

    # Insert cache entry for "butter" (committed so a new session can see it)
    async with resolution_session_factory() as sess:
        sess.add(
            IngredientCache(
                ingredient_name="butter",
                source_item_id="12345",
                source="sysco_catalog",
                provider="sysco",
            )
        )
        await sess.commit()

    # Catalog service returns a valid price
    price = PriceResult(cost_per_case=40.0, unit_of_measure="36 OZ")
    catalog_svc = make_mock_catalog_service(price_result=price)

    ingredient = Ingredient(name="butter", quantity="2 oz")
    result = await resolve_from_cache(
        ingredient, catalog_svc, resolution_session_factory
    )

    assert result is not None
    assert isinstance(result, IngredientMatch)
    assert result.source_item_id == "12345"
    assert result.source == "sysco_catalog"
    assert result.provider == "sysco"
    # get_price was called with the cached source_item_id
    catalog_svc.get_price.assert_called_once_with("12345", "sysco")
    # No LLM was invoked (catalog_svc.search never called)
    catalog_svc.search.assert_not_called()


# ---------------------------------------------------------------------------
# test_cache_hit_invalidation
# ---------------------------------------------------------------------------


async def test_cache_hit_invalidation(resolution_session_factory):
    """Cache entry pointing to non-existent item → invalidated, falls through."""
    from yes_chef.resolution.engine import resolve_from_cache

    # Insert cache entry for "milk"
    async with resolution_session_factory() as sess:
        sess.add(
            IngredientCache(
                ingredient_name="milk",
                source_item_id="99999",
                source="sysco_catalog",
                provider="sysco",
            )
        )
        await sess.commit()

    # get_price raises ItemNotFoundError (item no longer exists)
    catalog_svc = make_mock_catalog_service(price_raises=ItemNotFoundError("99999"))

    ingredient = Ingredient(name="milk", quantity="4 oz")
    result = await resolve_from_cache(
        ingredient, catalog_svc, resolution_session_factory
    )

    # Should return None (cache miss → fall through to agent)
    assert result is None

    # Cache entry should be invalidated (deleted)
    async with resolution_session_factory() as sess:
        stmt = select(IngredientCache).where(IngredientCache.ingredient_name == "milk")
        row = (await sess.execute(stmt)).scalar_one_or_none()
    assert row is None, "Cache entry should have been deleted on invalidation"


# ---------------------------------------------------------------------------
# test_matching_agent_finds_match
# ---------------------------------------------------------------------------


async def test_matching_agent_finds_match(resolution_session_factory):
    """Agent path: resolve beef tenderloin; verify IngredientMatch and cache."""
    from yes_chef.resolution.engine import IngredientMatch, matching_agent, resolve_item

    candidates = [
        CatalogCandidate(
            source_item_id="54321",
            description="BEEF TENDERLOIN WHOLE",
            unit_of_measure="CASE/12 LB",
            provider="sysco",
            similarity_score=0.92,
            cost_per_case=180.0,
            category=None,
            brand=None,
        )
    ]
    price = PriceResult(cost_per_case=180.0, unit_of_measure="CASE/12 LB")
    catalog_svc = make_mock_catalog_service(
        search_results=candidates, price_result=price
    )

    # Create a job + work item (committed so resolve_item's own session can see it)
    async with resolution_session_factory() as sess:
        job = Job(event="Test Beef Event", status="pending", menu_spec={})
        sess.add(job)
        await sess.flush()
        work_item = WorkItem(
            job_id=job.id,
            item_name="Beef Tenderloin",
            category="entrees",
            status="pending",
        )
        sess.add(work_item)
        await sess.commit()
        work_item_id = work_item.id

    mock_output = {
        "name": "beef tenderloin",
        "catalog_item": "BEEF TENDERLOIN WHOLE",
        "source_item_id": "54321",
        "provider": "sysco",
        "source": "sysco_catalog",
        "unit_cost": 15.0,
        "reasoning": "Close match based on catalog search",
    }

    ingredient = Ingredient(name="beef tenderloin", quantity="8 oz")

    with matching_agent.override(model=TestModel(custom_output_args=mock_output)):
        result = await resolve_item(
            ingredients=[ingredient],
            catalog_service=catalog_svc,
            work_item_id=work_item_id,
            session_factory=resolution_session_factory,
        )

    assert len(result.matches) == 1
    match = result.matches[0]
    assert isinstance(match, IngredientMatch)
    assert match.source == "sysco_catalog"
    assert match.source_item_id == "54321"

    # Cache should be populated
    async with resolution_session_factory() as sess:
        stmt = select(IngredientCache).where(
            IngredientCache.ingredient_name == "beef tenderloin"
        )
        cache_row = (await sess.execute(stmt)).scalar_one_or_none()
    assert cache_row is not None, "Cache entry must be created after agent resolution"
    assert cache_row.source_item_id == "54321"
    assert cache_row.source == "sysco_catalog"


# ---------------------------------------------------------------------------
# test_matching_agent_not_available
# ---------------------------------------------------------------------------


async def test_matching_agent_not_available(resolution_session_factory):
    """Agent returns not_available for 'truffle oil'; cache stores not_available."""
    from yes_chef.resolution.engine import IngredientMatch, matching_agent, resolve_item

    catalog_svc = make_mock_catalog_service(search_results=[], price_result=None)

    async with resolution_session_factory() as sess:
        job = Job(event="Test Truffle Event", status="pending", menu_spec={})
        sess.add(job)
        await sess.flush()
        work_item = WorkItem(
            job_id=job.id,
            item_name="Truffle Dish",
            category="entrees",
            status="pending",
        )
        sess.add(work_item)
        await sess.commit()
        work_item_id = work_item.id

    mock_output = {
        "name": "truffle oil",
        "catalog_item": None,
        "source_item_id": None,
        "provider": None,
        "source": "not_available",
        "unit_cost": None,
        "reasoning": "No matching catalog item found",
    }

    ingredient = Ingredient(name="truffle oil", quantity="1 tsp")

    with matching_agent.override(model=TestModel(custom_output_args=mock_output)):
        result = await resolve_item(
            ingredients=[ingredient],
            catalog_service=catalog_svc,
            work_item_id=work_item_id,
            session_factory=resolution_session_factory,
        )

    assert len(result.matches) == 1
    match = result.matches[0]
    assert isinstance(match, IngredientMatch)
    assert match.source == "not_available"
    assert match.unit_cost is None

    # Cache should store not_available
    async with resolution_session_factory() as sess:
        stmt = select(IngredientCache).where(
            IngredientCache.ingredient_name == "truffle oil"
        )
        cache_row = (await sess.execute(stmt)).scalar_one_or_none()
    assert cache_row is not None, "Cache entry must be created for not_available"
    assert cache_row.source == "not_available"
    assert cache_row.source_item_id is None


# ---------------------------------------------------------------------------
# test_cache_populated_after_agent
# ---------------------------------------------------------------------------


async def test_cache_populated_after_agent(resolution_session_factory):
    """After agent resolves, cache entry exists in DB with correct fields."""
    from yes_chef.resolution.engine import matching_agent, resolve_item

    candidates = [
        CatalogCandidate(
            source_item_id="77777",
            description="CHICKEN BREAST BONELESS",
            unit_of_measure="40 LB CASE",
            provider="sysco",
            similarity_score=0.88,
            cost_per_case=80.0,
            category=None,
            brand=None,
        )
    ]
    price = PriceResult(cost_per_case=80.0, unit_of_measure="40 LB CASE")
    catalog_svc = make_mock_catalog_service(
        search_results=candidates, price_result=price
    )

    async with resolution_session_factory() as sess:
        job = Job(event="Chicken Event", status="pending", menu_spec={})
        sess.add(job)
        await sess.flush()
        work_item = WorkItem(
            job_id=job.id,
            item_name="Chicken Breast",
            category="entrees",
            status="pending",
        )
        sess.add(work_item)
        await sess.commit()
        work_item_id = work_item.id

    mock_output = {
        "name": "chicken breast",
        "catalog_item": "CHICKEN BREAST BONELESS",
        "source_item_id": "77777",
        "provider": "sysco",
        "source": "sysco_catalog",
        "unit_cost": 2.0,
        "reasoning": "Good match",
    }

    ingredient = Ingredient(name="chicken breast", quantity="6 oz")

    with matching_agent.override(model=TestModel(custom_output_args=mock_output)):
        await resolve_item(
            ingredients=[ingredient],
            catalog_service=catalog_svc,
            work_item_id=work_item_id,
            session_factory=resolution_session_factory,
        )

    async with resolution_session_factory() as sess:
        stmt = select(IngredientCache).where(
            IngredientCache.ingredient_name == "chicken breast"
        )
        cache_row = (await sess.execute(stmt)).scalar_one_or_none()
    assert cache_row is not None
    assert cache_row.source_item_id == "77777"
    assert cache_row.source == "sysco_catalog"
    assert cache_row.provider == "sysco"


# ---------------------------------------------------------------------------
# test_subsequent_call_hits_fast_path
# ---------------------------------------------------------------------------


async def test_subsequent_call_hits_fast_path(resolution_session_factory):
    """Second call for same ingredient hits fast path (zero LLM calls)."""
    from yes_chef.resolution.engine import matching_agent, resolve_item

    # Pre-populate cache (committed)
    async with resolution_session_factory() as sess:
        sess.add(
            IngredientCache(
                ingredient_name="salmon fillet",
                source_item_id="88888",
                source="sysco_catalog",
                provider="sysco",
            )
        )
        await sess.commit()

    price = PriceResult(cost_per_case=120.0, unit_of_measure="20 LB CASE")
    catalog_svc = make_mock_catalog_service(price_result=price)

    async with resolution_session_factory() as sess:
        job = Job(event="Salmon Event", status="pending", menu_spec={})
        sess.add(job)
        await sess.flush()
        work_item = WorkItem(
            job_id=job.id,
            item_name="Salmon",
            category="entrees",
            status="pending",
        )
        sess.add(work_item)
        await sess.commit()
        work_item_id = work_item.id

    ingredient = Ingredient(name="salmon fillet", quantity="6 oz")

    # Track if agent was called
    agent_called = False
    original_run = matching_agent.run

    async def patched_run(*args, **kwargs):
        nonlocal agent_called
        agent_called = True
        return await original_run(*args, **kwargs)

    with patch.object(matching_agent, "run", side_effect=patched_run):
        result = await resolve_item(
            ingredients=[ingredient],
            catalog_service=catalog_svc,
            work_item_id=work_item_id,
            session_factory=resolution_session_factory,
        )

    assert len(result.matches) == 1
    assert agent_called is False, "Fast path must not invoke the LLM agent"
    assert result.matches[0].source_item_id == "88888"


# ---------------------------------------------------------------------------
# test_cost_rollup
# ---------------------------------------------------------------------------


async def test_cost_rollup():
    """Rollup sums only non-null unit_costs."""
    from yes_chef.resolution.engine import IngredientMatch, ResolveResult

    matches = [
        IngredientMatch(
            name="butter",
            catalog_item="BUTTER UNSALTED",
            source_item_id="111",
            provider="sysco",
            source="sysco_catalog",
            unit_cost=5.0,
            reasoning="good match",
        ),
        IngredientMatch(
            name="truffle oil",
            catalog_item=None,
            source_item_id=None,
            provider=None,
            source="not_available",
            unit_cost=None,
            reasoning="no match",
        ),
        IngredientMatch(
            name="cream",
            catalog_item="HEAVY CREAM",
            source_item_id="222",
            provider="sysco",
            source="sysco_catalog",
            unit_cost=3.0,
            reasoning="match",
        ),
    ]

    result = ResolveResult(
        matches=matches,
        ingredient_cost_per_unit=sum(
            m.unit_cost for m in matches if m.unit_cost is not None
        ),
    )

    assert result.ingredient_cost_per_unit == 8.0


# ---------------------------------------------------------------------------
# test_partial_ingredient_failure
# ---------------------------------------------------------------------------


async def test_partial_ingredient_failure(resolution_session_factory):
    """One ingredient fails; gets not_available. Work item status not marked failed."""
    from yes_chef.resolution.engine import matching_agent, resolve_item

    # First ingredient resolves fine
    candidates = [
        CatalogCandidate(
            source_item_id="11111",
            description="BUTTER UNSALTED",
            unit_of_measure="36 OZ",
            provider="sysco",
            similarity_score=0.95,
            cost_per_case=40.0,
            category=None,
            brand=None,
        )
    ]
    price = PriceResult(cost_per_case=40.0, unit_of_measure="36 OZ")
    catalog_svc = make_mock_catalog_service(
        search_results=candidates, price_result=price
    )

    async with resolution_session_factory() as sess:
        job = Job(event="Partial Failure Event", status="pending", menu_spec={})
        sess.add(job)
        await sess.flush()
        work_item = WorkItem(
            job_id=job.id,
            item_name="Test Dish",
            category="entrees",
            status="pending",
        )
        sess.add(work_item)
        await sess.commit()
        work_item_id = work_item.id

    # Good match output for butter
    good_output = {
        "name": "unsalted butter partial",
        "catalog_item": "BUTTER UNSALTED",
        "source_item_id": "11111",
        "provider": "sysco",
        "source": "sysco_catalog",
        "unit_cost": 2.5,
        "reasoning": "good match",
    }

    # We have two ingredients: one succeeds, one raises an exception in the agent
    ingredient_good = Ingredient(name="unsalted butter partial", quantity="2 oz")
    ingredient_bad = Ingredient(name="exotic ingredient xyz", quantity="1 oz")

    # First call succeeds (good output), second call raises
    call_count = 0

    async def selective_run(prompt, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Return good match
            with matching_agent.override(
                model=TestModel(custom_output_args=good_output)
            ):
                return await matching_agent.run(prompt, *args, **kwargs)
        else:
            raise RuntimeError("Simulated agent failure")

    with patch.object(matching_agent, "run", side_effect=selective_run):
        result = await resolve_item(
            ingredients=[ingredient_good, ingredient_bad],
            catalog_service=catalog_svc,
            work_item_id=work_item_id,
            session_factory=resolution_session_factory,
        )

    # Both ingredients should produce a match
    assert len(result.matches) == 2

    # The bad ingredient should be not_available with null cost
    bad_match = next(m for m in result.matches if m.name == "exotic ingredient xyz")
    assert bad_match.source == "not_available"
    assert bad_match.unit_cost is None

    # Work item should NOT be marked failed (resolve_item checkpoints as "completed")
    async with resolution_session_factory() as sess:
        wi = await sess.get(WorkItem, work_item_id)
        assert wi.status != "failed", "Partial failure must not mark work item failed"


# ---------------------------------------------------------------------------
# test_resolve_checkpoint
# ---------------------------------------------------------------------------


async def test_resolve_checkpoint(resolution_session_factory):
    """After resolve_item, work item status is 'completed' and step_data has results."""
    from yes_chef.resolution.engine import matching_agent, resolve_item

    candidates = [
        CatalogCandidate(
            source_item_id="33333",
            description="CREAM HEAVY",
            unit_of_measure="12/1 QT",
            provider="sysco",
            similarity_score=0.90,
            cost_per_case=60.0,
            category=None,
            brand=None,
        )
    ]
    price = PriceResult(cost_per_case=60.0, unit_of_measure="12/1 QT")
    catalog_svc = make_mock_catalog_service(
        search_results=candidates, price_result=price
    )

    async with resolution_session_factory() as sess:
        job = Job(event="Checkpoint Event", status="pending", menu_spec={})
        sess.add(job)
        await sess.flush()
        work_item = WorkItem(
            job_id=job.id,
            item_name="Cream Sauce",
            category="sauces",
            status="pending",
        )
        sess.add(work_item)
        await sess.commit()
        work_item_id = work_item.id

    mock_output = {
        "name": "heavy cream",
        "catalog_item": "CREAM HEAVY",
        "source_item_id": "33333",
        "provider": "sysco",
        "source": "sysco_catalog",
        "unit_cost": 5.0,
        "reasoning": "exact match",
    }

    ingredient = Ingredient(name="heavy cream", quantity="2 oz")

    with matching_agent.override(model=TestModel(custom_output_args=mock_output)):
        await resolve_item(
            ingredients=[ingredient],
            catalog_service=catalog_svc,
            work_item_id=work_item_id,
            session_factory=resolution_session_factory,
        )

    # Checkpoint: work item status (query via fresh session)
    async with resolution_session_factory() as sess:
        wi = await sess.get(WorkItem, work_item_id)

    assert wi.status == "completed", f"Expected 'completed', got '{wi.status}'"
    assert wi.step_data is not None

    # step_data must contain matches and cost
    assert "matches" in wi.step_data
    assert "ingredient_cost_per_unit" in wi.step_data
    assert len(wi.step_data["matches"]) == 1
    assert wi.step_data["ingredient_cost_per_unit"] == 5.0
