# app/db.py
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import MetaData
from .config import Settings, get_db_url  # your existing config

settings = Settings()
DATABASE_URL = get_db_url(settings)
# NOTE: if the password has '@', encode it: '@' -> %40

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,   # drop dead conns cleanly (App Runner helpful)
    pool_recycle=300,
)

class Base(DeclarativeBase):
    metadata = MetaData(schema="public")

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

from typing import AsyncGenerator
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    print("db :::::: get_session::::: getting session ; DB URL:", DATABASE_URL)
    async with SessionLocal() as session:
        yield session
