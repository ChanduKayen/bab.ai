

from models.chatstate import AgentState
from database.procurement_crud import ProcurementCRUD
from uuid import uuid4
from datetime import datetime
from database.models import RequestStatus  # Import your Enum

class ProcurementManager:
    def __init__(self, session):
        self.crud = ProcurementCRUD(session)

    async def persist_procurement(self, state: AgentState):
        """
        Persist the initially extracted materials into DB when user starts a procurement request.
        This is triggered only once after the first extraction.
        """
        procurement_details = state.get("procurement_details", {})
        sender_id = state.get("sender_id")
        project_id = state.get("active_project_id")

        materials = procurement_details.get("materials", [])
        location = procurement_details.get("location")
        notes = procurement_details.get("notes")
        expected_delivery_date = procurement_details.get("expected_delivery_date")
        user_editable = procurement_details.get("user_editable", True)

        if not materials:
            print("Procurement_manager :::: persist_procurement :::: if no material ::::  No materials to persist.")
            return

        # Build items for MaterialRequestItem model (order as per table)
        request_items = []
        state["active_material_request_id"] = str(uuid4())
        print("[Persist Procurement] Generated request ID:", state["active_material_request_id"])
        for m in materials:
            material_name = m.get("material")
            sub_type = m.get("sub_type")
            dimensions = m.get("dimensions")
            dimension_units = m.get("dimension_units")
            quantity = m.get("quantity")
            quantity_units = m.get("quantity_units")
            unit_price = m.get("unit_price")
            status = m.get("status", RequestStatus.DRAFT)
            vendor_notes = m.get("vendor_notes")
            if not material_name or not quantity:
                continue
            request_items.append({
                "material_name": material_name,
                "sub_type": sub_type,
                "dimensions": dimensions,
                "dimension_units": dimension_units,
                "quantity": quantity,
                "quantity_units": quantity_units,
                "unit_price": unit_price,
                "status": status,
                "vendor_notes": vendor_notes
            })

        try:
            print("[Persist Procurement] Saving procurement request with ID:", state["active_material_request_id"])
            await self.crud.save_procurement_request(
                request_id=state["active_material_request_id"],
                project_id=project_id,
                sender_id=sender_id,
                status=RequestStatus.DRAFT,
                delivery_location=location,
                notes=notes,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
                expected_delivery_date=expected_delivery_date,
                user_editable=user_editable,
                items=request_items
            )
            print("[Persist Procurement] Procurement request saved.")
        except Exception as e:
            print("[Persist Procurement] Failed to save procurement:", e)

    async def update_procurement_request(self, request_id: str, state: AgentState):
        """
        Update an existing material request (after full details confirmed).
        """
        details = state.get("procurement_details", {})
        delivery_location = details.get("location")
        notes = details.get("notes")
        expected_delivery_date = details.get("expected_delivery_date")
        user_editable = details.get("user_editable", True)
        status = RequestStatus.REQUESTED  # Finalized

        try:
            await self.crud.update_procurement_request(
                request_id=request_id,
                status=status,
                delivery_location=delivery_location,
                notes=notes,
                updated_at=datetime.utcnow(),
                expected_delivery_date=expected_delivery_date,
                user_editable=user_editable
            )

            # Optionally update material items as well
            updated_items = []
            for m in details.get("materials", []):
                if m.get("material") and m.get("quantity"):
                    updated_items.append({
                        "material_name": m.get("material"),
                        "sub_type": m.get("sub_type"),
                        "dimensions": m.get("dimensions"),
                        "dimension_units": m.get("dimension_units"),
                        "quantity": m.get("quantity"),
                        "quantity_units": m.get("quantity_units"),
                        "unit_price": m.get("unit_price"),
                        "status": m.get("status", RequestStatus.DRAFT),
                        "vendor_notes": m.get("vendor_notes")
                    })
            if updated_items:
                await self.crud.update_material_request_items(request_id, updated_items)

            print("[Update Procurement] Request and items updated after confirmation.")
        except Exception as e:
            print("[Update Procurement] Failed to update request:", e)



