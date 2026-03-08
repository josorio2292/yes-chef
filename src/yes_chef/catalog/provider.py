"""Sysco CSV catalog provider."""

import csv
import logging
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


class ItemNotFoundError(KeyError):
    """Raised when a source_item_id is not found in the catalog."""


@dataclass(frozen=True)
class CatalogRecord:
    source_item_id: str
    provider: str
    description: str
    unit_of_measure: str
    cost_per_case: float
    category: str | None = None
    brand: str | None = None
    source_metadata: dict | None = None


@dataclass(frozen=True)
class PriceResult:
    cost_per_case: float
    unit_of_measure: str


@runtime_checkable
class CatalogProvider(Protocol):
    @property
    def name(self) -> str: ...

    def load_catalog(self) -> list[CatalogRecord]: ...

    def get_price(self, source_item_id: str) -> PriceResult: ...


def _parse_cost(raw: str) -> float:
    """Strip leading '$' and convert to float."""
    return float(raw.strip().lstrip("$"))


class SyscoCsvProvider:
    """Loads and serves Sysco catalog data from a CSV file.

    CSV columns (0-indexed):
        0: Contract Item #
        1: AASIS Item #
        2: Sysco Item Number   <- source_item_id key
        3: Brand
        4: Product Description <- description
        5: Unit of Measure     <- unit_of_measure
        6: Cost                <- cost_per_case (e.g. "$289.50")
    """

    def __init__(self, csv_path: str) -> None:
        self._csv_path = csv_path
        self._items: dict[str, CatalogRecord] = {}

    @property
    def name(self) -> str:
        return "sysco"

    def load_catalog(self) -> list[CatalogRecord]:
        """Parse the CSV and return a list of CatalogRecord objects.

        Malformed rows are skipped with a warning.
        """
        self._items = {}

        with open(self._csv_path, newline="", encoding="utf-8") as fh:
            reader = csv.reader(fh)
            next(reader)  # skip header

            for line_num, row in enumerate(reader, start=2):
                if len(row) < 7:
                    logger.warning(
                        "Malformed row at line %d (expected >=7 columns, got %d)"
                        " — skipping: %r",
                        line_num,
                        len(row),
                        row,
                    )
                    continue

                try:
                    brand_raw = row[3].strip()
                    item = CatalogRecord(
                        source_item_id=row[2].strip(),
                        provider="sysco",
                        description=row[4].strip(),
                        unit_of_measure=row[5].strip(),
                        cost_per_case=_parse_cost(row[6]),
                        category=None,
                        brand=brand_raw if brand_raw else None,
                        source_metadata={
                            "contract_item_number": row[0].strip(),
                            "aasis_item_number": row[1].strip(),
                        },
                    )
                except (ValueError, IndexError) as exc:
                    logger.warning(
                        "Malformed row at line %d — skipping: %r (%s)",
                        line_num,
                        row,
                        exc,
                    )
                    continue

                self._items[item.source_item_id] = item

        return list(self._items.values())

    def get_price(self, source_item_id: str) -> PriceResult:
        """Return pricing for the given source_item_id.

        Raises ItemNotFoundError if source_item_id is not in the loaded catalog.
        Call load_catalog() before calling get_price().
        """
        item = self._items.get(source_item_id)
        if item is None:
            raise ItemNotFoundError(source_item_id)
        return PriceResult(
            cost_per_case=item.cost_per_case,
            unit_of_measure=item.unit_of_measure,
        )
