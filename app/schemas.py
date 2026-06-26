"""Pydantic schemas for the API layer."""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


# --- Accounts ---
class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    currency: str
    institution: Optional[str] = None
    type: str = "checking"


class AccountIn(BaseModel):
    name: str
    currency: str = "HKD"
    institution: Optional[str] = None
    type: str = "checking"


# --- Categories ---
class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    parent_id: Optional[int] = None
    color: str = "#6b7280"
    icon: Optional[str] = None


class CategoryIn(BaseModel):
    name: str
    parent_id: Optional[int] = None
    color: str = "#6b7280"
    icon: Optional[str] = None


# --- Transactions ---
class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    date: date
    description: str
    amount: float
    currency: str
    account_id: Optional[int] = None
    account_name: Optional[str] = None
    category_id: Optional[int] = None
    category_name: Optional[str] = None
    balance_after: Optional[float] = None
    source_file: Optional[str] = None
    is_transfer: bool = False
    transfer_pair_id: Optional[int] = None
    kind: str = ""
    account_type: Optional[str] = None
    category_event: str = ""


class TransactionUpdate(BaseModel):
    category_id: Optional[int] = None
    description: Optional[str] = None
    is_transfer: Optional[bool] = None
    # When true, the edit is also written back to the source CSV file
    # (statements/<source_file>) so the on-disk file stays in sync with the DB.
    save_to_csv: bool = False


# --- Rules ---
class RuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    field: str
    pattern: str
    is_regex: bool = False
    category_id: int
    priority: int = 0


class RuleIn(BaseModel):
    field: str = Field(pattern="^(description|account|amount)$")
    pattern: str
    is_regex: bool = False
    category_id: int
    priority: int = 0


# --- Import / dashboard ---
class ImportResult(BaseModel):
    file: str
    parser: str
    imported: int
    skipped: int
    transfers_linked: int = 0
    classified: int = 0
    categories_created: int = 0
    warnings: list[str] = []


class DashboardStats(BaseModel):
    total_balance: float
    spending_this_month: float
    income_this_month: float
    net_this_month: float
    spending_by_category: list[dict]
    monthly_trend: list[dict]
