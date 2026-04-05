from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database.models import MaterialRequest, MaterialRequestItem, RequestStatus


class SitePatternService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_hint_for_new_order(
        self,
        sender_id: str,
        new_items: List[Dict[str, Any]],
        project_id: Optional[str] = None,
    ) -> Optional[str]:
        """
        Compare new_items against last 3 orders for this sender.
        Returns one short contextual hint string, or None.
        Never raises — always fails silently.
        """
        try:
            stmt = (
                select(MaterialRequest)
                .where(
                    MaterialRequest.sender_id == sender_id,
                    MaterialRequest.status.in_([
                        RequestStatus.REQUESTED,
                        RequestStatus.QUOTED,
                        RequestStatus.APPROVED,
                    ])
                )
                .order_by(desc(MaterialRequest.updated_at))
                .limit(3)
                .options(selectinload(MaterialRequest.items))
            )
            if project_id:
                stmt = stmt.where(MaterialRequest.project_id == project_id)

            result = await self.session.execute(stmt)
            past_orders = result.scalars().all()

            if not past_orders:
                return None

            latest = past_orders[0]

            hint = self._check_long_gap(latest)
            if hint:
                return hint

            hint = self._check_similar_items(latest, new_items)
            if hint:
                return hint

            return None

        except Exception as e:
            print(f"site_pattern ::::: get_hint_for_new_order ::::: exception: {e}")
            return None

    def _check_long_gap(self, latest_order: MaterialRequest) -> Optional[str]:
        if not latest_order.updated_at:
            return None
        now = datetime.now(timezone.utc)
        last = latest_order.updated_at
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        gap_days = (now - last).days
        if gap_days > 30:
            weeks = gap_days // 7
            if weeks >= 4:
                return "Haven't seen an order from you in a while — welcome back! 👋"
            return f"It's been about {weeks} week{'s' if weeks != 1 else ''} since your last order."
        return None

    def _check_similar_items(
        self,
        latest_order: MaterialRequest,
        new_items: List[Dict[str, Any]],
    ) -> Optional[str]:
        if not latest_order.items:
            return None

        past_names = {
            item.material_name.lower().strip()
            for item in latest_order.items
            if item.material_name
        }
        new_names = {
            it.get("material", "").lstrip("*").lower().strip()
            for it in new_items
            if it.get("material")
        }

        if not past_names or not new_names:
            return None

        overlap = past_names & new_names
        overlap_ratio = len(overlap) / max(len(new_names), 1)

        if overlap_ratio >= 0.7:
            return "Looks similar to your last order. Same vendors?"

        return None
