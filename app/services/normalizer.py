"""Canonical CSV schema + normalization.

The canonical schema is the single contract every statement (built-in parser or
MCP `convert_statement` output) must satisfy before reaching the DB:

    date,description,amount,currency,account,balance_after,original_row,category,category_event

The last two columns carry the converter's best-guess category (free text
matching the app's taxonomy, e.g. "Coffee") and the event estimation that drove
the guess (e.g. "merchant_pattern", "lunar_new_year", "christmas", "payday",
"uncertain"). See `mcp_server/INSTRUCTIONS.md` for the full spec including the
merchant-pattern + HK calendar-event intelligence the agent applies during
conversion.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Iterable, Optional

from dateutil import parser as dateparser

from ..config import get_settings

CANONICAL_FIELDS = (
    "date",
    "description",
    "amount",
    "currency",
    "account",
    "balance_after",
    "original_row",
    "category",
    "category_event",
)


class NormalizationError(ValueError):
    """Raised when a row cannot be normalized to the canonical schema."""


@dataclass
class NormalizedRow:
    date: date
    description: str
    amount: float
    currency: str
    account: Optional[str]
    balance_after: Optional[float]
    original_row: str
    # Best-guess category text (matches app taxonomy, e.g. "Coffee"); "" if unknown.
    category: str = ""
    # Event estimation that drove the guess: "merchant_pattern", "lunar_new_year",
    # "christmas", "halloween", "mid_autumn", "payday", "school_holiday",
    # "amount_heuristic", "uncertain", "".
    category_event: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def hash(self) -> str:
        key = "|".join(
            [
                self.date.isoformat(),
                self.description.strip().lower(),
                f"{self.amount:.2f}",
                (self.account or "").strip().lower(),
            ]
        )
        return hashlib.sha256(key.encode("utf-8")).hexdigest()


# --- Field-level normalizers ---

_DATE_PATTERNS = ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d", "%d-%b-%Y", "%b %d, %Y")


def normalize_date(raw: str) -> date:
    raw = (raw or "").strip()
    if not raw:
        raise NormalizationError("empty date")
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        pass
    try:
        # Hong Kong convention: day-first (DD/MM/YYYY). ISO YYYY-MM-DD is handled above.
        return dateparser.parse(raw, dayfirst=True).date()
    except (ValueError, OverflowError) as e:
        raise NormalizationError(f"unparseable date: {raw!r}") from e


_AMOUNT_RE = re.compile(r"[^0-9+\-.\,]")


def normalize_amount(raw) -> float:
    """Parse a money string into a signed float.

    Conventions:
      * Parentheses `(1.23)` -> negative
      * Trailing `DB` / `DR` / `-` -> negative
      * Trailing `CR` / `+` -> positive
      * Thousands separators are stripped
    """
    if raw is None or raw == "":
        raise NormalizationError("empty amount")
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip()
    if not s:
        raise NormalizationError("empty amount")
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1]
    s = _AMOUNT_RE.sub("", s)
    if s in ("", "-", "+", "."):
        raise NormalizationError(f"unparseable amount: {raw!r}")
    # Handle European decimal comma: 1.234,56 -> 1234.56
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s and "." not in s:
        # Could be decimal comma or thousands. Heuristic: one comma + 2 decimals -> decimal.
        if re.fullmatch(r"\d+,\d{1,2}", s):
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")
    try:
        value = float(s)
    except ValueError as e:
        raise NormalizationError(f"unparseable amount: {raw!r}") from e
    if negative:
        value = -abs(value)
    return value


def normalize_currency(raw: Optional[str]) -> str:
    raw = (raw or "").strip().upper()
    if not raw:
        return get_settings().default_currency
    if len(raw) != 3 or not raw.isalpha():
        # Be lenient: accept anything but normalize to upper; caller can warn.
        return raw
    return raw


def normalize_text(raw, field: str, *, required: bool = True) -> str:
    if raw is None:
        if required:
            raise NormalizationError(f"missing required field: {field}")
        return ""
    s = str(raw).strip()
    if required and not s:
        raise NormalizationError(f"empty required field: {field}")
    return s


def normalize_optional_float(raw) -> Optional[float]:
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return normalize_amount(raw)
    except NormalizationError:
        return None


# --- Row-level normalization ---

def normalize_row(row: dict, *, source_file: Optional[str] = None) -> NormalizedRow:
    """Validate + normalize a dict-like row to NormalizedRow.

    `row` keys are matched case-insensitively against canonical + common aliases.
    """
    lower = {str(k).strip().lower(): v for k, v in row.items()}

    def pick(*keys: str):
        for k in keys:
            if k in lower and lower[k] not in (None, ""):
                return lower[k]
        return None

    raw_date = pick("date", "transaction date", "post date", "posting date", "trans date")
    raw_desc = pick("description", "details", "memo", "narration", "payee", "merchant")
    raw_amount = pick("amount", "transaction amount", "value")
    raw_debit = pick("debit", "withdrawal", "paid out", "withdrawals")
    raw_credit = pick("credit", "deposit", "paid in", "deposits")
    raw_currency = pick("currency", "ccy")
    raw_account = pick("account", "account id", "account number", "card")
    raw_balance = pick("balance_after", "balance", "running balance", "available balance")
    raw_original = pick("original_row", "raw", "source row")
    raw_category = pick("category", "cat", "category name")
    raw_category_event = pick("category_event", "cat_event", "event")

    if raw_date is None:
        raise NormalizationError("missing date")
    if raw_desc is None:
        raw_desc = ""
    if raw_amount is None and (raw_debit is not None or raw_credit is not None):
        debit = normalize_optional_float(raw_debit) or 0.0
        credit = normalize_optional_float(raw_credit) or 0.0
        amount = credit - debit
    elif raw_amount is not None:
        amount = normalize_amount(raw_amount)
    else:
        raise NormalizationError("missing amount")

    # Reconstruct original row for audit if not provided.
    if raw_original is None:
        raw_original = " | ".join(f"{k}={v}" for k, v in row.items() if v not in (None, ""))

    return NormalizedRow(
        date=normalize_date(str(raw_date)),
        description=normalize_text(raw_desc, "description", required=False),
        amount=amount,
        currency=normalize_currency(raw_currency),
        account=str(raw_account).strip() if raw_account is not None else None,
        balance_after=normalize_optional_float(raw_balance),
        original_row=str(raw_original)[:512],
        category=str(raw_category).strip() if raw_category is not None else "",
        category_event=str(raw_category_event).strip() if raw_category_event is not None else "",
    )


# --- CSV I/O ---

def write_canonical_csv(rows: Iterable[NormalizedRow], path) -> int:
    """Write rows to a canonical CSV file. Returns number of rows written."""
    n = 0
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CANONICAL_FIELDS)
        writer.writeheader()
        for r in rows:
            d = r.to_dict()
            d["date"] = r.date.isoformat()
            d["amount"] = f"{r.amount:.2f}"
            if r.balance_after is not None:
                d["balance_after"] = f"{r.balance_after:.2f}"
            else:
                d["balance_after"] = ""
            writer.writerow(d)
            n += 1
    return n


def read_canonical_csv(path) -> list[NormalizedRow]:
    """Read a canonical CSV file into NormalizedRow objects.

    Tolerates older files that lack the `category`/`category_event` columns
    (they default to "").
    """
    rows: list[NormalizedRow] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            rows.append(
                NormalizedRow(
                    date=normalize_date(raw["date"]),
                    description=raw.get("description", ""),
                    amount=float(raw["amount"]),
                    currency=normalize_currency(raw.get("currency")),
                    account=raw.get("account") or None,
                    balance_after=normalize_optional_float(raw.get("balance_after")),
                    original_row=raw.get("original_row", ""),
                    category=(raw.get("category") or "").strip(),
                    category_event=(raw.get("category_event") or "").strip(),
                )
            )
    return rows


def canonical_csv_string(rows: Iterable[NormalizedRow]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CANONICAL_FIELDS)
    writer.writeheader()
    for r in rows:
        d = r.to_dict()
        d["date"] = r.date.isoformat()
        d["amount"] = f"{r.amount:.2f}"
        d["balance_after"] = "" if r.balance_after is None else f"{r.balance_after:.2f}"
        writer.writerow(d)
    return buf.getvalue()
