"""FastAPI application factory."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import get_settings
from .database import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure folders exist + tables created on startup.
    settings = get_settings()
    settings.statements_path.mkdir(parents=True, exist_ok=True)
    settings.data_path.mkdir(parents=True, exist_ok=True)
    init_db()
    # Seed default categories + a default account once.
    from .services.category_service import seed_defaults
    from .database import SessionLocal
    with SessionLocal() as session:
        seed_defaults(session)
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Finance Assistance", lifespan=lifespan)

    # Static + templates
    static_dir = Path(__file__).resolve().parent / "static"
    templates_dir = Path(__file__).resolve().parent / "templates"
    static_dir.mkdir(parents=True, exist_ok=True)
    templates_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    app.state.templates = Jinja2Templates(directory=str(templates_dir))

    # Routers
    from .routes import api, web
    app.include_router(api.router)
    app.include_router(web.router)

    return app


app = create_app()


def run() -> None:
    """Entry point for the `finance-assistance` console script."""
    import uvicorn

    settings = get_settings()
    uvicorn.run("app.main:app", host=settings.app_host, port=settings.app_port, reload=False)
