"""Tests for the canonical normalizer."""
from __future__ import annotations

from datetime import date

import pytest

from app.services.normalizer import (
    NormalizationError,
    normalize_amount,
    normalize_date,
    normalize_row,
)


def test_normalize_amount_basic():
    assert normalize_amount("123.45") == 123.45
    assert normalize_amount("-12.34") == -12.34
    assert normalize_amount("(12.34)") == -12.34
    assert normalize_amount("$1,234.56") == 1234.56
    assert normalize_amount("1.234,56") == 1234.56
    assert normalize_amount("1,234.56") == 1234.56
    assert normalize_amount("1234") == 1234.0


def test_normalize_amount_invalid():
    with pytest.raises(NormalizationError):
        normalize_amount("")
    with pytest.raises(NormalizationError):
        normalize_amount("abc")


def test_normalize_date_variants():
    # Hong Kong convention: day-first (DD/MM/YYYY).
    assert normalize_date("2024-05-01") == date(2024, 5, 1)
    assert normalize_date("05/01/2024") == date(2024, 1, 5)
    assert normalize_date("01/05/2024") == date(2024, 5, 1)
    assert normalize_date("Jan 15, 2024") == date(2024, 1, 15)


def test_normalize_date_invalid():
    with pytest.raises(NormalizationError):
        normalize_date("")
    with pytest.raises(NormalizationError):
        normalize_date("notadate")


def test_normalize_row_debit_credit_columns():
    row = {
        "Date": "2024-05-01",
        "Description": "Coffee",
        "Debit": "4.50",
        "Credit": "",
        "Currency": "USD",
        "Account": "Checking",
    }
    n = normalize_row(row)
    assert n.date == date(2024, 5, 1)
    assert n.amount == -4.50
    assert n.currency == "USD"
    assert n.account == "Checking"
    assert n.hash() == n.hash()  # idempotent


def test_normalize_row_single_amount():
    row = {"transaction date": "2024-05-02", "memo": "Salary", "amount": "2000.00"}
    n = normalize_row(row)
    assert n.amount == 2000.0
    assert n.description == "Salary"
    assert n.currency == "HKD"  # default (Hong Kong)


def test_normalize_row_missing_amount():
    with pytest.raises(NormalizationError):
        normalize_row({"date": "2024-05-02", "description": "x"})


def test_normalized_row_hash_stable():
    row = {
        "date": "2024-05-01",
        "description": "Coffee",
        "amount": "-4.50",
        "account": "Checking",
    }
    n1 = normalize_row(row)
    n2 = normalize_row({"Date": "2024-05-01", "Description": "coffee", "Amount": "(4.50)", "Account": "checking"})
    # Same logical transaction -> same dedupe hash.
    assert n1.hash() == n2.hash()


def test_normalize_row_picks_category_fields():
    row = {
        "date": "2024-05-01",
        "description": "STARBUCKS",
        "amount": "-4.50",
        "category": "Coffee",
        "category_event": "merchant_pattern",
    }
    n = normalize_row(row)
    assert n.category == "Coffee"
    assert n.category_event == "merchant_pattern"


def test_normalize_row_category_defaults_blank():
    n = normalize_row({"date": "2024-05-01", "description": "x", "amount": "1.00"})
    assert n.category == ""
    assert n.category_event == ""


def test_canonical_csv_roundtrip_preserves_category(tmp_path):
    from app.services.normalizer import (
        canonical_csv_string,
        read_canonical_csv,
        write_canonical_csv,
    )
    rows = [
        normalize_row({
            "date": "2024-05-01", "description": "STARBUCKS", "amount": "-4.50",
            "category": "Coffee", "category_event": "merchant_pattern",
        }),
        normalize_row({"date": "2024-05-02", "description": "Salary", "amount": "2000"}),
    ]
    p = tmp_path / "canon.csv"
    assert write_canonical_csv(rows, p) == 2
    out = read_canonical_csv(p)
    assert len(out) == 2
    assert out[0].category == "Coffee"
    assert out[0].category_event == "merchant_pattern"
    assert out[1].category == ""
    assert out[1].category_event == ""
    # Header includes the new columns.
    text = canonical_csv_string(rows)
    assert "category" in text.splitlines()[0]
    assert "category_event" in text.splitlines()[0]
