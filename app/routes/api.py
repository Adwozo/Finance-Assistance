"""JSON API endpoints used by the HTML UI (fetch + HTMX)."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_session
from ..models import Account, Category, ImportWarning, Rule, Transaction
from ..schemas import (
    AccountIn, AccountOut, CategoryIn, CategoryOut, DashboardStats, ImportResult,
    RuleIn, RuleOut, TransactionOut, TransactionUpdate,
)
from ..services import category_service
from ..services.importer import ImportSummary, import_all, import_file, list_statement_files
from ..services.rule_engine import apply_rules_to_all

router = APIRouter(prefix="/api", tags=["api"])


# --- Accounts ---
@router.get("/accounts", response_model=list[AccountOut])
def list_accounts(session: Session = Depends(get_session)):
    return list(session.scalars(select(Account).order_by(Account.name)))


@router.post("/accounts", response_model=AccountOut, status_code=201)
def create_account(payload: AccountIn, session: Session = Depends(get_session)):
    a = Account(**payload.model_dump())
    session.add(a)
    session.commit()
    session.refresh(a)
    return a


@router.patch("/accounts/{account_id}", response_model=AccountOut)
def update_account(account_id: int, payload: dict, session: Session = Depends(get_session)):
    a = session.get(Account, account_id)
    if a is None:
        raise HTTPException(404, "account not found")
    valid = {"name", "currency", "institution", "type"}
    for k, v in payload.items():
        if k in valid and v is not None:
            setattr(a, k, v)
    session.commit()
    session.refresh(a)
    return a


@router.delete("/accounts/{account_id}", status_code=204)
def delete_account(account_id: int, session: Session = Depends(get_session)):
    a = session.get(Account, account_id)
    if a is None:
        raise HTTPException(404, "account not found")
    session.delete(a)
    session.commit()


# --- Categories ---
@router.get("/categories", response_model=list[CategoryOut])
def list_categories(session: Session = Depends(get_session)):
    return category_service.list_categories(session)


@router.get("/categories/tree")
def categories_tree(session: Session = Depends(get_session)):
    return category_service.build_tree(session)


@router.post("/categories", response_model=CategoryOut, status_code=201)
def create_category(payload: CategoryIn, session: Session = Depends(get_session)):
    c = category_service.create_category(session, **payload.model_dump())
    return c


@router.patch("/categories/{cat_id}", response_model=CategoryOut)
def update_category(cat_id: int, payload: dict, session: Session = Depends(get_session)):
    c = category_service.update_category(session, cat_id, **payload)
    if c is None:
        raise HTTPException(404, "category not found")
    return c


@router.delete("/categories/{cat_id}", status_code=204)
def delete_category(cat_id: int, session: Session = Depends(get_session)):
    if not category_service.delete_category(session, cat_id):
        raise HTTPException(404, "category not found")


# --- Rules ---
@router.get("/rules", response_model=list[RuleOut])
def list_rules(session: Session = Depends(get_session)):
    return list(session.scalars(select(Rule).order_by(Rule.priority.desc(), Rule.id)))


@router.post("/rules", response_model=RuleOut, status_code=201)
def create_rule(payload: RuleIn, session: Session = Depends(get_session)):
    r = Rule(**payload.model_dump())
    session.add(r)
    session.commit()
    session.refresh(r)
    return r


@router.delete("/rules/{rule_id}", status_code=204)
def delete_rule(rule_id: int, session: Session = Depends(get_session)):
    r = session.get(Rule, rule_id)
    if r is None:
        raise HTTPException(404, "rule not found")
    session.delete(r)
    session.commit()


@router.post("/rules/apply", response_model=dict)
def apply_rules(session: Session = Depends(get_session)):
    n = apply_rules_to_all(session)
    return {"assigned": n}


# --- Transactions ---
def _tx_to_out(tx: Transaction) -> TransactionOut:
    return TransactionOut(
        id=tx.id,
        date=tx.date,
        description=tx.description,
        amount=tx.amount,
        currency=tx.currency,
        account_id=tx.account_id,
        account_name=tx.account.name if tx.account else None,
        category_id=tx.category_id,
        category_name=tx.category.name if tx.category else None,
        balance_after=tx.balance_after,
        source_file=tx.source_file,
        is_transfer=tx.is_transfer,
        transfer_pair_id=tx.transfer_pair_id,
        kind=tx.kind,
        account_type=tx.account.type if tx.account else None,
        category_event=tx.category_event,
    )


@router.get("/transactions", response_model=list[TransactionOut])
def list_transactions(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    account_id: Optional[int] = None,
    category_id: Optional[int] = None,
    search: Optional[str] = None,
    is_transfer: Optional[bool] = None,
    limit: int = Query(500, le=5000),
    offset: int = 0,
    session: Session = Depends(get_session),
):
    q = select(Transaction).order_by(Transaction.date.desc(), Transaction.id.desc())
    if start_date:
        q = q.where(Transaction.date >= start_date)
    if end_date:
        q = q.where(Transaction.date <= end_date)
    if account_id:
        q = q.where(Transaction.account_id == account_id)
    if category_id:
        q = q.where(Transaction.category_id == category_id)
    if search:
        q = q.where(Transaction.description.ilike(f"%{search}%"))
    if is_transfer is not None:
        q = q.where(Transaction.is_transfer.is_(is_transfer))
    q = q.limit(limit).offset(offset)
    txs = list(session.scalars(q))
    return [_tx_to_out(t) for t in txs]


@router.patch("/transactions/{tx_id}", response_model=TransactionOut)
def update_transaction(tx_id: int, payload: TransactionUpdate,
                       session: Session = Depends(get_session)):
    tx = session.get(Transaction, tx_id)
    if tx is None:
        raise HTTPException(404, "transaction not found")
    if payload.category_id is not None:
        tx.category_id = payload.category_id or None
    if payload.description is not None:
        tx.description = payload.description
    if payload.is_transfer is not None:
        from ..services.transfer_service import mark_transfer, unmark_transfer
        if payload.is_transfer:
            mark_transfer(session, tx_id)
        else:
            unmark_transfer(session, tx_id)
        tx = session.get(Transaction, tx_id)  # reload after transfer mutation
    session.commit()
    session.refresh(tx)

    # Optionally write the edit back to the source CSV file.
    if payload.save_to_csv and tx.source_file:
        from ..services.csv_sync import sync_file
        try:
            sync_file(session, tx.source_file)
        except FileNotFoundError:
            pass  # source file removed; edit still persisted in DB

    return _tx_to_out(tx)


@router.delete("/transactions/{tx_id}", status_code=204)
def delete_transaction(tx_id: int, session: Session = Depends(get_session)):
    tx = session.get(Transaction, tx_id)
    if tx is None:
        raise HTTPException(404, "transaction not found")
    # Unlink a transfer counterpart before deleting.
    if tx.transfer_pair_id is not None:
        pair = session.get(Transaction, tx.transfer_pair_id)
        if pair is not None:
            pair.is_transfer = False
            pair.transfer_pair_id = None
    session.delete(tx)
    session.commit()


# --- Transfers ---
@router.post("/transfers/detect", response_model=dict)
def detect_transfers_endpoint(session: Session = Depends(get_session)):
    """Scan all transactions for transfer pairs and link them."""
    from ..services.transfer_service import detect_transfers
    pairs = detect_transfers(session)
    return {"linked": len(pairs)}


@router.get("/transfers", response_model=list[TransactionOut])
def list_transfers(session: Session = Depends(get_session)):
    txs = list(session.scalars(
        select(Transaction).where(Transaction.is_transfer.is_(True))
        .order_by(Transaction.date.desc())
    ))
    return [_tx_to_out(t) for t in txs]


# --- Investments ---
@router.post("/investments/classify", response_model=dict)
def classify_investments_endpoint(session: Session = Depends(get_session)):
    """Re-classify all investment-account transactions (buy/sell/dividend/...)."""
    from ..services.investment_service import classify_transactions
    n = classify_transactions(session)
    return {"classified": n}


@router.get("/investments/summary")
def investments_summary(session: Session = Depends(get_session)):
    """Portfolio summary: net cash flow + buys/sells/dividends/fees this month."""
    from datetime import date as _date
    today = _date.today()
    month_start = today.replace(day=1)

    def _sum(kind: str, sign_op) -> float:
        # sign_op: 'neg' for amount<0, 'pos' for amount>0
        cond = Transaction.amount < 0 if sign_op == "neg" else Transaction.amount > 0
        return float(session.scalar(
            select(func.coalesce(func.sum(Transaction.amount), 0.0))
            .where(Transaction.kind == kind)
            .where(Transaction.date >= month_start)
            .where(cond)
        ) or 0.0)

    # Portfolio "value" proxied by sum of all amounts on investment accounts.
    inv_total = float(session.scalar(
        select(func.coalesce(func.sum(Transaction.amount), 0.0))
        .join(Account, Transaction.account_id == Account.id)
        .where(Account.type == "investment")
    ) or 0.0)

    return {
        "portfolio_value": inv_total,
        "this_month": {
            "buys": abs(_sum("buy", "neg")),
            "sells": _sum("sell", "pos"),
            "dividends": _sum("dividend", "pos"),
            "interest": _sum("interest", "pos"),
            "fees": abs(_sum("fee", "neg")),
        },
    }


# --- Import ---
@router.get("/import/files")
def list_import_files():
    return [p.name for p in list_statement_files()]


@router.post("/import", response_model=list[ImportResult])
def trigger_import(format_hint: Optional[str] = None, session: Session = Depends(get_session)):
    summaries = import_all(session, format_hint=format_hint)
    return [
        ImportResult(file=s.file, parser=s.parser, imported=s.imported,
                     skipped=s.skipped, transfers_linked=s.transfers_linked,
                     classified=s.classified, categories_created=s.categories_created,
                     warnings=s.warnings)
        for s in summaries
    ]


@router.post("/import/{filename}", response_model=ImportResult)
def trigger_import_one(filename: str, format_hint: Optional[str] = None,
                       session: Session = Depends(get_session)):
    from ..config import get_settings
    path = get_settings().statements_path / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(404, f"file not found: {filename}")
    s = import_file(session, path, format_hint=format_hint)
    return ImportResult(file=s.file, parser=s.parser, imported=s.imported,
                        skipped=s.skipped, transfers_linked=s.transfers_linked,
                        classified=s.classified, categories_created=s.categories_created,
                        warnings=s.warnings)


# --- Dashboard stats ---
@router.get("/stats", response_model=DashboardStats)
def dashboard_stats(session: Session = Depends(get_session)):
    today = date.today()
    month_start = today.replace(day=1)

    # Total balance = sum of all amounts INCLUDING transfers (transfers are
    # movements between your own accounts, so net worth is unchanged) and
    # including investment buys/sells (cash->asset swap, net worth unchanged).
    total_balance = float(
        session.scalar(select(func.coalesce(func.sum(Transaction.amount), 0.0))) or 0.0
    )

    # "Real" spending / income excludes:
    #   - transfers (is_transfer)           : moving money between your accounts
    #   - kind in ('buy','sell')            : exchanging cash for securities (asset swap)
    # Dividends ('dividend') and interest ('interest') DO count as income.
    # Brokerage fees ('fee') DO count as spending.
    asset_swap = Transaction.kind.in_(("buy", "sell"))

    spending = float(
        session.scalar(
            select(func.coalesce(func.sum(Transaction.amount), 0.0))
            .where(Transaction.date >= month_start)
            .where(Transaction.amount < 0)
            .where(Transaction.is_transfer.is_(False))
            .where(~asset_swap)
        )
    ) or 0.0

    income = float(
        session.scalar(
            select(func.coalesce(func.sum(Transaction.amount), 0.0))
            .where(Transaction.date >= month_start)
            .where(Transaction.amount > 0)
            .where(Transaction.is_transfer.is_(False))
            .where(~asset_swap)
        )
    ) or 0.0

    # Spending by category (this month, excluding transfers + asset swaps)
    rows = session.execute(
        select(Category.name, Category.color, func.sum(Transaction.amount))
        .join(Transaction, Transaction.category_id == Category.id)
        .where(Transaction.date >= month_start)
        .where(Transaction.amount < 0)
        .where(Transaction.is_transfer.is_(False))
        .where(~asset_swap)
        .group_by(Category.id, Category.name, Category.color)
        .order_by(func.sum(Transaction.amount).asc())
    ).all()
    spending_by_category = [
        {"name": name, "color": color, "amount": float(total or 0)}
        for name, color, total in rows
    ]

    # Monthly trend (last 6 months, excluding transfers + asset swaps)
    months: list[dict] = []
    for i in range(5, -1, -1):
        m_start = month_start.replace(year=today.year - (1 if today.month - i <= 0 else 0),
                                       month=((today.month - i - 1) % 12) + 1)
        next_m = month_start.replace(year=today.year - (1 if today.month - i + 1 <= 0 else 0),
                                      month=((today.month - i) % 12) + 1)
        inc = float(session.scalar(
            select(func.coalesce(func.sum(Transaction.amount), 0.0))
            .where(Transaction.date >= m_start).where(Transaction.date < next_m)
            .where(Transaction.amount > 0)
            .where(Transaction.is_transfer.is_(False))
            .where(~asset_swap)
        ) or 0.0)
        exp = float(session.scalar(
            select(func.coalesce(func.sum(Transaction.amount), 0.0))
            .where(Transaction.date >= m_start).where(Transaction.date < next_m)
            .where(Transaction.amount < 0)
            .where(Transaction.is_transfer.is_(False))
            .where(~asset_swap)
        ) or 0.0)
        months.append({
            "month": m_start.strftime("%b %Y"),
            "income": inc,
            "spending": abs(exp),
            "net": inc + exp,
        })

    return DashboardStats(
        total_balance=total_balance,
        spending_this_month=abs(spending),
        income_this_month=income,
        net_this_month=income + spending,
        spending_by_category=spending_by_category,
        monthly_trend=months,
    )


# --- Spending analysis (drill-down page) ---
# Excludes transfers (is_transfer) and investment asset swaps
# (kind in buy/sell) from "spending", consistent with the dashboard.
_ASSET_SWAP = Transaction.kind.in_(("buy", "sell"))


def _spending_filter(q, start_date, end_date, account_id, exclude_income=True):
    if start_date:
        q = q.where(Transaction.date >= start_date)
    if end_date:
        q = q.where(Transaction.date <= end_date)
    if account_id:
        q = q.where(Transaction.account_id == account_id)
    q = q.where(Transaction.is_transfer.is_(False))
    q = q.where(~_ASSET_SWAP)
    if exclude_income:
        q = q.where(Transaction.amount < 0)
    return q


@router.get("/spending/breakdown")
def spending_breakdown(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    account_id: Optional[int] = None,
    session: Session = Depends(get_session),
):
    """Spending aggregated by category for the given filter window.

    Returns a list of {category_id, name, color, total, count, avg, pct} sorted
    by total spend (most negative first). `pct` is the share of total spending.
    Also includes a `total` field at the top level and an `uncategorized` bucket.
    """
    rows = session.execute(
        _spending_filter(
            select(
                Category.id, Category.name, Category.color,
                func.sum(Transaction.amount).label("total"),
                func.count(Transaction.id).label("cnt"),
            ).join(Transaction, Transaction.category_id == Category.id, isouter=True),
            start_date, end_date, account_id,
        ).group_by(Category.id, Category.name, Category.color)
         .order_by(func.sum(Transaction.amount).asc())
    ).all()

    buckets = []
    grand_total = 0.0
    for cat_id, name, color, total, cnt in rows:
        amt = float(total or 0)
        if amt == 0:
            continue
        grand_total += abs(amt)
        buckets.append({
            "category_id": cat_id,
            "name": name or "Uncategorized",
            "color": color or "#94a3b8",
            "total": amt,           # negative
            "abs_total": abs(amt),  # positive, for display
            "count": int(cnt or 0),
            "avg": abs(amt) / float(cnt) if cnt else 0.0,
        })

    # Attach percentage shares.
    for b in buckets:
        b["pct"] = (b["abs_total"] / grand_total * 100.0) if grand_total else 0.0

    return {
        "total": grand_total,
        "categories": buckets,
        "start_date": start_date.isoformat() if start_date else None,
        "end_date": end_date.isoformat() if end_date else None,
        "account_id": account_id,
    }


@router.get("/spending/category/{category_id}")
def spending_category_detail(
    category_id: int,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    account_id: Optional[int] = None,
    limit: int = Query(500, le=5000),
    session: Session = Depends(get_session),
):
    """Detail for a single category in the filter window: summary + transaction list.

    If `category_id` is 0, returns the Uncategorized bucket (category_id IS NULL).
    """
    base = _spending_filter(
        select(Transaction), start_date, end_date, account_id,
    )
    if category_id == 0:
        q = base.where(Transaction.category_id.is_(None))
    else:
        q = base.where(Transaction.category_id == category_id)

    txs = list(session.scalars(q.order_by(Transaction.date.desc(), Transaction.id.desc()).limit(limit)))
    total = float(session.scalar(
        _spending_filter(
            select(func.coalesce(func.sum(Transaction.amount), 0.0)),
            start_date, end_date, account_id,
        ).where(
            Transaction.category_id.is_(None) if category_id == 0
            else Transaction.category_id == category_id
        )
    ) or 0.0)
    count = int(session.scalar(
        _spending_filter(
            select(func.count(Transaction.id)),
            start_date, end_date, account_id,
        ).where(
            Transaction.category_id.is_(None) if category_id == 0
            else Transaction.category_id == category_id
        )
    ) or 0)

    return {
        "category_id": category_id,
        "total": total,
        "abs_total": abs(total),
        "count": count,
        "avg": abs(total) / float(count) if count else 0.0,
        "transactions": [_tx_to_out(t) for t in txs],
    }


@router.get("/spending/trend")
def spending_trend(
    category_id: Optional[int] = None,
    account_id: Optional[int] = None,
    months: int = Query(6, ge=1, le=36),
    session: Session = Depends(get_session),
):
    """Monthly spending trend for the last N months.

    If `category_id` is provided (0 = Uncategorized), the trend is scoped to
    that category; otherwise it's overall spending.
    """
    today = date.today()
    out: list[dict] = []
    for i in range(months - 1, -1, -1):
        m = (today.month - i - 1) % 12 + 1
        y = today.year - (1 if today.month - i <= 0 else 0)
        m_start = date(y, m, 1)
        next_m = date(y + (1 if m == 12 else 0), (m % 12) + 1, 1)

        q = _spending_filter(
            select(func.coalesce(func.sum(Transaction.amount), 0.0)),
            m_start, None, account_id,
        ).where(Transaction.date < next_m)
        if category_id is not None:
            if category_id == 0:
                q = q.where(Transaction.category_id.is_(None))
            else:
                q = q.where(Transaction.category_id == category_id)
        amt = float(session.scalar(q) or 0.0)
        out.append({
            "month": m_start.strftime("%b %Y"),
            "spending": abs(amt),
        })
    return out


# --- Action needed + CSV save-back ---
@router.get("/action/needed")
def action_needed(session: Session = Depends(get_session)):
    """Transactions and rows that need user attention, in three groups:

    1. `uncategorized`: transactions with no category_id.
    2. `uncertain`:     transactions whose category_event == 'uncertain'
                        (the converter couldn't guess — user should review).
    3. `skipped`:       rows the importer could not parse (from ImportWarning),
                        which never entered the DB and need the CSV fixed.

    Returns counts for each group plus the rows themselves.
    """
    # 1) Uncategorized transactions.
    uncategorized = list(session.scalars(
        select(Transaction).where(Transaction.category_id.is_(None))
        .order_by(Transaction.date.desc())
    ))

    # 2) Uncertain-event transactions (converter gave up).
    uncertain = list(session.scalars(
        select(Transaction).where(Transaction.category_event == "uncertain")
        .order_by(Transaction.date.desc())
    ))

    # 3) Skipped rows (never imported).
    skipped = list(session.scalars(
        select(ImportWarning).order_by(ImportWarning.source_file, ImportWarning.line_number)
    ))

    return {
        "counts": {
            "uncategorized": len(uncategorized),
            "uncertain": len(uncertain),
            "skipped": len(skipped),
            "total": len(uncategorized) + len(uncertain) + len(skipped),
        },
        "uncategorized": [_tx_to_out(t) for t in uncategorized],
        "uncertain": [_tx_to_out(t) for t in uncertain],
        "skipped": [
            {
                "id": w.id,
                "source_file": w.source_file,
                "line_number": w.line_number,
                "raw_line": w.raw_line,
                "reason": w.reason,
            }
            for w in skipped
        ],
    }


@router.post("/csv/sync")
def csv_sync_all(session: Session = Depends(get_session)):
    """Rewrite every statement CSV in statements/ from current DB state.

    For each row: if a matching Transaction exists, its category/category_event/
    description are written back; otherwise the original row is preserved
    (skipped/malformed rows stay verbatim). A .bak backup is created per file.
    """
    from ..services.csv_sync import sync_all
    results = sync_all(session)
    return {
        "files": [
            {
                "file": r.file,
                "rows_total": r.rows_total,
                "rows_updated": r.rows_updated,
                "rows_preserved": r.rows_preserved,
                "backup_path": r.backup_path,
            }
            for r in results
        ],
        "total_updated": sum(r.rows_updated for r in results),
    }


@router.post("/csv/sync/{filename}")
def csv_sync_one(filename: str, session: Session = Depends(get_session)):
    """Sync a single source CSV file from DB state."""
    from ..services.csv_sync import sync_file
    try:
        r = sync_file(session, filename)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    return {
        "file": r.file,
        "rows_total": r.rows_total,
        "rows_updated": r.rows_updated,
        "rows_preserved": r.rows_preserved,
        "backup_path": r.backup_path,
    }
