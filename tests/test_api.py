"""Tests for the FastAPI endpoints: health, submit, status, quote."""

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Minimal menu spec used across tests
# ---------------------------------------------------------------------------

VALID_MENU_SPEC = {
    "event": "Corporate Gala Dinner",
    "date": "2025-03-15",
    "venue": "Grand Ballroom",
    "guest_count_estimate": 150,
    "categories": {
        "appetizers": [
            {
                "name": "Eggs Benedict Bites",
                "description": "Miniature eggs Benedict on toasted brioche rounds",
                "service_style": "passed",
            }
        ]
    },
}

COMPLETED_JOB_QUOTE = {
    "quote_id": str(uuid.uuid4()),
    "event": "Corporate Gala Dinner",
    "date": "2025-03-15",
    "venue": "Grand Ballroom",
    "generated_at": "2025-03-15T00:00:00+00:00",
    "line_items": [
        {
            "item_name": "Eggs Benedict Bites",
            "category": "appetizers",
            "ingredients": [
                {
                    "name": "eggs",
                    "quantity": "2 each",
                    "unit_cost": 0.50,
                    "source": "sysco_catalog",
                    "source_item_id": "12345",
                }
            ],
            "ingredient_cost_per_unit": 0.50,
        }
    ],
}


# ---------------------------------------------------------------------------
# Mock DB helpers
# ---------------------------------------------------------------------------


def _make_mock_job(
    job_id: uuid.UUID,
    status: str = "pending",
    work_items: list[Any] | None = None,
    quote: dict | None = None,
) -> MagicMock:
    """Return a mock Job with the given attributes."""
    job = MagicMock()
    job.id = job_id
    job.status = status
    # Store quote in step_data-style slot for convenience
    job._quote = quote
    job.work_items = work_items or []
    return job


def _make_mock_work_item(
    item_name: str = "Eggs Benedict Bites",
    step: str = "pending",
    status: str = "pending",
    category: str = "appetizers",
) -> MagicMock:
    wi = MagicMock()
    wi.item_name = item_name
    wi.status = status
    wi.category = category
    wi.step_data = None
    wi.error = None
    return wi


# ---------------------------------------------------------------------------
# Fixture: TestClient with a fully mocked orchestrator
# ---------------------------------------------------------------------------


@pytest.fixture()
def client():
    """Return a TestClient whose orchestrator is fully mocked."""
    from yes_chef.api.app import create_app

    mock_orch = MagicMock()
    mock_orch.submit_job = AsyncMock()
    mock_orch.process_job = AsyncMock()

    app = create_app(orchestrator=mock_orch)
    with TestClient(app, raise_server_exceptions=True) as c:
        c._mock_orch = mock_orch  # expose for per-test configuration
        yield c


# ---------------------------------------------------------------------------
# Fixture: client with a mocked DB session for status/quote endpoints
# ---------------------------------------------------------------------------


@pytest.fixture()
def client_with_db():
    """Return a TestClient with orchestrator + a patchable DB session."""
    from yes_chef.api.app import create_app

    mock_orch = MagicMock()
    mock_orch.submit_job = AsyncMock()
    mock_orch.process_job = AsyncMock()

    app = create_app(orchestrator=mock_orch)
    with TestClient(app, raise_server_exceptions=True) as c:
        c._mock_orch = mock_orch
        yield c


# ---------------------------------------------------------------------------
# test_health_endpoint
# ---------------------------------------------------------------------------


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert "status" in body


# ---------------------------------------------------------------------------
# test_submit_job
# ---------------------------------------------------------------------------


def test_submit_job(client):
    job_id = uuid.uuid4()
    client._mock_orch.submit_job.return_value = job_id

    response = client.post("/jobs", json=VALID_MENU_SPEC)

    assert response.status_code == 201
    body = response.json()
    assert "job_id" in body
    assert "status" in body
    assert body["status"] == "pending"
    # job_id should be a valid UUID string
    assert uuid.UUID(body["job_id"]) == job_id


# ---------------------------------------------------------------------------
# test_submit_job_invalid_body
# ---------------------------------------------------------------------------


def test_submit_job_invalid_body(client):
    """Empty body (missing required fields) should return 422."""
    response = client.post("/jobs", json={})
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# test_get_job_status
# ---------------------------------------------------------------------------


def test_get_job_status(client):
    """POST /jobs, then GET /jobs/{id} → 200 with status + items."""
    job_id = uuid.uuid4()
    client._mock_orch.submit_job.return_value = job_id

    wi = _make_mock_work_item(status="pending")

    with patch("yes_chef.api.app._get_job_with_items") as mock_getter:
        mock_getter.return_value = (
            _make_mock_job(job_id, status="pending", work_items=[wi]),
            [wi],
        )
        response = client.get(f"/jobs/{job_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == str(job_id)
    assert "status" in body
    assert "items" in body
    assert isinstance(body["items"], list)
    assert "total_items" in body
    assert "completed_items" in body
    assert "failed_items" in body


# ---------------------------------------------------------------------------
# test_get_job_not_found
# ---------------------------------------------------------------------------


def test_get_job_not_found(client):
    """GET /jobs/{random_uuid} → 404."""
    random_id = uuid.uuid4()

    with patch("yes_chef.api.app._get_job_with_items") as mock_getter:
        mock_getter.return_value = (None, [])
        response = client.get(f"/jobs/{random_id}")

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# test_get_quote_not_ready
# ---------------------------------------------------------------------------


def test_get_quote_not_ready(client):
    """GET /jobs/{id}/quote when job is still pending → 409."""
    job_id = uuid.uuid4()

    with patch("yes_chef.api.app._get_job_by_id") as mock_getter:
        mock_getter.return_value = _make_mock_job(job_id, status="pending")
        response = client.get(f"/jobs/{job_id}/quote")

    assert response.status_code == 409


# ---------------------------------------------------------------------------
# test_get_quote_after_completion
# ---------------------------------------------------------------------------


def test_get_quote_after_completion(client):
    """GET /jobs/{id}/quote when job is completed → 200 with quote structure."""
    job_id = uuid.uuid4()

    wi = _make_mock_work_item(status="completed")
    wi.step_data = {
        "matches": [
            {
                "name": "eggs",
                "quantity": "2 each",
                "unit_cost": 0.50,
                "source": "sysco_catalog",
                "source_item_id": "12345",
            }
        ],
        "ingredient_cost_per_unit": 0.50,
    }

    mock_job = _make_mock_job(job_id, status="completed", quote=COMPLETED_JOB_QUOTE)
    mock_job.menu_spec = VALID_MENU_SPEC

    with patch("yes_chef.api.app._get_job_by_id") as mock_getter:
        mock_getter.return_value = mock_job
        with patch("yes_chef.api.app._get_job_with_items") as mock_items_getter:
            mock_items_getter.return_value = (mock_job, [wi])
            with patch("yes_chef.api.app._build_quote_from_job") as mock_quote_builder:
                mock_quote_builder.return_value = COMPLETED_JOB_QUOTE
                response = client.get(f"/jobs/{job_id}/quote")

    assert response.status_code == 200
    body = response.json()
    assert "quote_id" in body
    assert "event" in body
    assert "line_items" in body
    assert isinstance(body["line_items"], list)
    assert len(body["line_items"]) == 1

    line_item = body["line_items"][0]
    assert "item_name" in line_item
    assert "category" in line_item
    assert "ingredients" in line_item
    assert "ingredient_cost_per_unit" in line_item
