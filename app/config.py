"""Application configuration loaded from environment / .env."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root = parent of the `app` package directory.
ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    database_url: str = "sqlite:///data/transactions.db"
    statements_dir: str = "statements"
    data_dir: str = "data"
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    default_currency: str = "HKD"

    @property
    def statements_path(self) -> Path:
        p = Path(self.statements_dir)
        return p if p.is_absolute() else (ROOT / p)

    @property
    def data_path(self) -> Path:
        p = Path(self.data_dir)
        return p if p.is_absolute() else (ROOT / p)

    @property
    def sqlalchemy_url(self) -> str:
        url = self.database_url
        # Make SQLite paths relative to the project root so they resolve regardless of cwd.
        if url.startswith("sqlite:///") and not Path(url.removeprefix("sqlite:///")).is_absolute():
            db_path = ROOT / url.removeprefix("sqlite:///")
            db_path.parent.mkdir(parents=True, exist_ok=True)
            return f"sqlite:///{db_path.as_posix()}"
        return url

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()
