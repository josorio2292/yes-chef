"""Orchestrator engine: pipeline with checkpointing, resumability, and concurrency."""

import asyncio
import logging
import uuid
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from yes_chef.catalog.service import CatalogService
from yes_chef.db.models import MenuItem, Quote
from yes_chef.decomposition.engine import (
    DecompositionResult,
    Ingredient,
    SessionFactory,
    decompose_item,
)
from yes_chef.events import EventBus, SSEEvent
from yes_chef.resolution.engine import ResolveResult, resolve_item

logger = logging.getLogger(__name__)

# Type aliases for the injectable engine functions.
# Both engines receive the session_factory so they manage their own short-lived
# sessions, keeping DB connections free during long LLM API calls.
DecomposeFn = Callable[
    [str, str, uuid.UUID, SessionFactory],
    Coroutine[Any, Any, DecompositionResult],
]
ResolveFn = Callable[
    [list[Ingredient], CatalogService, uuid.UUID, SessionFactory],
    Coroutine[Any, Any, ResolveResult],
]


class Orchestrator:
    """Sequential pipeline orchestrator.

    Accepts a menu spec, creates a Quote + MenuItems, and processes each
    item through decomposition → resolution with checkpointing.
    Supports resumability: completed/decomposed/resolving items pick up
    where they left off without restarting from scratch.
    """

    def __init__(
        self,
        session_factory: Any,
        catalog_service: CatalogService,
        decompose_fn: DecomposeFn | None = None,
        resolve_fn: ResolveFn | None = None,
        max_concurrent: int = 3,
        event_bus: EventBus | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._catalog_service = catalog_service
        self._decompose_fn: DecomposeFn = decompose_fn or decompose_item
        self._resolve_fn: ResolveFn = resolve_fn or resolve_item
        self._max_concurrent = max_concurrent
        self._event_bus: EventBus | None = event_bus

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def submit_quote(self, menu_spec: dict) -> uuid.UUID:
        """Create a Quote and one MenuItem per menu item.

        Returns the quote UUID.
        """
        async with self._session_factory() as session:
            quote = Quote(
                event=menu_spec.get("event", ""),
                date=menu_spec.get("date"),
                venue=menu_spec.get("venue"),
                guest_count_estimate=menu_spec.get("guest_count_estimate"),
                notes=menu_spec.get("notes"),
                status="pending",
                menu_spec=menu_spec,
            )
            session.add(quote)
            await session.flush()

            categories: dict = menu_spec.get("categories", {})
            for category_name, items in categories.items():
                for item in items:
                    menu_item = MenuItem(
                        quote_id=quote.id,
                        item_name=item["name"],
                        category=category_name,
                        status="pending",
                    )
                    session.add(menu_item)

            await session.commit()
            return quote.id

    async def process_quote(self, quote_id: uuid.UUID) -> dict:
        """Process all menu items for a quote and return the assembled quote.

        Processing is sequential. Failed items do not block others.
        Completed/decomposed/resolving items are resumed from their last
        checkpoint.
        """
        # Mark quote as processing
        async with self._session_factory() as session:
            quote = await session.get(Quote, quote_id)
            if quote is None:
                raise ValueError(f"Quote {quote_id} not found")
            quote.status = "processing"
            await session.commit()

        # Load menu items
        async with self._session_factory() as session:
            result = await session.execute(
                select(MenuItem).where(MenuItem.quote_id == quote_id)
            )
            menu_items = result.scalars().all()

        # Separate already-completed items (skip reprocessing) from pending ones
        completed_items: list[dict] = []
        failed_count = 0

        for menu_item in menu_items:
            if menu_item.status == "completed":
                line_item = self._line_item_from_step_data(menu_item)
                if line_item is not None:
                    completed_items.append(line_item)

        pending_items = [mi for mi in menu_items if mi.status != "completed"]

        # Process pending items concurrently, bounded by the semaphore
        semaphore = asyncio.Semaphore(self._max_concurrent)

        async def process_with_semaphore(menu_item: MenuItem) -> dict | None:
            async with semaphore:
                return await self._process_item(menu_item, quote_id)

        tasks = [process_with_semaphore(mi) for mi in pending_items]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, BaseException):
                # _process_item never propagates; this would be an unexpected error
                logger.error(
                    "Unexpected exception from process_with_semaphore: %s", result
                )
                failed_count += 1
            elif result is not None:
                completed_items.append(result)
            else:
                failed_count += 1

        # Assemble quote
        async with self._session_factory() as session:
            quote = await session.get(Quote, quote_id)
            assembled_quote = self._assemble_quote(quote, completed_items)

            # Mark quote done
            if failed_count > 0:
                quote.status = "completed_with_errors"
            else:
                quote.status = "completed"
            await session.commit()

        await self._publish(
            str(quote_id),
            SSEEvent(
                event="quote_completed",
                data={
                    "quote_id": str(quote_id),
                    "timestamp": _now_iso(),
                },
            ),
        )

        return assembled_quote

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _process_item(
        self, menu_item: MenuItem, quote_id: uuid.UUID | None = None
    ) -> dict | None:
        """Process a single MenuItem through decompose → resolve.

        Returns a line item dict on success, None on failure.
        Checkpoints status at each stage.
        """
        item_id = menu_item.id
        item_name = menu_item.item_name
        category = menu_item.category
        quote_id_str = str(quote_id) if quote_id is not None else str(menu_item.quote_id)

        try:
            # ----------------------------------------------------------------
            # Decomposition stage
            # Skipped if item is already "decomposed" or "resolving"
            # ----------------------------------------------------------------
            if menu_item.status in ("pending", "decomposing"):
                logger.info("Decomposing: %s", item_name)
                # Find item description from menu spec via the quote (not needed here
                # because the menu item doesn't store it separately; use item_name)
                # The menu item doesn't store description — we need to fetch from quote
                description = await self._get_item_description(item_id)

                async with self._session_factory() as session:
                    # Mark as decomposing
                    mi = await session.get(MenuItem, item_id)
                    mi.status = "decomposing"
                    await session.commit()

                await self._publish(
                    quote_id_str,
                    SSEEvent(
                        event="item_step_change",
                        data={
                            "quote_id": quote_id_str,
                            "item_name": item_name,
                            "status": "decomposing",
                            "timestamp": _now_iso(),
                        },
                    ),
                )

                # Pass session_factory so decompose_item manages its own
                # short-lived sessions — no connection held during LLM calls.
                decomp_result = await self._decompose_fn(
                    item_name, description, item_id, self._session_factory
                )

                # Checkpoint: status → "decomposed", step_data has ingredients.
                # Opens a short-lived session after the LLM call has returned.
                async with self._session_factory() as session:
                    mi = await session.get(MenuItem, item_id)
                    mi.status = "decomposed"
                    mi.step_data = {
                        "ingredients": [
                            {"name": ing.name, "quantity": ing.quantity}
                            for ing in decomp_result.ingredients
                        ]
                    }
                    await session.commit()

                ingredients = decomp_result.ingredients

            else:
                # "decomposed" or "resolving": load ingredients from step_data
                logger.info(
                    "Resuming from checkpoint '%s': %s", menu_item.status, item_name
                )
                raw = (menu_item.step_data or {}).get("ingredients", [])
                ingredients = [
                    Ingredient(name=ing["name"], quantity=ing["quantity"])
                    for ing in raw
                ]

            # ----------------------------------------------------------------
            # Resolution stage
            # ----------------------------------------------------------------
            logger.info("Resolving: %s (%d ingredients)", item_name, len(ingredients))

            async with self._session_factory() as session:
                # Mark as resolving
                mi = await session.get(MenuItem, item_id)
                mi.status = "resolving"
                await session.commit()

            await self._publish(
                quote_id_str,
                SSEEvent(
                    event="item_step_change",
                    data={
                        "quote_id": quote_id_str,
                        "item_name": item_name,
                        "status": "resolving",
                        "timestamp": _now_iso(),
                    },
                ),
            )

            # Pass session_factory so resolve_item manages its own short-lived
            # sessions — no connection held during LLM calls.
            resolve_result = await self._resolve_fn(
                ingredients, self._catalog_service, item_id, self._session_factory
            )

            # Checkpoint: status → "completed", step_data has matches + cost.
            # Opens a short-lived session after the LLM call has returned.
            async with self._session_factory() as session:
                mi = await session.get(MenuItem, item_id)
                mi.status = "completed"
                mi.step_data = {
                    "matches": [m.model_dump() for m in resolve_result.matches],
                    "ingredient_cost_per_unit": resolve_result.ingredient_cost_per_unit,
                }
                await session.commit()

            await self._publish(
                quote_id_str,
                SSEEvent(
                    event="item_completed",
                    data={
                        "quote_id": quote_id_str,
                        "item_name": item_name,
                        "data": {
                            "ingredient_cost_per_unit": (
                                resolve_result.ingredient_cost_per_unit
                            ),
                        },
                        "timestamp": _now_iso(),
                    },
                ),
            )

            # Build line item from resolve result
            return {
                "item_name": item_name,
                "category": category,
                "ingredients": [
                    {
                        "name": m.name,
                        "quantity": _find_quantity(ingredients, m.name),
                        "unit_cost": m.unit_cost,
                        "source": m.source,
                        "source_item_id": m.source_item_id,
                    }
                    for m in resolve_result.matches
                ],
                "ingredient_cost_per_unit": resolve_result.ingredient_cost_per_unit,
            }

        except Exception as exc:
            logger.error("Failed processing item %s: %s", item_name, exc)
            async with self._session_factory() as session:
                mi = await session.get(MenuItem, item_id)
                if mi is not None:
                    mi.status = "failed"
                    mi.error = str(exc)
                    await session.commit()
            await self._publish(
                quote_id_str,
                SSEEvent(
                    event="item_failed",
                    data={
                        "quote_id": quote_id_str,
                        "item_name": item_name,
                        "error": str(exc),
                        "timestamp": _now_iso(),
                    },
                ),
            )
            return None

    async def _publish(self, quote_id: str, event: SSEEvent) -> None:
        """Publish an event to the event bus if one is configured."""
        if self._event_bus is not None:
            await self._event_bus.publish(quote_id, event)

    async def _get_item_description(self, menu_item_id: uuid.UUID) -> str:
        """Fetch the item description from the quote's menu_spec."""
        async with self._session_factory() as session:
            mi = await session.get(MenuItem, menu_item_id)
            if mi is None:
                return ""
            quote = await session.get(Quote, mi.quote_id)
            if quote is None or quote.menu_spec is None:
                return ""

            categories = quote.menu_spec.get("categories", {})
            for items in categories.values():
                for item in items:
                    if item.get("name") == mi.item_name:
                        return item.get("description", "")
            return ""

    def _line_item_from_step_data(self, menu_item: MenuItem) -> dict | None:
        """Build a line item dict from a completed menu item's step_data."""
        if menu_item.step_data is None:
            return None

        matches = menu_item.step_data.get("matches", [])
        cost = menu_item.step_data.get("ingredient_cost_per_unit", 0.0)

        return {
            "item_name": menu_item.item_name,
            "category": menu_item.category,
            "ingredients": [
                {
                    "name": m.get("name", ""),
                    "quantity": m.get("quantity", ""),
                    "unit_cost": m.get("unit_cost"),
                    "source": m.get("source", ""),
                    "source_item_id": m.get("source_item_id"),
                }
                for m in matches
            ],
            "ingredient_cost_per_unit": cost,
        }

    def _assemble_quote(self, quote: Quote, completed_items: list[dict]) -> dict:
        """Assemble the quote dict conforming to quote_schema.json structure."""
        return {
            "quote_id": str(uuid.uuid4()),
            "event": quote.event,
            "date": quote.date,
            "venue": quote.venue,
            "generated_at": datetime.now(UTC).isoformat(),
            "line_items": completed_items,
        }


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _find_quantity(ingredients: list[Ingredient], name: str) -> str:
    """Return the serving quantity for a named ingredient (case-insensitive)."""
    name_lower = name.lower().strip()
    for ing in ingredients:
        if ing.name.lower().strip() == name_lower:
            return ing.quantity
    return ""


def _now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(UTC).isoformat()
