

from urllib.parse import urlencode
import requests
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from uuid import UUID
from datetime import datetime
from typing import Any, Dict, Optional, List
#from database._init_ import AsyncSessionLocal
from app.db import get_sessionmaker
AsyncSessionLocal = get_sessionmaker()

from database.procurement_crud import ProcurementCRUD
from database.uoc_crud import DatabaseCRUD as UocCRUD
from database.sku_crud import SkuCRUD
from database.models import RequestStatus
from managers.quotation_handler import (
    handle_quote_flow,
    notify_user_vendor_quote_update,
    send_vendor_order_confirmation,
)

class MaterialItem(BaseModel):
    material_name: str
    sub_type: Optional[str] = None
    dimensions: Optional[str] = None
    dimension_units: Optional[str] = None
    quantity: float
    quantity_units: Optional[str] = None
    unit_price: Optional[float] = None
    status: Optional[str] = None
    vendor_notes: Optional[str] = None

class Project(BaseModel):
    id: str
    name: Optional[str] = None
    location: Optional[str] = None

class Vendor(BaseModel):
    vendor_id: UUID
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None

class SubmitOrderRequest(BaseModel):
    request_id: UUID
    sender_id: str
    status: str
    notes: Optional[str] = None
    expected_delivery_date: Optional[datetime] = None
    project: Project
    vendors: List[Vendor] = Field(default_factory=list)
    items: List[MaterialItem]
    
class VendorQuoteItem(BaseModel):
    requested_item_id: UUID = Field(alias="requested_item_id")
    quoted_price: float
    price_units: Optional[str] = "unit"
    delivery_days: Optional[int] = 0
    comments: Optional[str] = None

class VendorQuoteResponse(BaseModel):
    request_id: UUID
    vendor_id: UUID
    input: List[VendorQuoteItem]

    class Config:
        allow_population_by_field_name = True

router = APIRouter()

@router.post("/submit-order")
async def submit_order(payload: SubmitOrderRequest):
    print("submit_order :::: payload :", payload)
    from database.models import RequestStatus
    try:
        async with AsyncSessionLocal() as session:
            crud = ProcurementCRUD(session)
            uoc_crud = UocCRUD(session)
            project_id = payload.project.id or None
            #Update request metadata
            if (payload.status!="DRAFT"):
                return
            
            # Project handling policy for material requests:
            # - If an ID is present: do NOT modify existing project details.
            #   Validate it exists; otherwise, return an error.
            # - If no ID: create a new project from provided fields.
            if payload.project:
                if project_id:
                    existing = await uoc_crud.get_project(project_id)
                    if not existing:
                        raise HTTPException(status_code=404, detail="Project not found for provided project_id.")
                    # Do not update existing project fields; only keep the ID
                else:
                    new_project = await uoc_crud.create_project({
                        "name": payload.project.name or "Untitled Project",
                        "sender_id": payload.sender_id,
                        "location": payload.project.location,
                        "no_of_blocks": None,
                        "floors_per_block": None,
                        "flats_per_floor": None,
                    })
                    project_id = new_project.id

            print("apis ::::: submit_order ::::: updating procurement_reqeust")
            await crud.update_procurement_request(
                request_id=str(payload.request_id),
                status=RequestStatus.REQUESTED,
                project_id=project_id,
                delivery_location=payload.project.location,
                notes=payload.notes,
                expected_delivery_date=payload.expected_delivery_date,
                user_editable=False  # lock after submission
            )

            print("apis ::::: submit_order ::::: updating procurement_reqeust done ")
            for item in payload.items:
                item.status = RequestStatus.REQUESTED

            print("apis ::::: submit_order ::::: items status updated. calling  sync")
            #Update individual items
            try:
                await crud.sync_material_request_items_by_ids(
                    request_id=str(payload.request_id),
                    payload_items=[item.dict() for item in payload.items]
                )
            except Exception as e:
                print(f"apis ::::: submit_order ::::: sync failed : {e}")
                raise HTTPException(status_code=500, detail="Failed to sync procurement items.")
            
            print("apis ::::: submit_order :::::  sync finished")

            vendor_targets: List[Dict[str, Optional[str]]] = []
            vendor_ids = []
            seen_vendor_ids = set()
            for vendor in payload.vendors:
                if not vendor.vendor_id:
                    continue
                vendor_ids.append(vendor.vendor_id)
                vid_str = str(vendor.vendor_id)
                if vid_str in seen_vendor_ids:
                    continue
                seen_vendor_ids.add(vid_str)
                vendor_targets.append({
                    "vendor_id": vid_str,
                    "phone": vendor.phone,
                    "name": vendor.name,
                })

            if vendor_ids:
                await crud.add_quote_request_vendors(payload.request_id, vendor_ids)
                print(f"apis ::::: submit_order ::::: vendor_ids persisted : {vendor_ids}")
            else:
                vendor_targets = []
                print("apis ::::: submit_order ::::: no vendors provided in payload")

            print(f"apis ::::: submit_order ::::: vendor targets : {vendor_targets}")
            sender_id = payload.sender_id or await crud.get_sender_id_from_request(str(payload.request_id))
            print(f"apis ::::: submit_order :::::  sender id : {sender_id}")
            if not sender_id:
                raise HTTPException(status_code=404, detail="Request ID not found; cannot resolve sender_id.")
            print(f"submit_order ::::: sender id : {sender_id}, and request id : {payload.request_id}")
            # #Update vendor UUIDs
            # vendor_uuids = list({item.vendor_notes for item in payload.items if item.vendor_notes})

            #Kickoff vendor quote flow
            # state = {
            #     "user_id": await crud.get_user_id_from_request(str(payload.request_id)),  # Optional helper
            #     "messages": [{"content": "Quote requested"}]
            # }
            state = {}

            print("submit_order ::::: sender id : calling handle quote flow")
            await handle_quote_flow(
                state,
                sender_id,
                vendor_targets,
                str(payload.request_id),
                [item.dict() for item in payload.items],
                project_name=payload.project.name,
                project_location=payload.project.location,
            )
            print(f"submit_order ::::: sender id : handle quote flow done")

        return {"success": True, "message": "Procurement request submitted and quote flow started."}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@router.post("/vendor-quotes")
async def vendor_quote_response(payload: VendorQuoteResponse):
    print(f"apis ::::: vendor_quote_response ::::: payload : {payload}")
    try:
        async with AsyncSessionLocal() as session:
            crud = ProcurementCRUD(session)
            had_existing = await crud.insert_vendor_quotes(
                request_id=payload.request_id,
                vendor_id=payload.vendor_id,
                items=payload.input,
            )

            summary = await crud.get_request_summary(payload.request_id)
            vendor_record = await crud.get_vendor_by_id(payload.vendor_id)
            user_id = summary.get("sender_id") if summary else None
            if not user_id:
                user_id = await crud.get_sender_id_from_request(str(payload.request_id))
            vendor_name = getattr(vendor_record, "name", None)

        await notify_user_vendor_quote_update(
            user_id=user_id,
            vendor_name=vendor_name,
            request_id=str(payload.request_id),
            project_name=summary.get("project_name") if summary else None,
            project_location=summary.get("project_location") if summary else None,
            is_update=had_existing,
        )

        return {"success": True, "message": "Quote submitted successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/vendor-quotes/{request_id}")
async def get_vendor_quotes(request_id: UUID):
    print(f"apis ::::: get_vnedor_quotes ::::: request id : {request_id}")
    try:
        async with AsyncSessionLocal() as session:
            crud = ProcurementCRUD(session)
            return await crud.fetch_vendor_quotes_for_request(request_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sku")
async def get_sku_details(
    keyword: str = Query(..., min_length=1, description="Free-form search (brand, size, grade, etc.)"),
    limit: int = Query(25, ge=1, le=100)
) -> Dict[str, Any]:
    try:
        async with AsyncSessionLocal() as session:
            crud = SkuCRUD(session)  
            results = await crud.search_skus_score_sql(keyword, limit=limit)
            return {"count": len(results), "items": results}
    except Exception as e:
        print(f"apis ::::: get_sku_details ::::: exception caught : {e}")


def get_review_order_url(url: str, headers: dict = None, params: dict = None) -> str:
    try:
        print("get_review_order_url:::: Verifying URL")
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        
        if params:
            url = f"{url}?{urlencode(params)}"
        print("get_review_order_url :::: response :", str(response))
        print("get_review_order_url :::: response params code :", params)
        return url
    except Exception as e:
        print("get_review_order_json :::: Error fetching data:", str(e))
        return "Error fetching data: " + str(e)


# ------------------------------ Confirm Order ------------------------------

class ConfirmOrderRequest(BaseModel):
    request_id: UUID
    vendor_id: UUID
    notes: Optional[str] = None
    expected_delivery_date: Optional[datetime] = None


@router.post("/confirm-order")
async def confirm_order(payload: ConfirmOrderRequest):
    """
    Approve a single vendor for the given request, mark other vendor quotes as not selected,
    lock the request and items, compute total, and notify the vendor via WhatsApp (CTA + buttons).
    """
    try:
        async with AsyncSessionLocal() as session:
            crud = ProcurementCRUD(session)

            # Approve vendor + compute order summary
            summary = await crud.approve_vendor_for_request(
                request_id=payload.request_id,
                vendor_id=payload.vendor_id,
                expected_delivery_date=payload.expected_delivery_date,
                notes=payload.notes,
            )

            # Notify vendor via WhatsApp
            try:
                vendor_record = await crud.get_vendor_by_id(payload.vendor_id)
                vendor_phone = getattr(vendor_record, "phone_number", None) if vendor_record else None
                print(f"apis ::::: confirm_order ::::: resolved vendor phone : {vendor_phone}")
                await send_vendor_order_confirmation(
                    request_id=str(payload.request_id),
                    vendor_id=str(payload.vendor_id),
                    order_summary=summary,
                    phone=vendor_phone,
                )
            except Exception as me:
                # Non-fatal
                print(f"apis ::::: confirm_order ::::: vendor notify failed : {me}")

            return {
                "success": True,
                "message": "Order confirmed with selected vendor.",
                "order_total": summary.get("order_total"),
                "vendor_name": summary.get("vendor_name"),
                "line_items": summary.get("items", []),
            }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
