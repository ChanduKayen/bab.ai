# app/db.py
import os, ssl, certifi
from typing import Optional, AsyncGenerator
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine, async_sessionmaker, AsyncSession
from app.config import Settings, get_db_url

class Base(DeclarativeBase):
    pass

_engine: Optional[AsyncEngine] = None
_SessionLocal: Optional[async_sessionmaker] = None

def _normalize_url(url: str) -> str:
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("sqlite://") and "+aiosqlite" not in url:
        url = url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    return url

def _ssl_args_for_postgres() -> dict:
    mode = os.getenv("DB_SSLMODE", "verify-full").lower()
    if mode == "disable":
        return {}

    ctx = ssl.create_default_context()
 
    # Priority: explicit PEM path → PEM string → certifi bundle
    ca_path = os.getenv("DB_SSLROOTCERT")  
    ca_pem  = os.getenv("DB_CA_PEM")
    if ca_pem:
        ctx.load_verify_locations(cadata=ca_pem)
    elif ca_path: 
        ctx.load_verify_locations(cafile=ca_path)
    else:
        ctx.load_verify_locations(cafile=certifi.where())
 
    if mode == "verify-full":
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED 
    elif mode == "verify-ca":
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_REQUIRED
    elif mode == "require":
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    else:
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED

    return {"ssl": ctx}

def get_engine() -> AsyncEngine:
    url = get_db_url(Settings())
    # ...
    kwargs = {}
    if url.startswith("postgresql+asyncpg://"):
        # fail faster if network is wrong
        connect_args = _ssl_args_for_postgres()
        timeout = float(os.getenv("DB_CONNECT_TIMEOUT", "10"))
        connect_args.setdefault("timeout", timeout)
        kwargs.update(pool_size=10, max_overflow=20, connect_args=connect_args)

    return create_async_engine(url, echo=False, **kwargs)

def get_sessionmaker() -> async_sessionmaker:
    global _SessionLocal
    if _SessionLocal:
        return _SessionLocal
    _SessionLocal = async_sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionLocal
def get_session() -> async_sessionmaker:
    return get_sessionmaker()

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async_session = get_sessionmaker()
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()