from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database.models import (
    MaterialRequest,
    MaterialRequestItem,
    Project,
    QuoteRequestVendor,
    QuoteRequestVendorStatus,
    RequestStatus,
    Vendor,
    VendorQuoteItem,
)


@dataclass
class VendorSnapshot:
    vendor_id: str
    name: Optional[str]
    status: Optional[str]
    categories: List[str]


@dataclass
class OrderSummary:
    lifecycle: str
    request_id: str
    status: str
    project_name: Optional[str]
    project_location: Optional[str]
    created_at: Optional[str]
    updated_at: Optional[str]
    expected_delivery_date: Optional[str]
    delivered_at: Optional[str]
    approved_vendor: Optional[VendorSnapshot]
    vendors: List[VendorSnapshot]
    material_count: int
    sample_materials: List[str]
    vendor_categories: List[str]

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        if self.approved_vendor is not None:
            data["approved_vendor"] = asdict(self.approved_vendor)
        return data


class OrderContextService:
    """
    Aggregates procurement requests for a sender into lifecycle buckets.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_orders_for_sender(self, sender_id: str, *, limit: int = 20) -> Dict[str, List[Dict[str, Any]]]:
        stmt = (
            select(MaterialRequest)
            .where(MaterialRequest.sender_id == sender_id)
            .options(
                selectinload(MaterialRequest.project),
                selectinload(MaterialRequest.items),
                selectinload(MaterialRequest.vendor_quote_items).selectinload(VendorQuoteItem.vendor),
            )
            .order_by(MaterialRequest.updated_at.desc())
            .limit(limit)
        )

        result = await self.session.execute(stmt)
        requests = result.scalars().unique().all()
        if not requests:
            return {"draft": [], "active": [], "fulfilled": []}

        request_ids = [req.id for req in requests]
        invites = await self._load_quote_requests(request_ids)
        vendor_map = await self._load_vendors(requests, invites)
        invites_by_request = self._group_invites_by_request(invites)

        buckets: Dict[str, List[Dict[str, Any]]] = {"draft": [], "active": [], "fulfilled": []}
        for req in requests:
            lifecycle = self._determine_lifecycle(req)
            summary = self._summarize_request(req, lifecycle, vendor_map, invites_by_request.get(req.id, []))
            buckets[lifecycle].append(summary.to_dict())

        return buckets

    async def get_order_by_vendor(self, sender_id: str, vendor_identifier: str) -> Optional[Dict[str, Any]]:
        orders = await self.get_orders_for_sender(sender_id)
        needle = vendor_identifier.strip().lower()

        for lifecycle in ("active", "fulfilled", "draft"):
            for record in orders[lifecycle]:
                approved = record.get("approved_vendor")
                if approved and self._matches_vendor(approved, needle):
                    return record

                for vendor in record.get("vendors", []):
                    if self._matches_vendor(vendor, needle):
                        return record

        return None

    def _summarize_request(
        self,
        req: MaterialRequest,
        lifecycle: str,
        vendor_map: Dict[str, Vendor],
        invite_rows: List[QuoteRequestVendor],
    ) -> OrderSummary:
        project: Optional[Project] = req.project
        approved_vendor_snapshot = self._build_vendor_snapshot(
            vendor_id=req.approved_vendor,
            vendor_map=vendor_map,
            status=None,
            lifecycle_vendor=True,
        )

        vendor_snapshots: List[VendorSnapshot] = []
        vendor_categories: set[str] = set()
        for invite in invite_rows:
            vendor_snapshot = self._build_vendor_snapshot(
                vendor_id=str(invite.vendor_id),
                vendor_map=vendor_map,
                status=invite.status,
            )
            if vendor_snapshot:
                vendor_snapshots.append(vendor_snapshot)
                vendor_categories.update(vendor_snapshot.categories)

        material_names = [self._clean_material_name(item) for item in req.items if self._clean_material_name(item)]
        material_names = material_names[:5]

        status_value: str
        if isinstance(req.status, RequestStatus):
            status_value = req.status.value.lower()
        else:
            status_value = str(req.status).lower() if req.status else ""

        return OrderSummary(
            lifecycle=lifecycle,
            request_id=str(req.id),
            status=status_value,
            project_name=project.name if project else None,
            project_location=project.location if project else None,
            created_at=self._iso_dt(req.created_at),
            updated_at=self._iso_dt(req.updated_at),
            expected_delivery_date=self._iso_date(req.expected_delivery_date),
            delivered_at=self._iso_dt(req.delivered_at),
            approved_vendor=approved_vendor_snapshot,
            vendors=vendor_snapshots,
            material_count=len(req.items),
            sample_materials=material_names,
            vendor_categories=sorted(vendor_categories),
        )

    def _determine_lifecycle(self, req: MaterialRequest) -> str:
        if req.status == RequestStatus.DRAFT:
            return "draft"
        if req.approved_vendor and req.delivered_at:
            return "fulfilled"
        return "active"

    async def _load_quote_requests(self, request_ids: List[Optional[str]]) -> List[QuoteRequestVendor]:
        stmt = select(QuoteRequestVendor).where(QuoteRequestVendor.quote_request_id.in_(request_ids))
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def _load_vendors(
        self,
        requests: List[MaterialRequest],
        invite_rows: List[QuoteRequestVendor],
    ) -> Dict[str, Vendor]:
        vendor_ids = {
            str(req.approved_vendor)
            for req in requests
            if getattr(req, "approved_vendor", None)
        }
        vendor_ids.update(str(inv.vendor_id) for inv in invite_rows if inv.vendor_id)

        if not vendor_ids:
            return {}

        stmt = select(Vendor).where(Vendor.vendor_id.in_(vendor_ids))
        result = await self.session.execute(stmt)
        vendors = result.scalars().all()
        return {str(v.vendor_id): v for v in vendors}

    @staticmethod
    def _group_invites_by_request(invites: List[QuoteRequestVendor]) -> Dict[Any, List[QuoteRequestVendor]]:
        grouped: Dict[Any, List[QuoteRequestVendor]] = defaultdict(list)
        for invite in invites:
            grouped[invite.quote_request_id].append(invite)
        return grouped

    def _build_vendor_snapshot(
        self,
        vendor_id: Optional[str],
        vendor_map: Dict[str, Vendor],
        status: Optional[Any] = None,
        lifecycle_vendor: bool = False,
    ) -> Optional[VendorSnapshot]:
        if not vendor_id:
            return None

        vendor_row = vendor_map.get(str(vendor_id))
        name = getattr(vendor_row, "name", None)
        raw_categories = list(getattr(vendor_row, "material_categories", []) or [])
        categories = [
            cat.value if hasattr(cat, "value") else str(cat)
            for cat in raw_categories
        ]

        status_value: Optional[str]
        if lifecycle_vendor:
            status_value = "approved"
        elif isinstance(status, QuoteRequestVendorStatus):
            status_value = status.value.lower()
        elif isinstance(status, str):
            status_value = status.lower()
        else:
            status_value = None

        return VendorSnapshot(
            vendor_id=str(vendor_id),
            name=name,
            status=status_value,
            categories=categories,
        )

    @staticmethod
    def _matches_vendor(vendor: Dict[str, Any], needle: str) -> bool:
        vendor_id = vendor.get("vendor_id", "")
        vendor_name = vendor.get("name") or ""
        return needle in vendor_id.lower() or needle in vendor_name.lower()

    @staticmethod
    def _iso_dt(value: Optional[datetime]) -> Optional[str]:
        if not value:
            return None
        return value.isoformat()

    @staticmethod
    def _iso_date(value: Optional[Any]) -> Optional[str]:
        if not value:
            return None
        return value.isoformat()

    @staticmethod
    def _clean_material_name(item: MaterialRequestItem) -> Optional[str]:
        name = getattr(item, "material_name", None)
        if not name:
            return None
        return name.strip()
