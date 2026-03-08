"""Tests for SSE streaming endpoint: GET /jobs/{id}/stream."""

import asyncio
import json
import os
import uuid
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from yes_chef.db.models import Base

TEST_DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/yeschef_test",
)

# ---------------------------------------------------------------------------
# DB fixtures (session-scoped engine, function-scoped sessions)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def sse_engine():
    eng = create_async_engine(TEST_DB_URL, echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture(loop_scope="session", scope="session")
def sse_session_factory(sse_engine):
    return async_sessionmaker(sse_engine, class_=AsyncSession, expire_on_commit=False)


# ---------------------------------------------------------------------------
# App fixture — creates app with mocked orchestrator + real DB
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_orchestrator():
    orch = MagicMock()
    orch.submit_job = AsyncMock(return_value=uuid.uuid4())
    orch.process_job = AsyncMock()
    return orch


def _make_app_with_state(orchestrator, session_factory, event_bus=None):
    """Create an app and pre-populate app.state (bypassing lifespan)."""
    from yes_chef.api.app import create_app
    from yes_chef.events import EventBus

    app = create_app(
        orchestrator=orchestrator,
        session_factory=session_factory,
        event_bus=event_bus,
    )
    # Pre-populate state so endpoints work without lifespan
    app.state.session_factory = session_factory
    app.state.orchestrator = orchestrator
    app.state.event_bus = event_bus if event_bus is not None else EventBus()
    return app


# ---------------------------------------------------------------------------
# test_sse_stream_content_type
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sse_stream_content_type(sse_session_factory, mock_orchestrator):
    """GET /jobs/{id}/stream for existing job → 200, Content-Type: text/event-stream."""
    from yes_chef.db.models import Job
    from yes_chef.events import EventBus

    # Create a real job in the DB
    async with sse_session_factory() as session:
        job = Job(
            event="Test Event",
            status="processing",
            menu_spec={},
        )
        session.add(job)
        await session.commit()
        job_id = job.id

    event_bus = EventBus()
    app = _make_app_with_state(mock_orchestrator, sse_session_factory, event_bus)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Publish job_completed in background so stream closes
        async def close_stream():
            await asyncio.sleep(0.1)
            from yes_chef.events import SSEEvent

            await event_bus.publish(
                str(job_id),
                SSEEvent(
                    event="job_completed",
                    data={"job_id": str(job_id), "timestamp": "t"},
                ),
            )

        close_task = asyncio.create_task(close_stream())
        async with client.stream("GET", f"/jobs/{job_id}/stream") as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers["content-type"]
            # Drain the stream so it closes cleanly
            async for _ in response.aiter_lines():
                pass
        await close_task


# ---------------------------------------------------------------------------
# test_sse_stream_receives_events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sse_stream_receives_events(sse_session_factory, mock_orchestrator):
    """Publish events to event bus → SSE client receives them in correct format."""
    from yes_chef.db.models import Job
    from yes_chef.events import EventBus, SSEEvent

    # Create a real job in the DB
    async with sse_session_factory() as session:
        job = Job(
            event="Test Event",
            status="processing",
            menu_spec={},
        )
        session.add(job)
        await session.commit()
        job_id = job.id

    event_bus = EventBus()
    app = _make_app_with_state(mock_orchestrator, sse_session_factory, event_bus)

    received_events = []

    async def publish_and_complete():
        # Give the client time to connect and subscribe
        await asyncio.sleep(0.1)
        await event_bus.publish(
            str(job_id),
            SSEEvent(
                event="item_step_change",
                data={
                    "job_id": str(job_id),
                    "item_name": "Test Item",
                    "status": "decomposing",
                    "timestamp": "2025-01-01T00:00:00+00:00",
                },
            ),
        )
        await asyncio.sleep(0.05)
        await event_bus.publish(
            str(job_id),
            SSEEvent(
                event="job_completed",
                data={
                    "job_id": str(job_id),
                    "timestamp": "2025-01-01T00:00:01+00:00",
                },
            ),
        )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Start publishing in the background
        publish_task = asyncio.create_task(publish_and_complete())
        async with client.stream("GET", f"/jobs/{job_id}/stream") as response:
            assert response.status_code == 200
            async for line in response.aiter_lines():
                if line.startswith("event:"):
                    received_events.append({"event": line[len("event:") :].strip()})
                elif line.startswith("data:") and received_events:
                    received_events[-1]["data"] = json.loads(
                        line[len("data:") :].strip()
                    )
        await publish_task

    # Should have received at least the item_step_change and job_completed events
    event_types = [e["event"] for e in received_events]
    assert "item_step_change" in event_types
    assert "job_completed" in event_types

    # Verify item_step_change event has correct data
    step_event = next(e for e in received_events if e["event"] == "item_step_change")
    assert step_event["data"]["job_id"] == str(job_id)
    assert step_event["data"]["item_name"] == "Test Item"
    assert step_event["data"]["status"] == "decomposing"


# ---------------------------------------------------------------------------
# test_sse_job_not_found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sse_job_not_found(sse_session_factory, mock_orchestrator):
    """GET /jobs/{random_uuid}/stream → 404."""
    random_id = uuid.uuid4()
    app = _make_app_with_state(mock_orchestrator, sse_session_factory)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/jobs/{random_id}/stream")
        assert response.status_code == 404
