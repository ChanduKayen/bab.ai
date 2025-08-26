# database/whatsapp_crud.py
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert
from database.models import WhatsAppEvent

async def first_time_event(session: AsyncSession, event_id: str) -> bool:
    stmt = insert(WhatsAppEvent).values(event_id=event_id).on_conflict_do_nothing()
    res = await session.execute(stmt)
    await session.commit()
    return res.rowcount == 1  # True if newly inserted
