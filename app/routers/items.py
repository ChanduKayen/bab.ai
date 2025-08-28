from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ..db import get_session
from ..models import Item

router = APIRouter(prefix="/items", tags=["items"])

@router.get("/")
async def list_items(db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(select(Item))).scalars().all()
    return [{"id": r.id, "name": r.name} for r in rows]
