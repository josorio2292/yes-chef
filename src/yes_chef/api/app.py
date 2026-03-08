"""Yes Chef FastAPI application.

Exposes:
  - GET  /health
  - POST /jobs                 → 201
  - GET  /jobs/{job_id}        → 200 | 404
  - GET  /jobs/{job_id}/quote  → 200 | 404 | 409
"""

import asyncio
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from yes_chef.events import EventBus

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pydantic request / response models
# ---------------------------------------------------------------------------


class JobSubmitRequest(BaseModel):
    event: str
    date: str | None = None
    venue: str | None = None
    guest_count_estimate: int | None = None
    notes: str | None = None
    categories: dict  # category name → list of item dicts


class JobSubmitResponse(BaseModel):
    job_id: str
    status: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    total_items: int
    completed_items: int
    failed_items: int
    items: list[dict]


# ---------------------------------------------------------------------------
# Internal DB helpers (module-level so tests can patch them)
# ---------------------------------------------------------------------------


async def _get_job_with_items(
    job_id: uuid.UUID,
    session_factory: async_sessionmaker,
) -> tuple[Any | None, list[Any]]:
    """Return (Job, [WorkItem]) or (None, []) if not found."""
    from yes_chef.db.models import Job, WorkItem

    async with session_factory() as session:
        job = await session.get(Job, job_id)
        if job is None:
            return None, []
        result = await session.execute(
            select(WorkItem).where(WorkItem.job_id == job_id)
        )
        items = result.scalars().all()
        return job, list(items)


async def _get_job_by_id(
    job_id: uuid.UUID,
    session_factory: async_sessionmaker,
) -> Any | None:
    """Return Job or None."""
    from yes_chef.db.models import Job

    async with session_factory() as session:
        return await session.get(Job, job_id)


def _build_quote_from_job(job: Any, work_items: list[Any] | None = None) -> dict:
    """Assemble a quote dict from a completed Job's work items."""
    import datetime

    items = work_items or []
    line_items = []

    for wi in items:
        if wi.status != "completed" or wi.step_data is None:
            continue
        matches = wi.step_data.get("matches", [])
        cost = wi.step_data.get("ingredient_cost_per_unit", 0.0)
        line_items.append(
            {
                "item_name": wi.item_name,
                "category": wi.category,
                "ingredients": [
                    {
                        "name": m.get("name", ""),
                        "quantity": m.get("quantity", ""),
                        "unit_cost": m.get("unit_cost"),
                        "source": m.get("source", "not_available"),
                        "sysco_item_number": m.get("sysco_item_number"),
                    }
                    for m in matches
                ],
                "ingredient_cost_per_unit": cost,
            }
        )

    return {
        "quote_id": str(uuid.uuid4()),
        "event": job.event,
        "date": getattr(job, "date", None),
        "venue": getattr(job, "venue", None),
        "generated_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "line_items": line_items,
    }


# ---------------------------------------------------------------------------
# App factory (accepts an injectable orchestrator for testing)
# ---------------------------------------------------------------------------


def create_app(
    orchestrator: Any | None = None,
    session_factory: async_sessionmaker | None = None,
    event_bus: EventBus | None = None,
) -> FastAPI:
    """Create and return the FastAPI application.

    Parameters
    ----------
    orchestrator:
        An :class:`~yes_chef.orchestrator.engine.Orchestrator` instance.
        If *None*, the default production orchestrator is built at startup.
    session_factory:
        An async session factory.  If *None*, the production factory from
        ``yes_chef.db.engine`` is used.
    event_bus:
        An :class:`~yes_chef.events.EventBus` instance for SSE streaming.
        If *None*, a new one is created at startup.
    """

    @asynccontextmanager
    async def _lifespan(application: FastAPI):  # noqa: RUF029
        # Startup: resolve defaults
        if session_factory is not None:
            application.state.session_factory = session_factory
        else:
            from yes_chef.db.engine import async_session_factory as default_factory

            application.state.session_factory = default_factory

        # Set up event bus
        application.state.event_bus = event_bus if event_bus is not None else EventBus()

        if orchestrator is not None:
            application.state.orchestrator = orchestrator
        else:
            from yes_chef.catalog.provider import SyscoCsvProvider
            from yes_chef.catalog.service import CatalogService
            from yes_chef.orchestrator.engine import Orchestrator

            csv_path = os.environ.get("SYSCO_CSV_PATH", "data/sysco_catalog.csv")
            sysco = SyscoCsvProvider(csv_path)
            sysco.load_catalog()
            catalog = CatalogService(
                providers={"sysco": sysco},
                session_factory=application.state.session_factory,
            )
            if not await catalog.has_embeddings():
                logger.info(
                    "No embeddings found in DB — running embed_catalog() for first run…"
                )
                await catalog.embed_catalog()
            else:
                logger.info("Embeddings already in DB — skipping embed_catalog().")
            application.state.orchestrator = Orchestrator(
                session_factory=application.state.session_factory,
                catalog_service=catalog,
                event_bus=application.state.event_bus,
            )
        yield
        # Shutdown: nothing to clean up

    app = FastAPI(title="Yes Chef", version="0.1.0", lifespan=_lifespan)

    # ------------------------------------------------------------------
    # GET /health
    # ------------------------------------------------------------------

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    # ------------------------------------------------------------------
    # POST /jobs  → 201
    # ------------------------------------------------------------------

    @app.post("/jobs", status_code=201, response_model=JobSubmitResponse)
    async def submit_job(request: JobSubmitRequest) -> JobSubmitResponse:
        orch = app.state.orchestrator
        menu_spec = request.model_dump(exclude_none=False)
        job_id = await orch.submit_job(menu_spec)

        # Fire-and-forget background processing
        asyncio.create_task(_run_processing(orch, job_id))

        return JobSubmitResponse(job_id=str(job_id), status="pending")

    # ------------------------------------------------------------------
    # GET /jobs/{job_id}  → 200 | 404
    # ------------------------------------------------------------------

    @app.get("/jobs/{job_id}", response_model=JobStatusResponse)
    async def get_job_status(job_id: uuid.UUID) -> JobStatusResponse:
        sf = app.state.session_factory
        job, items = await _get_job_with_items(job_id, sf)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")

        total = len(items)
        completed = sum(1 for wi in items if wi.status == "completed")
        failed = sum(1 for wi in items if wi.status == "failed")

        item_summaries = [
            {
                "item_name": wi.item_name,
                "step": wi.status,
                "status": wi.status,
            }
            for wi in items
        ]

        return JobStatusResponse(
            job_id=str(job.id),
            status=job.status,
            total_items=total,
            completed_items=completed,
            failed_items=failed,
            items=item_summaries,
        )

    # ------------------------------------------------------------------
    # GET /jobs/{job_id}/quote  → 200 | 404 | 409
    # ------------------------------------------------------------------

    @app.get("/jobs/{job_id}/quote")
    async def get_quote(job_id: uuid.UUID) -> dict:
        sf = app.state.session_factory
        job = await _get_job_by_id(job_id, sf)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")

        if job.status not in ("completed", "completed_with_errors"):
            raise HTTPException(
                status_code=409,
                detail=f"Quote not ready — job status is '{job.status}'",
            )

        _, items = await _get_job_with_items(job_id, sf)
        quote = _build_quote_from_job(job, items)
        return quote

    # ------------------------------------------------------------------
    # GET /jobs/{job_id}/stream  → 200 text/event-stream | 404
    # ------------------------------------------------------------------

    @app.get("/jobs/{job_id}/stream")
    async def stream_events(job_id: uuid.UUID) -> StreamingResponse:
        sf = app.state.session_factory
        job = await _get_job_by_id(job_id, sf)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")

        bus: EventBus = app.state.event_bus
        queue = bus.subscribe(str(job_id))

        async def event_generator():
            # Send a connection confirmation event first
            yield f"event: connected\ndata: {json.dumps({'job_id': str(job_id)})}\n\n"
            try:
                while True:
                    event = await queue.get()
                    yield f"event: {event.event}\ndata: {json.dumps(event.data)}\n\n"
                    if event.event == "job_completed":
                        break
            finally:
                bus.unsubscribe(str(job_id), queue)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
        )

    return app


# ---------------------------------------------------------------------------
# Background processing task
# ---------------------------------------------------------------------------


async def _run_processing(orchestrator: Any, job_id: uuid.UUID) -> None:
    """Run the orchestrator pipeline in the background."""
    try:
        await orchestrator.process_job(job_id)
    except Exception:
        logger.exception("Background processing failed for job %s", job_id)


# ---------------------------------------------------------------------------
# Production app instance (for uvicorn)
# ---------------------------------------------------------------------------

app = create_app()
