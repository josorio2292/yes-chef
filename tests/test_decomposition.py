"""Tests for the decomposition engine (Exa + PydanticAI)."""

import os
import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from pydantic_ai.models.test import TestModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from yes_chef.db.models import Base, MenuItem, Quote
from yes_chef.decomposition.engine import (
    DecompositionResult,
    decompose_item,
    decomposition_agent,
)

TEST_DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/yeschef_test",
)

TEST_DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/yeschef_test",
)

EGGS_BENEDICT_RECIPE = """
Eggs Benedict Recipe (serves 1):

For the hollandaise sauce:
- 3 egg yolks
- 1 tbsp lemon juice
- 4 oz (113g) unsalted butter, melted
- pinch of cayenne pepper
- salt to taste

For the benedict:
- 2 large eggs (for poaching)
- 2 slices Canadian bacon (about 2 oz)
- 2 brioche rounds, toasted
- 1 tsp white vinegar (for poaching water)
- fresh chives for garnish

Instructions: Make hollandaise by whisking egg yolks with lemon juice...
"""


# ---------------------------------------------------------------------------
# DB fixtures (session-scoped so they share the same Postgres connection)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def test_engine():
    eng = create_async_engine(TEST_DB_URL, echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()


@pytest_asyncio.fixture(loop_scope="session", scope="session")
def test_session_factory(test_engine):
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture(loop_scope="session")
async def db_session(test_session_factory):
    async with test_session_factory() as sess:
        yield sess
        await sess.rollback()


# ---------------------------------------------------------------------------
# test_extract_ingredients_from_recipe
# ---------------------------------------------------------------------------


async def test_extract_ingredients_from_recipe():
    """Agent returns structured base purchasable ingredients for Eggs Benedict."""
    mock_output = {
        "ingredients": [
            {"name": "egg yolks", "quantity": "3 each"},
            {"name": "lemon juice", "quantity": "1 tbsp"},
            {"name": "unsalted butter", "quantity": "4 oz"},
            {"name": "large eggs", "quantity": "2 each"},
            {"name": "Canadian bacon", "quantity": "2 oz"},
            {"name": "brioche rounds", "quantity": "2 each"},
            {"name": "cayenne pepper", "quantity": "pinch"},
            {"name": "white vinegar", "quantity": "1 tsp"},
        ]
    }

    with decomposition_agent.override(model=TestModel(custom_output_args=mock_output)):
        result = await decomposition_agent.run(f"Recipe:\n{EGGS_BENEDICT_RECIPE}")

    decomp: DecompositionResult = result.output
    assert len(decomp.ingredients) > 0, "Must return at least one ingredient"

    for ingredient in decomp.ingredients:
        assert isinstance(ingredient.name, str), "name must be a str"
        assert len(ingredient.name) > 0, "name must be non-empty"
        assert isinstance(ingredient.quantity, str), "quantity must be a str"
        assert len(ingredient.quantity) > 0, "quantity must be non-empty"

    # Hollandaise compound sauce must be decomposed to base ingredients
    names = {i.name.lower() for i in decomp.ingredients}
    hollandaise_bases = {"unsalted butter", "egg yolks", "lemon juice"}
    assert hollandaise_bases.issubset(names), (
        f"Hollandaise must be decomposed to base ingredients. "
        f"Missing: {hollandaise_bases - names}"
    )
    assert "hollandaise" not in names, (
        "Hollandaise should be decomposed, not listed as a single ingredient"
    )


# ---------------------------------------------------------------------------
# test_extract_ingredients_min_length
# ---------------------------------------------------------------------------


async def test_extract_ingredients_min_length():
    """DecompositionResult rejects an empty ingredients list (min_length=1)."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError) as exc_info:
        DecompositionResult(ingredients=[])

    errors = exc_info.value.errors()
    has_min_len_error = any(
        "min_length" in str(e) or "too_short" in str(e["type"]) for e in errors
    )
    assert has_min_len_error, f"Expected a min_length validation error, got: {errors}"


# ---------------------------------------------------------------------------
# test_fallback_without_exa
# ---------------------------------------------------------------------------


async def test_fallback_without_exa():
    """When Exa is unavailable, engine falls back to LLM-only using dish description."""
    mock_output = {
        "ingredients": [
            {"name": "eggs", "quantity": "2 each"},
            {"name": "Canadian bacon", "quantity": "2 slices"},
            {"name": "brioche buns", "quantity": "2 each"},
            {"name": "unsalted butter", "quantity": "2 oz"},
            {"name": "egg yolks", "quantity": "2 each"},
            {"name": "lemon juice", "quantity": "1 tbsp"},
        ]
    }

    # Patch fetch_recipe to simulate Exa failure (returns None)
    with patch(
        "yes_chef.decomposition.engine.fetch_recipe",
        new_callable=AsyncMock,
        return_value=None,
    ):
        with decomposition_agent.override(
            model=TestModel(custom_output_args=mock_output)
        ):
            result = await decompose_item(
                item_name="Eggs Benedict Bites",
                item_description=(
                    "Miniature eggs Benedict on toasted brioche rounds "
                    "with Canadian bacon and hollandaise"
                ),
                menu_item_id=uuid.uuid4(),
                session_factory=None,  # no DB persistence for this test
            )

    assert isinstance(result, DecompositionResult)
    assert len(result.ingredients) >= 1
    for ingredient in result.ingredients:
        assert isinstance(ingredient.name, str)
        assert isinstance(ingredient.quantity, str)


# ---------------------------------------------------------------------------
# test_checkpoint_writes_to_db
# ---------------------------------------------------------------------------


async def test_checkpoint_writes_to_db(test_session_factory):
    """After decomposition, ingredients are persisted and status is 'decomposed'."""
    # Create a quote and menu item (committed so decompose_item's own session sees it)
    async with test_session_factory() as sess:
        quote = Quote(event="Test Event", status="pending", menu_spec={})
        sess.add(quote)
        await sess.flush()
        menu_item = MenuItem(
            quote_id=quote.id,
            item_name="Eggs Benedict Bites",
            category="appetizers",
            status="pending",
        )
        sess.add(menu_item)
        await sess.commit()
        menu_item_id = menu_item.id

    mock_output = {
        "ingredients": [
            {"name": "egg yolks", "quantity": "3 each"},
            {"name": "unsalted butter", "quantity": "4 oz"},
            {"name": "lemon juice", "quantity": "1 tbsp"},
            {"name": "Canadian bacon", "quantity": "2 oz"},
            {"name": "brioche rounds", "quantity": "2 each"},
            {"name": "large eggs", "quantity": "2 each"},
        ]
    }

    with patch(
        "yes_chef.decomposition.engine.fetch_recipe",
        new_callable=AsyncMock,
        return_value=EGGS_BENEDICT_RECIPE,
    ):
        with decomposition_agent.override(
            model=TestModel(custom_output_args=mock_output)
        ):
            result = await decompose_item(
                item_name="Eggs Benedict Bites",
                item_description=(
                    "Miniature eggs Benedict on toasted brioche rounds "
                    "with Canadian bacon and hollandaise"
                ),
                menu_item_id=menu_item_id,
                session_factory=test_session_factory,
            )

    # Verify result shape
    assert isinstance(result, DecompositionResult)
    assert len(result.ingredients) >= 1

    # Verify DB was updated (query via fresh session)
    async with test_session_factory() as sess:
        mi = await sess.get(MenuItem, menu_item_id)

    assert mi.status == "decomposed", f"Expected status 'decomposed', got '{mi.status}'"
    assert mi.step_data is not None, "step_data should be populated"

    # step_data should contain the ingredients list
    persisted_ingredients = mi.step_data.get("ingredients", [])
    assert len(persisted_ingredients) >= 1, "Persisted ingredients must be non-empty"
    first = persisted_ingredients[0]
    assert "name" in first, "Each persisted ingredient must have 'name'"
    assert "quantity" in first, "Each persisted ingredient must have 'quantity'"
