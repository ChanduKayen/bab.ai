# database/whatsapp_crud.py
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert
from database.models import WhatsAppEvent

async def first_time_event(session: AsyncSession, event_id: str) -> bool:
    print("whatsapp_crud :::::: first_time_event::::: Recording First event for event_id:", event_id)
    try:
        stmt = insert(WhatsAppEvent).values(event_id=event_id).on_conflict_do_nothing()
        res = await session.execute(stmt)
        await session.commit()
        return res.rowcount == 1  # True if newly inserted
    except Exception as e:
        print(f"Error in first_time_event for event_id {event_id}: {e}")
        await session.rollback()
        return False
