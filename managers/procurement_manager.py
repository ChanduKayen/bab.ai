

from models.chatstate import AgentState
from database.procurement_crud import ProcurementCRUD
from uuid import uuid4
from datetime import datetime
from database.models import RequestStatus  # Import your Enum
import json

class ProcurementManager:
    def __init__(self, session):
        self.crud = ProcurementCRUD(session)

    def _to_number(self, x):
        if x is None: 
            return None
        s = str(x).strip()
        try:
            return int(s) if "." not in s else float(s)
        except:
            return None

    def _as_list_of_dicts(self, obj):
        """
        Accepts list, dict, json-string, or list of json-strings.
        Returns a clean list[dict].
        """
        if obj is None:
            return []
        # If whole thing is a JSON string
        if isinstance(obj, str):
            try:
                obj = json.loads(obj)
            except Exception:
                return []
        # If wrapped like {"materials":[...]} or {"items":[...]}
        if isinstance(obj, dict):
            obj = obj.get("materials") or obj.get("items") or obj.get("data") or []
        # Ensure list
        if not isinstance(obj, list):
            obj = [obj]
        out = []
        for it in obj:
            if isinstance(it, str):
                try:
                    it = json.loads(it)
                except Exception:
                    continue
            if isinstance(it, dict):
                out.append(it)
        return out


    async def persist_procurement(self, state: dict):
        """
        Persist the initially extracted materials into DB when user starts a procurement request.
        Robust to JSON-strings and mixed types.
        """
        procurement_details = state.get("procurement_details") or {}
        # If procurement_details itself is a JSON string
        if isinstance(procurement_details, str):
            try:
                procurement_details = json.loads(procurement_details)
            except Exception:
                procurement_details = {}

        sender_id = state.get("sender_id")
        project_id = state.get("active_project_id")

        materials_raw = procurement_details.get("materials")
        materials = self._as_list_of_dicts(materials_raw)

        location = procurement_details.get("location")
        notes = procurement_details.get("notes")
        expected_delivery_date = procurement_details.get("expected_delivery_date")
        user_editable = procurement_details.get("user_editable", True)

        if not materials:
            print("Procurement_manager : persist_procurement : No materials to persist. "
                f"type(procurement_details)={type(procurement_details)}, "
                f"type(materials_raw)={type(materials_raw)}, value_preview={repr(str(materials_raw)[:200])}")
            return

        request_items = []
        state["active_material_request_id"] = str(uuid4())
        print("[Persist Procurement] Generated request ID:", state["active_material_request_id"])

        for idx, m in enumerate(materials):
            if not isinstance(m, dict):
                print(f"[Persist Procurement] Skip non-dict at index {idx}: {type(m)} -> {repr(m)[:120]}")
                continue

            material_name   = (m.get("material") or "").strip() or None
            sub_type        = (m.get("sub_type") or "").strip() or None
            dimensions      = (m.get("dimensions") or "").strip() or None
            dimension_units = (m.get("dimension_units") or "").strip() or None

            quantity        = self._to_number(m.get("quantity"))
            quantity_units  = (m.get("quantity_units") or "").strip() or None
            unit_price      = self._to_number(m.get("unit_price"))
            status_val      = m.get("status", RequestStatus.DRAFT)
            vendor_notes    = (m.get("vendor_notes") or "").strip() or None

            # ---- NEW: Default quantity for checklist-style inputs ----
            if material_name and quantity is None:
                quantity = 1
                if not quantity_units:
                    quantity_units = "units"
            # ----------------------------------------------------------

            # Normalize status if string
            if isinstance(status_val, str):
                try:
                    status_val = RequestStatus[status_val]
                except Exception:
                    try:
                        status_val = RequestStatus(status_val)
                    except Exception:
                        status_val = RequestStatus.DRAFT

            if not material_name or quantity is None:
                print(f"[Persist Procurement] Skipping item idx={idx} due to missing material/quantity: {m}")
                continue

            request_items.append({
                "material_name": material_name,
                "sub_type": sub_type,
                "dimensions": dimensions,
                "dimension_units": dimension_units,
                "quantity": quantity,
                "quantity_units": quantity_units,
                "unit_price": unit_price,
                "status": status_val,
                "vendor_notes": vendor_notes
            })

        if not request_items:
            print("[Persist Procurement] No valid items after normalization.")
            return

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
            #return state["active_material_request_id"]
        except Exception as e:
            print("[Persist Procurement] Failed to save procurement:", e)
            raise

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
                        "status": m.get("status", RequestStatus.REQUESTED),
                        "vendor_notes": m.get("vendor_notes")
                    })
            if updated_items:
                await self.crud.update_material_request_items(request_id, updated_items)

            print("[Update Procurement] Request and items updated after confirmation.")
        except Exception as e:
            print("[Update Procurement] Failed to update request:", e)



