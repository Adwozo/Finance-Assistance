"""Rule engine: auto-categorize transactions based on user-defined rules.

Rules match on `description`, `account`, or `amount` and assign a category.
The highest-priority matching rule wins. Regex or substring matching.
"""
from __future__ import annotations

import re
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Category, Rule, Transaction


def _matches(rule: Rule, tx: Transaction) -> bool:
    if rule.field == "description":
        target = tx.description or ""
    elif rule.field == "account":
        target = tx.account.name if tx.account else ""
    elif rule.field == "amount":
        target = f"{tx.amount:.2f}"
    else:
        return False

    if rule.is_regex:
        try:
            return re.search(rule.pattern, target, re.IGNORECASE) is not None
        except re.error:
            return False
    return rule.pattern.lower() in target.lower()


def apply_rules_to_tx(session: Session, tx: Transaction) -> Optional[Category]:
    rules = list(session.scalars(select(Rule).order_by(Rule.priority.desc(), Rule.id)))
    for rule in rules:
        if _matches(rule, tx):
            cat = session.get(Category, rule.category_id)
            if cat:
                tx.category_id = cat.id
                return cat
    return None


def apply_rules_to_all(session: Session) -> int:
    """Apply rules to every uncategorized transaction. Returns count assigned."""
    count = 0
    txs = list(session.scalars(select(Transaction).where(Transaction.category_id.is_(None))))
    for tx in txs:
        if apply_rules_to_tx(session, tx) is not None:
            count += 1
    session.commit()
    return count


def apply_rules_for_import(session: Session, txs: list[Transaction]) -> int:
    """Apply rules to a batch of freshly imported transactions."""
    count = 0
    for tx in txs:
        if tx.category_id is None and apply_rules_to_tx(session, tx) is not None:
            count += 1
    return count
