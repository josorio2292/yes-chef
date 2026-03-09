"""Yes Chef FastAPI application.

Exposes:
  - GET  /health
  - POST /quotes                   → 201
  - GET  /quotes/{quote_id}        → 200 | 404
  - GET  /quotes/{quote_id}/result → 200 | 404 | 409
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


class QuoteSubmitRequest(BaseModel):
    event: str
    date: str | None = None
    venue: str | None = None
    guest_count_estimate: int | None = None
    notes: str | None = None
    categories: dict  # category name → list of item dicts


class QuoteSubmitResponse(BaseModel):
    quote_id: str
    status: str


class QuoteStatusResponse(BaseModel):
    quote_id: str
    status: str
    total_items: int
    completed_items: int
    failed_items: int
    items: list[dict]


class QuoteSummary(BaseModel):
    quote_id: str
    event: str
    date: str | None
    venue: str | None
    guest_count_estimate: int | None
    status: str
    total_items: int
    completed_items: int
    failed_items: int
    created_at: str


# ---------------------------------------------------------------------------
# Internal DB helpers (module-level so tests can patch them)
# ---------------------------------------------------------------------------


async def _get_all_quotes(
    session_factory: async_sessionmaker,
) -> list[Any]:
    """Return all Quotes with their menu_items, ordered by created_at descending."""
    from sqlalchemy.orm import selectinload

    from yes_chef.db.models import Quote

    async with session_factory() as session:
        result = await session.execute(
            select(Quote)
            .options(selectinload(Quote.menu_items))
            .order_by(Quote.created_at.desc())
        )
        return list(result.scalars().all())


async def _get_quote_with_menu_items(
    quote_id: uuid.UUID,
    session_factory: async_sessionmaker,
) -> tuple[Any | None, list[Any]]:
    """Return (Quote, [MenuItem]) or (None, []) if not found."""
    from yes_chef.db.models import MenuItem, Quote

    async with session_factory() as session:
        quote = await session.get(Quote, quote_id)
        if quote is None:
            return None, []
        result = await session.execute(
            select(MenuItem).where(MenuItem.quote_id == quote_id)
        )
        items = result.scalars().all()
        return quote, list(items)


async def _get_quote_by_id(
    quote_id: uuid.UUID,
    session_factory: async_sessionmaker,
) -> Any | None:
    """Return Quote or None."""
    from yes_chef.db.models import Quote

    async with session_factory() as session:
        return await session.get(Quote, quote_id)


async def _get_stalled_quotes(
    session_factory: async_sessionmaker,
) -> list[Any]:
    """Return all Quotes whose status is 'processing' (stalled mid-pipeline)."""
    from yes_chef.db.models import Quote

    async with session_factory() as session:
        result = await session.execute(
            select(Quote).where(Quote.status == "processing")
        )
        return list(result.scalars().all())


def _build_quote_from_quote(quote: Any, menu_items: list[Any] | None = None) -> dict:
    """Assemble a quote dict from a completed Quote's menu items."""
    import datetime

    items = menu_items or []
    line_items = []

    for mi in items:
        if mi.status != "completed" or mi.step_data is None:
            continue
        matches = mi.step_data.get("matches", [])
        cost = mi.step_data.get("ingredient_cost_per_unit", 0.0)
        line_items.append(
            {
                "item_name": mi.item_name,
                "category": mi.category,
                "ingredients": [
                    {
                        "name": m.get("name", ""),
                        "quantity": m.get("quantity", ""),
                        "unit_cost": m.get("unit_cost"),
                        "source": m.get("source", "not_available"),
                        "source_item_id": m.get("source_item_id"),
                    }
                    for m in matches
                ],
                "ingredient_cost_per_unit": cost,
            }
        )

    return {
        "quote_id": str(uuid.uuid4()),
        "event": quote.event,
        "date": getattr(quote, "date", None),
        "venue": getattr(quote, "venue", None),
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
                    "No embeddings found in DB — running ingest() for first run…"
                )
                await catalog.ingest("sysco")
            else:
                logger.info("Embeddings already in DB — skipping ingest().")
            application.state.orchestrator = Orchestrator(
                session_factory=application.state.session_factory,
                catalog_service=catalog,
                event_bus=application.state.event_bus,
            )

        # Resume any quotes that were mid-processing when the server last stopped
        try:
            stalled = await _get_stalled_quotes(application.state.session_factory)
        except Exception:
            logger.exception(
                "Failed to query stalled quotes during startup — skipping recovery"
            )
            stalled = []
        for quote in stalled:
            logger.info("Resuming stalled quote %s", quote.id)
            asyncio.create_task(
                _run_processing(application.state.orchestrator, quote.id)
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
    # GET /quotes  → 200
    # ------------------------------------------------------------------

    @app.get("/quotes", response_model=list[QuoteSummary])
    async def list_quotes() -> list[QuoteSummary]:
        sf = app.state.session_factory
        quotes = await _get_all_quotes(sf)
        summaries = []
        for q in quotes:
            items = q.menu_items
            total = len(items)
            completed = sum(1 for mi in items if mi.status == "completed")
            failed = sum(1 for mi in items if mi.status == "failed")
            summaries.append(
                QuoteSummary(
                    quote_id=str(q.id),
                    event=q.event,
                    date=q.date,
                    venue=q.venue,
                    guest_count_estimate=q.guest_count_estimate,
                    status=q.status,
                    total_items=total,
                    completed_items=completed,
                    failed_items=failed,
                    created_at=q.created_at.isoformat(),
                )
            )
        return summaries

    # ------------------------------------------------------------------
    # POST /quotes  → 201
    # ------------------------------------------------------------------

    @app.post("/quotes", status_code=201, response_model=QuoteSubmitResponse)
    async def submit_quote(request: QuoteSubmitRequest) -> QuoteSubmitResponse:
        orch = app.state.orchestrator
        menu_spec = request.model_dump(exclude_none=False)
        quote_id = await orch.submit_quote(menu_spec)

        # Fire-and-forget background processing
        asyncio.create_task(_run_processing(orch, quote_id))

        return QuoteSubmitResponse(quote_id=str(quote_id), status="pending")

    # ------------------------------------------------------------------
    # GET /quotes/{quote_id}  → 200 | 404
    # ------------------------------------------------------------------

    @app.get("/quotes/{quote_id}", response_model=QuoteStatusResponse)
    async def get_quote_status(quote_id: uuid.UUID) -> QuoteStatusResponse:
        sf = app.state.session_factory
        quote, items = await _get_quote_with_menu_items(quote_id, sf)
        if quote is None:
            raise HTTPException(status_code=404, detail="Quote not found")

        total = len(items)
        completed = sum(1 for mi in items if mi.status == "completed")
        failed = sum(1 for mi in items if mi.status == "failed")

        item_summaries = [
            {
                "item_name": mi.item_name,
                "step": mi.status,
                "status": mi.status,
            }
            for mi in items
        ]

        return QuoteStatusResponse(
            quote_id=str(quote.id),
            status=quote.status,
            total_items=total,
            completed_items=completed,
            failed_items=failed,
            items=item_summaries,
        )

    # ------------------------------------------------------------------
    # GET /quotes/{quote_id}/result  → 200 | 404 | 409
    # ------------------------------------------------------------------

    @app.get("/quotes/{quote_id}/result")
    async def get_result(quote_id: uuid.UUID) -> dict:
        sf = app.state.session_factory
        quote = await _get_quote_by_id(quote_id, sf)
        if quote is None:
            raise HTTPException(status_code=404, detail="Quote not found")

        if quote.status not in ("completed", "completed_with_errors"):
            raise HTTPException(
                status_code=409,
                detail=f"Quote not ready — quote status is '{quote.status}'",
            )

        _, items = await _get_quote_with_menu_items(quote_id, sf)
        result = _build_quote_from_quote(quote, items)
        return result

    # ------------------------------------------------------------------
    # GET /quotes/{quote_id}/stream  → 200 text/event-stream | 404
    # ------------------------------------------------------------------

    @app.get("/quotes/{quote_id}/stream")
    async def stream_events(quote_id: uuid.UUID) -> StreamingResponse:
        sf = app.state.session_factory
        quote = await _get_quote_by_id(quote_id, sf)
        if quote is None:
            raise HTTPException(status_code=404, detail="Quote not found")

        bus: EventBus = app.state.event_bus
        queue = bus.subscribe(str(quote_id))

        async def event_generator():
            # Send a connection confirmation event first
            yield f"event: connected\ndata: {json.dumps({'quote_id': str(quote_id)})}\n\n"
            try:
                while True:
                    event = await queue.get()
                    yield f"event: {event.event}\ndata: {json.dumps(event.data)}\n\n"
                    if event.event == "quote_completed":
                        break
            finally:
                bus.unsubscribe(str(quote_id), queue)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
        )

    return app


# ---------------------------------------------------------------------------
# Background processing task
# ---------------------------------------------------------------------------


async def _run_processing(orchestrator: Any, quote_id: uuid.UUID) -> None:
    """Run the orchestrator pipeline in the background."""
    try:
        await orchestrator.process_quote(quote_id)
    except Exception:
        logger.exception("Background processing failed for quote %s", quote_id)


# ---------------------------------------------------------------------------
# Production app instance (for uvicorn)
# ---------------------------------------------------------------------------

app = create_app()
