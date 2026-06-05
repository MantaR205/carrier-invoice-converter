"""
Royal Mail invoice converter.

Royal Mail bills at the *manifest* (docket) level — each docket line on the
invoice covers N parcels of one service type for a total net value, with
surcharges itemised in annotation rows beneath it. The individual tracking
numbers live in separate "outbound" manifest files.

This converter:
  1. Parses the invoice CSV(s) → per-docket cost + surcharge + service
  2. Parses the outbound CSV(s) → individual parcels (barcode, weight, refs)
  3. Allocates each docket's cost/surcharge evenly across its parcels
  4. Emits one NormalizedRow per tracking number

When a docket has no matching outbound rows (e.g. missing file), a single
docket-level fallback row is emitted so no cost is silently dropped.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional

from .base import ConversionResult, NormalizedRow
from .detect import FileType


# ── Invoice CSV (pipe-delimited) column indices ──────────────────────────────
INV_DELIM = "|"
INV_COL_INVOICE_NUMBER = 2
INV_COL_TOTAL_NET = 4
INV_COL_TOTAL_VAT = 5
INV_COL_TOTAL_GROSS = 6
INV_COL_INVOICE_DATE = 7
INV_COL_DOCKET = 23
INV_COL_POSTING_DATE = 24
INV_COL_SENDERS_REF = 26
INV_COL_SERVICE = 28
INV_COL_QUANTITY = 29
INV_COL_WEIGHT = 30
INV_COL_NET_VALUE = 32
INV_COL_ANNOTATION = 31  # surcharge text lives here on annotation rows

# ── Outbound CSV (comma-delimited) column indices ────────────────────────────
OUT_COL_BARCODE = 0
OUT_COL_WEIGHT_GRAMS = 2
OUT_COL_OVERSIZED = 3
OUT_COL_DATE_RAISED = 10
OUT_COL_RM_ORDER = 11       # docket number — links to invoice
OUT_COL_CUSTOMER_REF = 14

# ── RTS CSV column indices ───────────────────────────────────────────────────
RTS_COL_BARCODE = 0
RTS_COL_DATE_RAISED = 6
RTS_COL_RM_ORDER = 7
RTS_COL_CUSTOMER_REF = 10


@dataclass
class ManifestGroup:
    """
    One docket (manifest) on the invoice. A docket may span several service
    lines, so net_value / quantity / surcharge accumulate across them.

    net_value is the authoritative figure straight from the invoice and already
    includes surcharges; the base (pre-surcharge) cost is derived as
    net_value − surcharge.
    """

    docket: str
    service: Optional[str] = None
    quantity: int = 0
    net_value: float = 0.0        # full net, incl. surcharges (sum of all lines)
    surcharge: float = 0.0        # surcharge + green surcharge combined
    posting_date: Optional[str] = None

    @property
    def base_total(self) -> float:
        return max(self.net_value - self.surcharge, 0.0)

    def per_parcel_cost(self) -> float:
        return round(self.base_total / self.quantity, 4) if self.quantity else 0.0

    def per_parcel_surcharge(self) -> float:
        return round(self.surcharge / self.quantity, 4) if self.quantity else 0.0


@dataclass
class OutboundParcel:
    barcode: str
    weight_kg: Optional[float]
    oversized: bool
    docket: Optional[str]
    customer_ref: Optional[str]
    despatch_date: Optional[str]
    is_rts: bool = False


# ─────────────────────────────────────────────────────────────────────────────
#  Parsing helpers
# ─────────────────────────────────────────────────────────────────────────────

_MONEY_RE = re.compile(r"[-+]?\d[\d,]*\.?\d*")
# "Surcharge of £9.53" and "Green Surcharge of £4.56" — selected per-row so the
# two never double-count (a green row also literally contains "Surcharge of").
_SURCHARGE_RE = re.compile(r"Surcharge of\s*[£\W]?\s*([\d.]+)", re.IGNORECASE)


def _money(value: str) -> Optional[float]:
    if value is None:
        return None
    m = _MONEY_RE.search(value.replace(",", ""))
    return float(m.group()) if m else None


def _parse_date(value: str) -> Optional[str]:
    """Return ISO date string (YYYY-MM-DD) or None."""
    if not value:
        return None
    value = value.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y", "%d %b %Y"):
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _decode(raw: bytes) -> str:
    """Royal Mail files are latin-1; fall back to utf-8 with replacement."""
    try:
        return raw.decode("iso-8859-1")
    except (UnicodeDecodeError, AttributeError):
        return raw.decode("utf-8", errors="replace")


# ─────────────────────────────────────────────────────────────────────────────
#  Invoice parsing → manifest groups
# ─────────────────────────────────────────────────────────────────────────────

def parse_invoice(raw: bytes) -> dict:
    """
    Parse a Royal Mail invoice CSV.

    Returns dict with: invoice_number, invoice_date, total_net, total_vat,
    total_gross, groups ({docket: ManifestGroup}).
    """
    text = _decode(raw)
    reader = csv.reader(io.StringIO(text), delimiter=INV_DELIM)
    rows = [r for r in reader if r]

    invoice_number = None
    invoice_date = None
    total_net = total_vat = total_gross = None
    groups: dict[str, ManifestGroup] = {}

    current_docket: Optional[str] = None

    for i, row in enumerate(rows):
        if i == 0:
            continue  # header

        def col(idx: int) -> str:
            return row[idx].strip() if idx < len(row) else ""

        # Invoice header row (first data row, carries totals)
        if i == 1:
            invoice_number = col(INV_COL_INVOICE_NUMBER) or None
            invoice_date = _parse_date(col(INV_COL_INVOICE_DATE))
            total_net = _money(col(INV_COL_TOTAL_NET))
            total_vat = _money(col(INV_COL_TOTAL_VAT))
            total_gross = _money(col(INV_COL_TOTAL_GROSS))
            continue

        docket = col(INV_COL_DOCKET)

        if docket:
            # Docket data row. A docket may have several service lines, so
            # accumulate rather than overwrite.
            current_docket = docket
            net_value = _money(col(INV_COL_NET_VALUE)) or 0.0
            quantity = int(_money(col(INV_COL_QUANTITY)) or 1)
            service = col(INV_COL_SERVICE) or None

            if docket in groups:
                grp = groups[docket]
                grp.net_value += net_value
                grp.quantity += quantity
                if not grp.service:
                    grp.service = service
            else:
                groups[docket] = ManifestGroup(
                    docket=docket,
                    service=service,
                    quantity=quantity,
                    net_value=net_value,
                    surcharge=0.0,
                    posting_date=_parse_date(col(INV_COL_POSTING_DATE)),
                )
        else:
            # Annotation row beneath the current docket. Select exactly one
            # surcharge type so green and standard surcharges never double-count.
            annotation = col(INV_COL_ANNOTATION)
            if annotation and current_docket and current_docket in groups:
                grp = groups[current_docket]
                low = annotation.lower()
                if "green surcharge" in low:
                    m = _SURCHARGE_RE.search(annotation)
                    if m:
                        grp.surcharge += float(m.group(1))
                elif "surcharge of" in low:
                    m = _SURCHARGE_RE.search(annotation)
                    if m:
                        grp.surcharge += float(m.group(1))

    return {
        "invoice_number": invoice_number,
        "invoice_date": invoice_date,
        "total_net": total_net,
        "total_vat": total_vat,
        "total_gross": total_gross,
        "groups": groups,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Outbound / RTS parsing → parcels
# ─────────────────────────────────────────────────────────────────────────────

def parse_outbound(raw: bytes) -> list[OutboundParcel]:
    text = _decode(raw)
    reader = csv.reader(io.StringIO(text))
    parcels = []
    for i, row in enumerate(reader):
        if i == 0 or not row:
            continue
        if len(row) <= OUT_COL_RM_ORDER:
            continue

        def col(idx):
            return row[idx].strip() if idx < len(row) else ""

        barcode = col(OUT_COL_BARCODE)
        if not barcode:
            continue

        grams = _money(col(OUT_COL_WEIGHT_GRAMS))
        parcels.append(OutboundParcel(
            barcode=barcode,
            weight_kg=round(grams / 1000, 3) if grams else None,
            oversized=col(OUT_COL_OVERSIZED) == "1",
            docket=col(OUT_COL_RM_ORDER) or None,
            customer_ref=col(OUT_COL_CUSTOMER_REF) or None,
            despatch_date=_parse_date(col(OUT_COL_DATE_RAISED)),
        ))
    return parcels


def parse_rts(raw: bytes) -> list[OutboundParcel]:
    text = _decode(raw)
    reader = csv.reader(io.StringIO(text))
    parcels = []
    for i, row in enumerate(reader):
        if i == 0 or not row:
            continue
        if len(row) <= RTS_COL_RM_ORDER:
            continue

        def col(idx):
            return row[idx].strip() if idx < len(row) else ""

        barcode = col(RTS_COL_BARCODE)
        if not barcode:
            continue

        parcels.append(OutboundParcel(
            barcode=barcode,
            weight_kg=None,
            oversized=False,
            docket=col(RTS_COL_RM_ORDER) or None,
            customer_ref=col(RTS_COL_CUSTOMER_REF) or None,
            despatch_date=_parse_date(col(RTS_COL_DATE_RAISED)),
            is_rts=True,
        ))
    return parcels


# ─────────────────────────────────────────────────────────────────────────────
#  Top-level conversion
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class UploadedFile:
    name: str
    file_type: FileType
    data: bytes


def convert(files: list[UploadedFile]) -> ConversionResult:
    """Convert a batch of Royal Mail files into normalized rows."""
    result = ConversionResult(carrier="royal-mail")

    invoice_files = [f for f in files if f.file_type == FileType.RM_INVOICE]
    outbound_files = [f for f in files if f.file_type == FileType.RM_OUTBOUND]
    rts_files = [f for f in files if f.file_type == FileType.RM_RTS]

    # ── 1. Parse invoices → merged manifest groups + totals ──
    groups: dict[str, ManifestGroup] = {}
    total_net = total_gross = 0.0
    for f in invoice_files:
        parsed = parse_invoice(f.data)
        result.invoice_number = result.invoice_number or parsed["invoice_number"]
        result.invoice_date = result.invoice_date or parsed["invoice_date"]
        total_net += parsed["total_net"] or 0.0
        total_gross += parsed["total_gross"] or 0.0
        for docket, grp in parsed["groups"].items():
            groups[docket] = grp
        result.file_summary.append({
            "name": f.name, "file_type": "Invoice",
            "rows": len(parsed["groups"]),
        })

    result.stated_total_net = round(total_net, 2) if total_net else None
    result.stated_total_gross = round(total_gross, 2) if total_gross else None

    # ── 2. Parse outbound + RTS → parcels ──
    parcels: list[OutboundParcel] = []
    for f in outbound_files:
        p = parse_outbound(f.data)
        parcels.extend(p)
        result.file_summary.append({"name": f.name, "file_type": "Outbound", "rows": len(p)})
    for f in rts_files:
        p = parse_rts(f.data)
        parcels.extend(p)
        result.file_summary.append({"name": f.name, "file_type": "RTS", "rows": len(p)})

    # ── 3. Allocate cost per parcel from its docket ──
    dockets_seen: set[str] = set()
    for parcel in parcels:
        grp = groups.get(parcel.docket) if parcel.docket else None
        if grp:
            dockets_seen.add(parcel.docket)
            cost = grp.per_parcel_cost()
            surcharge = grp.per_parcel_surcharge()
            service = grp.service
        else:
            cost = 0.0
            surcharge = 0.0
            service = "Return to Sender" if parcel.is_rts else None
            if parcel.docket:
                result.warnings.append(
                    f"Parcel {parcel.barcode}: docket {parcel.docket} not found on invoice — cost set to 0."
                )

        result.rows.append(NormalizedRow(
            tracking_number=parcel.barcode,
            cost=cost,
            surcharge=surcharge,
            weight=parcel.weight_kg,
            service=service,
            despatch_date=parcel.despatch_date,
            order_ref=parcel.customer_ref,
        ))

    # ── 4. Dockets with no tracking data → fallback docket-level row ──
    for docket, grp in groups.items():
        if docket in dockets_seen:
            continue
        result.warnings.append(
            f"Docket {docket} ({grp.quantity} parcels) had no outbound tracking file — "
            f"emitted as a single docket-level row."
        )
        result.rows.append(NormalizedRow(
            tracking_number=docket,
            cost=grp.base_total,
            surcharge=grp.surcharge,
            weight=None,
            service=grp.service,
            despatch_date=grp.posting_date,
            order_ref=None,
        ))

    return result
