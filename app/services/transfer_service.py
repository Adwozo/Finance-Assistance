"""Transfer detection: pair bank-account payments with credit-card receipts.

When you pay a credit card from a bank account, two rows land in the DB:
  - bank statement:   "PAYMENT TO HSBC CREDIT CARD"   amount = -5000
  - card statement:   "PAYMENT RECEIVED - THANK YOU"  amount = +5000

Without pairing these, the dashboard double-counts the move as both spending
(-5000) and income (+5000) and pollutes the category donut.

Detection heuristics:
  1. One row negative, the other positive, equal magnitude (within tolerance).
  2. Different accounts.
  3. Dates within ±DAY_TOLERANCE of each other.
  4. At least one description matches a transfer keyword (card payment, payment
     received, transfer to/from, etc.) OR the user has manually flagged one side.

When a pair is found, both rows get `is_transfer=True` and each row's
`transfer_pair_id` points at the other. The rule engine still assigns categories
to non-transfer rows; transfers keep their category (often "Transfer") but are
excluded from spending/income/donut in the stats endpoint.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Transaction

# Window for matching the two sides of a transfer.
DAY_TOLERANCE = 3
# Amount tolerance for treating magnitudes as equal (handles fees/rounding).
AMOUNT_TOLERANCE = 1.0

# Keywords that strongly indicate a transfer / card-payment leg.
TRANSFER_KEYWORDS = [
    r"\bcredit card\b",
    r"\bcard payment\b",
    r"\bpayment received\b",
    r"\bpayment to\b",
    r"\bpayment from\b",
    r"\btransfer to\b",
    r"\btransfer from\b",
    r"\btransfer\b",
    r"\bthank you\b",            # common card-statement receipt marker
    r"\bbank ?card\b",
    r"\bvisa\b",
    r"\bmastercard\b",
    # Brokerage cash movements (bank <-> investment account)
    r"\bbrokerage\b",
    r"\bdeposit to account\b",
    r"\bdeposit from\b",
    r"\bwithdrawal to\b",
    r"\bwithdrawal from\b",
    r"\bach deposit\b",
    r"\bach withdrawal\b",
    r"\bwire in\b",
    r"\bwire out\b",
    r"\bwire transfer\b",
    r"\bfunding\b",
    r"\bcash deposit\b",
    r"\bcash withdrawal\b",
]
_TRANSFER_RE = re.compile("|".join(TRANSFER_KEYWORDS), re.IGNORECASE)


def looks_like_transfer(description: str) -> bool:
    return bool(_TRANSFER_RE.search(description or ""))


@dataclass
class TransferPair:
    outgoing: Transaction  # negative amount
    incoming: Transaction  # positive amount


def _find_pair(session: Session, tx: Transaction) -> Optional[Transaction]:
    """Find a candidate counterpart for `tx` among non-paired transactions."""
    if tx.is_transfer or tx.kind:
        return None
    want_positive = tx.amount < 0
    target_amount = -tx.amount  # opposite sign
    date_lo = tx.date - timedelta(days=DAY_TOLERANCE)
    date_hi = tx.date + timedelta(days=DAY_TOLERANCE)

    q = select(Transaction).where(
        Transaction.is_transfer.is_(False),
        Transaction.kind == "",
        Transaction.id != tx.id,
        Transaction.account_id != tx.account_id,
        Transaction.date >= date_lo,
        Transaction.date <= date_hi,
    )
    if want_positive:
        q = q.where(Transaction.amount > 0)
    else:
        q = q.where(Transaction.amount < 0)

    candidates = list(session.scalars(q))
    if not candidates:
        return None

    # Prefer: amount-magnitude match (within tolerance) + transfer-keyword match
    # on either side. Fall back to keyword match with closest amount.
    def score(c: Transaction) -> tuple:
        amount_delta = abs(abs(c.amount) - abs(target_amount))
        keyword_hit = looks_like_transfer(tx.description) or looks_like_transfer(c.description)
        return (keyword_hit, -amount_delta)

    candidates.sort(key=score, reverse=True)
    best = candidates[0]
    best_delta = abs(abs(best.amount) - abs(target_amount))
    keyword_hit = looks_like_transfer(tx.description) or looks_like_transfer(best.description)
    # Require either a tight amount match, or a keyword hit on one side.
    if best_delta <= AMOUNT_TOLERANCE or keyword_hit:
        return best
    return None


def detect_transfers(session: Session, only_ids: Optional[list[int]] = None) -> list[TransferPair]:
    """Scan for transfer pairs and link them.

    If `only_ids` is given, only those transactions (and their potential
    counterparts) are considered — used to scope detection to freshly imported rows.
    Returns the list of pairs that were linked in this pass.
    """
    q = select(Transaction).where(Transaction.is_transfer.is_(False))
    if only_ids is not None:
        # Consider the freshly imported rows as one side; counterparts can be any row.
        q = q.where(Transaction.id.in_(only_ids))
    candidates = list(session.scalars(q))

    paired: list[TransferPair] = []
    paired_ids: set[int] = set()
    for tx in candidates:
        if tx.id in paired_ids:
            continue
        # Skip investment buy/sell/dividend/interest/fee rows — they're asset
        # swaps or income/fees, not cash transfers between accounts.
        if tx.kind:
            continue
        counterpart = _find_pair(session, tx)
        if counterpart is None or counterpart.id in paired_ids:
            continue
        if counterpart.kind:
            continue
        # Link both sides.
        tx.is_transfer = True
        counterpart.is_transfer = True
        tx.transfer_pair_id = counterpart.id
        counterpart.transfer_pair_id = tx.id
        paired_ids.update({tx.id, counterpart.id})
        out, inc = (tx, counterpart) if tx.amount < 0 else (counterpart, tx)
        paired.append(TransferPair(outgoing=out, incoming=inc))

    if paired:
        session.commit()
    return paired


def unmark_transfer(session: Session, tx_id: int) -> bool:
    """Remove the transfer flag from a row and its counterpart (if any)."""
    tx = session.get(Transaction, tx_id)
    if tx is None or not tx.is_transfer:
        return False
    pair_id = tx.transfer_pair_id
    tx.is_transfer = False
    tx.transfer_pair_id = None
    if pair_id is not None:
        pair = session.get(Transaction, pair_id)
        if pair is not None:
            pair.is_transfer = False
            pair.transfer_pair_id = None
    session.commit()
    return True


def mark_transfer(session: Session, tx_id: int) -> bool:
    """Manually flag a single row as a transfer (without pairing)."""
    tx = session.get(Transaction, tx_id)
    if tx is None or tx.is_transfer:
        return False
    tx.is_transfer = True
    session.commit()
    return True
