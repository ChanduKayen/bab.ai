# database/_init_.py
import os
from typing import Optional
from pydantic import BaseModel, ValidationError, field_validator
from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    pass
 
class Settings(BaseModel):
    DATABASE_URL: str

    @field_validator("DATABASE_URL")
    @classmethod
    def normalize_async_url(cls, v: str) -> str:
        # Convert common sync URLs to async
        if v.startswith("postgresql://"):
            v = v.replace("postgresql://", "postgresql+asyncpg://", 1)
        if v.startswith("sqlite://") and "+aiosqlite" not in v:
            v = v.replace("sqlite://", "sqlite+aiosqlite://", 1)
        return v

_engine: Optional[AsyncEngine] = None
_SessionLocal: Optional[async_sessionmaker] = None

def get_engine() -> AsyncEngine:
    global _engine
    if _engine:
        return _engine

    raw = os.getenv("DATABASE_URL")
    try:
        settings = Settings(DATABASE_URL=raw)
    except ValidationError:
        raise RuntimeError(
            "DATABASE_URL is missing/invalid. Example:\n"
            "  postgresql+asyncpg://USER:PASSWORD@HOST:5432/DB\n"
            "  sqlite+aiosqlite:///./app.db"
        )

    url = settings.DATABASE_URL

    # Pool kw only for non-SQLite
    pool_kwargs = {}
    if not url.startswith("sqlite+aiosqlite://"):
        pool_kwargs = dict(pool_size=10, max_overflow=20)

    _engine = create_async_engine(url, echo=False, **pool_kwargs)
    return _engine

def get_sessionmaker() -> async_sessionmaker:
    global _SessionLocal
    if _SessionLocal:
        return _SessionLocal
    _SessionLocal = async_sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionLocal
