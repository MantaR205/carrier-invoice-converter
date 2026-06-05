"""
Conversion store — tracks every conversion batch so finance can see which
invoices have been converted and which have been uploaded to Unify.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import duckdb

logger = logging.getLogger(__name__)
DEFAULT_DB_PATH = Path(__file__).parent / "data" / "conversions.duckdb"


class ConversionStore:
    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = duckdb.connect(str(self.db_path))
        self._init_schema()

    def _init_schema(self):
        self.conn.execute("CREATE SEQUENCE IF NOT EXISTS batch_seq START 1")
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS conversion_batches (
                id              INTEGER PRIMARY KEY,
                created_at      TIMESTAMP NOT NULL DEFAULT current_timestamp,
                carrier         VARCHAR NOT NULL,
                invoice_number  VARCHAR,
                invoice_date    VARCHAR,
                source_files    INTEGER NOT NULL DEFAULT 0,
                row_count       INTEGER NOT NULL DEFAULT 0,
                total_cost      DECIMAL(12,2) NOT NULL DEFAULT 0,
                total_surcharge DECIMAL(12,2) NOT NULL DEFAULT 0,
                stated_net      DECIMAL(12,2),
                output_filename VARCHAR,
                status          VARCHAR NOT NULL DEFAULT 'converted',  -- converted | uploaded
                uploaded_at     TIMESTAMP,
                notes           VARCHAR
            )
        """)
        # Guard against re-converting the same invoice number twice unnoticed
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS batch_files (
                batch_id   INTEGER NOT NULL,
                filename   VARCHAR NOT NULL,
                file_type  VARCHAR NOT NULL,
                row_count  INTEGER NOT NULL DEFAULT 0
            )
        """)

    def record_batch(
        self,
        carrier: str,
        invoice_number: Optional[str],
        invoice_date: Optional[str],
        source_files: int,
        row_count: int,
        total_cost: float,
        total_surcharge: float,
        stated_net: Optional[float],
        output_filename: str,
        file_summary: list[dict],
    ) -> int:
        batch_id = self.conn.execute("SELECT nextval('batch_seq')").fetchone()[0]
        self.conn.execute("""
            INSERT INTO conversion_batches
                (id, created_at, carrier, invoice_number, invoice_date, source_files,
                 row_count, total_cost, total_surcharge, stated_net, output_filename, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'converted')
        """, [
            batch_id, datetime.now(timezone.utc), carrier, invoice_number, invoice_date,
            source_files, row_count, round(total_cost, 2), round(total_surcharge, 2),
            stated_net, output_filename,
        ])
        for f in file_summary:
            self.conn.execute("""
                INSERT INTO batch_files (batch_id, filename, file_type, row_count)
                VALUES (?, ?, ?, ?)
            """, [batch_id, f.get("name", ""), f.get("file_type", ""), f.get("rows", 0)])
        logger.info("Recorded conversion batch %d (%s)", batch_id, invoice_number)
        return batch_id

    def mark_uploaded(self, batch_id: int):
        self.conn.execute("""
            UPDATE conversion_batches
            SET status = 'uploaded', uploaded_at = ?
            WHERE id = ?
        """, [datetime.now(timezone.utc), batch_id])

    def find_existing_invoice(self, carrier: str, invoice_number: Optional[str]) -> Optional[dict]:
        if not invoice_number:
            return None
        row = self.conn.execute("""
            SELECT id, created_at, status FROM conversion_batches
            WHERE carrier = ? AND invoice_number = ?
            ORDER BY created_at DESC LIMIT 1
        """, [carrier, invoice_number]).fetchone()
        if not row:
            return None
        return {"id": row[0], "created_at": row[1], "status": row[2]}

    def recent_batches(self, limit: int = 50):
        """Return a list of dicts for display."""
        rows = self.conn.execute("""
            SELECT id, created_at, carrier, invoice_number, invoice_date, source_files,
                   row_count, total_cost, total_surcharge, stated_net, status, uploaded_at
            FROM conversion_batches
            ORDER BY created_at DESC
            LIMIT ?
        """, [limit]).fetchall()
        cols = ["id", "created_at", "carrier", "invoice_number", "invoice_date",
                "source_files", "row_count", "total_cost", "total_surcharge",
                "stated_net", "status", "uploaded_at"]
        return [dict(zip(cols, r)) for r in rows]

    def stats(self) -> dict:
        total = self.conn.execute("SELECT COUNT(*) FROM conversion_batches").fetchone()[0]
        uploaded = self.conn.execute(
            "SELECT COUNT(*) FROM conversion_batches WHERE status = 'uploaded'"
        ).fetchone()[0]
        return {"total_batches": total, "uploaded": uploaded, "pending": total - uploaded}

    def close(self):
        self.conn.close()
