"""
Tests for the Royal Mail converter — focused on the subtle billing rules:
multi-line dockets, surcharge de-duplication, and per-parcel cost allocation.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from converters.detect import FileType, detect_file_type, detect_carrier  # noqa: E402
from converters.royal_mail import (  # noqa: E402
    convert,
    parse_invoice,
    UploadedFile,
)


# ── Minimal synthetic fixtures ───────────────────────────────────────────────

INVOICE_HEADER = (
    "Royal Mail Address|Document Type|Document Number|Account Number|Total Net|"
    "Total VAT|Total Gross|Invoice Date|Customer Address|Terms|Payment Type/Code|"
    "Legal Entity|Pay By|VAT Rate|VAT Total|VAT Rate|VAT Total|VAT Rate|VAT Total|"
    "VAT Rate|VAT Total|VAT Rate|VAT Total|Docket Number|Posting Date|Poster|"
    "Senders Ref|Format|Service|Quantity|Weight (kg)|Unit Cost|Net Value|VAT Code"
)


def _pipe_row(cols: dict) -> str:
    row = [""] * 34
    for idx, val in cols.items():
        row[idx] = str(val)
    return "|".join(row)


def _make_invoice() -> bytes:
    """
    One invoice, docket 1001 with TWO service lines, plus surcharge annotations.
      Line A: 2 parcels, net 10.00 (base 9.00 + surcharge 0.80 + green 0.20)
      Line B: 1 parcel,  net  5.00 (base 4.50 + surcharge 0.50)
    Docket total: 3 parcels, net 15.00, surcharge 1.50, base 13.50
    """
    lines = [INVOICE_HEADER]
    # Header/totals row
    lines.append(_pipe_row({2: "INV-001", 4: "15.00", 5: "3.00", 6: "18.00", 7: "27 Apr 2026"}))
    # Docket 1001, line A
    lines.append(_pipe_row({23: "1001", 24: "16/04/2026", 28: "TRACKED 48", 29: "2.000", 32: "10.00"}))
    lines.append(_pipe_row({31: "Total Cost Pre Surcharge \xa39.00, Surcharge of \xa30.80"}))
    lines.append(_pipe_row({31: "Green Surcharge of \xa30.20"}))
    # Docket 1001, line B (same docket, different service line)
    lines.append(_pipe_row({23: "1001", 24: "16/04/2026", 28: "TRACKED 24", 29: "1.000", 32: "5.00"}))
    lines.append(_pipe_row({31: "Total Cost Pre Surcharge \xa34.50, Surcharge of \xa30.50"}))
    return ("\n".join(lines)).encode("iso-8859-1")


def _make_outbound() -> bytes:
    header = (
        "Item Barcode,Item Overweight,Item Measured Weight Grams,Item Over Sized,"
        "Item Length (cm),Item Width (cm),Item Height (cm),Item Volume (m),"
        "Item Signature Flag,Item Requesting Notification Package,Date Raised,"
        "Order Number,Preadvice Number,Posting Location,Customer Senders Reference"
    )
    rows = [header]
    # 3 parcels, all on docket 1001
    rows.append("AA111111111GB,0,200,0,,,,,0,0,2026-04-16 03:24:23,1001,,9000,#REF1")
    rows.append("AA222222222GB,0,300,0,,,,,0,0,2026-04-16 03:24:23,1001,,9000,#REF2")
    rows.append("AA333333333GB,0,100,0,,,,,0,0,2026-04-16 03:24:23,1001,,9000,#REF3")
    return ("\n".join(rows)).encode("iso-8859-1")


# ── Detection ────────────────────────────────────────────────────────────────

def test_detects_invoice():
    assert detect_file_type("x.csv", "Royal Mail Address|Document Type|...") == FileType.RM_INVOICE


def test_detects_outbound():
    assert detect_file_type("x.csv", "Item Barcode,Item Overweight,...") == FileType.RM_OUTBOUND


def test_detects_rts():
    assert detect_file_type("x.csv", "Item Barcode,Item Ready for Delivery On,...") == FileType.RM_RTS


def test_detects_carrier():
    assert detect_carrier([FileType.RM_INVOICE, FileType.RM_OUTBOUND]) == "royal-mail"


# ── Invoice parsing ──────────────────────────────────────────────────────────

def test_multi_line_docket_is_aggregated():
    parsed = parse_invoice(_make_invoice())
    assert parsed["invoice_number"] == "INV-001"
    assert parsed["total_net"] == 15.00
    groups = parsed["groups"]
    assert set(groups.keys()) == {"1001"}, "two service lines should merge into one docket"
    grp = groups["1001"]
    assert grp.quantity == 3
    assert round(grp.net_value, 2) == 15.00


def test_green_surcharge_not_double_counted():
    grp = parse_invoice(_make_invoice())["groups"]["1001"]
    # Surcharges: 0.80 + 0.20 (green) + 0.50 = 1.50  — NOT 1.70 (which a double-count gives)
    assert round(grp.surcharge, 2) == 1.50
    # Base = net - surcharge = 15.00 - 1.50 = 13.50
    assert round(grp.base_total, 2) == 13.50


# ── Full conversion ──────────────────────────────────────────────────────────

def test_convert_allocates_cost_evenly_and_reconciles():
    files = [
        UploadedFile("inv.csv", FileType.RM_INVOICE, _make_invoice()),
        UploadedFile("out.csv", FileType.RM_OUTBOUND, _make_outbound()),
    ]
    result = convert(files)

    assert len(result.rows) == 3
    # Base 13.50 / 3 = 4.50 per parcel; surcharge 1.50 / 3 = 0.50 per parcel
    for row in result.rows:
        assert round(row.cost, 2) == 4.50
        assert round(row.surcharge, 2) == 0.50

    # Computed total reconciles to the stated net
    assert result.computed_total == result.stated_total_net == 15.00
    assert not result.warnings


def test_weight_converted_grams_to_kg():
    files = [
        UploadedFile("inv.csv", FileType.RM_INVOICE, _make_invoice()),
        UploadedFile("out.csv", FileType.RM_OUTBOUND, _make_outbound()),
    ]
    result = convert(files)
    weights = sorted(r.weight for r in result.rows)
    assert weights == [0.1, 0.2, 0.3]


def test_docket_without_outbound_emits_fallback_row():
    # Invoice only, no outbound file → one fallback row carrying full docket cost
    files = [UploadedFile("inv.csv", FileType.RM_INVOICE, _make_invoice())]
    result = convert(files)

    assert len(result.rows) == 1
    assert result.rows[0].tracking_number == "1001"
    assert round(result.rows[0].cost, 2) == 13.50
    assert round(result.rows[0].surcharge, 2) == 1.50
    assert len(result.warnings) == 1
    assert result.computed_total == 15.00
