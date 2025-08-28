# app/main.py
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.config import Settings
from app.db import engine, Base              # single source of engine/Base
import database.models                      # <-- registers models on Base

from whatsapp.webhook import router as whatsapp_router
from whatsapp.apis import router as apis
from app.routers import items
from app.logging_config import logger

settings = Settings()
logger.info("main.py loaded")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # DB ping
    async with engine.connect() as conn:
        await conn.execute(text("select 1"))
    # create any missing tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield

app = FastAPI(title=settings.APP_NAME, lifespan=lifespan)
app.include_router(whatsapp_router, prefix="/whatsapp")
# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # tighten later
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers — mount ONCE
# If whatsapp.webhook defines router = APIRouter(prefix="/whatsapp"), DO NOT add another prefix here.
#app.include_router(whatsapp_router)          # no extra prefix
app.include_router(apis)                     # assumes it has its own paths/prefixes
app.include_router(items.router)

# Utility routes
@app.get("/")
def read_root():
    return {"status": "backend is live"}

@app.get("/routes")
def show_routes():
    return [r.path for r in app.routes]

@app.get("/health")
def health():
    return {"ok": True, "stage": settings.STAGE}

for route in app.routes:
    print(" Registered route:", route.path)
    logger.info(f" Registered route: {route.path}")
# Optional: log routes at startup
@app.on_event("startup")
async def _log_routes():
    for r in app.routes:
        try:
            logger.info(f"Registered route: {r.path}")
            print(" Registered route:", r.path)
        except Exception:
            pass
