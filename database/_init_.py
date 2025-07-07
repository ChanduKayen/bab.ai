import asyncio
from database.models import Base
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from dotenv import load_dotenv
import os

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

#DATABASE_URL = "postgresql+asyncpg://babai_admin:Babai@2025@localhost/babai"

engine = create_async_engine(DATABASE_URL, echo=True, pool_size=10, max_overflow=20)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)
