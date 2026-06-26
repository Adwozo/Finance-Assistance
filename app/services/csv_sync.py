"""Sync DB edits back to the source canonical CSV files in statements/.

When the user edits a transaction (category, description, is_transfer) in the
UI, those edits live in the DB. This service rewrites the matching source CSV
file so the on-disk file stays in sync with the DB.

Strategy: for each source CSV, read it row-by-row with csv.DictReader, compute
the dedup hash for each row (same formula as the importer), look up the
matching Transaction in the DB, and rewrite the row's `category`,
`category_event`, and `description` columns from the DB. Rows that have no
matching Transaction (skipped/malformed rows) are preserved verbatim so the
user doesn't lose data. A backup of the original file is written first.
"""
from __future__ import annotations

import csv
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Category, Transaction
from .normalizer import CANONICAL_FIELDS, NormalizedRow, normalize_date, normalize_amount, normalize_optional_float, normalize_currency


@dataclass
class SyncResult:
    file: str
    rows_total: int
    rows_updated: int
    rows_preserved: int
    backup_path: Optional[str]


def _row_hash(raw: dict) -> Optional[str]:
    """Compute the importer's dedup hash for a raw CSV row, or None if it
    can't be normalized (e.g. a skipped/malformed row)."""
    try:
        date_val = raw.get("date")
        desc = raw.get("description", "") or ""
        amt = raw.get("amount")
        if amt in (None, ""):
            return None
        return NormalizedRow(
            date=normalize_date(str(date_val)),
            description=desc,
            amount=normalize_amount(amt),
            currency=normalize_currency(raw.get("currency")),
            account=raw.get("account") or None,
            balance_after=normalize_optional_float(raw.get("balance_after")),
            original_row=raw.get("original_row", "") or "",
            category=raw.get("category", "") or "",
            category_event=raw.get("category_event", "") or "",
        ).hash()
    except Exception:
        return None


def sync_file(session: Session, filename: str) -> SyncResult:
    """Rewrite a single source CSV from DB state. Returns a SyncResult."""
    path = get_settings().statements_path / filename
    if not path.exists():
        raise FileNotFoundError(f"statement file not found: {filename}")

    # Read all rows first (so we can rewrite in place).
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or list(CANONICAL_FIELDS)
        raw_rows = list(reader)

    # Make sure the canonical columns exist in the header (older files may lack
    # category/category_event); add them so edits can be written back.
    for col in CANONICAL_FIELDS:
        if col not in fieldnames:
            fieldnames = list(fieldnames) + [col]
            for r in raw_rows:
                r.setdefault(col, "")

    # Build a hash -> Transaction lookup for all rows in this file.
    hashes: list[Optional[str]] = []
    for r in raw_rows:
        hashes.append(_row_hash(r))
    known = {h for h in hashes if h}
    tx_by_hash: dict[str, Transaction] = {}
    cat_cache: dict[int, str] = {}
    if known:
        txs = list(session.scalars(
            select(Transaction).where(
                Transaction.hash.in_(known),
                Transaction.source_file == filename,
            )
        ))
        for t in txs:
            tx_by_hash[t.hash] = t
            if t.category_id is not None and t.category_id not in cat_cache:
                cat = session.get(Category, t.category_id)
                cat_cache[t.category_id] = cat.name if cat else ""

    # Backup the original file (once per sync).
    backup = path.with_suffix(path.suffix + ".bak")
    shutil.copy2(path, backup)

    updated = 0
    preserved = 0
    out_rows: list[dict] = []
    for raw, h in zip(raw_rows, hashes):
        if h and h in tx_by_hash:
            t = tx_by_hash[h]
            # Apply DB edits back to the row.
            if t.description:
                raw["description"] = t.description
            cat_name = cat_cache.get(t.category_id, "")
            raw["category"] = cat_name
            raw["category_event"] = t.category_event or ""
            updated += 1
        else:
            preserved += 1
        out_rows.append(raw)

    # Rewrite the file with the (possibly extended) header.
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for r in out_rows:
            writer.writerow(r)

    return SyncResult(
        file=filename,
        rows_total=len(raw_rows),
        rows_updated=updated,
        rows_preserved=preserved,
        backup_path=str(backup),
    )


def sync_all(session: Session) -> list[SyncResult]:
    """Sync every statement CSV in the statements folder."""
    from .importer import list_statement_files
    results: list[SyncResult] = []
    for p in list_statement_files():
        results.append(sync_file(session, p.name))
    return results
