"""Investment transaction classification.

For accounts with `account.type == 'investment'`, transactions are tagged with
a `kind` so the stats layer can treat them correctly:

  kind        | typical description pattern        | stats treatment
  ------------|-----------------------------------|--------------------------------
  'buy'       | "BUY AAPL", "PURCHASE ..."        | asset swap — NOT spending
  'sell'      | "SELL AAPL", "SALE ..."           | asset swap — NOT income
  'dividend'  | "DIVIDEND ...", "DVD ..."         | income (counted)
  'interest'  | "INTEREST", "SWEEP INTEREST"      | income (counted)
  'fee'       | "COMMISSION", "PLATFORM FEE"      | spending (counted)
  ''          | generic cash movement             | transfer if paired, else normal

Only runs on investment accounts; non-investment rows keep kind = ''.
"""
from __future__ import annotations

import re
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Transaction

# Ordered: check sell before buy (some statements prefix "SELL" before ticker).
_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("buy", re.compile(
        r"\b(buy|bought|purchase|purch|acquisition|acq)\b", re.IGNORECASE)),
    ("sell", re.compile(
        r"\b(sell|sold|sale|sell ?to ?cover|dispose|disposal)\b", re.IGNORECASE)),
    ("dividend", re.compile(
        r"\b(dividend|dvd|div ?rec|distribution|dist ?rec)\b", re.IGNORECASE)),
    ("interest", re.compile(
        r"\b(interest|sweep ?interest|cash ?interest|int ?rec)\b", re.IGNORECASE)),
    ("fee", re.compile(
        r"\b(commission|comm|platform fee|management fee|custody fee|fee|"
        r"transaction charge|service charge)\b", re.IGNORECASE)),
]


def classify(description: str) -> str:
    desc = description or ""
    for kind, pat in _PATTERNS:
        if pat.search(desc):
            return kind
    return ""


def classify_for_account_type(description: str, account_type: Optional[str]) -> str:
    """Only classify when the account is an investment account."""
    if account_type != "investment":
        return ""
    return classify(description)


def classify_transactions(session: Session, only_ids: Optional[list[int]] = None) -> int:
    """Tag investment-account transactions with their kind. Returns count tagged.

    Re-classifies all (or only the given) transactions; rows on non-investment
    accounts keep kind=''. Buy/sell/dividend/interest/fee rows are never marked
    as transfers — they're asset swaps or income/fees, not cash moves.
    """
    q = select(Transaction).where(Transaction.kind == "")
    if only_ids is not None:
        q = q.where(Transaction.id.in_(only_ids))
    # Also include rows whose kind was set but the user changed the account type
    # back — those will be re-evaluated below.
    q_all = select(Transaction)
    if only_ids is not None:
        q_all = q_all.where(Transaction.id.in_(only_ids))

    tagged = 0
    for tx in session.scalars(q_all):
        acct_type = tx.account.type if tx.account else None
        new_kind = classify_for_account_type(tx.description, acct_type)
        if new_kind != tx.kind:
            tx.kind = new_kind
            tagged += 1
        # A buy/sell/dividend/interest/fee should never be a transfer.
        if new_kind and tx.is_transfer:
            tx.is_transfer = False
            tx.transfer_pair_id = None
    if tagged:
        session.commit()
    return tagged
