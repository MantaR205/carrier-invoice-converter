"""
File type detection for uploaded carrier files.

Detection is by content (first line) rather than filename, so it is robust to
renamed downloads. Filenames are used only as a secondary hint (e.g. Oversize).
"""

from __future__ import annotations

from enum import Enum


class FileType(str, Enum):
    RM_INVOICE = "rm_invoice"        # Pipe-delimited Royal Mail billing CSV
    RM_OUTBOUND = "rm_outbound"      # Comma-delimited outbound tracking manifest
    RM_RTS = "rm_rts"                # Comma-delimited return-to-sender manifest
    RM_NO_DATA = "rm_no_data"        # Order numbers with no tracking data
    UNKNOWN = "unknown"


def detect_file_type(filename: str, first_line: str) -> FileType:
    """Identify a file from its name + first line of content."""
    line = (first_line or "").strip()

    if line.startswith("Royal Mail Address|"):
        return FileType.RM_INVOICE

    # RTS manifests have "Item Ready for Delivery On" as the 2nd column
    if line.startswith("Item Barcode,Item Ready for Delivery On"):
        return FileType.RM_RTS

    # Outbound / oversize / overweight manifests share this header
    if line.startswith("Item Barcode,Item Overweight"):
        return FileType.RM_OUTBOUND

    if line == "Order Number":
        return FileType.RM_NO_DATA

    return FileType.UNKNOWN


def detect_carrier(file_types: list[FileType]) -> str:
    """Infer the carrier from the set of detected file types."""
    if any(ft.value.startswith("rm_") for ft in file_types):
        return "royal-mail"
    return "unknown"
