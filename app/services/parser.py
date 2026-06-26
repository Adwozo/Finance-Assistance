"""Pluggable statement parser registry.

Each parser is a callable `(reader: csv.DictReader, path: Path) -> Iterable[dict]`
yielding raw row dicts that `normalizer.normalize_row` can consume.

Add a new bank by defining a function decorated with `@register("bank_name")`.
The importer picks a parser via `select_parser(path)`:
  1. explicit `format_hint` if provided
  2. filename hint (e.g. `chase_*.csv`)
  3. header heuristics (`detect_parser`)
  4. fallback to `generic_csv`
"""
from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Callable, Iterable, Optional

from .normalizer import NormalizationError, normalize_row

Parser = Callable[[csv.DictReader, Path], Iterable[dict]]

_PARSERS: dict[str, Parser] = {}


def register(name: str) -> Callable[[Parser], Parser]:
    def deco(fn: Parser) -> Parser:
        _PARSERS[name] = fn
        return fn
    return deco


def available_parsers() -> list[str]:
    return sorted(_PARSERS.keys())


def get_parser(name: str) -> Parser:
    if name not in _PARSERS:
        raise KeyError(f"unknown parser: {name!r} (available: {available_parsers()})")
    return _PARSERS[name]


def detect_parser(path: Path, sample_rows: int = 5) -> str:
    """Heuristically pick a parser by inspecting the header line."""
    header = _peek_header(path)
    h = {x.lower().strip() for x in header}
    for name, fn in _PARSERS.items():
        if name == "generic_csv":
            continue
        hints = getattr(fn, "_detect_headers", None)
        if hints and hints.issubset(h):
            return name
    return "generic_csv"


def select_parser(path: Path, format_hint: Optional[str] = None) -> str:
    if format_hint and format_hint in _PARSERS:
        return format_hint
    stem = path.stem.lower()
    for name in _PARSERS:
        if name == "generic_csv":
            continue
        if name in stem:
            return name
    return detect_parser(path)


def _peek_header(path: Path) -> list[str]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        # Skip blank lines.
        for line in f:
            if line.strip():
                # Use csv to handle quoted headers correctly.
                f.seek(0)
                reader = csv.reader(f)
                try:
                    return next(reader)
                except StopIteration:
                    return []
    return []


def _open_reader(path: Path) -> tuple[csv.DictReader, "object"]:
    f = open(path, newline="", encoding="utf-8-sig")
    return csv.DictReader(f), f


def parse_file(path: Path, format_hint: Optional[str] = None) -> tuple[str, list[dict], list[dict]]:
    """Parse a statement file into a list of raw canonical dicts.

    Returns (parser_name, rows, warnings) where each warning is a dict with
    keys: line_number, raw_line, reason. This lets the importer persist skipped
    rows to the ImportWarning table so the Action page can surface them.
    """
    name = select_parser(path, format_hint)
    parser = get_parser(name)
    warnings: list[dict] = []
    rows: list[dict] = []
    reader, f = _open_reader(path)
    line_number = 1  # header line
    try:
        for raw in parser(reader, path):
            line_number += 1
            try:
                n = normalize_row(raw)
            except NormalizationError as e:
                warnings.append({
                    "line_number": line_number,
                    "raw_line": _stringify_raw(raw),
                    "reason": str(e),
                })
                continue
            rows.append(n.to_dict())
    finally:
        f.close()
    return name, rows, warnings


def _stringify_raw(raw: dict) -> str:
    """Best-effort single-line representation of a raw row for audit."""
    try:
        return " | ".join(f"{k}={v}" for k, v in raw.items() if v not in (None, ""))[:1024]
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Built-in parsers
# ---------------------------------------------------------------------------

@register("generic_csv")
def parse_generic(reader: csv.DictReader, path: Path) -> Iterable[dict]:
    """Generic CSV: pass rows through; rely on normalize_row's alias matching."""
    for row in reader:
        yield row


@register("chase")
def parse_chase(reader: csv.DictReader, path: Path) -> Iterable[dict]:
    """Chase bank statement: columns 'Transaction Date','Description','Amount','Balance'.

    Output rows use canonical aliases that normalize_row understands.
    """
    for row in reader:
        yield {
            "date": row.get("Transaction Date") or row.get("Date"),
            "description": row.get("Description"),
            "amount": row.get("Amount"),
            "balance_after": row.get("Balance"),
            "account": path.stem,
        }
parse_chase._detect_headers = {"transaction date", "description", "amount"}  # type: ignore[attr-defined]


@register("amazon")
def parse_amazon(reader: csv.DictReader, path: Path) -> Iterable[dict]:
    """Amazon transactions CSV: 'Date','Description','Amount','Balance'."""
    for row in reader:
        yield {
            "date": row.get("Date"),
            "description": row.get("Description"),
            "amount": row.get("Amount"),
            "account": "Amazon",
        }
parse_amazon._detect_headers = {"date", "description", "amount", "balance"}  # type: ignore[attr-defined]


@register("amex")
def parse_amex(reader: csv.DictReader, path: Path) -> Iterable[dict]:
    """Amex statement: 'Date','Description','Amount'."""
    for row in reader:
        yield {
            "date": row.get("Date"),
            "description": row.get("Description"),
            "amount": row.get("Amount"),
            "account": "Amex",
        }
parse_amex._detect_headers = {"date", "description", "amount", "card member"}  # type: ignore[attr-defined]
