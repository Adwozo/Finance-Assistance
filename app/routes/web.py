"""HTML routes — Mint-style pages."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_session
from ..models import Account, Category
from ..services import category_service
from ..services.importer import list_statement_files

router = APIRouter(tags=["web"])


def _base_context(request: Request, session: Session) -> dict:
    from ..config import get_settings
    return {
        "request": request,
        "accounts": list(session.scalars(select(Account).order_by(Account.name))),
        "category_tree": category_service.build_tree(session),
        "statement_files": [p.name for p in list_statement_files()],
        "active": "",
        "default_currency": get_settings().default_currency,
    }


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, session: Session = Depends(get_session)):
    ctx = _base_context(request, session)
    ctx["active"] = "dashboard"
    return request.app.state.templates.TemplateResponse(request, "dashboard.html", ctx)


@router.get("/transactions", response_class=HTMLResponse)
def transactions_page(request: Request, session: Session = Depends(get_session)):
    ctx = _base_context(request, session)
    ctx["active"] = "transactions"
    return request.app.state.templates.TemplateResponse(request, "transactions.html", ctx)


@router.get("/categories", response_class=HTMLResponse)
def categories_page(request: Request, session: Session = Depends(get_session)):
    ctx = _base_context(request, session)
    ctx["active"] = "categories"
    return request.app.state.templates.TemplateResponse(request, "categories.html", ctx)


@router.get("/spending", response_class=HTMLResponse)
def spending_page(request: Request, session: Session = Depends(get_session)):
    ctx = _base_context(request, session)
    ctx["active"] = "spending"
    return request.app.state.templates.TemplateResponse(request, "spending.html", ctx)


@router.get("/action", response_class=HTMLResponse)
def action_page(request: Request, session: Session = Depends(get_session)):
    ctx = _base_context(request, session)
    ctx["active"] = "action"
    return request.app.state.templates.TemplateResponse(request, "action.html", ctx)


@router.get("/import", response_class=HTMLResponse)
def import_page(request: Request, session: Session = Depends(get_session)):
    ctx = _base_context(request, session)
    ctx["active"] = "import"
    return request.app.state.templates.TemplateResponse(request, "import.html", ctx)


@router.get("/rules", response_class=HTMLResponse)
def rules_page(request: Request, session: Session = Depends(get_session)):
    ctx = _base_context(request, session)
    ctx["active"] = "rules"
    return request.app.state.templates.TemplateResponse(request, "rules.html", ctx)


@router.get("/accounts", response_class=HTMLResponse)
def accounts_page(request: Request, session: Session = Depends(get_session)):
    ctx = _base_context(request, session)
    ctx["active"] = "accounts"
    return request.app.state.templates.TemplateResponse(request, "accounts.html", ctx)
