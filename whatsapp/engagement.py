# whatsapp/engagement.py
import asyncio 
from whatsapp.builder_out import whatsapp_output

async def run_with_engagement(sender_id: str, work_coro, *, first_nudge_after: int = 10):
    """
    Immediately sends a 'got it' receipt.
    If work_coro still runs after `first_nudge_after` seconds,
    sends ONE heartbeat with actionable buttons.
    Returns the work result.
    """
    # 1) Instant receipt (keeps the session warm)
    whatsapp_output(
        sender_id,
        """✅ *Got it*

→ We’re formatting your requirement into a professinal list.
"""
    )

    task = asyncio.create_task(work_coro)

    async def heartbeat():
        try:
            await asyncio.sleep(first_nudge_after)
            if not task.done():
                # A single, useful heartbeat (no spam)
                whatsapp_output(
                    sender_id,
                    """⏳ *Processing your request…*

_Please allow up to 2 minutes._
                    """,
                    message_type="plain",
                    extra_data=[

                    ],
                )
        except asyncio.CancelledError:
            pass

    hb = asyncio.create_task(heartbeat())
    try:
        return await task
    finally:
        if not hb.done():
            hb.cancel()
