"""Tests for the built-in category classifier (statement->CSV step)."""
from __future__ import annotations

from datetime import date

from mcp_server.category_classifier import classify


def test_merchant_pattern_coffee():
    g = classify("STARBUCKS STORE #123", -4.50)
    assert g.category == "Coffee"
    assert g.event == "merchant_pattern"


def test_merchant_pattern_salary():
    g = classify("ACME CORP PAYROLL", 2000.00)
    assert g.category == "Income"
    assert g.event == "merchant_pattern"


def test_merchant_pattern_case_insensitive():
    g = classify("mtr octopus topup", -50.00)
    assert g.category == "Transport"


def test_lunar_new_year_dining():
    # 2024-02-10 falls in the 2024 CNY window.
    g = classify("family reunion dinner", -800.00, date(2024, 2, 10))
    assert g.category == "Dining"
    assert g.event == "lunar_new_year"


def test_lunar_new_year_lai_see():
    g = classify("lai see red packet", -200.00, date(2025, 1, 29))
    assert g.category == "Gifts"
    assert g.event == "lunar_new_year"


def test_christmas_gift():
    g = classify("christmas gift for mom", -300.00, date(2024, 12, 20))
    assert g.category == "Gifts"
    assert g.event == "christmas"


def test_payday_salary():
    # A salary credit (matches Income merchant pattern) landing on the 25th
    # (a payday) -> the event label is upgraded from "merchant_pattern" to
    # "payday", category stays "Income".
    g = classify("ACME CORP SALARY", 15000.00, date(2024, 5, 25))
    assert g.category == "Income"
    assert g.event == "payday"


def test_salary_off_payday_uses_merchant_pattern():
    # Same salary credit but mid-month -> stays "merchant_pattern".
    g = classify("ACME CORP SALARY", 15000.00, date(2024, 5, 14))
    assert g.category == "Income"
    assert g.event == "merchant_pattern"


def test_amount_heuristic_income():
    # Round positive >= 1000 with a "deposit" keyword but phrased so it does
    # NOT match the Transfer merchant pattern ("deposit from") and does NOT
    # match the Income merchant pattern (salary/payroll/wages/bonus). The
    # amount heuristic explicitly checks for "deposit".
    g = classify("employer deposit", 5000.00)
    assert g.category == "Income"
    assert g.event == "amount_heuristic"


def test_amount_heuristic_refund():
    g = classify("store refund", 49.99)
    assert g.category == "Refund"
    assert g.event == "amount_heuristic"


def test_uncertain_when_no_match():
    g = classify("acme xyz vendor 9999", -12.34)
    assert g.category == ""
    assert g.event == "uncertain"


def test_empty_description():
    g = classify("", -10.00)
    assert g.category == ""
    assert g.event == "uncertain"


def test_merchant_pattern_beats_event():
    # "salary" would match payday, but "mtr" merchant pattern wins (first layer).
    g = classify("mtr salary deduction", -50.00, date(2024, 5, 25))
    assert g.category == "Transport"
    assert g.event == "merchant_pattern"


def test_event_requires_date_in_window():
    # Same description but date outside CNY window -> falls through to uncertain
    # (no merchant match, no amount heuristic).
    g = classify("family reunion dinner", -800.00, date(2024, 6, 15))
    assert g.event == "uncertain"
