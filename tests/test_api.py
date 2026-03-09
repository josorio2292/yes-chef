"""Tests for the FastAPI endpoints: health, submit, status, result."""

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

COMPLETED_QUOTE = {
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


def _make_mock_quote(
    quote_id: uuid.UUID,
    status: str = "pending",
    menu_items: list[Any] | None = None,
    quote: dict | None = None,
) -> MagicMock:
    """Return a mock Quote with the given attributes."""
    mock_quote = MagicMock()
    mock_quote.id = quote_id
    mock_quote.status = status
    mock_quote._quote = quote
    mock_quote.menu_items = menu_items or []
    return mock_quote


def _make_mock_menu_item(
    item_name: str = "Eggs Benedict Bites",
    status: str = "pending",
    category: str = "appetizers",
) -> MagicMock:
    mi = MagicMock()
    mi.item_name = item_name
    mi.status = status
    mi.category = category
    mi.step_data = None
    mi.error = None
    return mi


# ---------------------------------------------------------------------------
# Fixture: TestClient with a fully mocked orchestrator
# ---------------------------------------------------------------------------


@pytest.fixture()
def client():
    """Return a TestClient whose orchestrator is fully mocked."""
    from yes_chef.api.app import create_app

    mock_orch = MagicMock()
    mock_orch.submit_quote = AsyncMock()
    mock_orch.process_quote = AsyncMock()

    app = create_app(orchestrator=mock_orch)
    with TestClient(app, raise_server_exceptions=True) as c:
        c._mock_orch = mock_orch  # expose for per-test configuration
        yield c


# ---------------------------------------------------------------------------
# Fixture: client with a mocked DB session for status/result endpoints
# ---------------------------------------------------------------------------


@pytest.fixture()
def client_with_db():
    """Return a TestClient with orchestrator + a patchable DB session."""
    from yes_chef.api.app import create_app

    mock_orch = MagicMock()
    mock_orch.submit_quote = AsyncMock()
    mock_orch.process_quote = AsyncMock()

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
# test_submit_quote
# ---------------------------------------------------------------------------


def test_submit_quote(client):
    quote_id = uuid.uuid4()
    client._mock_orch.submit_quote.return_value = quote_id

    response = client.post("/quotes", json=VALID_MENU_SPEC)

    assert response.status_code == 201
    body = response.json()
    assert "quote_id" in body
    assert "status" in body
    assert body["status"] == "pending"
    # quote_id should be a valid UUID string
    assert uuid.UUID(body["quote_id"]) == quote_id


# ---------------------------------------------------------------------------
# test_submit_quote_invalid_body
# ---------------------------------------------------------------------------


def test_submit_quote_invalid_body(client):
    """Empty body (missing required fields) should return 422."""
    response = client.post("/quotes", json={})
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# test_get_quote_status
# ---------------------------------------------------------------------------


def test_get_quote_status(client):
    """POST /quotes, then GET /quotes/{id} → 200 with status + items."""
    quote_id = uuid.uuid4()
    client._mock_orch.submit_quote.return_value = quote_id

    mi = _make_mock_menu_item(status="pending")

    with patch("yes_chef.api.app._get_quote_with_menu_items") as mock_getter:
        mock_getter.return_value = (
            _make_mock_quote(quote_id, status="pending", menu_items=[mi]),
            [mi],
        )
        response = client.get(f"/quotes/{quote_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["quote_id"] == str(quote_id)
    assert "status" in body
    assert "items" in body
    assert isinstance(body["items"], list)
    assert "total_items" in body
    assert "completed_items" in body
    assert "failed_items" in body


# ---------------------------------------------------------------------------
# test_get_quote_not_found
# ---------------------------------------------------------------------------


def test_get_quote_not_found(client):
    """GET /quotes/{random_uuid} → 404."""
    random_id = uuid.uuid4()

    with patch("yes_chef.api.app._get_quote_with_menu_items") as mock_getter:
        mock_getter.return_value = (None, [])
        response = client.get(f"/quotes/{random_id}")

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# test_get_result_not_ready
# ---------------------------------------------------------------------------


def test_get_result_not_ready(client):
    """GET /quotes/{id}/result when quote is still pending → 409."""
    quote_id = uuid.uuid4()

    with patch("yes_chef.api.app._get_quote_by_id") as mock_getter:
        mock_getter.return_value = _make_mock_quote(quote_id, status="pending")
        response = client.get(f"/quotes/{quote_id}/result")

    assert response.status_code == 409


# ---------------------------------------------------------------------------
# test_get_result_after_completion
# ---------------------------------------------------------------------------


def test_get_result_after_completion(client):
    """GET /quotes/{id}/result when quote is completed → 200 with quote structure."""
    quote_id = uuid.uuid4()

    mi = _make_mock_menu_item(status="completed")
    mi.step_data = {
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

    mock_quote = _make_mock_quote(quote_id, status="completed", quote=COMPLETED_QUOTE)
    mock_quote.menu_spec = VALID_MENU_SPEC

    with patch("yes_chef.api.app._get_quote_by_id") as mock_getter:
        mock_getter.return_value = mock_quote
        with patch("yes_chef.api.app._get_quote_with_menu_items") as mock_items_getter:
            mock_items_getter.return_value = (mock_quote, [mi])
            with patch(
                "yes_chef.api.app._build_quote_from_quote"
            ) as mock_quote_builder:
                mock_quote_builder.return_value = COMPLETED_QUOTE
                response = client.get(f"/quotes/{quote_id}/result")

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
