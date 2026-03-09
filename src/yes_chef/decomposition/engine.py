"""Decomposition engine: Exa recipe retrieval + PydanticAI structured extraction."""

import os
from collections.abc import Callable
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

# Type alias for an async session factory
SessionFactory = Callable[..., Any]

# ---------------------------------------------------------------------------
# Pydantic output models
# ---------------------------------------------------------------------------


class Ingredient(BaseModel):
    """A single base purchasable ingredient with a per-serving quantity."""

    name: str
    quantity: str  # e.g. "8 oz", "2 tbsp", "3 each"


class DecompositionResult(BaseModel):
    """Structured output from the decomposition agent."""

    ingredients: list[Ingredient] = Field(min_length=1)


# ---------------------------------------------------------------------------
# PydanticAI decomposition agent
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """
You are a professional catering ingredient analyst.

Given a recipe or dish description, extract ALL base purchasable ingredients.

Rules:
- Decompose every compound preparation to its base components.
  For example: hollandaise sauce → butter, egg yolks, lemon juice, cayenne.
  Do NOT list "hollandaise" as a single ingredient.
- List only raw, purchasable items (e.g., "unsalted butter", "egg yolks",
  "Canadian bacon"), not technique names or equipment.
- Include a realistic per-serving quantity for each ingredient.
  Use standard units: "oz", "tbsp", "tsp", "each", "cup", "lb", "clove".
- Be comprehensive: include every ingredient needed to prepare the dish.
- Return a structured list with one entry per unique ingredient.
""".strip()


def _default_model():
    """
    Resolve the LLM model to use.

    Uses DECOMPOSITION_MODEL env var when set, otherwise falls back to
    TestModel so that imports never require an API key (tests override via
    `decomposition_agent.override(model=TestModel(...))`).

    In production, set DECOMPOSITION_MODEL=openai:gpt-4o-mini.
    """
    model_name = os.environ.get("DECOMPOSITION_MODEL")
    if model_name:
        return model_name
    return TestModel()


decomposition_agent: Agent[None, DecompositionResult] = Agent(
    model=_default_model(),
    output_type=DecompositionResult,
    system_prompt=_SYSTEM_PROMPT,
)


# ---------------------------------------------------------------------------
# Exa recipe retrieval
# ---------------------------------------------------------------------------


async def fetch_recipe(dish_name: str, dish_description: str) -> str | None:
    """
    Query Exa for a professional catering recipe and return the text.

    Returns None if Exa is unavailable or no useful results are found.
    """
    api_key = os.environ.get("EXA_API_KEY")
    if not api_key:
        return None

    try:
        from exa_py import Exa

        client = Exa(api_key=api_key)
        query = f"professional catering recipe {dish_name} ingredients per serving"
        response = client.search(
            query,
            num_results=3,
            contents={"text": True},
        )

        texts: list[str] = []
        for result in response.results:
            if result.text:
                texts.append(result.text[:3000])

        if not texts:
            return None

        return "\n\n---\n\n".join(texts)

    except Exception:
        return None


# ---------------------------------------------------------------------------
# Engine orchestration
# ---------------------------------------------------------------------------


async def decompose_item(
    item_name: str,
    item_description: str,
    menu_item_id: UUID,
    session_factory: SessionFactory | None,
) -> DecompositionResult:
    """
    Decompose a menu item into base purchasable ingredients.

    Each DB operation uses its own short-lived session so no connection is
    held open across long-running LLM API calls.

    Steps:
    1. Try Exa for a professional recipe.
    2. Fall back to the dish description if Exa fails.
    3. Run the decomposition agent for structured extraction.
    4. Persist results to DB and update work item status (if session_factory provided).
    5. Return the DecompositionResult.
    """
    # 1. Try Exa
    recipe_text = await fetch_recipe(item_name, item_description)

    # 2. Fall back to description
    if recipe_text:
        prompt = (
            f"Dish: {item_name}\n"
            f"Description: {item_description}\n\n"
            f"Recipe source:\n{recipe_text}"
        )
    else:
        prompt = (
            f"Dish: {item_name}\n"
            f"Description: {item_description}\n\n"
            "No recipe source was found. Use the dish description above to "
            "identify and list all base purchasable ingredients with "
            "per-serving quantities."
        )

    # 3. Run decomposition agent (no DB connection held during LLM call)
    run_result = await decomposition_agent.run(prompt)
    decomp_result: DecompositionResult = run_result.output

    # 4. Checkpoint: persist to DB in a short-lived session if factory provided
    if session_factory is not None:
        from sqlalchemy import select

        from yes_chef.db.models import MenuItem

        async with session_factory() as session:
            async with session.begin():
                stmt = select(MenuItem).where(MenuItem.id == menu_item_id)
                db_result = await session.execute(stmt)
                menu_item = db_result.scalar_one_or_none()

                if menu_item is not None:
                    menu_item.status = "decomposed"
                    menu_item.step_data = {
                        "ingredients": [
                            {"name": ing.name, "quantity": ing.quantity}
                            for ing in decomp_result.ingredients
                        ]
                    }

    return decomp_result
