"""Orchestrator engine: pipeline with checkpointing, resumability, and concurrency."""

import asyncio
import logging
import uuid
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yes_chef.catalog.service import CatalogService
from yes_chef.db.models import Job, WorkItem
from yes_chef.decomposition.engine import (
    DecompositionResult,
    Ingredient,
    decompose_item,
)
from yes_chef.events import EventBus, SSEEvent
from yes_chef.resolution.engine import ResolveResult, resolve_item

logger = logging.getLogger(__name__)

# Type aliases for the injectable engine functions
DecomposeFn = Callable[
    [str, str, uuid.UUID, AsyncSession],
    Coroutine[Any, Any, DecompositionResult],
]
ResolveFn = Callable[
    [list[Ingredient], CatalogService, uuid.UUID, AsyncSession],
    Coroutine[Any, Any, ResolveResult],
]


class Orchestrator:
    """Sequential pipeline orchestrator.

    Accepts a menu spec, creates a Job + WorkItems, and processes each
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

    async def submit_job(self, menu_spec: dict) -> uuid.UUID:
        """Create a Job and one WorkItem per menu item.

        Returns the job UUID.
        """
        async with self._session_factory() as session:
            job = Job(
                event=menu_spec.get("event", ""),
                date=menu_spec.get("date"),
                venue=menu_spec.get("venue"),
                guest_count_estimate=menu_spec.get("guest_count_estimate"),
                notes=menu_spec.get("notes"),
                status="pending",
                menu_spec=menu_spec,
            )
            session.add(job)
            await session.flush()

            categories: dict = menu_spec.get("categories", {})
            for category_name, items in categories.items():
                for item in items:
                    work_item = WorkItem(
                        job_id=job.id,
                        item_name=item["name"],
                        category=category_name,
                        status="pending",
                    )
                    session.add(work_item)

            await session.commit()
            return job.id

    async def process_job(self, job_id: uuid.UUID) -> dict:
        """Process all work items for a job and return the assembled quote.

        Processing is sequential. Failed items do not block others.
        Completed/decomposed/resolving items are resumed from their last
        checkpoint.
        """
        # Mark job as processing
        async with self._session_factory() as session:
            job = await session.get(Job, job_id)
            if job is None:
                raise ValueError(f"Job {job_id} not found")
            job.status = "processing"
            await session.commit()

        # Load work items
        async with self._session_factory() as session:
            result = await session.execute(
                select(WorkItem).where(WorkItem.job_id == job_id)
            )
            work_items = result.scalars().all()

        # Separate already-completed items (skip reprocessing) from pending ones
        completed_items: list[dict] = []
        failed_count = 0

        for work_item in work_items:
            if work_item.status == "completed":
                line_item = self._line_item_from_step_data(work_item)
                if line_item is not None:
                    completed_items.append(line_item)

        pending_items = [wi for wi in work_items if wi.status != "completed"]

        # Process pending items concurrently, bounded by the semaphore
        semaphore = asyncio.Semaphore(self._max_concurrent)

        async def process_with_semaphore(work_item: WorkItem) -> dict | None:
            async with semaphore:
                return await self._process_item(work_item, job_id)

        tasks = [process_with_semaphore(wi) for wi in pending_items]
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
            job = await session.get(Job, job_id)
            quote = self._assemble_quote(job, completed_items)

            # Mark job done
            if failed_count > 0:
                job.status = "completed_with_errors"
            else:
                job.status = "completed"
            await session.commit()

        await self._publish(
            str(job_id),
            SSEEvent(
                event="job_completed",
                data={
                    "job_id": str(job_id),
                    "timestamp": _now_iso(),
                },
            ),
        )

        return quote

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _process_item(
        self, work_item: WorkItem, job_id: uuid.UUID | None = None
    ) -> dict | None:
        """Process a single WorkItem through decompose → resolve.

        Returns a line item dict on success, None on failure.
        Checkpoints status at each stage.
        """
        item_id = work_item.id
        item_name = work_item.item_name
        category = work_item.category
        job_id_str = str(job_id) if job_id is not None else str(work_item.job_id)

        try:
            # ----------------------------------------------------------------
            # Decomposition stage
            # Skipped if item is already "decomposed" or "resolving"
            # ----------------------------------------------------------------
            if work_item.status in ("pending", "decomposing"):
                logger.info("Decomposing: %s", item_name)
                # Find item description from menu spec via the job (not needed here
                # because the work item doesn't store it separately; use item_name)
                # The work item doesn't store description — we need to fetch from job
                description = await self._get_item_description(item_id)

                async with self._session_factory() as session:
                    # Mark as decomposing
                    wi = await session.get(WorkItem, item_id)
                    wi.status = "decomposing"
                    await session.commit()

                await self._publish(
                    job_id_str,
                    SSEEvent(
                        event="item_step_change",
                        data={
                            "job_id": job_id_str,
                            "item_name": item_name,
                            "status": "decomposing",
                            "timestamp": _now_iso(),
                        },
                    ),
                )

                async with self._session_factory() as session:
                    decomp_result = await self._decompose_fn(
                        item_name, description, item_id, session
                    )
                    # Checkpoint: status → "decomposed", step_data has ingredients
                    wi = await session.get(WorkItem, item_id)
                    wi.status = "decomposed"
                    wi.step_data = {
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
                    "Resuming from checkpoint '%s': %s", work_item.status, item_name
                )
                raw = (work_item.step_data or {}).get("ingredients", [])
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
                wi = await session.get(WorkItem, item_id)
                wi.status = "resolving"
                await session.commit()

            await self._publish(
                job_id_str,
                SSEEvent(
                    event="item_step_change",
                    data={
                        "job_id": job_id_str,
                        "item_name": item_name,
                        "status": "resolving",
                        "timestamp": _now_iso(),
                    },
                ),
            )

            async with self._session_factory() as session:
                resolve_result = await self._resolve_fn(
                    ingredients, self._catalog_service, item_id, session
                )
                # Checkpoint: status → "completed", step_data has matches + cost
                wi = await session.get(WorkItem, item_id)
                wi.status = "completed"
                wi.step_data = {
                    "matches": [m.model_dump() for m in resolve_result.matches],
                    "ingredient_cost_per_unit": resolve_result.ingredient_cost_per_unit,
                }
                await session.commit()

            await self._publish(
                job_id_str,
                SSEEvent(
                    event="item_completed",
                    data={
                        "job_id": job_id_str,
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
                        "sysco_item_number": m.sysco_item_number,
                    }
                    for m in resolve_result.matches
                ],
                "ingredient_cost_per_unit": resolve_result.ingredient_cost_per_unit,
            }

        except Exception as exc:
            logger.error("Failed processing item %s: %s", item_name, exc)
            async with self._session_factory() as session:
                wi = await session.get(WorkItem, item_id)
                if wi is not None:
                    wi.status = "failed"
                    wi.error = str(exc)
                    await session.commit()
            await self._publish(
                job_id_str,
                SSEEvent(
                    event="item_failed",
                    data={
                        "job_id": job_id_str,
                        "item_name": item_name,
                        "error": str(exc),
                        "timestamp": _now_iso(),
                    },
                ),
            )
            return None

    async def _publish(self, job_id: str, event: SSEEvent) -> None:
        """Publish an event to the event bus if one is configured."""
        if self._event_bus is not None:
            await self._event_bus.publish(job_id, event)

    async def _get_item_description(self, work_item_id: uuid.UUID) -> str:
        """Fetch the item description from the job's menu_spec."""
        async with self._session_factory() as session:
            wi = await session.get(WorkItem, work_item_id)
            if wi is None:
                return ""
            job = await session.get(Job, wi.job_id)
            if job is None or job.menu_spec is None:
                return ""

            categories = job.menu_spec.get("categories", {})
            for items in categories.values():
                for item in items:
                    if item.get("name") == wi.item_name:
                        return item.get("description", "")
            return ""

    def _line_item_from_step_data(self, work_item: WorkItem) -> dict | None:
        """Build a line item dict from a completed work item's step_data."""
        if work_item.step_data is None:
            return None

        matches = work_item.step_data.get("matches", [])
        cost = work_item.step_data.get("ingredient_cost_per_unit", 0.0)

        return {
            "item_name": work_item.item_name,
            "category": work_item.category,
            "ingredients": [
                {
                    "name": m.get("name", ""),
                    "quantity": m.get("quantity", ""),
                    "unit_cost": m.get("unit_cost"),
                    "source": m.get("source", ""),
                    "sysco_item_number": m.get("sysco_item_number"),
                }
                for m in matches
            ],
            "ingredient_cost_per_unit": cost,
        }

    def _assemble_quote(self, job: Job, completed_items: list[dict]) -> dict:
        """Assemble the quote dict conforming to quote_schema.json structure."""
        return {
            "quote_id": str(uuid.uuid4()),
            "event": job.event,
            "date": job.date,
            "venue": job.venue,
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
