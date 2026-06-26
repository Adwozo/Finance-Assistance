"""Tests for investment account handling + investment-aware stats."""
from __future__ import annotations

import csv
from pathlib import Path

from app import database
from app.database import init_db
from app.models import Account, Transaction
from app.services.importer import import_file
from app.services.investment_service import classify, classify_for_account_type


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


def test_classify_patterns():
    assert classify("BUY AAPL 10 SH @ 150") == "buy"
    assert classify("PURCHASE TSLA") == "buy"
    assert classify("SELL MSFT 5 SH") == "sell"
    assert classify("SALE GOOG") == "sell"
    assert classify("DIVIDEND AAPL Q2") == "dividend"
    assert classify("DVD AAPL") == "dividend"
    assert classify("SWEEP INTEREST") == "interest"
    assert classify("INTEREST ON CASH") == "interest"
    assert classify("PLATFORM FEE") == "fee"
    assert classify("COMMISSION") == "fee"
    assert classify("COFFEE STARBUCKS") == ""


def test_classify_only_for_investment_accounts():
    assert classify_for_account_type("BUY AAPL", "investment") == "buy"
    # A "BUY" on a checking account (rare but possible) is NOT classified.
    assert classify_for_account_type("BUY AAPL", "checking") == ""
    assert classify_for_account_type("BUY AAPL", None) == ""


def test_imported_investment_rows_get_classified(tmp_path: Path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    # Create an investment account and a checking account first.
    with database.SessionLocal() as s:
        s.add(Account(name="IBKR", currency="HKD", institution="Interactive Brokers", type="investment"))
        s.add(Account(name="HSBC", currency="HKD", institution="HSBC", type="checking"))
        s.commit()

    stmt = tmp_path / "ibkr.csv"
    _write(stmt, ["date", "description", "amount", "currency", "account"], [
        ["2024-05-01", "BUY AAPL 10 SH", "-1500.00", "HKD", "IBKR"],
        ["2024-05-05", "SELL AAPL 5 SH", "800.00", "HKD", "IBKR"],
        ["2024-05-10", "DIVIDEND AAPL", "12.50", "HKD", "IBKR"],
        ["2024-05-12", "PLATFORM FEE", "-5.00", "HKD", "IBKR"],
        ["2024-05-15", "DEPOSIT FROM BANK", "5000.00", "HKD", "IBKR"],  # transfer leg
    ])
    with database.SessionLocal() as s:
        summary = import_file(s, stmt)

    # 5 imported; 4 classified (buy/sell/dividend/fee); the deposit is not
    # classified (no keyword) so it can still pair as a transfer.
    assert summary.imported == 5
    assert summary.classified == 4

    with database.SessionLocal() as s:
        from sqlalchemy import select
        txs = {t.description: t for t in s.scalars(select(Transaction))}
        assert txs["BUY AAPL 10 SH"].kind == "buy"
        assert txs["SELL AAPL 5 SH"].kind == "sell"
        assert txs["DIVIDEND AAPL"].kind == "dividend"
        assert txs["PLATFORM FEE"].kind == "fee"
        assert txs["DEPOSIT FROM BANK"].kind == ""


def test_bank_to_brokerage_funding_pairs_as_transfer(tmp_path: Path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    with database.SessionLocal() as s:
        s.add(Account(name="HSBC", currency="HKD", type="checking"))
        s.add(Account(name="IBKR", currency="HKD", institution="IBKR", type="investment"))
        s.commit()

    bank = tmp_path / "bank.csv"
    _write(bank, ["date", "description", "amount", "currency", "account"], [
        ["2024-06-01", "WIRE OUT TO BROKERAGE", "-5000.00", "HKD", "HSBC"],
    ])
    broker = tmp_path / "broker.csv"
    _write(broker, ["date", "description", "amount", "currency", "account"], [
        ["2024-06-02", "ACH DEPOSIT FROM BANK", "5000.00", "HKD", "IBKR"],
    ])
    with database.SessionLocal() as s:
        b = import_file(s, bank)
        br = import_file(s, broker)

    # The broker deposit has no buy/sell/dividend keyword -> kind stays '' -> pairs.
    assert br.transfers_linked == 1
    with database.SessionLocal() as s:
        from sqlalchemy import select
        transfers = list(s.scalars(select(Transaction).where(Transaction.is_transfer.is_(True))))
        assert len(transfers) == 2
        assert all(t.kind == "" for t in transfers)


def test_sell_not_paired_with_random_debit(tmp_path: Path, monkeypatch):
    """A SELL +1500 on the brokerage must NOT pair with an unrelated -1500 debit."""
    _fresh_db(tmp_path, monkeypatch)
    with database.SessionLocal() as s:
        s.add(Account(name="IBKR", currency="HKD", type="investment"))
        s.add(Account(name="HSBC", currency="HKD", type="checking"))
        s.commit()

    broker = tmp_path / "broker.csv"
    _write(broker, ["date", "description", "amount", "currency", "account"], [
        ["2024-07-01", "SELL AAPL", "1500.00", "HKD", "IBKR"],
    ])
    bank = tmp_path / "bank.csv"
    _write(bank, ["date", "description", "amount", "currency", "account"], [
        ["2024-07-01", "RENT PAYMENT", "-1500.00", "HKD", "HSBC"],
    ])
    with database.SessionLocal() as s:
        import_file(s, broker)
        bank_summary = import_file(s, bank)

    # SELL is classified -> skipped by transfer detector -> no pair.
    assert bank_summary.transfers_linked == 0
    with database.SessionLocal() as s:
        from sqlalchemy import select
        transfers = list(s.scalars(select(Transaction).where(Transaction.is_transfer.is_(True))))
        assert transfers == []
