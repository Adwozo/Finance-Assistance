"""ORM models. Generic types only — SQLite + PostgreSQL compatible."""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="HKD")
    institution: Mapped[Optional[str]] = mapped_column(String(120))
    # Account type drives how transactions on this account are counted in stats:
    #   checking / savings      -> normal: debits=spending, credits=income
    #   credit_card             -> normal (payments to it are transfers)
    #   investment              -> buys/sells are asset swaps (excluded from
    #                              spending & income); dividends/interest still
    #                              count as income; cash deposits/withdrawals
    #                              are transfers
    type: Mapped[str] = mapped_column(String(20), nullable=False, default="checking", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

    transactions: Mapped[list["Transaction"]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Account id={self.id} name={self.name!r}>"


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    parent_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), nullable=True
    )
    color: Mapped[str] = mapped_column(String(9), default="#6b7280")
    icon: Mapped[Optional[str]] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

    parent: Mapped[Optional["Category"]] = relationship(
        remote_side="Category.id", back_populates="children"
    )
    children: Mapped[list["Category"]] = relationship(
        back_populates="parent", cascade="all, delete-orphan"
    )
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="category")

    def __repr__(self) -> str:
        return f"<Category id={self.id} name={self.name!r}>"


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="HKD")
    account_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    category_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), nullable=True, index=True
    )
    balance_after: Mapped[Optional[float]] = mapped_column(Float)
    original_row: Mapped[Optional[str]] = mapped_column(String(512))
    source_file: Mapped[Optional[str]] = mapped_column(String(255))
    hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    # Transfer handling: a payment from account A paired with a receipt on
    # account B (e.g. bank -> credit-card payment). Transfers are excluded from
    # spending/income stats but kept in the transactions list and in total balance.
    is_transfer: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    transfer_pair_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True
    )
    transfer_pair: Mapped[Optional["Transaction"]] = relationship(
        remote_side="Transaction.id", post_update=True
    )
    # Investment classification (only relevant when account.type == 'investment'):
    #   ''          -> generic brokerage cash movement (deposit/withdrawal/fee)
    #   'buy'       -> security purchase (asset swap, excluded from spending)
    #   'sell'      -> security sale (asset swap, excluded from income)
    #   'dividend'  -> dividend received (counts as income)
    #   'interest'  -> interest on cash/sweep (counts as income)
    #   'fee'       -> brokerage fee (counts as spending)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="", index=True)
    # Event estimation that drove the converter's category guess (carried from
    # the canonical CSV's `category_event` column). Examples: "merchant_pattern",
    # "lunar_new_year", "christmas", "payday", "uncertain", "".
    category_event: Mapped[str] = mapped_column(String(32), nullable=False, default="", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

    account: Mapped[Optional[Account]] = relationship(back_populates="transactions")
    category: Mapped[Optional[Category]] = relationship(back_populates="transactions")

    def __repr__(self) -> str:
        return f"<Transaction id={self.id} date={self.date} amount={self.amount}>"


class Rule(Base):
    __tablename__ = "rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    field: Mapped[str] = mapped_column(String(32), nullable=False)  # description | account | amount
    pattern: Mapped[str] = mapped_column(String(255), nullable=False)  # substring or regex
    is_regex: Mapped[bool] = mapped_column(Boolean, default=False)
    category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.id", ondelete="CASCADE"), nullable=False
    )
    priority: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

    category: Mapped[Category] = relationship()

    def __repr__(self) -> str:
        return f"<Rule id={self.id} field={self.field!r} pattern={self.pattern!r}>"


class ImportWarning(Base):
    """A row the importer could not normalize (skipped during import).

    Captured so the Action Needed page can show the user exactly which source
    rows failed to import and let them fix the underlying CSV.
    """
    __tablename__ = "import_warnings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_file: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    line_number: Mapped[Optional[int]] = mapped_column(Integer)
    raw_line: Mapped[Optional[str]] = mapped_column(String(1024))
    reason: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

    def __repr__(self) -> str:
        return f"<ImportWarning id={self.id} file={self.source_file!r} reason={self.reason!r}>"
