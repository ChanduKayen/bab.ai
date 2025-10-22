from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_sessionmaker
from database.models import (
    MaterialRequest,
    Project,
    QuoteRequestVendor,
    QuoteRequestVendorStatus,
    Vendor,
    VendorFollowupNudge,
)
from managers.vendor_followup import compute_next_due, NUDGE_SCHEDULE
from whatsapp.builder_out import whatsapp_output


async def _load_due_entries(session: AsyncSession, now: datetime) -> List[tuple]:
    stmt = (
        select(
            VendorFollowupNudge,
            QuoteRequestVendor,
            MaterialRequest,
            Vendor,
            Project,
        )
        .join(
            QuoteRequestVendor,
            (QuoteRequestVendor.quote_request_id == VendorFollowupNudge.quote_request_id)
            & (QuoteRequestVendor.vendor_id == VendorFollowupNudge.vendor_id),
        )
        .join(MaterialRequest, MaterialRequest.id == VendorFollowupNudge.quote_request_id)
        .join(Vendor, Vendor.vendor_id == VendorFollowupNudge.vendor_id)
        .outerjoin(Project, Project.id == MaterialRequest.project_id)
        .where(VendorFollowupNudge.next_nudge_at <= now)
    )
    result = await session.execute(stmt)
    return result.all()


def _format_elapsed(invited_at: datetime, now: datetime) -> str:
    elapsed = now - invited_at
    hours = int(elapsed.total_seconds() // 3600)
    minutes = int((elapsed.total_seconds() % 3600) // 60)
    if hours >= 24:
        days = hours // 24
        return f"{days} day{'s' if days != 1 else ''}"
    if hours:
        return f"{hours} hour{'s' if hours != 1 else ''}"
    return f"{minutes} minute{'s' if minutes != 1 else ''}"


async def _send_vendor_nudge(
    vendor_phone: Optional[str],
    vendor_name: Optional[str],
    project_name: Optional[str],
    delivery_location: Optional[str],
    invited_at: datetime,
    now: datetime,
) -> None:
    if not vendor_phone:
        return
    friendly_name = vendor_name or "there"
    location_bits = [part for part in [project_name, delivery_location] if part]
    location_text = ", ".join(location_bits) if location_bits else "the requested site"
    elapsed_text = _format_elapsed(invited_at, now)
    message = (
        f"👋 Hi {friendly_name},\n"
        f"The supervisor for {location_text} has been waiting for your quote for about {elapsed_text}.\n"
        "Please share your quotation when you can—thanks!"
    )
    whatsapp_output(vendor_phone, message, message_type="plain")


async def _notify_supervisor(
    supervisor_phone: Optional[str],
    vendor_name: Optional[str],
    request_id: str,
    project_name: Optional[str],
    delivery_location: Optional[str],
) -> None:
    if not supervisor_phone:
        return
    location_bits = [part for part in [project_name, delivery_location] if part]
    location_text = ", ".join(location_bits) if location_bits else "your site"
    vendor_label = vendor_name or "the vendor"
    short_id = request_id[:8].upper()
    message = (
        f"FYI, we nudged {vendor_label} again for order {short_id} ({location_text}).\n"
        "We’ll notify you as soon as their quote comes in."
    )
    whatsapp_output(supervisor_phone, message, message_type="plain")


async def process_vendor_nudges(now: Optional[datetime] = None) -> None:
    """
    Scan for pending vendor follow-up nudges and dispatch reminders
    to both the vendor and the requesting supervisor.
    """
    now = (now or datetime.utcnow()).replace(tzinfo=timezone.utc)
    session_factory = get_sessionmaker()

    async with session_factory() as session:
        entries = await _load_due_entries(session, now)
        if not entries:
            return

        to_delete = []
        for nudge, qr, request, vendor, project in entries:
            # Skip if vendor already responded or is no longer active
            if qr.status not in (
                QuoteRequestVendorStatus.INVITED,
                QuoteRequestVendorStatus.NOTIFIED,
            ):
                to_delete.append((nudge.quote_request_id, nudge.vendor_id))
                continue

            # Send nudges for any overdue stages
            while (
                nudge.nudge_stage < len(NUDGE_SCHEDULE)
                and nudge.next_nudge_at <= now
            ):
                await _send_vendor_nudge(
                    vendor_phone=vendor.phone_number,
                    vendor_name=vendor.name,
                    project_name=getattr(project, "name", None),
                    delivery_location=request.delivery_location,
                    invited_at=nudge.invited_at,
                    now=now,
                )

                await _notify_supervisor(
                    supervisor_phone=request.sender_id,
                    vendor_name=vendor.name,
                    request_id=str(request.id),
                    project_name=getattr(project, "name", None),
                    delivery_location=request.delivery_location,
                )

                nudge.nudge_stage += 1
                nudge.last_nudged_at = now
                next_due = compute_next_due(nudge.invited_at, nudge.nudge_stage)
                if next_due is None:
                    to_delete.append((nudge.quote_request_id, nudge.vendor_id))
                    break
                nudge.next_nudge_at = next_due
                nudge.updated_at = now

            if (
                nudge.nudge_stage < len(NUDGE_SCHEDULE)
                and (nudge.quote_request_id, nudge.vendor_id) not in to_delete
            ):
                session.add(nudge)

        for req_id, vendor_id in to_delete:
            await session.execute(
                delete(VendorFollowupNudge).where(
                    VendorFollowupNudge.quote_request_id == req_id,
                    VendorFollowupNudge.vendor_id == vendor_id,
                )
            )

        await session.commit()


async def main() -> None:
    await process_vendor_nudges()


if __name__ == "__main__":
    asyncio.run(main())
