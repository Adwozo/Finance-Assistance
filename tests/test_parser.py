"""Tests for the parser registry + generic parser."""
from __future__ import annotations

import csv
from pathlib import Path

from app.services import parser as parser_mod
from app.services.parser import parse_file, select_parser


def _write(path: Path, header: list[str], rows: list[list[str]]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow(r)


def test_registry_has_generic(tmp_path: Path):
    assert "generic_csv" in parser_mod.available_parsers()
    assert "chase" in parser_mod.available_parsers()


def test_generic_parse(tmp_path: Path):
    p = tmp_path / "stmt.csv"
    _write(p, ["date", "description", "amount", "currency"], [
        ["2024-05-01", "Coffee", "-4.50", "USD"],
        ["2024-05-02", "Salary", "2000", "USD"],
    ])
    name, rows, warnings = parse_file(p)
    assert name == "generic_csv"
    assert len(rows) == 2
    assert rows[0]["amount"] == -4.5
    assert rows[1]["amount"] == 2000.0
    assert warnings == []


def test_filename_hint_selects_chase(tmp_path: Path):
    p = tmp_path / "chase_may.csv"
    _write(p, ["Transaction Date", "Description", "Amount", "Balance"], [
        ["2024-05-01", "Coffee", "-4.50", "1000.00"],
    ])
    name, rows, warnings = parse_file(p)
    assert name == "chase"
    assert rows[0]["amount"] == -4.5
    assert rows[0]["balance_after"] == 1000.0
    assert rows[0]["account"] == "chase_may"


def test_select_parser_unknown(tmp_path: Path):
    p = tmp_path / "unknown.csv"
    _write(p, ["date", "description", "amount"], [["2024-05-01", "x", "1.00"]])
    assert select_parser(p) == "generic_csv"
