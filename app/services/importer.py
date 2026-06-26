"""Importer: scan the statements folder, parse, dedupe by hash, persist."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Account, Category, ImportWarning, Transaction
from . import parser as parser_mod
from .normalizer import NormalizedRow
from .rule_engine import apply_rules_for_import
from .transfer_service import detect_transfers


SUPPORTED_EXT = {".csv", ".txt"}


@dataclass
class ImportSummary:
    file: str
    parser: str
    imported: int
    skipped: int
    transfers_linked: int = 0
    classified: int = 0
    categories_created: int = 0
    warnings: list[str] = field(default_factory=list)


def list_statement_files(folder: Optional[Path] = None) -> list[Path]:
    folder = folder or get_settings().statements_path
    if not folder.exists():
        return []
    return sorted(
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXT
    )


def _ensure_account(session: Session, name: Optional[str], currency: str) -> Optional[Account]:
    if not name:
        return None
    account = session.scalar(select(Account).where(Account.name == name))
    if account is None:
        account = Account(name=name, currency=currency)
        session.add(account)
        session.flush()
    return account


def _resolve_category(session: Session, category_text: str) -> tuple[Optional[int], bool]:
    """Map a converter-supplied category text to a Category row id.

    Case-insensitive match against existing category names; if no match, create
    a new top-level Category (color grey) so the guess is preserved. Returns
    (category_id, created). Empty text -> (None, False).
    """
    text = (category_text or "").strip()
    if not text:
        return None, False
    cat = session.scalar(select(Category).where(func.lower(Category.name) == text.lower()))
    if cat is None:
        cat = Category(name=text, color="#64748b")  # "Other" grey for auto-created
        session.add(cat)
        session.flush()
        return cat.id, True
    return cat.id, False


def import_file(
    session: Session,
    path: Path,
    format_hint: Optional[str] = None,
) -> ImportSummary:
    name, rows, warnings = parser_mod.parse_file(path, format_hint=format_hint)
    imported = 0
    skipped = 0
    categories_created = 0

    # Persist skipped-row warnings so the Action page can surface them. Clear
    # any prior warnings for this file first (a re-import supersedes them).
    from sqlalchemy import delete as _delete
    session.execute(_delete(ImportWarning).where(ImportWarning.source_file == path.name))
    warning_strings: list[str] = []
    for w in warnings:
        session.add(ImportWarning(
            source_file=path.name,
            line_number=w.get("line_number"),
            raw_line=(w.get("raw_line") or "")[:1024],
            reason=w.get("reason", ""),
        ))
        warning_strings.append(
            f"line {w.get('line_number')}: {w.get('reason', '')}"
        )

    # Preload existing hashes for this batch to avoid per-row queries.
    candidate_hashes = [NormalizedRow(
        date=r["date"],
        description=r["description"],
        amount=r["amount"],
        currency=r["currency"],
        account=r["account"],
        balance_after=r["balance_after"],
        original_row=r["original_row"],
        category=r.get("category", ""),
        category_event=r.get("category_event", ""),
    ).hash() for r in rows]
    existing = set(
        session.scalars(
            select(Transaction.hash).where(Transaction.hash.in_(candidate_hashes))
        ).all()
    )

    just_imported: list[Transaction] = []
    for r, h in zip(rows, candidate_hashes):
        if h in existing:
            skipped += 1
            continue
        account = _ensure_account(session, r.get("account"), r.get("currency", get_settings().default_currency))
        # Map the converter's category text -> Category row (case-insensitive,
        # create-on-demand if it doesn't match an existing category). This is
        # the "intelligent" category guess baked into the statement->CSV step.
        category_id, category_created = _resolve_category(
            session, r.get("category", "")
        )
        tx = Transaction(
            date=r["date"] if isinstance(r["date"], date) else date.fromisoformat(r["date"]),
            description=r["description"],
            amount=r["amount"],
            currency=r["currency"],
            account_id=account.id if account else None,
            category_id=category_id,
            balance_after=r.get("balance_after"),
            original_row=r.get("original_row"),
            source_file=path.name,
            hash=h,
            category_event=(r.get("category_event") or "").strip(),
        )
        session.add(tx)
        existing.add(h)
        imported += 1
        if category_created:
            categories_created += 1
        just_imported.append(tx)

    # Apply user rules only to rows the converter did NOT categorize — the
    # converter's intelligent guess takes precedence over generic rules.
    uncategorized = [t for t in just_imported if t.category_id is None]
    if uncategorized:
        apply_rules_for_import(session, uncategorized)
    session.commit()

    # After commit, freshly imported rows have ids. Classify investment
    # transactions FIRST (buys/sells/dividends/fees) so the transfer detector
    # doesn't try to pair e.g. a SELL +1500 with a random -1500 debit.
    new_ids = [t.id for t in just_imported]
    classified = 0
    if new_ids:
        from .investment_service import classify_transactions
        classified = classify_transactions(session, only_ids=new_ids)

    # Then run transfer detection scoped to the freshly imported rows so
    # card-payment / brokerage-funding legs pair with existing rows.
    transfers_linked = 0
    if new_ids:
        pairs = detect_transfers(session, only_ids=new_ids)
        transfers_linked = len(pairs)

    return ImportSummary(
        file=path.name,
        parser=name,
        imported=imported,
        skipped=skipped,
        transfers_linked=transfers_linked,
        classified=classified,
        categories_created=categories_created,
        warnings=warning_strings,
    )


def import_all(
    session: Session,
    folder: Optional[Path] = None,
    format_hint: Optional[str] = None,
) -> list[ImportSummary]:
    return [
        import_file(session, p, format_hint=format_hint)
        for p in list_statement_files(folder)
    ]
