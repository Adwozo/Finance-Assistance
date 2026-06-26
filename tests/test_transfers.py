"""Tests for transfer detection + stat exclusion."""
from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from app import database
from app.database import init_db
from app.models import Transaction
from app.services.importer import import_file
from app.services.transfer_service import detect_transfers, looks_like_transfer


def _write(path: Path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow(r)


def _fresh_db(tmp_path: Path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    db = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db.as_posix()}")
    from app import config
    config.get_settings.cache_clear()
    database.engine.dispose()
    settings = config.get_settings()
    monkeypatch.setattr(database, "_settings", settings)
    new_engine = create_engine(
        settings.sqlalchemy_url,
        connect_args={"check_same_thread": False},
        future=True,
    )
    monkeypatch.setattr(database, "engine", new_engine)
    monkeypatch.setattr(
        database, "SessionLocal",
        sessionmaker(bind=new_engine, autoflush=False, autocommit=False, future=True),
    )
    init_db()


def test_looks_like_transfer_keywords():
    assert looks_like_transfer("PAYMENT TO HSBC CREDIT CARD")
    assert looks_like_transfer("PAYMENT RECEIVED - THANK YOU")
    assert looks_like_transfer("TRANSFER TO SAVINGS")
    assert not looks_like_transfer("STARBUCKS")


def test_detect_transfer_pairs_bank_to_card(tmp_path: Path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    bank = tmp_path / "bank.csv"
    _write(bank, ["date", "description", "amount", "currency", "account"], [
        ["2024-05-01", "STARBUCKS", "-4.50", "HKD", "HSBC Checking"],
        ["2024-05-10", "PAYMENT TO HSBC CREDIT CARD", "-5000.00", "HKD", "HSBC Checking"],
    ])
    card = tmp_path / "card.csv"
    _write(card, ["date", "description", "amount", "currency", "account"], [
        ["2024-05-03", "NETFLIX", "-15.99", "HKD", "HSBC Credit Card"],
        ["2024-05-12", "PAYMENT RECEIVED - THANK YOU", "5000.00", "HKD", "HSBC Credit Card"],
    ])

    with database.SessionLocal() as s:
        import_file(s, bank)
        import_file(s, card)

    with database.SessionLocal() as s:
        from sqlalchemy import select
        txs = list(s.scalars(select(Transaction).order_by(Transaction.id)))
        transfers = [t for t in txs if t.is_transfer]
        assert len(transfers) == 2
        out, inc = transfers[0], transfers[1]
        assert out.amount == -5000.00
        assert inc.amount == 5000.00
        # Paired both ways.
        assert out.transfer_pair_id == inc.id
        assert inc.transfer_pair_id == out.id
        # Non-transfer rows untouched.
        non_transfer = [t for t in txs if not t.is_transfer]
        assert len(non_transfer) == 2  # starbucks + netflix


def test_detect_transfers_returns_pairs(tmp_path: Path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    bank = tmp_path / "b.csv"
    _write(bank, ["date", "description", "amount", "currency", "account"], [
        ["2024-06-01", "TRANSFER TO SAVINGS", "-1000.00", "HKD", "Checking"],
    ])
    savings = tmp_path / "s.csv"
    _write(savings, ["date", "description", "amount", "currency", "account"], [
        ["2024-06-01", "TRANSFER FROM CHECKING", "1000.00", "HKD", "Savings"],
    ])
    with database.SessionLocal() as s:
        import_file(s, bank)
        import_file(s, savings)
        # Re-running detection on already-paired rows should find 0 new pairs.
        again = detect_transfers(s)
        assert again == []


def test_transfer_amount_tolerance(tmp_path: Path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    a = tmp_path / "a.csv"
    _write(a, ["date", "description", "amount", "currency", "account"], [
        ["2024-07-01", "CARD PAYMENT", "-5000.00", "HKD", "Bank"],
    ])
    b = tmp_path / "b.csv"
    _write(b, ["date", "description", "amount", "currency", "account"], [
        ["2024-07-02", "PAYMENT RECEIVED", "4999.50", "HKD", "Card"],  # 0.50 short
    ])
    with database.SessionLocal() as s:
        import_file(s, a)
        import_file(s, b)
    with database.SessionLocal() as s:
        from sqlalchemy import select
        transfers = list(s.scalars(select(Transaction).where(Transaction.is_transfer.is_(True))))
        assert len(transfers) == 2  # within AMOUNT_TOLERANCE (1.0)


def test_unmatched_payment_not_flagged(tmp_path: Path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    a = tmp_path / "a.csv"
    _write(a, ["date", "description", "amount", "currency", "account"], [
        ["2024-08-01", "CARD PAYMENT", "-5000.00", "HKD", "Bank"],
    ])
    # No counterpart file.
    with database.SessionLocal() as s:
        summary = import_file(s, a)
    # No pair found -> 0 transfers linked.
    assert summary.transfers_linked == 0
    with database.SessionLocal() as s:
        from sqlalchemy import select
        flagged = list(s.scalars(select(Transaction).where(Transaction.is_transfer.is_(True))))
        assert flagged == []
