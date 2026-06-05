"""
Shared types for all carrier converters.

Every carrier-specific converter takes a set of uploaded files and produces a
list of NormalizedRow — the single, simple output schema that Unify consumes.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


# The normalized output columns, in order. This is the contract with Unify.
OUTPUT_COLUMNS = [
    "tracking_number",
    "cost",
    "surcharge",
    "weight",
    "service",
    "despatch_date",
    "order_ref",
]


@dataclass
class NormalizedRow:
    """One parcel in the normalized output CSV."""

    tracking_number: str
    cost: float = 0.0                       # base cost (pre-surcharge)
    surcharge: float = 0.0                  # total surcharges (0 if none)
    weight: Optional[float] = None          # kg
    service: Optional[str] = None           # carrier service description
    despatch_date: Optional[str] = None     # YYYY-MM-DD
    order_ref: Optional[str] = None         # customer's own order reference

    def to_output_dict(self) -> dict:
        d = asdict(self)
        # Round monetary + weight values for a clean CSV
        d["cost"] = round(self.cost, 2)
        d["surcharge"] = round(self.surcharge, 2)
        d["weight"] = round(self.weight, 3) if self.weight is not None else None
        return {k: d.get(k) for k in OUTPUT_COLUMNS}


@dataclass
class ConversionResult:
    """The outcome of converting one batch of uploaded files."""

    carrier: str
    rows: list[NormalizedRow] = field(default_factory=list)
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    # Sum of the source invoice's stated totals, for a reconciliation sanity check
    stated_total_net: Optional[float] = None
    stated_total_gross: Optional[float] = None
    warnings: list[str] = field(default_factory=list)
    file_summary: list[dict] = field(default_factory=list)  # [{name, file_type, rows}]

    @property
    def total_cost(self) -> float:
        return round(sum(r.cost for r in self.rows), 2)

    @property
    def total_surcharge(self) -> float:
        return round(sum(r.surcharge for r in self.rows), 2)

    @property
    def computed_total(self) -> float:
        return round(self.total_cost + self.total_surcharge, 2)
