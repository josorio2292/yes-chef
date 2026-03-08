"""Tests for the Sysco CSV catalog provider."""

import csv
import logging
import pathlib
import tempfile

import pytest

from yes_chef.catalog.provider import (
    CatalogItem,
    CatalogProvider,
    ItemNotFoundError,
    PriceResult,
    SyscoCsvProvider,
)

DATA_DIR = pathlib.Path(__file__).parent.parent / "data"
SYSCO_CSV = DATA_DIR / "sysco_catalog.csv"


@pytest.fixture
def provider() -> SyscoCsvProvider:
    return SyscoCsvProvider(csv_path=str(SYSCO_CSV))


def test_load_catalog_returns_items(provider: SyscoCsvProvider) -> None:
    items = provider.load_catalog()
    assert len(items) > 0
    assert all(isinstance(item, CatalogItem) for item in items)


def test_load_catalog_item_count(provider: SyscoCsvProvider) -> None:
    items = provider.load_catalog()
    assert len(items) == 565


def test_load_catalog_spot_check(provider: SyscoCsvProvider) -> None:
    items = provider.load_catalog()
    by_number = {item.item_number: item for item in items}

    # First row: Sysco Item Number 2867825
    item = by_number["2867825"]
    assert "BEEF" in item.description
    assert "TENDERLOIN" in item.description
    assert item.unit_of_measure == "2/6.5 LB"
    assert abs(item.cost_per_case - 289.50) < 0.01


def test_get_price_valid_item(provider: SyscoCsvProvider) -> None:
    provider.load_catalog()
    result = provider.get_price("5614226")
    assert isinstance(result, PriceResult)
    assert abs(result.cost_per_case - 315.80) < 0.01
    assert result.unit_of_measure == "20/8 OZ"


def test_get_price_unknown_item(provider: SyscoCsvProvider) -> None:
    provider.load_catalog()
    with pytest.raises(ItemNotFoundError):
        provider.get_price("NONEXISTENT_ITEM_000")


def test_load_catalog_malformed_row(caplog: pytest.LogCaptureFixture) -> None:
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".csv",
        delete=False,
        newline="",
    ) as f:
        writer = csv.writer(f)
        # Write header
        writer.writerow(
            [
                "Contract Item #",
                "AASIS Item #",
                "Sysco Item Number",
                "Brand",
                "Product Description",
                "Unit of Measure",
                "Cost",
            ]
        )
        # Good row
        writer.writerow(
            [
                "1",
                "10167621",
                "2867825",
                "SYS SUP",
                "BEEF, TENDERLOIN",
                "2/6.5 LB",
                "$289.50",
            ]  # noqa: E501
        )
        # Malformed row — missing columns
        writer.writerow(["BAD_ROW_ONLY_ONE_COLUMN"])
        # Another good row
        writer.writerow(
            [
                "2",
                "10006556",
                "5614226",
                "SYS SUP",
                "CHICKEN BREAST",
                "20/8 OZ",
                "$315.80",
            ]  # noqa: E501
        )
        tmp_path = f.name

    prov = SyscoCsvProvider(csv_path=tmp_path)
    with caplog.at_level(logging.WARNING):
        items = prov.load_catalog()

    assert len(items) == 2
    assert any("malformed" in record.message.lower() for record in caplog.records)


def test_catalog_provider_protocol() -> None:
    """SyscoCsvProvider satisfies the CatalogProvider protocol at runtime."""
    prov = SyscoCsvProvider(csv_path=str(SYSCO_CSV))
    assert isinstance(prov, CatalogProvider)
    assert hasattr(prov, "load_catalog")
    assert hasattr(prov, "get_price")
