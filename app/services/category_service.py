"""Category CRUD + tree helpers + default seeding."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Category

DEFAULT_CATEGORIES = [
    ("Income", "#22c55e", None, [
        ("Salary", "#16a34a", None),
        ("Interest", "#65a30d", None),
        ("Refunds", "#84cc16", None),
    ]),
    ("Housing", "#3b82f6", None, [
        ("Rent", "#2563eb", None),
        ("Mortgage", "#1d4ed8", None),
        ("Utilities", "#60a5fa", None),
    ]),
    ("Food & Dining", "#f97316", None, [
        ("Groceries", "#ea580c", None),
        ("Restaurants", "#f59e0b", None),
        ("Coffee", "#fbbf24", None),
    ]),
    ("Transportation", "#a855f7", None, [
        ("Gas", "#9333ea", None),
        ("Public Transit", "#c084fc", None),
        ("Rideshare", "#d8b4fe", None),
    ]),
    ("Shopping", "#ec4899", None, [
        ("Clothing", "#db2777", None),
        ("Electronics", "#f472b6", None),
    ]),
    ("Entertainment", "#14b8a6", None, [
        ("Streaming", "#0d9488", None),
        ("Hobbies", "#2dd4bf", None),
    ]),
    ("Health & Fitness", "#ef4444", None, [
        ("Gym", "#dc2626", None),
        ("Medical", "#b91c1c", None),
        ("Pharmacy", "#f87171", None),
    ]),
    ("Travel", "#0ea5e9", None, [
        ("Flights", "#0284c7", None),
        ("Hotels", "#38bdf8", None),
    ]),
    ("Fees & Charges", "#6b7280", None, [
        ("Bank Fees", "#4b5563", None),
        ("Interest Charges", "#9ca3af", None),
    ]),
    ("Transfer", "#8b5cf6", None, []),
    ("Other", "#64748b", None, []),
]


def seed_defaults(session: Session) -> None:
    """Insert default categories + a default account if none exist."""
    if session.scalar(select(Category).limit(1)) is not None:
        return

    def add(name, color, parent_id):
        c = Category(name=name, color=color, parent_id=parent_id)
        session.add(c)
        session.flush()
        return c.id

    for top_name, top_color, _, children in DEFAULT_CATEGORIES:
        top_id = add(top_name, top_color, None)
        for child_name, child_color, _ in children:
            add(child_name, child_color, top_id)

    # Default account
    from ..config import get_settings
    from ..models import Account
    if session.scalar(select(Account).limit(1)) is None:
        session.add(Account(name="Default", currency=get_settings().default_currency,
                            institution=None, type="checking"))
    session.commit()


def list_categories(session: Session) -> list[Category]:
    return list(session.scalars(select(Category).order_by(Category.parent_id, Category.name)))


def build_tree(session: Session) -> list[dict]:
    cats = list_categories(session)
    by_id = {c.id: {"id": c.id, "name": c.name, "color": c.color, "icon": c.icon,
                    "parent_id": c.parent_id, "children": []} for c in cats}
    roots: list[dict] = []
    for c in cats:
        node = by_id[c.id]
        if c.parent_id and c.parent_id in by_id:
            by_id[c.parent_id]["children"].append(node)
        else:
            roots.append(node)
    return roots


def create_category(session: Session, name: str, parent_id: Optional[int] = None,
                    color: str = "#6b7280", icon: Optional[str] = None) -> Category:
    c = Category(name=name, parent_id=parent_id, color=color, icon=icon)
    session.add(c)
    session.commit()
    session.refresh(c)
    return c


def update_category(session: Session, cat_id: int, **fields) -> Optional[Category]:
    c = session.get(Category, cat_id)
    if c is None:
        return None
    for k, v in fields.items():
        if hasattr(c, k) and v is not None:
            setattr(c, k, v)
    session.commit()
    session.refresh(c)
    return c


def delete_category(session: Session, cat_id: int) -> bool:
    c = session.get(Category, cat_id)
    if c is None:
        return False
    session.delete(c)
    session.commit()
    return True
