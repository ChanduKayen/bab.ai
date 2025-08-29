# app/main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.config import Settings
from app.db import get_engine, get_sessionmaker, Base
import database.models  # registers models on Base
import os

settings = Settings()

@asynccontextmanager
async def lifespan(app: FastAPI):
    engine = get_engine()
    try:
        async with engine.connect() as conn:
            await conn.exec_driver_sql("SET statement_timeout = 5000")  # 5s
            await conn.exec_driver_sql("select 1")
    except Exception:
        import logging
        logging.getLogger("uvicorn.error").exception(
            "DB startup ping failed (url_scheme=%s, DB_SSLMODE=%s, rootcert_set=%s)",
            str(engine.url).split("://",1)[0],
            os.getenv("DB_SSLMODE", "verify-ca"),
            bool(os.getenv("DB_SSLROOTCERT") or os.getenv("DB_CA_PEM")),
        )
        raise  # re-raise so App Runner logs the root error
    # dev-only: create tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield

app = FastAPI(title=settings.APP_NAME, lifespan=lifespan)

# Routers — import AFTER app exists (still fine), but ensure those modules
# don't create engines/sessions at import time.
from whatsapp.webhook import router as whatsapp_router
from whatsapp.apis import router as apis
from app.routers import items
from app.logging_config import logger

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount routers (avoid duplicate prefixes)
app.include_router(whatsapp_router, prefix="/whatsapp")
app.include_router(apis)
app.include_router(items.router)

@app.get("/")
def read_root():
    return {"status": "backend is live"}

@app.get("/routes")
def show_routes():
    out = []
    for r in app.routes:
        path = getattr(r, "path", None)
        methods = sorted(getattr(r, "methods", []) or [])
        out.append({"path": path, "methods": methods})
    return out

@app.get("/health")
def health():
    return {"ok": True, "stage": settings.STAGE}
