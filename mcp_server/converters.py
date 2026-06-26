"""Generic converters used by the MCP `convert_statement` tool.

These are heuristic and intentionally lenient: the agent reads
`INSTRUCTIONS.md` for the canonical schema and per-bank quirks, then calls
`convert_statement`, which dispatches here.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from app.services.normalizer import (
    NormalizationError,
    canonical_csv_string,
    normalize_amount,
    normalize_date,
    normalize_optional_float,
)

from .category_classifier import classify as classify_category


@dataclass
class ConversionResult:
    rows: list[dict]
    account_guess: Optional[str]
    currency_guess: str
    warnings: list[str]


# Header aliases -> canonical field
ALIASES = {
    "date": "date",
    "transaction date": "date",
    "trans date": "date",
    "post date": "date",
    "posting date": "date",
    "value date": "date",
    "description": "description",
    "details": "description",
    "memo": "description",
    "narration": "description",
    "payee": "description",
    "merchant": "description",
    "amount": "amount",
    "transaction amount": "amount",
    "value": "amount",
    "debit": "debit",
    "withdrawal": "debit",
    "paid out": "debit",
    "withdrawals": "debit",
    "credit": "credit",
    "deposit": "credit",
    "paid in": "credit",
    "deposits": "credit",
    "currency": "currency",
    "ccy": "currency",
    "account": "account",
    "account id": "account",
    "account number": "account",
    "card": "account",
    "balance": "balance_after",
    "running balance": "balance_after",
    "available balance": "balance_after",
    "balance after": "balance_after",
}


def _map_header(h: str) -> str:
    return ALIASES.get(h.strip().lower(), "")


def _extract_text(path: Path) -> str:
    """Read CSV/TXT as text; best-effort PDF text extraction."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader  # optional dependency
        except ImportError as e:
            raise RuntimeError("PDF support requires `pip install pypdf`") from e
        reader = PdfReader(str(path))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    # Default: read as text/CSV.
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        return f.read()


def _looks_like_amount(s: str) -> bool:
    return bool(re.match(r"^[()\-\+\$.,\d\s]+$", s or "")) and any(c.isdigit() for c in s)


def _detect_account(path: Path, header: list[str], sample: list[dict]) -> Optional[str]:
    for row in sample:
        for k, v in row.items():
            if _map_header(k) == "account" and v:
                return str(v).strip()
    # Fall back to filename stem.
    return path.stem


def _detect_currency(header: list[str], sample: list[dict], default: str = "USD") -> str:
    for row in sample:
        for k, v in row.items():
            if _map_header(k) == "currency" and v:
                return str(v).strip().upper() or default
    return default


def convert(path: Path, default_currency: str = "USD") -> ConversionResult:
    """Convert any supported statement file into canonical rows."""
    text = _extract_text(path)
    warnings: list[str] = []

    # Parse as CSV (lenient: tolerate whitespace, different delimiters).
    sample = text[:2048]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel  # type: ignore[assignment]

    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    header = reader.fieldnames or []
    mapping = {h: _map_header(h) for h in header}
    if not any(mapping.values()):
        # No recognized header — try treating the file as fixed-width / plain rows.
        warnings.append("no recognizable CSV header; attempting positional parse")

    raw_rows = list(reader)
    account_guess = _detect_account(path, header, raw_rows[:10])
    currency_guess = _detect_currency(header, raw_rows[:10], default_currency)

    canonical: list[dict] = []
    for raw in raw_rows:
        # Remap columns to canonical names.
        remapped: dict = {}
        for k, v in raw.items():
            canon = mapping.get(k, "")
            if canon and v not in (None, ""):
                remapped.setdefault(canon, v)

        # Combine debit/credit into amount if needed.
        if "amount" not in remapped and ("debit" in remapped or "credit" in remapped):
            debit = normalize_optional_float(remapped.get("debit")) or 0.0
            credit = normalize_optional_float(remapped.get("credit")) or 0.0
            remapped["amount"] = credit - debit

        if "date" not in remapped or "amount" not in remapped:
            warnings.append(f"skipped row lacking date/amount: {raw}")
            continue

        try:
            d = normalize_date(str(remapped["date"]))
            amt = normalize_amount(remapped["amount"])
        except NormalizationError as e:
            warnings.append(f"skipped row: {e}")
            continue

        description = str(remapped.get("description", "") or "")
        # Intelligent category guess baked into the conversion step (see
        # INSTRUCTIONS.md): merchant patterns + HK calendar events + amount
        # heuristics. Always returns a guess when possible.
        guess = classify_category(description, amt, tx_date=d)

        original = " | ".join(f"{k}={v}" for k, v in raw.items() if v not in (None, ""))
        canonical.append({
            "date": d.isoformat(),
            "description": description,
            "amount": f"{amt:.2f}",
            "currency": str(remapped.get("currency") or currency_guess).upper(),
            "account": str(remapped.get("account") or account_guess or ""),
            "balance_after": (
                f"{normalize_optional_float(remapped.get('balance_after')):.2f}"
                if normalize_optional_float(remapped.get("balance_after")) is not None else ""
            ),
            "original_row": original[:512],
            "category": guess.category,
            "category_event": guess.event,
        })

    return ConversionResult(
        rows=canonical,
        account_guess=account_guess,
        currency_guess=currency_guess,
        warnings=warnings,
    )


def to_canonical_csv(result: ConversionResult) -> str:
    """Render a ConversionResult as a canonical CSV string."""
    from datetime import date as _date

    from app.services.normalizer import NormalizedRow

    rows = [
        NormalizedRow(
            date=_date.fromisoformat(r["date"]),
            description=r["description"],
            amount=float(r["amount"]),
            currency=r["currency"],
            account=r["account"] or None,
            balance_after=float(r["balance_after"]) if r["balance_after"] else None,
            original_row=r["original_row"],
            category=r.get("category", ""),
            category_event=r.get("category_event", ""),
        )
        for r in result.rows
    ]
    return canonical_csv_string(rows)
