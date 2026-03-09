"""Resolution engine: cache fast path + PydanticAI matching agent."""

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.test import TestModel
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from yes_chef.catalog.provider import ItemNotFoundError
from yes_chef.catalog.service import CatalogService
from yes_chef.db.models import IngredientCache, MenuItem
from yes_chef.decomposition.engine import Ingredient

# Type alias for an async session factory
SessionFactory = Callable[..., Any]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pydantic output models
# ---------------------------------------------------------------------------


class IngredientMatch(BaseModel):
    """Resolution result for a single ingredient."""

    name: str
    catalog_item: str | None  # matched catalog description
    source_item_id: str | None
    provider: str | None
    source: Literal["sysco_catalog", "estimated", "not_available"]
    unit_cost: float | None  # per-serving cost
    reasoning: str  # logged, not in quote


class ResolveResult(BaseModel):
    """Resolution result for a full ingredient list."""

    matches: list[IngredientMatch]
    ingredient_cost_per_unit: float  # sum of non-null unit_costs


# ---------------------------------------------------------------------------
# Agent dependencies
# ---------------------------------------------------------------------------


@dataclass
class ResolutionDeps:
    catalog_service: CatalogService
    ingredient_name: str
    serving_quantity: str
    session_factory: SessionFactory


# ---------------------------------------------------------------------------
# PydanticAI matching agent
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """
You are matching a recipe ingredient to a supplier catalog item for catering
cost estimation.

You are given:
- The ingredient name
- The serving quantity

Your tasks:
1. Search the catalog using the ingredient name and any relevant terms.
2. Evaluate the candidates returned and pick the best match.
3. Call get_price on the best candidate to retrieve cost and unit of measure.
4. Interpret the UOM string to compute a per-serving unit_cost.
   - UOM examples: "20/8 OZ" = 20 pieces of 8 oz each = 160 oz total per case.
   - "40 LB CASE" = 40 lbs per case = 640 oz per case.
   - Use the serving quantity to determine the fraction of the case per serving.
   - Divide case cost by total servings per case to get unit_cost.
5. Classify your result:
   - "sysco_catalog": exact or near match found in the catalog.
   - "estimated": approximation used (e.g., similar item, different brand/size).
   - "not_available": no suitable match found.
6. Call update_cache to persist your result for future lookups.
7. Return a structured IngredientMatch with your reasoning.

Important:
- Do NOT use regex to parse UOM — reason about it as text.
- If no match is found, return source="not_available", source_item_id=None,
  unit_cost=None.
- Always call update_cache before returning.
""".strip()


def _default_matching_model():
    """
    Resolve the LLM model for the matching agent.

    Uses MATCHING_MODEL env var when set, otherwise falls back to TestModel.
    In production, set MATCHING_MODEL=openai:gpt-4o-mini.
    """
    model_name = os.environ.get("MATCHING_MODEL")
    if model_name:
        return model_name
    return TestModel()


matching_agent: Agent[ResolutionDeps, IngredientMatch] = Agent(
    model=_default_matching_model(),
    output_type=IngredientMatch,
    system_prompt=_SYSTEM_PROMPT,
)


# ---------------------------------------------------------------------------
# Agent tools
# ---------------------------------------------------------------------------


@matching_agent.tool
async def search_catalog(ctx: RunContext[ResolutionDeps], query: str) -> list[dict]:
    """Search the catalog for candidates matching the query.

    Returns enriched candidates with source_item_id, description,
    unit_of_measure, cost_per_case, category, brand, provider, and similarity_score.
    """
    candidates = await ctx.deps.catalog_service.search(query)
    return [
        {
            "source_item_id": c.source_item_id,
            "description": c.description,
            "unit_of_measure": c.unit_of_measure,
            "cost_per_case": c.cost_per_case,
            "provider": c.provider,
            "similarity_score": c.similarity_score,
            "category": c.category,
            "brand": c.brand,
        }
        for c in candidates
    ]


@matching_agent.tool
async def get_price(
    ctx: RunContext[ResolutionDeps], source_item_id: str, provider: str
) -> dict:
    """Retrieve pricing for a catalog item.

    Returns cost_per_case and unit_of_measure.
    The agent (LLM) interprets the UOM string to compute per-serving cost.
    """
    price_result = ctx.deps.catalog_service.get_price(source_item_id, provider)
    return {
        "cost_per_case": price_result.cost_per_case,
        "unit_of_measure": price_result.unit_of_measure,
    }


@matching_agent.tool
async def update_cache(
    ctx: RunContext[ResolutionDeps],
    ingredient_name: str,
    source_item_id: str | None,
    source: str,
    provider: str | None,
) -> str:
    """Upsert an ingredient → catalog mapping in the cache table.

    Returns a confirmation string.
    """
    normalized = ingredient_name.lower().strip()

    async with ctx.deps.session_factory() as session:
        async with session.begin():
            stmt = (
                pg_insert(IngredientCache)
                .values(
                    ingredient_name=normalized,
                    source_item_id=source_item_id,
                    source=source,
                    provider=provider,
                )
                .on_conflict_do_update(
                    index_elements=["ingredient_name"],
                    set_={
                        "source_item_id": source_item_id,
                        "source": source,
                        "provider": provider,
                        "updated_at": func.now(),
                    },
                )
            )
            await session.execute(stmt)

    return f"Cached: {normalized} → {source_item_id} ({source})"


# ---------------------------------------------------------------------------
# Cache fast path
# ---------------------------------------------------------------------------


async def resolve_from_cache(
    ingredient: Ingredient,
    catalog_service: CatalogService,
    session_factory: SessionFactory,
) -> IngredientMatch | None:
    """Try to resolve an ingredient from the cache.

    Returns:
        IngredientMatch if cache hit and price lookup succeeds.
        None if cache miss or price lookup fails (cache invalidated on failure).
    """
    normalized = ingredient.name.lower().strip()

    async with session_factory() as session:
        # Query cache
        stmt = select(IngredientCache).where(
            IngredientCache.ingredient_name == normalized
        )
        result = await session.execute(stmt)
        cache_entry = result.scalar_one_or_none()

        if cache_entry is None:
            return None  # cache miss

        # not_available cached — return immediately, no price lookup needed
        if cache_entry.source == "not_available":
            logger.debug("Cache hit (not_available): %s", normalized)
            return IngredientMatch(
                name=ingredient.name,
                catalog_item=None,
                source_item_id=None,
                provider=None,
                source="not_available",
                unit_cost=None,
                reasoning="Resolved from cache (not_available)",
            )

        # Cached source_item_id — try price lookup
        source_item_id = cache_entry.source_item_id
        provider = cache_entry.provider
        source = cache_entry.source

    try:
        price_result = catalog_service.get_price(source_item_id, provider)
        logger.debug("Cache hit: %s → %s", normalized, source_item_id)
        return IngredientMatch(
            name=ingredient.name,
            catalog_item=None,  # not stored in cache; agent result had it
            source_item_id=source_item_id,
            provider=provider,
            source=source,
            unit_cost=price_result.cost_per_case,  # cached cost (no UOM re-parsing)
            reasoning="Resolved from cache",
        )
    except (ItemNotFoundError, Exception) as exc:
        logger.warning(
            "Cache invalidation: %s → %s (%s). Falling through to agent.",
            normalized,
            source_item_id,
            exc,
        )
        # Invalidate stale cache entry in a short-lived session
        async with session_factory() as session:
            async with session.begin():
                await session.execute(
                    delete(IngredientCache).where(
                        IngredientCache.ingredient_name == normalized
                    )
                )
        return None  # fall through to agent


# ---------------------------------------------------------------------------
# Engine orchestration
# ---------------------------------------------------------------------------


async def resolve_item(
    ingredients: list[Ingredient],
    catalog_service: CatalogService,
    menu_item_id: UUID,
    session_factory: SessionFactory,
) -> ResolveResult:
    """Resolve all ingredients to catalog items.

    Each DB operation uses its own short-lived session so no connection is
    held open across long-running LLM API calls.

    For each ingredient:
    1. Try cache fast path.
    2. If miss, run the matching agent.
    3. If agent fails, mark as not_available (partial failure).

    After all ingredients resolved:
    - Checkpoint: update work item status to "completed" with step_data.
    - Return ResolveResult.
    """
    matches: list[IngredientMatch] = []

    for ingredient in ingredients:
        match: IngredientMatch | None = None

        # 1. Try cache fast path (short-lived session inside)
        try:
            match = await resolve_from_cache(
                ingredient, catalog_service, session_factory
            )
        except Exception as exc:
            logger.warning("Cache lookup error for %s: %s", ingredient.name, exc)
            match = None

        if match is not None:
            matches.append(match)
            continue

        # 2. Run matching agent — no DB connection held during LLM calls
        try:
            deps = ResolutionDeps(
                catalog_service=catalog_service,
                ingredient_name=ingredient.name,
                serving_quantity=ingredient.quantity,
                session_factory=session_factory,
            )
            prompt = (
                f"Ingredient: {ingredient.name}\n"
                f"Serving quantity: {ingredient.quantity}\n\n"
                "Search the catalog, find the best match, price it, "
                "and cache the result."
            )
            run_result = await matching_agent.run(prompt, deps=deps)
            match = run_result.output
            logger.info(
                "Agent resolved %s → %s (%s)",
                ingredient.name,
                match.source_item_id,
                match.source,
            )
            # Guarantee cache is populated after agent resolution.
            # The agent's update_cache tool may not have fired (e.g. TestModel),
            # so we upsert here unconditionally based on the structured output.
            normalized = ingredient.name.lower().strip()
            async with session_factory() as session:
                async with session.begin():
                    stmt = (
                        pg_insert(IngredientCache)
                        .values(
                            ingredient_name=normalized,
                            source_item_id=match.source_item_id,
                            source=match.source,
                            provider=match.provider,
                        )
                        .on_conflict_do_update(
                            index_elements=["ingredient_name"],
                            set_={
                                "source_item_id": match.source_item_id,
                                "source": match.source,
                                "provider": match.provider,
                                "updated_at": func.now(),
                            },
                        )
                    )
                    await session.execute(stmt)
        except Exception as exc:
            logger.error("Agent failed for %s: %s", ingredient.name, exc)
            # Partial failure — mark as not_available
            match = IngredientMatch(
                name=ingredient.name,
                catalog_item=None,
                source_item_id=None,
                provider=None,
                source="not_available",
                unit_cost=None,
                reasoning=f"Resolution failed: {exc}",
            )

        matches.append(match)

    # Cost rollup
    cost = sum(m.unit_cost for m in matches if m.unit_cost is not None)

    # Checkpoint: update work item in a short-lived session
    async with session_factory() as session:
        async with session.begin():
            stmt = select(MenuItem).where(MenuItem.id == menu_item_id)
            db_result = await session.execute(stmt)
            menu_item = db_result.scalar_one_or_none()

            if menu_item is not None:
                menu_item.status = "completed"
                menu_item.step_data = {
                    "matches": [m.model_dump() for m in matches],
                    "ingredient_cost_per_unit": cost,
                }

    return ResolveResult(matches=matches, ingredient_cost_per_unit=cost)
