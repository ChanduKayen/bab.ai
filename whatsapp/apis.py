

from urllib.parse import urlencode
import requests
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from uuid import UUID
from datetime import datetime
from typing import Optional, List
#from database._init_ import AsyncSessionLocal
from app.db import get_sessionmaker
AsyncSessionLocal = get_sessionmaker()

from database.procurement_crud import ProcurementCRUD

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

class SubmitOrderRequest(BaseModel):
    request_id: UUID
    status: str = Field(default="requested")
    delivery_location: Optional[str] = None
    notes: Optional[str] = None
    expected_delivery_date: Optional[datetime] = None
    items: List[MaterialItem]
    
class VendorQuoteItem(BaseModel):
    item_id: UUID
    quoted_price: float
    delivery_days: Optional[int]
    comments: Optional[str]

class VendorQuoteResponse(BaseModel):
    request_id: str
    vendor_id: str
    items: List[VendorQuoteItem]

router = APIRouter()

@router.post("/submit-order")
async def submit_order(payload: SubmitOrderRequest):
    print("submit_order :::: payload :", payload)
    from database.models import RequestStatus
    try:
        async with AsyncSessionLocal() as session:
            crud = ProcurementCRUD(session)
            
            payload.status= RequestStatus.REQUESTED
            #Update request metadata
            await crud.update_procurement_request(
                request_id=str(payload.request_id),
                status=payload.status,
                delivery_location=payload.delivery_location,
                notes=payload.notes,
                expected_delivery_date=payload.expected_delivery_date,
                user_editable=False  # lock after submission
            )
            
            for item in payload.items:
                item.status = RequestStatus.REQUESTED

            #Update individual items
            await crud.update_material_request_items(
                request_id=str(payload.request_id),
                updated_items=[item.dict() for item in payload.items]
            )
            
            sender_id = await crud.get_sender_id_from_request(str(payload.request_id)) # To be opitmized further. Get it from state when possible. 
            if not sender_id:
                raise HTTPException(status_code=404, detail="Request ID not found; cannot resolve sender_id.")
            print(f"submit_order ::::: sender id : {sender_id}, and request id : {payload.request_id}")

            #Update individual items
            await crud.update_material_request_items(
                request_id=str(payload.request_id),
                updated_items=[item.dict() for item in payload.items]
            )

            #Update vendor UUIDs
            vendor_uuids = list({item.vendor_notes for item in payload.items if item.vendor_notes})
            from managers.quotation_handler import handle_quote_flow
            state={} 
            await handle_quote_flow(state, vendor_uuids, str(payload.request_id), [item.dict() for item in payload.items])

        return {"success": True, "message": "Procurement request submitted and quote flow started."}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@router.post("/vendor-quote-response")
async def vendor_quote_response(payload: VendorQuoteResponse):
    try:
        async with AsyncSessionLocal() as session:
            crud = ProcurementCRUD(session)
            await crud.insert_vendor_quotes(
                request_id=payload.request_id,
                vendor_id=payload.vendor_id,
                items=payload.items
            )
        return {"success": True, "message": "Quote submitted successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get-vendor-quotes")
async def get_vendor_quotes(request_id: str):
    try:
        async with AsyncSessionLocal() as session:
            crud = ProcurementCRUD(session)
            return await crud.fetch_vendor_quotes_for_request(request_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) 
 

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
    


