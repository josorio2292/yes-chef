"""Tests for the orchestrator: sequential pipeline and checkpointing."""

import os
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from yes_chef.catalog.service import CatalogService
from yes_chef.db.models import Base, Job, WorkItem
from yes_chef.decomposition.engine import DecompositionResult, Ingredient
from yes_chef.resolution.engine import IngredientMatch, ResolveResult

TEST_DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/yeschef_test",
)

# ---------------------------------------------------------------------------
# Shared menu spec for tests
# ---------------------------------------------------------------------------

MENU_SPEC_3_ITEMS = {
    "event": "Test Wedding",
    "date": "2025-09-20",
    "venue": "The Grand Hall",
    "guest_count_estimate": 100,
    "notes": "Test event",
    "categories": {
        "appetizers": [
            {
                "name": "Bacon-Wrapped Scallops",
                "description": "Pan-seared scallops wrapped in bacon",
                "dietary_notes": "GF",
                "service_style": "passed",
            },
            {
                "name": "Truffle Arancini",
                "description": "Crispy risotto balls filled with black truffle",
                "dietary_notes": "V",
                "service_style": "passed",
            },
        ],
        "main_plates": [
            {
                "name": "Filet Mignon",
                "description": "8oz center-cut filet mignon",
                "dietary_notes": "GF",
            },
        ],
    },
}


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


def make_mock_decompose_fn(ingredients=None):
    """Return an async mock that produces a fixed DecompositionResult."""
    if ingredients is None:
        ingredients = [
            Ingredient(name="test ingredient", quantity="1 oz"),
        ]
    result = DecompositionResult(ingredients=ingredients)
    return AsyncMock(return_value=result)


def make_mock_resolve_fn(unit_cost=5.0):
    """Return an async mock that produces a fixed ResolveResult."""
    match = IngredientMatch(
        name="test ingredient",
        catalog_item="TEST ITEM",
        sysco_item_number="99999",
        provider="sysco",
        source="sysco_catalog",
        unit_cost=unit_cost,
        reasoning="test match",
    )
    result = ResolveResult(matches=[match], ingredient_cost_per_unit=unit_cost)
    return AsyncMock(return_value=result)


def make_mock_catalog_service():
    return MagicMock(spec=CatalogService)


# ---------------------------------------------------------------------------
# DB fixtures (session-scoped engine, function-scoped sessions)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def orch_engine():
    eng = create_async_engine(TEST_DB_URL, echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()


@pytest_asyncio.fixture(loop_scope="session", scope="session")
def orch_session_factory(orch_engine):
    return async_sessionmaker(orch_engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture(loop_scope="session")
async def db_session(orch_session_factory):
    async with orch_session_factory() as sess:
        yield sess
        await sess.rollback()


# ---------------------------------------------------------------------------
# test_submit_job_creates_work_items
# ---------------------------------------------------------------------------


async def test_submit_job_creates_work_items(orch_session_factory):
    """Submit a menu spec → Job created (pending), 3 WorkItems created (pending)."""
    from yes_chef.orchestrator.engine import Orchestrator

    orch = Orchestrator(
        session_factory=orch_session_factory,
        catalog_service=make_mock_catalog_service(),
        decompose_fn=make_mock_decompose_fn(),
        resolve_fn=make_mock_resolve_fn(),
    )

    job_id = await orch.submit_job(MENU_SPEC_3_ITEMS)

    assert job_id is not None
    assert isinstance(job_id, uuid.UUID)

    async with orch_session_factory() as sess:
        job = await sess.get(Job, job_id)
        assert job is not None
        assert job.status == "pending"
        assert job.event == "Test Wedding"
        assert job.date == "2025-09-20"
        assert job.venue == "The Grand Hall"

        result = await sess.execute(select(WorkItem).where(WorkItem.job_id == job_id))
        items = result.scalars().all()
        assert len(items) == 3

        for item in items:
            assert item.status == "pending"

        names = {item.item_name for item in items}
        assert "Bacon-Wrapped Scallops" in names
        assert "Truffle Arancini" in names
        assert "Filet Mignon" in names

        categories = {item.category for item in items}
        assert "appetizers" in categories
        assert "main_plates" in categories


# ---------------------------------------------------------------------------
# test_process_job_all_complete
# ---------------------------------------------------------------------------


async def test_process_job_all_complete(orch_session_factory):
    """Process a job; all 3 items should reach 'completed', job status 'completed'."""
    from yes_chef.orchestrator.engine import Orchestrator

    decompose_fn = make_mock_decompose_fn()
    resolve_fn = make_mock_resolve_fn()

    orch = Orchestrator(
        session_factory=orch_session_factory,
        catalog_service=make_mock_catalog_service(),
        decompose_fn=decompose_fn,
        resolve_fn=resolve_fn,
    )

    job_id = await orch.submit_job(MENU_SPEC_3_ITEMS)
    await orch.process_job(job_id)

    async with orch_session_factory() as sess:
        job = await sess.get(Job, job_id)
        assert job.status == "completed"

        result = await sess.execute(select(WorkItem).where(WorkItem.job_id == job_id))
        items = result.scalars().all()
        assert len(items) == 3

        for item in items:
            assert item.status == "completed", (
                f"Item {item.item_name} has status {item.status!r},"
                " expected 'completed'"
            )

    # Both engines must have been called 3 times
    assert decompose_fn.call_count == 3
    assert resolve_fn.call_count == 3


# ---------------------------------------------------------------------------
# test_process_job_produces_quote
# ---------------------------------------------------------------------------


async def test_process_job_produces_quote(orch_session_factory):
    """Quote output has the right structure and includes all items."""
    from yes_chef.orchestrator.engine import Orchestrator

    orch = Orchestrator(
        session_factory=orch_session_factory,
        catalog_service=make_mock_catalog_service(),
        decompose_fn=make_mock_decompose_fn(),
        resolve_fn=make_mock_resolve_fn(unit_cost=10.0),
    )

    job_id = await orch.submit_job(MENU_SPEC_3_ITEMS)
    quote = await orch.process_job(job_id)

    assert "quote_id" in quote
    assert quote["event"] == "Test Wedding"
    assert quote["date"] == "2025-09-20"
    assert quote["venue"] == "The Grand Hall"
    assert "generated_at" in quote
    assert "line_items" in quote

    line_items = quote["line_items"]
    assert len(line_items) == 3

    for li in line_items:
        assert "item_name" in li
        assert "category" in li
        assert "ingredients" in li
        assert "ingredient_cost_per_unit" in li
        assert li["ingredient_cost_per_unit"] == 10.0

        for ing in li["ingredients"]:
            assert "name" in ing
            assert "quantity" in ing
            assert "unit_cost" in ing
            assert "source" in ing
            assert "sysco_item_number" in ing

    item_names = {li["item_name"] for li in line_items}
    assert "Bacon-Wrapped Scallops" in item_names
    assert "Truffle Arancini" in item_names
    assert "Filet Mignon" in item_names


# ---------------------------------------------------------------------------
# test_item_failure_doesnt_block_others
# ---------------------------------------------------------------------------


async def test_item_failure_doesnt_block_others(orch_session_factory):
    """One item's decomposition fails; other 2 items complete normally."""
    from yes_chef.orchestrator.engine import Orchestrator

    call_count = 0

    async def failing_decompose(item_name, item_description, work_item_id, session):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("Decomposition failed for first item")
        return DecompositionResult(
            ingredients=[Ingredient(name="test ingredient", quantity="1 oz")]
        )

    resolve_fn = make_mock_resolve_fn()

    orch = Orchestrator(
        session_factory=orch_session_factory,
        catalog_service=make_mock_catalog_service(),
        decompose_fn=failing_decompose,
        resolve_fn=resolve_fn,
    )

    job_id = await orch.submit_job(MENU_SPEC_3_ITEMS)
    await orch.process_job(job_id)

    async with orch_session_factory() as sess:
        result = await sess.execute(select(WorkItem).where(WorkItem.job_id == job_id))
        items = result.scalars().all()
        assert len(items) == 3

        failed = [i for i in items if i.status == "failed"]
        completed = [i for i in items if i.status == "completed"]

        assert len(failed) == 1
        assert len(completed) == 2
        assert failed[0].error is not None
        assert "Decomposition failed" in failed[0].error

    # resolve called for the 2 successful items
    assert resolve_fn.call_count == 2


# ---------------------------------------------------------------------------
# test_partial_quote
# ---------------------------------------------------------------------------


async def test_partial_quote(orch_session_factory):
    """Job with failed items: quote contains only successful items."""
    from yes_chef.orchestrator.engine import Orchestrator

    call_count = 0

    async def failing_decompose(item_name, item_description, work_item_id, session):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("Decomposition failed for item 2")
        return DecompositionResult(
            ingredients=[Ingredient(name="test ingredient", quantity="1 oz")]
        )

    orch = Orchestrator(
        session_factory=orch_session_factory,
        catalog_service=make_mock_catalog_service(),
        decompose_fn=failing_decompose,
        resolve_fn=make_mock_resolve_fn(),
    )

    job_id = await orch.submit_job(MENU_SPEC_3_ITEMS)
    quote = await orch.process_job(job_id)

    # Only 2 successful items in quote
    assert len(quote["line_items"]) == 2

    # Job status reflects failure(s) — either "completed_with_errors" or "completed"
    async with orch_session_factory() as sess:
        job = await sess.get(Job, job_id)
        assert job.status in ("completed", "completed_with_errors")


# ---------------------------------------------------------------------------
# test_resume_completed_items_skipped
# ---------------------------------------------------------------------------


async def test_resume_completed_items_skipped(orch_session_factory):
    """A work item already 'completed' is NOT reprocessed on resume."""
    from yes_chef.orchestrator.engine import Orchestrator

    decompose_fn = make_mock_decompose_fn()
    resolve_fn = make_mock_resolve_fn()

    orch = Orchestrator(
        session_factory=orch_session_factory,
        catalog_service=make_mock_catalog_service(),
        decompose_fn=decompose_fn,
        resolve_fn=resolve_fn,
    )

    job_id = await orch.submit_job(MENU_SPEC_3_ITEMS)

    # Manually mark one item as "completed" before processing
    async with orch_session_factory() as sess:
        result = await sess.execute(select(WorkItem).where(WorkItem.job_id == job_id))
        items = result.scalars().all()
        first_item = items[0]
        first_item_id = first_item.id

        first_item.status = "completed"
        first_item.step_data = {
            "matches": [
                {
                    "name": "pre-resolved ingredient",
                    "catalog_item": "ITEM",
                    "sysco_item_number": "11111",
                    "provider": "sysco",
                    "source": "sysco_catalog",
                    "unit_cost": 3.0,
                    "reasoning": "pre-resolved",
                }
            ],
            "ingredient_cost_per_unit": 3.0,
        }
        await sess.commit()

    await orch.process_job(job_id)

    # Only 2 items should have been processed (not 3)
    assert decompose_fn.call_count == 2
    assert resolve_fn.call_count == 2

    # Pre-completed item should still be completed with original data
    async with orch_session_factory() as sess:
        item = await sess.get(WorkItem, first_item_id)
        assert item.status == "completed"
        assert item.step_data["ingredient_cost_per_unit"] == 3.0


# ---------------------------------------------------------------------------
# test_resume_decomposed_items_resume_at_resolve
# ---------------------------------------------------------------------------


async def test_resume_decomposed_items_resume_at_resolve(orch_session_factory):
    """An item at 'decomposed' status skips decompose, runs resolve."""
    from yes_chef.orchestrator.engine import Orchestrator

    decompose_fn = make_mock_decompose_fn()
    resolve_fn = make_mock_resolve_fn()

    orch = Orchestrator(
        session_factory=orch_session_factory,
        catalog_service=make_mock_catalog_service(),
        decompose_fn=decompose_fn,
        resolve_fn=resolve_fn,
    )

    job_id = await orch.submit_job(MENU_SPEC_3_ITEMS)

    # Manually mark one item as "decomposed" with ingredients in step_data
    persisted_ingredients = [
        {"name": "persisted ingredient A", "quantity": "2 oz"},
        {"name": "persisted ingredient B", "quantity": "1 tbsp"},
    ]
    async with orch_session_factory() as sess:
        result = await sess.execute(select(WorkItem).where(WorkItem.job_id == job_id))
        items = result.scalars().all()
        decomposed_item = items[0]

        decomposed_item.status = "decomposed"
        decomposed_item.step_data = {"ingredients": persisted_ingredients}
        await sess.commit()

    await orch.process_job(job_id)

    # decompose called only for the 2 non-decomposed items
    assert decompose_fn.call_count == 2
    # resolve called for all 3 items
    assert resolve_fn.call_count == 3

    # Verify resolve was called with the persisted ingredients for the decomposed item
    # The persisted ingredients should be passed as Ingredient objects to resolve_fn
    resolve_calls = resolve_fn.call_args_list
    # Find the call that used persisted ingredients
    ingredient_sets = [{ing.name for ing in call.args[0]} for call in resolve_calls]
    assert {"persisted ingredient A", "persisted ingredient B"} in ingredient_sets


# ---------------------------------------------------------------------------
# test_resume_resolving_items_restart_resolve
# ---------------------------------------------------------------------------


async def test_resume_resolving_items_restart_resolve(orch_session_factory):
    """An item stuck at 'resolving' restarts resolution on resume."""
    from yes_chef.orchestrator.engine import Orchestrator

    decompose_fn = make_mock_decompose_fn()
    resolve_fn = make_mock_resolve_fn()

    orch = Orchestrator(
        session_factory=orch_session_factory,
        catalog_service=make_mock_catalog_service(),
        decompose_fn=decompose_fn,
        resolve_fn=resolve_fn,
    )

    job_id = await orch.submit_job(MENU_SPEC_3_ITEMS)

    # Mark one item as "resolving" with decomposed ingredients in step_data
    async with orch_session_factory() as sess:
        result = await sess.execute(select(WorkItem).where(WorkItem.job_id == job_id))
        items = result.scalars().all()
        resolving_item = items[0]
        resolving_item_id = resolving_item.id

        resolving_item.status = "resolving"
        resolving_item.step_data = {
            "ingredients": [
                {"name": "resolving ingredient", "quantity": "3 oz"},
            ]
        }
        await sess.commit()

    await orch.process_job(job_id)

    # decompose NOT called for the resolving item (only 2 other items)
    assert decompose_fn.call_count == 2
    # resolve called for ALL 3 items (including the one that was stuck at resolving)
    assert resolve_fn.call_count == 3

    # Resolving item should now be completed
    async with orch_session_factory() as sess:
        item = await sess.get(WorkItem, resolving_item_id)
        assert item.status == "completed"


# ---------------------------------------------------------------------------
# test_resume_pending_items_restart
# ---------------------------------------------------------------------------


async def test_resume_pending_items_restart(orch_session_factory):
    """Pending items run the full pipeline (decompose + resolve)."""
    from yes_chef.orchestrator.engine import Orchestrator

    decompose_fn = make_mock_decompose_fn()
    resolve_fn = make_mock_resolve_fn()

    orch = Orchestrator(
        session_factory=orch_session_factory,
        catalog_service=make_mock_catalog_service(),
        decompose_fn=decompose_fn,
        resolve_fn=resolve_fn,
    )

    job_id = await orch.submit_job(MENU_SPEC_3_ITEMS)
    await orch.process_job(job_id)

    # All 3 items: decompose + resolve each
    assert decompose_fn.call_count == 3
    assert resolve_fn.call_count == 3

    async with orch_session_factory() as sess:
        result = await sess.execute(select(WorkItem).where(WorkItem.job_id == job_id))
        items = result.scalars().all()
        for item in items:
            assert item.status == "completed"
