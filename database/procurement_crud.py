
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum as PyEnum
from typing import Any, Dict, List, Optional, Set
from uuid import UUID, UUID as _UUID, uuid4

from pydantic import BaseModel
from sqlalchemy import delete, func, literal_column, or_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import joinedload

from database.models import (
    MaterialRequest,
    MaterialRequestItem,
    Project,
    QuoteRequestVendor,
    QuoteRequestVendorStatus,
    QuoteResponse,
    QuoteStatus,
    RequestStatus,
    SkuMaster,
    SkuVendorPrice,
    Vendor,
    VendorQuoteItem as VendorQuoteItemDB,
    VendorFollowupNudge,
)
from database.sku_crud import SkuCRUD
from managers.vendor_followup import compute_next_due

class ProcurementCRUD:
    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    def _now_iso(when: Optional[datetime] = None) -> str:
        dt = (when or datetime.utcnow()).replace(tzinfo=timezone.utc)
        return dt.isoformat()

    def _merge_status_history(self, history: Optional[Dict[str, Any]], status: Any, *, when: Optional[datetime] = None) -> Dict[str, Any]:
        if isinstance(status, PyEnum):
            key = status.value
        else:
            key = str(status)
        updated = dict(history or {})
        updated[key] = self._now_iso(when)
        return updated

    async def _schedule_vendor_followups(
        self,
        request_id: _UUID,
        vendor_ids: Set[_UUID],
        invited_at: datetime,
    ) -> None:
        if not vendor_ids:
            return
        first_due = compute_next_due(invited_at, 0)
        if first_due is None:
            return
        payload = [
            {
                "quote_request_id": request_id,
                "vendor_id": ven_id,
                "invited_at": invited_at,
                "next_nudge_at": first_due,
                "last_nudged_at": None,
                "nudge_stage": 0,
                "updated_at": invited_at,
            }
            for ven_id in vendor_ids
        ]
        stmt = pg_insert(VendorFollowupNudge).values(payload)
        stmt = stmt.on_conflict_do_update(
            index_elements=[VendorFollowupNudge.quote_request_id, VendorFollowupNudge.vendor_id],
            set_={
                "invited_at": invited_at,
                "next_nudge_at": first_due,
                "nudge_stage": 0,
                "last_nudged_at": None,
                "updated_at": invited_at,
            },
        )
        await self.session.execute(stmt)

    async def _clear_vendor_followup(
        self,
        request_id: _UUID,
        vendor_id: _UUID,
    ) -> None:
        await self.session.execute(
            delete(VendorFollowupNudge).where(
                VendorFollowupNudge.quote_request_id == request_id,
                VendorFollowupNudge.vendor_id == vendor_id,
            )
        )

    @staticmethod
    def _coerce_request_status(value: Any, default: RequestStatus) -> RequestStatus:
        if isinstance(value, RequestStatus):
            return value
        if isinstance(value, str):
            try:
                return RequestStatus(value.upper())
            except ValueError:
                return default
        return default

    @staticmethod
    def _coerce_quote_status(value: Any, default: QuoteStatus) -> QuoteStatus:
        if isinstance(value, QuoteStatus):
            return value
        if isinstance(value, str):
            try:
                return QuoteStatus(value.upper())
            except ValueError:
                return default
        return default

    @staticmethod
    def _coerce_vendor_status(value: Any, default: QuoteRequestVendorStatus) -> QuoteRequestVendorStatus:
        if isinstance(value, QuoteRequestVendorStatus):
            return value
        if isinstance(value, str):
            try:
                return QuoteRequestVendorStatus(value.upper())
            except ValueError:
                return default
        return default

    async def _set_request_status(
        self,
        request_id: _UUID,
        status: RequestStatus,
        *,
        extra_values: Optional[Dict[str, Any]] = None,
        when: Optional[datetime] = None,
    ) -> None:
        result = await self.session.execute(
            select(MaterialRequest.status_history).where(MaterialRequest.id == request_id)
        )
        history = result.scalar_one_or_none() or {}
        merged = self._merge_status_history(history, status, when=when)
        values = {"status_history": merged, "status": status}
        if extra_values:
            values.update(extra_values)
        await self.session.execute(
            update(MaterialRequest).where(MaterialRequest.id == request_id).values(**values)
        )

    async def _set_request_items_status(
        self,
        request_id: _UUID,
        status: RequestStatus,
        *,
        when: Optional[datetime] = None,
    ) -> None:
        result = await self.session.execute(
            select(MaterialRequestItem.id, MaterialRequestItem.status_history)
            .where(MaterialRequestItem.material_request_id == request_id)
        )
        rows = result.all()
        for item_id, history in rows:
            merged = self._merge_status_history(history or {}, status, when=when)
            await self.session.execute(
                update(MaterialRequestItem)
                .where(MaterialRequestItem.id == item_id)
                .values(status_history=merged, status=status)
            )

    async def _set_quote_request_vendor_status(
        self,
        request_id: _UUID,
        vendor_id: _UUID,
        status: QuoteRequestVendorStatus,
        *,
        when: Optional[datetime] = None,
    ) -> None:
        result = await self.session.execute(
            select(QuoteRequestVendor.status_history)
            .where(
                QuoteRequestVendor.quote_request_id == request_id,
                QuoteRequestVendor.vendor_id == vendor_id,
            )
        )
        history = result.scalar_one_or_none() or {}
        merged = self._merge_status_history(history, status, when=when)
        await self.session.execute(
            update(QuoteRequestVendor)
            .where(
                QuoteRequestVendor.quote_request_id == request_id,
                QuoteRequestVendor.vendor_id == vendor_id,
            )
            .values(status_history=merged, status=status)
        )
        if status not in (
            QuoteRequestVendorStatus.INVITED,
            QuoteRequestVendorStatus.NOTIFIED,
        ):
            await self._clear_vendor_followup(request_id, vendor_id)

    async def _set_vendor_quote_item_status(
        self,
        request_id: _UUID,
        vendor_id: _UUID,
        status: QuoteStatus,
        *,
        when: Optional[datetime] = None,
        exclude_vendor: Optional[_UUID] = None,
    ) -> None:
        stmt = select(
            VendorQuoteItemDB.id,
            VendorQuoteItemDB.status_history,
        ).where(VendorQuoteItemDB.quote_request_id == request_id)
        if exclude_vendor is not None:
            stmt = stmt.where(VendorQuoteItemDB.vendor_id != exclude_vendor)
        else:
            stmt = stmt.where(VendorQuoteItemDB.vendor_id == vendor_id)

        rows = (await self.session.execute(stmt)).all()
        for quote_id, history in rows:
            merged = self._merge_status_history(history or {}, status, when=when)
            await self.session.execute(
                update(VendorQuoteItemDB)
                .where(VendorQuoteItemDB.id == quote_id)
                .values(status_history=merged, status=status)
            )

    async def save_procurement_request(
        self,
        request_id: str,
        project_id: str,
        sender_id: str,
        status,
        delivery_location: str,
        notes: str,
        created_at,
        updated_at=None,
        expected_delivery_date=None,
        user_editable=True,
        items: list = None
    ):
        try:
            status_enum = self._coerce_request_status(status, RequestStatus.DRAFT)
            request = MaterialRequest(
                id=request_id,
                project_id=project_id,
                sender_id=sender_id,
                status_history=self._merge_status_history({}, status_enum, when=created_at),
                status=status_enum,
                delivery_location=delivery_location,
                notes=notes,
                created_at=created_at,
                updated_at=updated_at or created_at,
                expected_delivery_date=expected_delivery_date,
                user_editable=user_editable
            )

            print("procurement_crud.py :::: save_procurement_request :::: material request : ", request)
            for item in items or []:
                item_status = self._coerce_request_status(item.get("status"), RequestStatus.DRAFT)
                request_item = MaterialRequestItem(
                    material_request_id=request_id,
                    material_name=item["material_name"],
                    sub_type=item.get("sub_type"),
                    dimensions=item.get("dimensions"),
                    dimension_units=item.get("dimension_units"),
                    quantity=item["quantity"],
                    quantity_units=item.get("quantity_units"),
                    unit_price=item.get("unit_price"),
                    status_history=self._merge_status_history({}, item_status, when=created_at),
                    status=item_status,
                    vendor_notes=item.get("vendor_notes")
                )
                request.items.append(request_item)
                print("procurement_crud.py :::: save_procurement_request :::: material request item : ", request_item)

            self.session.add(request)
            print("procurement_crud.py :::: save_procurement_request :::: session added request")
            await self.session.commit()
            print("procurement_crud :::: [CRUD] Procurement request saved to DB.")
            return
        except Exception as e:
            await self.session.rollback()
            print("procurement_crud ::::: Error in saving :::: [CRUD] Failed to save procurement request:", e)
            raise

    async def update_procurement_request(
        self,
        request_id: str,
        status,
        project_id,
        delivery_location: str,
        notes: str,
        updated_at=None,
        expected_delivery_date=None,
        user_editable=True
    ):
        try:
            print("procurement_crud ::::: update_procurement_request ::::: request_id : ", request_id)
            result = await self.session.execute(
                select(MaterialRequest.status_history).where(MaterialRequest.id == request_id)
            )
            current_history = result.scalar_one_or_none() or {}
            status_enum = self._coerce_request_status(status, RequestStatus.REQUESTED)
            merged_history = self._merge_status_history(current_history, status_enum)

            await self.session.execute(
                update(MaterialRequest)
                .where(MaterialRequest.id == request_id)
                .values(
                    status_history=merged_history,
                    status=status_enum,
                    project_id=project_id,
                    delivery_location=delivery_location,
                    notes=notes,
                    updated_at=updated_at or datetime.utcnow(),
                    expected_delivery_date=expected_delivery_date,
                    user_editable=user_editable
                )
            )
            await self.session.commit()
        except SQLAlchemyError as e:
            await self.session.rollback()
            print("procurement_crud ::::: Error in updating request ::::", e)
            raise

    async def update_material_request_items(self, request_id, updated_items):
        try:
            # Fetch existing items
            existing_items = (await self.session.execute(
                select(MaterialRequestItem).where(MaterialRequestItem.material_request_id == request_id)
            )).scalars().all()

            existing_lookup = {
                (item.material_name.lower().strip()): item
                for item in existing_items
            }

            for upd in updated_items:
                key = upd["material_name"].lower().strip()
                if key in existing_lookup:
                    # Update existing row
                    existing_item = existing_lookup[key]
                    existing_item.sub_type = upd.get("sub_type")
                    existing_item.dimensions = upd.get("dimensions")
                    existing_item.dimension_units = upd.get("dimension_units")
                    existing_item.quantity = upd["quantity"]
                    existing_item.quantity_units = upd.get("quantity_units")
                    existing_item.unit_price = upd.get("unit_price")
                    status_enum = self._coerce_request_status(
                        upd.get("status"), existing_item.status or RequestStatus.DRAFT
                    )
                    existing_item.status_history = self._merge_status_history(
                        existing_item.status_history, status_enum
                    )
                    existing_item.status = status_enum
                    existing_item.vendor_notes = upd.get("vendor_notes")
                else:
                    # New item to insert
                    status_enum = self._coerce_request_status(upd.get("status"), RequestStatus.DRAFT)
                    new_item = MaterialRequestItem(
                        material_request_id=request_id,
                        material_name=upd["material_name"],
                        sub_type=upd.get("sub_type"),
                        dimensions=upd.get("dimensions"),
                        dimension_units=upd.get("dimension_units"),
                        quantity=upd["quantity"],
                        quantity_units=upd.get("quantity_units"),
                        unit_price=upd.get("unit_price"),
                        status_history=self._merge_status_history({}, status_enum),
                        status=status_enum,
                        vendor_notes=upd.get("vendor_notes")
                    )
                    self.session.add(new_item)

            await self.session.commit()

        except SQLAlchemyError as e:
            await self.session.rollback()
            print("procurement_crud ::::: Error in updating items ::::", e)
            raise
    
    async def get_sender_id_from_request(self, request_id: str) -> Optional[str]:
        """
        Given a material_request.id, return the associated sender_id.
        """
        try:
            result = await self.session.execute(
                select(MaterialRequest.sender_id).where(MaterialRequest.id == request_id)
            )
            sender_id = result.scalar_one_or_none()
            return sender_id  # e.g. "919966330468" or None if not found
        except SQLAlchemyError as e:
            print("procurement_crud ::::: Error in get_sender_id_from_request ::::", e)
            raise
    
    async def sync_material_request_items_by_ids(
        self,
        request_id: str,                       # UUID string OK
        payload_items: List[Dict[str, Any]],   # e.g. [item.dict() for item in payload.items]
        default_status=RequestStatus.REQUESTED                    # e.g. RequestStatus.REQUESTED (optional)
    ) -> Dict[str, int]:
        """
        Full sync for material_request_items of a given request in TWO statements:
          1) DELETE rows for this request whose id is NOT in payload
          2) INSERT ... ON CONFLICT(id) DO UPDATE for ALL payload rows
        Returns server-accurate counts: {"inserted": X, "updated": Y, "deleted": Z}
        """
        try:
            # ---------- Normalize payload: ensure UUID ids & attach request_id ----------
             # Snapshot existing ids (so we can split inserted vs updated without system cols)
            req_uuid = _UUID(str(request_id))
            existing_ids: Set[_UUID] = set(
                (await self.session.execute(
                    select(MaterialRequestItem.id).where(
                        MaterialRequestItem.material_request_id == req_uuid
                    )
                )).scalars().all()
            )
            
            rows: List[Dict[str, Any]] = []
            payload_ids: List[_UUID] = []


            for src in (payload_items or []):
                row_id = src.get("id")
                if row_id:
                    row_id = _UUID(str(row_id))
                else:
                    row_id = uuid4()  # generate for truly new rows

                print(f"procurement_crud ::::: payload status {src.get('status')}")
                raw_status = src.get("status")
                if default_status is not None:
                    raw_status = default_status
                status_enum = self._coerce_request_status(raw_status, RequestStatus.DRAFT)

                rows.append({
                    "id": row_id,
                    "material_request_id": req_uuid,
                    "material_name": src.get("material_name"),
                    "sub_type": src.get("sub_type"),
                    "dimensions": src.get("dimensions"),
                    "dimension_units": src.get("dimension_units"),
                    "quantity": src.get("quantity"),
                    "quantity_units": src.get("quantity_units"),
                    "unit_price": src.get("unit_price"),
                    "status_history": self._merge_status_history({}, status_enum),
                    "status": status_enum,
                    "vendor_notes": src.get("vendor_notes"),
                })
                payload_ids.append(row_id)
            print(f"procurement_crud ::::: sync_material_request_items_by_ids ::::: rows : {rows}")
            print(f"procurement_crud ::::: sync_material_request_items_by_ids ::::: payload_ids : {payload_ids}")
            # ----------------------------- 1) BULK DELETE ------------------------------
            if payload_ids:
                del_stmt = (
                    delete(MaterialRequestItem)
                    .where(MaterialRequestItem.material_request_id == req_uuid)
                    .where(~MaterialRequestItem.id.in_(payload_ids))
                    .returning(MaterialRequestItem.id)
                )
            else:
                # Empty payload => remove all items for this request
                del_stmt = (
                    delete(MaterialRequestItem)
                    .where(MaterialRequestItem.material_request_id == req_uuid)
                    .returning(MaterialRequestItem.id)
                )

            del_res = await self.session.execute(del_stmt)
            deleted = len(del_res.fetchall())
            print(f"procurement_crud ::::: sync_material_request_items_by_ids ::::: deleted : {deleted} rows")
            # ----------------------------- 2) BULK UPSERT ------------------------------
            if rows:
                upsert_stmt = pg_insert(MaterialRequestItem).values(rows)
                excluded = upsert_stmt.excluded

                # WHERE guards against rows "jumping" between requests on conflict
                upsert_stmt = (
                    upsert_stmt.on_conflict_do_update(
                        index_elements=[MaterialRequestItem.id],
                        set_={
                            "material_name":   excluded.material_name,
                            "sub_type":        excluded.sub_type,
                            "dimensions":      excluded.dimensions,
                            "dimension_units": excluded.dimension_units,
                            "quantity":        excluded.quantity,
                            "quantity_units":  excluded.quantity_units,
                            "unit_price":      excluded.unit_price,
                            "status_history":  func.merge_status_history(
                                MaterialRequestItem.status_history,
                                excluded.status_history,
                            ),
                            "status":          excluded.status,
                            "vendor_notes":    excluded.vendor_notes,
                            "material_request_id": excluded.material_request_id,  # stays same by WHERE
                        },
                        where=(MaterialRequestItem.material_request_id == excluded.material_request_id),
                    )
                    # Distinguish inserted vs updated via xmax system column (Postgres)
                    .returning(MaterialRequestItem.id)
                )
                print(f"procurement_crud ::::: sync_material_request_items_by_ids ::::: upsert_stmt : {upsert_stmt}")
                upsert_res = await self.session.execute(upsert_stmt)
                print(f"procurement_crud ::::: sync_material_request_items_by_ids ::::: upsert_res : {upsert_res}")
                upserted_ids = [row[0] for row in upsert_res.fetchall()]
                inserted = sum(1 for _id in upserted_ids if _id not in existing_ids)
                updated  = len(upserted_ids) - inserted
            else:
                inserted = 0
                updated = 0
            print(f"procurement_crud ::::: sync_material_request_items_by_ids ::::: commiting session")
            await self.session.commit()
            print(f"procurement_crud ::::: sync_material_request_items_by_ids ::::: inserted : {inserted}, updated : {updated}, deleted : {deleted}")
            return {"inserted": inserted, "updated": updated, "deleted": deleted}

        except SQLAlchemyError as e:
            await self.session.rollback()
            print("procurement_crud ::::: Error in sync_material_request_items_by_ids ::::", e)
            raise


    async def add_quote_request_vendors(self, request_id: _UUID, vendor_ids: List[_UUID]) -> None:
        try:
            unique_ids: Set[_UUID] = {_UUID(str(v_id)) for v_id in vendor_ids if v_id}
            if not unique_ids:
                print("procurement_crud ::::: add_quote_request_vendors ::::: no vendor ids provided")
                return

            req_uuid = _UUID(str(request_id))
            invited_at = datetime.utcnow()
            print(f"procurement_crud ::::: add_quote_request_vendors ::::: unique vendor ids : {unique_ids}")
            values = [
                {
                    "quote_request_id": req_uuid,
                    "vendor_id": ven_id,
                    "status_history": self._merge_status_history(
                        {}, QuoteRequestVendorStatus.INVITED
                    ),
                    "status": QuoteRequestVendorStatus.INVITED,
                }
                for ven_id in unique_ids
            ]

            stmt = pg_insert(QuoteRequestVendor).values(values)
            stmt = stmt.on_conflict_do_nothing()
            print(f"procurement_crud ::::: add_quote_request_vendors ::::: inserting vendors : {values}")
            await self.session.execute(stmt)
            await self._schedule_vendor_followups(req_uuid, unique_ids, invited_at)
            await self.session.commit()
            print(f"procurement_crud ::::: add_quote_request_vendors ::::: inserted count : {len(values)}")
        except Exception as e:
            await self.session.rollback()
            print("procurement_crud ::::: add_quote_request_vendors ::::: exception :", e)
            raise

    async def get_vendor_by_id(self, vendor_id: _UUID) -> Optional[Vendor]:
        try:
            if not vendor_id:
                return None
            vid = _UUID(str(vendor_id))
            result = await self.session.execute(
                select(Vendor).where(Vendor.vendor_id == vid)
            )
            vendor = result.scalar_one_or_none()
            if not vendor:
                print(f"procurement_crud ::::: get_vendor_by_id ::::: vendor not found for id : {vendor_id}")
            else:
                print(f"procurement_crud ::::: get_vendor_by_id ::::: fetched vendor : {vendor.vendor_id}")
            return vendor
        except Exception as e:
            print("procurement_crud ::::: get_vendor_by_id ::::: exception :", e)
            raise

    async def get_request_summary(self, request_id: _UUID) -> Dict[str, Any]:
        try:
            req_uuid = _UUID(str(request_id))
            stmt = (
                select(
                    MaterialRequest.sender_id,
                    MaterialRequest.delivery_location,
                    MaterialRequest.expected_delivery_date,
                    Project.name,
                    Project.location,
                )
                .outerjoin(Project, Project.id == MaterialRequest.project_id)
                .where(MaterialRequest.id == req_uuid)
            )
            row = (await self.session.execute(stmt)).first()
            if not row:
                return {}

            sender_id, delivery_location, expected_date, project_name, project_location = row
            return {
                "sender_id": sender_id,
                "delivery_location": delivery_location,
                "expected_delivery_date": expected_date.isoformat() if expected_date else None,
                "project_name": project_name,
                "project_location": project_location,
            }
        except Exception as e:
            print("procurement_crud ::::: get_request_summary ::::: exception :", e)
            return {}

    async def get_request_item_specs(self, request_id) -> Dict[str, dict]:
        try:
            result = await self.session.execute(
                select(MaterialRequestItem).where(MaterialRequestItem.material_request_id==request_id)
            )
            items_details = result.scalars().all()
            return {
            str(row.id): {
                "material_name": row.material_name,
                "sub_type": row.sub_type,
                "dimensions": row.dimensions,
                "dimension_units": row.dimension_units,
            }
            for row in items_details
        }
        except Exception as e:
            print(f"apis ::::: get_materials_requested_from_request_id ::::: Exception in fetch : {e}")

    class VendorQuoteItemPayload(BaseModel):
        requested_item_id: UUID
        quoted_price: float
        price_units: Optional[str] = "unit"
        sku_id: str
        delivery_days: Optional[int] = 0
        comments: Optional[str] = None  

    async def insert_vendor_quotes(
        self,
        request_id: _UUID,
        vendor_id: _UUID,
        items: List[VendorQuoteItemPayload],
    ) -> bool:
        """
        Upsert each vendor quote line by (quote_request_id, vendor_id, request_item_id).
        Sets created_at on insert and updated_at on both insert/update.
        """
        print(
            "procurement_crud ::::: insert_vendor_quotes ::::: started for request_id :",
            request_id,
            ", vendor_id :",
            vendor_id,
        )

        now = datetime.utcnow()
        skuCRUD = SkuCRUD(self.session)
        sku_queue: List[VendorQuoteItemPayload] = []

        req_uuid = _UUID(str(request_id))
        ven_uuid = _UUID(str(vendor_id))

        existing_stmt = (
            select(func.count())
            .select_from(VendorQuoteItemDB)
            .where(
                VendorQuoteItemDB.quote_request_id == req_uuid,
                VendorQuoteItemDB.vendor_id == ven_uuid,
            )
        )
        existing_count = (await self.session.execute(existing_stmt)).scalar() or 0
        had_existing = existing_count > 0

        for item in items:
            try:
                price_unit = item.price_units or "unit"

                new_history = self._merge_status_history({}, QuoteStatus.QUOTED, when=now)
                stmt = (
                    pg_insert(VendorQuoteItemDB)
                    .values(
                        quote_request_id=req_uuid,
                        vendor_id=ven_uuid,
                        request_item_id=item.requested_item_id,
                        quoted_price=item.quoted_price,
                        price_unit=price_unit,
                        delivery_days=item.delivery_days,
                        comments=item.comments,
                        status_history=new_history,
                        status=QuoteStatus.QUOTED,
                        created_at=now,
                        updated_at=now,
                    )
                    .on_conflict_do_update(
                        constraint="uq_vendor_quote_unique_line",
                        set_={
                            "quoted_price": item.quoted_price,
                            "price_unit": price_unit,
                            "delivery_days": item.delivery_days,
                            "comments": item.comments,
                            "status_history": func.merge_status_history(
                                VendorQuoteItemDB.status_history,
                                new_history,
                            ),
                            "status": QuoteStatus.QUOTED,
                            "updated_at": now,
                        },
                    )
                )

                print(f"procurement_crud ::::: insert_vendor_quotes ::::: executing stmt : {stmt}")
                await self.session.execute(stmt)
                print(f"procurement_crud ::::: insert_vendor_quotes ::::: upsert OK for item_name={item.requested_item_id}")

                sku_queue.append(item)

            except Exception as e:
                await self.session.rollback()
                print(f"procurement_crud ::::: insert_vendor_quotes ::::: exception for item_id={item.requested_item_id} : {e}")
                raise

        for pending_item in sku_queue:
            try:
                await skuCRUD.process_vendor_quote_item(str(req_uuid), str(ven_uuid), pending_item)
            except Exception as err:
                await self.session.rollback()
                print(
                    "procurement_crud ::::: insert_vendor_quotes ::::: sku processing failed for item_id="
                    f"{getattr(pending_item, 'requested_item_id', None)} : {err}"
                )
                raise

        await self._set_quote_request_vendor_status(
            req_uuid,
            ven_uuid,
            QuoteRequestVendorStatus.RESPONDED,
            when=now,
        )
        await self.session.commit()
        return had_existing
    
    async def fetch_vendor_quotes_for_request(self, request_id: _UUID):
        """
        Return quotes grouped by vendor for a given request_id.
        Includes material details via join to MaterialRequestItem.
        """
        result = await self.session.execute(
            select(VendorQuoteItemDB)
            .filter(VendorQuoteItemDB.quote_request_id == request_id)
            .options(
                joinedload(VendorQuoteItemDB.vendor),
                joinedload(VendorQuoteItemDB.request_item),
            )
        )
        quotes = result.scalars().all()

        response = {}
        for quote in quotes:
            quote_vendor_id = str(quote.vendor_id)
            if quote_vendor_id not in response:
                response[quote_vendor_id] = {
                    "vendor_name": quote.vendor.name if isinstance(quote.vendor, Vendor) else "Unknown",
                    "quotes": [],
                }

            material_request_item: MaterialRequestItem = quote.request_item
            response[quote_vendor_id]["quotes"].append(
                {
                    "request_item_id": str(quote.request_item_id),
                    "material_name": getattr(material_request_item, "material_name", None),
                    "sub_type": getattr(material_request_item, "sub_type", None),
                    "dimensions": getattr(material_request_item, "dimensions", None),
                    "dimension_units": getattr(material_request_item, "dimension_units", None),
                    "quantity": getattr(material_request_item, "quantity", None),
                    "quantity_units": getattr(material_request_item, "quantity_units", None),
                    "quoted_price": quote.quoted_price,
                    "price_unit": quote.price_unit,
                    "delivery_days": quote.delivery_days,
                    "comments": quote.comments,
                    "created_at": quote.created_at.isoformat() if quote.created_at else None,
                    "updated_at": quote.updated_at.isoformat() if quote.updated_at else None,
                }
            )

        return response

    async def approve_vendor_for_request(
        self,
        request_id: _UUID,
        vendor_id: _UUID,
        expected_delivery_date: Optional[datetime] = None,
        notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Approve a single vendor for a request.
        - Validates vendor has quotes for request
        - Sets MaterialRequest.status=APPROVED, locks editing
        - Sets all MaterialRequestItem.status=APPROVED
        - Sets chosen VendorQuoteItem.status=APPROVED; others => REJECTED
        - Returns a summary with items and order_total
        """
        try:
            req_uuid = _UUID(str(request_id))
            ven_uuid = _UUID(str(vendor_id))

            # Validate vendor has quotes for this request
            exists_q = (
                select(VendorQuoteItemDB.id)
                .where(
                    VendorQuoteItemDB.quote_request_id == req_uuid,
                    VendorQuoteItemDB.vendor_id == ven_uuid,
                )
                .limit(1)
            )
            exists = (await self.session.execute(exists_q)).scalar_one_or_none()
            if not exists:
                raise ValueError("Selected vendor has no quotes for this request")

            now = datetime.utcnow()

            await self._set_request_status(
                req_uuid,
                RequestStatus.APPROVED,
                when=now,
                extra_values={
                    "approved_vendor": ven_uuid,
                    "expected_delivery_date": expected_delivery_date,
                    "notes": notes,
                    "updated_at": now,
                    "user_editable": False,
                },
            )

            await self._set_request_items_status(
                req_uuid,
                RequestStatus.APPROVED,
                when=now,
            )

            await self._set_vendor_quote_item_status(
                req_uuid,
                ven_uuid,
                QuoteStatus.APPROVED,
                when=now,
            )
            await self._set_vendor_quote_item_status(
                req_uuid,
                ven_uuid,
                QuoteStatus.REJECTED,
                when=now,
                exclude_vendor=ven_uuid,
            )

            await self._set_quote_request_vendor_status(
                req_uuid,
                ven_uuid,
                QuoteRequestVendorStatus.APPROVED,
                when=now,
            )

            other_vendor_ids = (
                await self.session.execute(
                    select(QuoteRequestVendor.vendor_id)
                    .where(
                        QuoteRequestVendor.quote_request_id == req_uuid,
                        QuoteRequestVendor.vendor_id != ven_uuid,
                    )
                )
            ).scalars().all()
            for other_vendor in other_vendor_ids:
                await self._set_quote_request_vendor_status(
                    req_uuid,
                    other_vendor,
                    QuoteRequestVendorStatus.REJECTED,
                    when=now,
                )

            # Fetch summary rows for total computation
            q = (
                select(
                    VendorQuoteItemDB.request_item_id,
                    VendorQuoteItemDB.quoted_price,
                    VendorQuoteItemDB.price_unit,
                    VendorQuoteItemDB.delivery_days,
                    VendorQuoteItemDB.comments,
                    MaterialRequestItem.material_name,
                    MaterialRequestItem.quantity,
                    MaterialRequestItem.quantity_units,
                    MaterialRequestItem.sub_type,
                    MaterialRequestItem.dimensions,
                    MaterialRequestItem.dimension_units,
                )
                .join(
                    MaterialRequestItem,
                    MaterialRequestItem.id == VendorQuoteItemDB.request_item_id,
                )
                .where(
                    VendorQuoteItemDB.quote_request_id == req_uuid,
                    VendorQuoteItemDB.vendor_id == ven_uuid,
                )
            )
            rows = (await self.session.execute(q)).all()

            # Vendor name
            vendor_res = await self.session.execute(select(Vendor).where(Vendor.vendor_id == ven_uuid))
            vendor_row: Optional[Vendor] = vendor_res.scalar_one_or_none()
            vendor_name = vendor_row.name if vendor_row else "Selected Vendor"

            items: List[Dict[str, Any]] = []
            order_total = 0.0
            for r in rows:
                # SQLAlchemy row tuple mapping
                request_item_id, quoted_price, price_unit, delivery_days, comments, \
                    material_name, quantity, quantity_units, sub_type, dimensions, dimension_units = r

                line_total = (quantity or 0) * float(quoted_price or 0)
                order_total += line_total
                items.append({
                    "request_item_id": str(request_item_id),
                    "material_name": material_name,
                    "sub_type": sub_type,
                    "dimensions": dimensions,
                    "dimension_units": dimension_units,
                    "quantity": quantity,
                    "quantity_units": quantity_units,
                    "quoted_price": quoted_price,
                    "price_unit": price_unit,
                    "delivery_days": delivery_days,
                    "comments": comments,
                    "line_total": line_total,
                })

            summary_info = await self.get_request_summary(req_uuid)
            await self.session.commit()

            return {
                "vendor_id": str(vendor_id),
                "vendor_name": vendor_name,
                "order_total": round(order_total, 2),
                "items": items,
                "project_name": summary_info.get("project_name") if summary_info else None,
                "project_location": summary_info.get("project_location") if summary_info else None,
                "delivery_location": summary_info.get("delivery_location") if summary_info else None,
                "expected_delivery_date": summary_info.get("expected_delivery_date") if summary_info else expected_delivery_date,
            }

        except Exception as e:
            await self.session.rollback()
            print("procurement_crud ::::: approve_vendor_for_request ::::: exception :", e)
            raise

    async def vendor_decline_and_reopen(self, request_id: _UUID, vendor_id: _UUID) -> None:
        """
        Handle vendor decline after approval: mark vendor quotes REJECTED and
        reopen the request for reselection by setting request and items to QUOTED.
        """
        try:
            req_uuid = _UUID(str(request_id))
            ven_uuid = _UUID(str(vendor_id))

            now = datetime.utcnow()

            await self._set_vendor_quote_item_status(
                req_uuid,
                ven_uuid,
                QuoteStatus.REJECTED,
                when=now,
            )
            await self.session.execute(
                update(VendorQuoteItemDB)
                .where(
                    VendorQuoteItemDB.quote_request_id == req_uuid,
                    VendorQuoteItemDB.vendor_id == ven_uuid,
                )
                .values(comments="Declined by vendor")
            )

            await self._set_request_status(
                req_uuid,
                RequestStatus.QUOTED,
                when=now,
                extra_values={
                    "updated_at": now,
                    "user_editable": False,
                    "approved_vendor": None,
                    "delivered_at": None,
                },
            )

            await self._set_request_items_status(
                req_uuid,
                RequestStatus.QUOTED,
                when=now,
            )
            await self._set_quote_request_vendor_status(
                req_uuid,
                ven_uuid,
                QuoteRequestVendorStatus.DECLINED,
                when=now,
            )

            await self.session.commit()
        except Exception as e:
            await self.session.rollback()
            print("procurement_crud ::::: vendor_decline_and_reopen ::::: exception :", e)
            raise

    async def mark_vendor_confirmation(self, request_id: _UUID, vendor_id: _UUID) -> None:
        """
        Capture vendor confirmation by setting delivered_at and reinforcing invite status.
        Only updates when the confirming vendor matches the approved vendor.
        """
        try:
            req_uuid = _UUID(str(request_id))
            ven_uuid = _UUID(str(vendor_id))
            now = datetime.utcnow()

            await self.session.execute(
                update(MaterialRequest)
                .where(
                    MaterialRequest.id == req_uuid,
                    MaterialRequest.approved_vendor == ven_uuid,
                )
                .values(delivered_at=now, updated_at=now)
            )
            await self._set_quote_request_vendor_status(
                req_uuid,
                ven_uuid,
                QuoteRequestVendorStatus.APPROVED,
                when=now,
            )
            await self.session.commit()
        except Exception as e:
            await self.session.rollback()
            print("procurement_crud ::::: mark_vendor_confirmation ::::: exception :", e)
            raise
