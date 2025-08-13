
from database.models import MaterialRequest, MaterialRequestItem, QuoteRequestVendor, QuoteResponse
from database.models import VendorQuoteItem
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import update, delete
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import joinedload
from datetime import datetime
from typing import List

class ProcurementCRUD:
    def __init__(self, session: AsyncSession):
        self.session = session
  
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
            request = MaterialRequest(
                id=request_id,
                project_id=project_id,
                sender_id=sender_id,
                status=status,
                delivery_location=delivery_location,
                notes=notes,
                created_at=created_at,
                updated_at=updated_at or created_at,
                expected_delivery_date=expected_delivery_date,
                user_editable=user_editable
            )

            print("procurement_crud.py :::: save_procurement_request :::: material request : ", request)
            for item in items or []:
                request_item = MaterialRequestItem(
                    material_request_id=request_id,
                    material_name=item["material_name"],
                    sub_type=item.get("sub_type"),
                    dimensions=item.get("dimensions"),
                    dimension_units=item.get("dimension_units"),
                    quantity=item["quantity"],
                    quantity_units=item.get("quantity_units"),
                    unit_price=item.get("unit_price"),
                    status=item.get("status"),
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
        delivery_location: str,
        notes: str,
        updated_at=None,
        expected_delivery_date=None,
        user_editable=True
    ):
        try:
            await self.session.execute(
                update(MaterialRequest)
                .where(MaterialRequest.id == request_id)
                .values(
                    status=status,
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
                    existing_item.status = upd.get("status")
                    existing_item.vendor_notes = upd.get("vendor_notes")
                else:
                    # New item to insert
                    new_item = MaterialRequestItem(
                        material_request_id=request_id,
                        material_name=upd["material_name"],
                        sub_type=upd.get("sub_type"),
                        dimensions=upd.get("dimensions"),
                        dimension_units=upd.get("dimension_units"),
                        quantity=upd["quantity"],
                        quantity_units=upd.get("quantity_units"),
                        unit_price=upd.get("unit_price"),
                        status=upd.get("status"),
                        vendor_notes=upd.get("vendor_notes")
                    )
                    self.session.add(new_item)

            await self.session.commit()
        except SQLAlchemyError as e:
            await self.session.rollback()
            print("procurement_crud ::::: Error in updating items ::::", e)
            raise

    async def insert_vendor_quotes(self, request_id: str, vendor_id: str, items: List[VendorQuoteItem]):
        from uuid import uuid4
        from database.models import QuoteResponse

        # Ensure vendor is tracked in quote_request_vendors
        exists = await self.session.execute(
            select(QuoteRequestVendor).filter_by(
                quote_request_id=request_id, vendor_id=vendor_id
            )
        )
        if not exists.scalar():
            self.session.add(QuoteRequestVendor(
                quote_request_id=request_id,
                vendor_id=vendor_id
            ))

        for item in items:
            quote = QuoteResponse(
                id=uuid4(),
                quote_request_id=request_id,
                vendor_id=vendor_id,
                material_name="UNKNOWN",  # update if you can link it
                specification=item.comments,
                unit="unit",  # pull from related request item if needed
                price=item.quoted_price,
                available_quantity=None,
                notes=item.comments,
            )
            self.session.add(quote)

        await self.session.commit()
    
    async def fetch_vendor_quotes_for_request(self, request_id: str):
        result = await self.session.execute(
            select(QuoteResponse)
            .filter(QuoteResponse.quote_request_id == request_id)
            .order_by(QuoteResponse.created_at)
            .options(joinedload(QuoteResponse.vendor))
        )
        quotes = result.scalars().all()

        # Return structured JSON
        response = {}
        for quote in quotes:
            vendor_id = str(quote.vendor_id)
            if vendor_id not in response:
                response[vendor_id] = {
                    "vendor_name": quote.vendor.name if quote.vendor else "Unknown",
                    "quotes": []
                }
            response[vendor_id]["quotes"].append({
                "material_name": quote.material_name,
                "specification": quote.specification,
                "unit": quote.unit,
                "price": quote.price,
                "available_quantity": quote.available_quantity,
                "notes": quote.notes,
                "created_at": quote.created_at.isoformat()
            })
        return response
 