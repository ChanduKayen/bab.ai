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

We’ll format your details and present them in a refined, professional way to get accurate and reliable quotations from manufacturers.
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
                    """⏳ *Almost there…*

_Just refining your details - this might take a minute or two._
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
