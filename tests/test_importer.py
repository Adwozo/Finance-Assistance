"""Tests for the importer (dedupe by hash)."""
from __future__ import annotations

import csv
from pathlib import Path

from app import database
from app.database import init_db
from app.models import Transaction
from app.services.importer import import_all, import_file


def _write(path: Path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow(r)


def _fresh_db(tmp_path: Path, monkeypatch):
    import importlib
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Point DB at a temp file and re-init.
    db = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db.as_posix()}")
    # Reset cached settings + engine.
    from app import config, database
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
        database,
        "SessionLocal",
        sessionmaker(bind=new_engine, autoflush=False, autocommit=False, future=True),
    )
    init_db()


def test_import_dedupes(tmp_path: Path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    stmt = tmp_path / "stmt.csv"
    _write(stmt, ["date", "description", "amount", "currency", "account"], [
        ["2024-05-01", "Coffee", "-4.50", "USD", "Checking"],
        ["2024-05-02", "Salary", "2000", "USD", "Checking"],
    ])

    with database.SessionLocal() as s:
        first = import_file(s, stmt)
        assert first.imported == 2
        assert first.skipped == 0

    with database.SessionLocal() as s:
        second = import_file(s, stmt)
        assert second.imported == 0
        assert second.skipped == 2

    with database.SessionLocal() as s:
        count = s.query(Transaction).count()
        assert count == 2


def test_import_all_scans_folder(tmp_path: Path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    folder = tmp_path / "statements"
    folder.mkdir()
    _write(folder / "a.csv", ["date", "description", "amount"], [
        ["2024-05-01", "A", "1.00"],
    ])
    _write(folder / "b.csv", ["date", "description", "amount"], [
        ["2024-05-02", "B", "2.00"],
    ])
    with database.SessionLocal() as s:
        summaries = import_all(s, folder=folder)
        assert len(summaries) == 2
        total = sum(m.imported for m in summaries)
        assert total == 2


def test_import_maps_converter_category(tmp_path: Path, monkeypatch):
    """The converter's `category`+`category_event` columns are mapped to a
    Category row (created on demand) and stored on the Transaction."""
    from app.models import Category

    _fresh_db(tmp_path, monkeypatch)
    stmt = tmp_path / "stmt.csv"
    _write(stmt, ["date", "description", "amount", "currency", "account",
                  "category", "category_event"], [
        ["2024-05-01", "STARBUCKS", "-4.50", "HKD", "Checking",
         "Coffee", "merchant_pattern"],
        ["2024-05-02", "Mystery vendor", "-10.00", "HKD", "Checking",
         "", "uncertain"],
    ])

    with database.SessionLocal() as s:
        summary = import_file(s, stmt)
        assert summary.imported == 2
        assert summary.categories_created == 1  # "Coffee" auto-created

    with database.SessionLocal() as s:
        txs = s.query(Transaction).order_by(Transaction.id).all()
        # First row mapped to the auto-created "Coffee" category.
        coffees = s.query(Category).filter(Category.name == "Coffee").all()
        assert len(coffees) == 1
        assert txs[0].category_id == coffees[0].id
        assert txs[0].category_event == "merchant_pattern"
        # Second row left uncategorized; event stored as "uncertain".
        assert txs[1].category_id is None
        assert txs[1].category_event == "uncertain"
