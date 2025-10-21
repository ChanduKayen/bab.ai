# agents/procurement_agent.py

import asyncio
import base64, requests
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from managers.uoc_manager import UOCManager
from whatsapp.builder_out import whatsapp_output
import os
from managers.procurement_manager import ProcurementManager
from managers.order_context import OrderContextService
from managers.quotation_handler import (
    notify_user_vendor_confirmed,
    notify_user_vendor_declined,
)
from models.chatstate import AgentState
from database.procurement_crud import ProcurementCRUD
from database.uoc_crud import DatabaseCRUD
from dotenv import load_dotenv
import json  # Import the json module
import re
from urllib.parse import quote
#from app.db import SessionLocal

from app.db import get_sessionmaker
AsyncSessionLocal = get_sessionmaker()

from whatsapp import apis
from agents.credit_agent import run_credit_agent
from utils.convo_router import route_and_respond
from pathlib import Path
from whatsapp.engagement import run_with_engagement

# -----------------------------------------------------------------------------
# Environment & Model Setup
# -----------------------------------------------------------------------------
load_dotenv()  # lodad environment variables from .env file
#llm = ChatOpenAI(model="gpt-4", temperature=0)
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
upload_dir_value = os.getenv("DEFAULT_UPLOAD_DIR")
if not upload_dir_value:
    raise RuntimeError("Environment variable `DEFAULT_UPLOAD_DIR` must be set.")
UPLOAD_IMAGES_DIR = Path(upload_dir_value)
# llm = ChatOpenAI(
#     model="gpt-4o-mini", #gpt-5
#     temperature=0,
#     openai_api_key=os.getenv("OPENAI_API_KEY")  # safely pulls from env
# )  

MODEL = "gpt-5"

def chat_llm(model=MODEL):
    # Models like "gpt-5" don't accept temperature!=1; omit it entirely.
    safe_kwargs = {
        # Force JSON-only output from the model itself
        "model_kwargs": {"response_format": {"type": "json_object"}}
    }
    return ChatOpenAI(model=model, openai_api_key=os.getenv("OPENAI_API_KEY"), **safe_kwargs)

llm = chat_llm()

# -----------------------------------------------------------------------------
# Regex & JSON Utilities
# -----------------------------------------------------------------------------
_JSON_PATTERN = re.compile(r"\{.*\}", re.S) 

_CODEFence = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE | re.MULTILINE)

def safe_json(text: str, default=None):
    """
    Parse messy LLM JSON reliably.
    - Returns a Python object when the input is a single valid JSON value.
    - If multiple JSON values are found (e.g., NDJSON / several top-level blocks),
      returns a list combining them (flattening arrays).
    - On failure, returns `default` (or {} if default is None).
    """
    if text is None:
        return default if default is not None else {}

    txt = text.strip()
    # Strip code fences like ```json ... ```
    txt = _CODEFence.sub("", txt).strip()

    # Fast path: clean JSON
    try:
        return json.loads(txt)
    except Exception:
        pass

    # Fallback: find all balanced JSON blobs and parse each
    blobs = _extract_json_blobs(txt)
    parsed = []
    for blob in blobs:
        try:
            val = json.loads(blob)
            if isinstance(val, list):
                parsed.extend(val)
            else:
                parsed.append(val)
        except Exception:
            continue

    if parsed:
        # If there's only one element, return it; else return the merged list
        return parsed[0] if len(parsed) == 1 else parsed

    return default if default is not None else {}

def _extract_json_blobs(s: str):
    """
    Scan the string and return a list of substrings that are balanced JSON values
    starting with { or [ and ending at the matching bracket.
    This tolerates text before/between/after blobs.
    """
    blobs = []
    i = 0
    n = len(s)
    while i < n:
        # Find next start
        while i < n and s[i] not in "{[":
            i += 1
        if i >= n:
            break

        start = i
        stack = [s[i]]
        i += 1
        in_str = False
        esc = False

        while i < n and stack:
            ch = s[i]

            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch in "{[":
                    stack.append(ch)
                elif ch in "}]":
                    if not stack:
                        break
                    top = stack[-1]
                    if (top == "{" and ch == "}") or (top == "[" and ch == "]"):
                        stack.pop()
                    else:
                        # mismatched; abort this blob
                        stack = []
                        break
            i += 1

        if not stack:  # matched
            blobs.append(s[start:i])
        # else: unmatched/malformed; skip this opener and continue
    return blobs

# -----------------------------------------------------------------------------
# Small Helpers
# -----------------------------------------------------------------------------
def encode_image_base64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")

def _cap_len(msg: str, limit: int = 120) -> str:
    return msg if len(msg) <= limit else msg[:limit-1] + "…"

def _one_emoji(msg: str) -> str:
    # Light filter: if multiple emoji-like chars, keep the first
    seen = 0
    out = []
    for ch in msg:
        if ord(ch) >= 0x1F000:
            seen += 1
            if seen > 1:
                continue
        out.append(ch)
    return "".join(out)

def _last_two_user_msgs(state: dict) -> tuple[str, str]:
    """Return (prev, last) user messages' text; empty strings if missing."""
    msgs = state.get("messages", [])
    user_texts = [m.get("content","") for m in msgs if m.get("role") == "user"]
    last = user_texts[-1] if len(user_texts) >= 1 else ""
    prev = user_texts[-2] if len(user_texts) >= 2 else ""
    return prev.strip(), last.strip()

# -----------------------------------------------------------------------------
# Order context helpers
# -----------------------------------------------------------------------------
RECENT_DRAFT_WINDOW = timedelta(hours=4)
NEW_ORDER_PHOTO_BUTTON_ID = "start_new_order_from_photo"
FOCUS_MORE_BUTTON_ID = "focus_more"
ADD_MORE_PHOTOS_BUTTON_ID = "add_more_photos"
GENERATE_ORDER_BUTTON_ID = "generate_order"
BULK_AUTO_FINALIZE_SECONDS = 120

_bulk_finalize_tasks: Dict[str, asyncio.Task] = {}


def _parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        value = value.replace("Z", "+00:00")
        return datetime.fromisoformat(value)
    except Exception:
        return None


def _humanize_timestamp(dt: Optional[datetime]) -> str:
    if not dt:
        return "recently"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = datetime.now(tz=dt.tzinfo)
    if dt.date() == now.date():
        return dt.strftime("%I:%M %p").lstrip("0")
    if (now.date() - dt.date()).days == 1:
        return "yesterday " + dt.strftime("%I:%M %p").lstrip("0")
    return dt.strftime("%d %b %I:%M %p").lstrip("0")


def _vendor_summary(record: dict) -> Optional[str]:
    approved = record.get("approved_vendor") or {}
    name = approved.get("name")
    if name:
        return name
    vendors = record.get("vendors") or []
    names = [v.get("name") for v in vendors if v.get("name")]
    if not names:
        return None
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return ", ".join(names)
    return ", ".join(names[:2]) + " +"


def _primary_category(record: dict) -> str:
    categories = record.get("vendor_categories") or []
    if categories:
        return categories[0]
    samples = record.get("sample_materials") or []
    if samples:
        return samples[0]
    return "materials"


def _compose_draft_prompt(drafts: list[dict]) -> tuple[str, list[dict], dict]:
    lines = ["I found draft orders you started recently:"]
    buttons: list[dict] = []
    option_map: dict[str, dict] = {}

    for idx, record in enumerate(drafts, start=1):
        vendor_label = _vendor_summary(record) or "Draft order"
        category_label = _primary_category(record)
        updated_at = _parse_iso_datetime(record.get("updated_at"))
        human_time = _humanize_timestamp(updated_at)

        full_line = f"{idx}. {vendor_label} – {category_label} ({human_time})"
        lines.append(full_line)

        short_vendor = vendor_label if len(vendor_label) <= 18 else vendor_label[:17] + "…"
        button_id = f"merge_draft_{record['request_id']}"
        buttons.append({"id": button_id, "title": f"Add: {short_vendor}"})
        option_map[str(record["request_id"])] = record

    buttons.append({"id": NEW_ORDER_PHOTO_BUTTON_ID, "title": "New order"})

    message = "\n".join(lines) + "\n\nShould I add these materials to one of them or start a new order?"
    return message, buttons, option_map


async def _fetch_recent_drafts(sender_id: str, limit: int = 10) -> list[dict]:
    async with AsyncSessionLocal() as session:
        service = OrderContextService(session)
        context = await service.get_orders_for_sender(sender_id, limit=limit)

    drafts = context.get("draft", []) if context else []
    if not drafts:
        return []

    threshold = datetime.now(timezone.utc) - RECENT_DRAFT_WINDOW
    recent: list[dict] = []
    for record in drafts:
        updated_at = _parse_iso_datetime(record.get("updated_at"))
        if not updated_at:
            recent.append(record)
            continue
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        if updated_at >= threshold:
            recent.append(record)
    return recent


def _store_pending_photo(state: dict, image_path: str, caption: Optional[str]) -> None:
    state["pending_photo"] = {
        "image_path": image_path,
        "caption": caption or "",
        "stored_at": datetime.utcnow().isoformat(),
    }


def _clear_pending_photo_state(state: dict) -> None:
    state.pop("pending_photo", None)
    state.pop("awaiting_photo_merge_decision", None)


async def _extract_items_from_photo(state: dict, photo: dict, *, suppress_engagement: bool = False) -> list[dict]:
    sender_id = state.get("sender_id")
    img_path = photo.get("image_path")
    caption = photo.get("caption", "")
    img_b64 = None
    if img_path and os.path.exists(img_path):
        img_b64 = encode_image_base64(img_path)

    combined = caption.strip()
    if suppress_engagement:
        items = await extract_materials(combined, img_b64)
    else:
        items = await run_with_engagement(
            sender_id=sender_id,
            work_coro=extract_materials(combined, img_b64),
            first_nudge_after=8,
        )
    return items or []


async def _prepare_review_response(state: dict, request_id: Optional[str], items: list[dict]) -> None:
    item_lines: List[str] = []
    for item in items[:5]:
        name = item.get("material") or "Material"
        qty = item.get("quantity")
        unit = item.get("quantity_units")
        if qty is not None and unit:
            item_lines.append(f"• {name} – {qty} {unit}")
        elif qty is not None:
            item_lines.append(f"• {name} – {qty}")
        else:
            item_lines.append(f"• {name}")
    if len(items) > 5:
        item_lines.append("• …")

    message_lines = ["*Your request is ready.*", "", "Please review unclear items before continuing."]
    if item_lines:
        message_lines.append("")
        message_lines.append("Items captured:")
        message_lines.extend(item_lines)

    review_url = apis.get_review_order_url(
        os.getenv("REVIEW_ORDER_URL_BASE", ""),
        {},
        {
            "senderId": state.get("sender_id", ""),
            "uuid": request_id or state.get("active_material_request_id", ""),
        },
    )

    state.update(
        latest_respons="\n".join(message_lines).strip(),
        uoc_next_message_type="link_cta",
        uoc_question_type="procurement_new_user_flow",
        uoc_next_message_extra_data={
            "display_text": "Review Order",
            "url": review_url,
        },
        needs_clarification=True,
        agent_first_run=False,
    )


async def _handle_new_order_workflow(state: dict, items: list[dict]) -> bool:
    state.setdefault("procurement_details", {})["materials"] = items
    try:
        async with AsyncSessionLocal() as session:
            manager = ProcurementManager(session)
            await manager.persist_procurement(state)
    except Exception as exc:
        print("Procurement Agent:::: _handle_new_order_workflow : Error persisting request:", exc)
        state.update(
            latest_respons="Sorry, there was an error saving your procurement request. Please try again later.",
            uoc_next_message_type="plain",
            needs_clarification=False,
        )
        return False

    await _prepare_review_response(state, state.get("active_material_request_id"), items)
    return True


async def _handle_append_workflow(
    state: dict,
    request_id: str,
    items: list[dict],
    option_map: Optional[Dict[str, dict]] = None,
) -> bool:
    try:
        async with AsyncSessionLocal() as session:
            manager = ProcurementManager(session)
            appended = await manager.append_materials_to_request(request_id, items)
    except Exception as exc:
        print("Procurement Agent:::: _handle_append_workflow : Error appending materials:", exc)
        state.update(
            latest_respons="Couldn't update that draft right now. Please try again shortly.",
            uoc_next_message_type="plain",
            needs_clarification=False,
        )
        return False

    if appended == 0:
        state.update(
            latest_respons="I couldn’t recognise any new materials in that image. Could you resend a clearer photo or describe them?",
            uoc_next_message_type="plain",
            needs_clarification=True,
        )
        return False

    record = option_map.get(request_id) if option_map else None
    vendor_label = _vendor_summary(record) if record else None
    if not vendor_label:
        vendor_label = "your draft order"
    category_label = _primary_category(record) if record else "materials"

    review_url = apis.get_review_order_url(
        os.getenv("REVIEW_ORDER_URL_BASE", ""),
        {},
        {"senderId": state.get("sender_id", ""), "uuid": request_id},
    )

    state.update(
        latest_respons=(
            f"Added {appended} item(s) to the draft with {vendor_label} "
            f"({category_label}). Review and confirm when you're ready."
        ),
        uoc_next_message_type="link_cta",
        uoc_question_type="procurement_new_user_flow",
        uoc_next_message_extra_data={"display_text": "Review order", "url": review_url},
        needs_clarification=True,
        agent_first_run=False,
        active_material_request_id=request_id,
    )
    return True


async def _process_pending_photo(state: dict, request_id: Optional[str]) -> dict:
    photos = state.pop("batched_photos", None)
    if photos:
        state.pop("pending_photo", None)
    else:
        pending = state.get("pending_photo")
        if not pending:
            state.update(
                latest_respons="I couldn’t find that photo. Please resend it and I’ll process it right away.",
                uoc_next_message_type="plain",
                needs_clarification=True,
            )
            return state
        photos = [pending]
        state.pop("pending_photo", None)

    return await _process_photo_batch(state, photos, request_id)


async def _process_photo_batch(
    state: dict,
    photos: List[dict],
    request_id: Optional[str],
) -> dict:
    aggregated_new_items: List[dict] = []
    first_photo = True
    for photo in photos:
        extracted = await _extract_items_from_photo(state, photo, suppress_engagement=not first_photo)
        first_photo = False
        if not extracted:
            continue
        _append_bulk_items(state, extracted, photo)
        aggregated_new_items.extend(extracted)

    if not aggregated_new_items:
        state.update(
            latest_respons="I couldn’t recognise any materials in that image. Could you share a clearer photo or describe them?",
            uoc_next_message_type="plain",
            needs_clarification=True,
        )
        return state

    if request_id is not None:
        state["bulk_target_request_id"] = request_id
    elif "bulk_target_request_id" not in state:
        state["bulk_target_request_id"] = None

    total_items = len(state.get("bulk_pending_items") or [])
    summary_lines = _build_bulk_summary(state.get("bulk_pending_items") or [])
    summary_body = "\n".join(summary_lines) if summary_lines else "No materials recognised yet."

    target_record = None
    if state.get("bulk_target_request_id"):
        option_map = state.get("pending_photo_options") or {}
        target_record = option_map.get(state["bulk_target_request_id"])

    if target_record:
        target_label = _vendor_summary(target_record) or "your draft order"
    else:
        target_label = "a new order"

    message_lines = [
        "🧾 *Photo processed.*",
        f"Items so far ({total_items}):",
        summary_body,
        "",
        f"This batch will be saved to {target_label}.",
        "Tap *Add more photos* if you have more pages, or *Generate order* when you're done.",
    ]

    state.update(
        latest_respons="\n".join(message_lines).strip(),
        uoc_next_message_type="button",
        uoc_question_type="procurement_new_user_flow",
        uoc_next_message_extra_data={
            "buttons": [
                {"id": ADD_MORE_PHOTOS_BUTTON_ID, "title": "Add more photos"},
                {"id": GENERATE_ORDER_BUTTON_ID, "title": "Generate order"},
            ]
        },
        needs_clarification=True,
        agent_first_run=False,
    )

    state["awaiting_photo_merge_decision"] = False
    _schedule_bulk_auto_finalize(state)
    return state


STATUS_KEYWORDS = (
    "status",
    "where",
    "deliver",
    "delivery",
    "arrive",
    "progress",
    "update",
    "quote",
    "vendor",
    "confirm",
)

FOLLOWUP_KEYWORDS = ("status", "delivery", "deliver", "arrive", "confirm", "quote", "update", "progress")
STATUS_NEW_ORDER_BUTTON_ID = "status_new_order"
MY_ORDERS_BUTTON_ID = "my_orders"
def _looks_like_followup(message: str) -> bool:
    ml = (message or "").lower()
    return any(word in ml for word in FOLLOWUP_KEYWORDS)


def _score_order_for_query(order: dict, message: str) -> int:
    if not message:
        return 0
    message = message.lower()
    score = 0

    rid = str(order.get("request_id", "")).lower()
    if rid and rid in message:
        score += 5

    vendor_names = []
    approved = order.get("approved_vendor") or {}
    if approved.get("name"):
        vendor_names.append(approved["name"])
    for vendor in order.get("vendors") or []:
        name = vendor.get("name")
        if name:
            vendor_names.append(name)
    for name in vendor_names:
        name_l = name.lower()
        if name_l and name_l in message:
            score += 4

    for cat in (order.get("vendor_categories") or []):
        cat_l = cat.lower()
        if cat_l and cat_l in message:
            score += 3

    for material in (order.get("sample_materials") or [])[:3]:
        mat_l = material.lower()
        if mat_l and mat_l in message:
            score += 2

    status = (order.get("status") or "").lower()
    if "draft" in message and status == "draft":
        score += 1
    if "active" in message and status in {"requested", "quoted"}:
        score += 1
    if ("delivered" in message or "arrived" in message) and order.get("delivered_at"):
        score += 2

    return score


def _format_date_string(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        if "T" in value:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt.strftime("%d %b %Y")
        dt = datetime.fromisoformat(value)
        return dt.strftime("%d %b %Y")
    except Exception:
        return None


def _build_review_url(sender_id: Optional[str], request_id: Optional[str]) -> Optional[str]:
    base = os.getenv("REVIEW_ORDER_URL_BASE")
    if not base or not sender_id or not request_id:
        return None
    return f"{base}?senderId={sender_id}&uuid={request_id}"


def _build_quote_summary_url(request_id: Optional[str]) -> Optional[str]:
    base = os.getenv("QUOTE_SUMMARY_URL")
    if not base or not request_id:
        return None
    return f"{base}?uuid={request_id}"


def _parse_focus_selection(message: str, index_map: Dict[int, str]) -> Optional[str]:
    if not message:
        return None
    text = message.strip().lower()
    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits:
        return None
    try:
        idx = int(digits)
    except ValueError:
        return None
    return index_map.get(idx)


def _respond_with_order_detail(state: dict, order: dict) -> None:
    raw_status = order.get("status")
    status = (raw_status or "").replace("_", " ").capitalize()
    short_id = str(order.get("request_id", ""))[:8].upper()
    vendor_label = _vendor_summary(order) or "No vendor selected yet"
    category_label = _primary_category(order)

    lines = [f"{status} order {short_id}"]
    if vendor_label:
        lines.append(f"Vendor: {vendor_label}")

    delivered_at = _parse_iso_datetime(order.get("delivered_at"))
    expected = _format_date_string(order.get("expected_delivery_date"))
    if delivered_at:
        lines.append(f"Delivered: {_humanize_timestamp(delivered_at)}")
    elif expected:
        lines.append(f"Expected delivery: {expected}")

    materials = order.get("sample_materials") or []
    if materials:
        lines.append("Key materials: " + ", ".join(materials[:3]))

    lines.append(f"Category: {category_label}")
    message = "\n".join(lines)

    status_lower = (raw_status or "").lower()
    if status_lower == "draft":
        cta_url = _build_review_url(state.get("sender_id"), order.get("request_id"))
        cta_label = "Review order"
    else:
        cta_url = _build_quote_summary_url(order.get("request_id"))
        cta_label = "Compare quotes"

    state.update(
        latest_respons=message,
        uoc_next_message_type="link_cta" if cta_url else "plain",
        needs_clarification=True,
        agent_first_run=False,
        focus_request_id=order.get("request_id"),
    )
    if cta_url:
        state["uoc_next_message_extra_data"] = {"display_text": cta_label, "url": cta_url}


def _sections_from_context(context: Dict[str, List[dict]]) -> List[tuple[str, List[dict]]]:
    sections: List[tuple[str, List[dict]]] = []
    active = context.get("active") or []
    drafts = context.get("draft") or []
    fulfilled = context.get("fulfilled") or []
    if active:
        sections.append(("Active orders:", active))
    if drafts:
        sections.append(("Draft orders:", drafts))
    if fulfilled and not sections:
        sections.append(("Recently fulfilled orders:", fulfilled))
    return sections


def _gather_focus_entries(
    sections: List[tuple[str, List[dict]]]
) -> tuple[List[dict], Dict[int, str], dict]:
    entries: List[dict] = []
    option_map: dict[str, dict] = {}
    index_map: Dict[int, str] = {}
    entry_index = 1

    for heading, orders in sections:
        if not orders:
            continue
        for order in orders:
            vendor_label = _vendor_summary(order) or "Draft order"
            category_label = _primary_category(order)
            status = (order.get("status") or "").capitalize()
            updated_at = _parse_iso_datetime(order.get("updated_at"))
            when_text = _humanize_timestamp(updated_at)

            rid = str(order["request_id"])
            index_map[entry_index] = rid
            option_map[rid] = order
            entries.append(
                {
                    "index": entry_index,
                    "request_id": rid,
                    "heading": heading,
                    "vendor": vendor_label,
                    "category": category_label,
                    "status": status,
                    "when": when_text,
                }
            )
            entry_index += 1

    return entries, index_map, option_map


# -----------------------------------------------------------------------------
# Bulk Photo Helpers
# -----------------------------------------------------------------------------
def _cancel_bulk_auto_finalize(state: dict) -> None:
    sender_id = state.get("sender_id")
    if not sender_id:
        return
    task = _bulk_finalize_tasks.pop(sender_id, None)
    if task and not task.done():
        task.cancel()


def _schedule_bulk_auto_finalize(state: dict) -> None:
    sender_id = state.get("sender_id")
    if not sender_id:
        return
    _cancel_bulk_auto_finalize(state)

    async def _auto_finalize_after_delay() -> None:
        try:
            await asyncio.sleep(BULK_AUTO_FINALIZE_SECONDS)
            if not state.get("bulk_pending_items"):
                return
            if state.get("bulk_auto_locked"):
                return
            state["bulk_auto_locked"] = True
            summary = await _finalize_bulk_batch(state, auto=True)
            if summary:
                message, message_type, extra = summary
                whatsapp_output(sender_id, message, message_type=message_type, extra_data=extra)
            else:
                state.pop("bulk_auto_locked", None)
        except asyncio.CancelledError:
            return
        finally:
            _bulk_finalize_tasks.pop(sender_id, None)

    state["bulk_auto_finalize_deadline"] = (
        datetime.utcnow() + timedelta(seconds=BULK_AUTO_FINALIZE_SECONDS)
    ).isoformat()
    _bulk_finalize_tasks[sender_id] = asyncio.create_task(_auto_finalize_after_delay())


def _clear_bulk_state(state: dict) -> None:
    _cancel_bulk_auto_finalize(state)
    for key in (
        "bulk_pending_items",
        "bulk_pending_photos",
        "bulk_mode_active",
        "bulk_target_request_id",
        "bulk_auto_finalize_deadline",
        "bulk_auto_locked",
    ):
        state.pop(key, None)
    state.pop("pending_photo_options", None)
    details = state.get("procurement_details")
    if isinstance(details, dict):
        details.pop("materials", None)
    state.pop("batched_photos", None)


def _append_bulk_items(state: dict, items: List[dict], photo: dict) -> None:
    pending_items = list(state.get("bulk_pending_items") or [])
    pending_items.extend(items)
    state["bulk_pending_items"] = pending_items

    details = state.setdefault("procurement_details", {})
    stored = details.get("materials") or []
    stored = list(stored)
    stored.extend(items)
    details["materials"] = stored

    pending_photos = list(state.get("bulk_pending_photos") or [])
    pending_photos.append(photo)
    state["bulk_pending_photos"] = pending_photos
    state["bulk_mode_active"] = True


def _build_bulk_summary(items: List[dict], limit: int = 5) -> List[str]:
    lines: List[str] = []
    for idx, item in enumerate(items[-limit:], start=max(len(items) - limit + 1, 1)):
        name = item.get("material") or "Material"
        qty = item.get("quantity")
        unit = item.get("quantity_units")
        if qty is not None and unit:
            lines.append(f"{idx}. {name} – {qty} {unit}")
        elif qty is not None:
            lines.append(f"{idx}. {name} – {qty}")
        else:
            lines.append(f"{idx}. {name}")
    return lines


async def _finalize_bulk_batch(state: dict, *, auto: bool = False) -> Optional[tuple[str, str, dict]]:
    items = state.get("bulk_pending_items") or []
    if not items:
        return None

    request_id = state.get("bulk_target_request_id")
    option_map = state.get("pending_photo_options") or {}

    if request_id:
        success = await _handle_append_workflow(state, request_id, items, option_map)
    else:
        success = await _handle_new_order_workflow(state, items)

    if not success:
        return None

    message = state.get("latest_respons", "")
    message_type = state.get("uoc_next_message_type", "plain")
    extra_data = state.get("uoc_next_message_extra_data")

    _clear_bulk_state(state)
    if auto:
        state["needs_clarification"] = False
    return message, message_type, extra_data


def _format_focus_chunk(entries: List[dict]) -> str:
    if not entries:
        return "I don't see any orders yet. Start a new one?"

    lines: List[str] = []
    last_heading: Optional[str] = None
    for entry in entries:
        if entry["heading"] != last_heading:
            lines.append(entry["heading"])
            last_heading = entry["heading"]
        lines.append(
            f"{entry['index']}. {entry['vendor']} – {entry['category']} "
            f"({entry['status']}, {entry['when']})"
        )
    return "\n".join(lines)


def _present_focus_options(
    state: dict,
    entries: List[dict],
    index_map: Dict[int, str],
    option_map: dict,
    *,
    max_chars: int = 900,
    page_size: int = 4,
) -> None:
    queue = entries.copy()
    chunk: List[dict] = []
    while queue and len(chunk) < page_size:
        candidate = queue.pop(0)
        test_chunk = chunk + [candidate]
        message = _format_focus_chunk(test_chunk)
        instructions = (
            message
            + f"\n\nReply with the order number (e.g., {test_chunk[0]['index']}) to focus on it."
        )
        if len(instructions) > max_chars and chunk:
            queue.insert(0, candidate)
            break
        chunk.append(candidate)

    message = _format_focus_chunk(chunk)
    if chunk:
        example_idx = chunk[0]["index"]
        instructions = (
            message
            + f"\n\nReply with the order number (e.g., {example_idx}) to focus on it. "
            + ("Tap More orders to see the next set. " if queue else "")
            + "Tap ➕ New Order to start a fresh request."
        )
    else:
        instructions = message + "\n\nTap ➕ New Order to create your first request."

    buttons: List[dict] = []
    if queue:
        buttons.append({"id": FOCUS_MORE_BUTTON_ID, "title": "More orders"})
    buttons.append({"id": STATUS_NEW_ORDER_BUTTON_ID, "title": "➕ New Order"})

    state["pending_focus_options"] = option_map
    state["focus_index_map"] = index_map
    state["focus_entry_queue"] = queue
    state.update(
        latest_respons=instructions,
        uoc_next_message_type="button",
        uoc_question_type="procurement_new_user_flow",
        uoc_next_message_extra_data={"buttons": buttons},
        needs_clarification=True,
    )


async def _handle_focus_selection(state: dict, request_id: str) -> dict:
    options = state.get("pending_focus_options") or {}
    order = options.get(request_id)

    if not order:
        async with AsyncSessionLocal() as session:
            service = OrderContextService(session)
            context = await service.get_orders_for_sender(state.get("sender_id", ""), limit=20)
        order = None
        if context:
            for bucket in ("draft", "active", "fulfilled"):
                for candidate in context.get(bucket, []):
                    if str(candidate.get("request_id")) == request_id:
                        order = candidate
                        break
                if order:
                    break
            state["order_context_cache"] = context

    if not order:
        state.update(
            latest_respons="I couldn’t find that order. Please try again or share the order link.",
            uoc_next_message_type="plain",
            needs_clarification=True,
        )
        return state

    _respond_with_order_detail(state, order)
    state.pop("pending_focus_options", None)
    state.pop("awaiting_focus_selection", None)
    state.pop("focus_index_map", None)
    state.pop("focus_entry_queue", None)
    return state


async def _handle_order_status_query(state: dict, query_text: str) -> bool:
    sender_id = state.get("sender_id")
    if not sender_id:
        return False

    force_list = (query_text == MY_ORDERS_BUTTON_ID)

    async with AsyncSessionLocal() as session:
        service = OrderContextService(session)
        context = await service.get_orders_for_sender(sender_id, limit=20)

    if not context:
        state.update(
            latest_respons="I don’t see any procurement orders for you yet. Share a requirement and I’ll start one.",
            uoc_next_message_type="plain",
            needs_clarification=True,
        )
        return True

    state["order_context_cache"] = context

    drafts = context.get("draft", [])
    active = context.get("active", [])
    fulfilled = context.get("fulfilled", [])
    all_orders = active + drafts + fulfilled

    if not all_orders:
        state.update(
            latest_respons="I don’t see any procurement orders for you yet. Share a requirement and I’ll start one.",
            uoc_next_message_type="plain",
            needs_clarification=True,
        )
        return True

    order_id_slot = (state.get("extracted_slots") or {}).get("order_id")
    query_text_lc = (query_text or "").lower()

    candidate = None
    if order_id_slot and not force_list:
        order_id_slot = str(order_id_slot).lower()
        for order in all_orders:
            rid = str(order.get("request_id", "")).lower()
            if order_id_slot in rid:
                candidate = order
                break

    if not candidate and not force_list:
        scores = []
        for order in all_orders:
            score = _score_order_for_query(order, query_text_lc)
            if score > 0:
                scores.append((score, order))
        if scores:
            candidate = max(scores, key=lambda s: s[0])[1]

    if not candidate and not force_list and len(active) == 1:
        candidate = active[0]
    if (
        not candidate
        and not force_list
        and ("delivered" in query_text_lc or "arrive" in query_text_lc)
        and len(fulfilled) == 1
    ):
        candidate = fulfilled[0]

    if candidate and not force_list:
        state.pop("pending_focus_options", None)
        state.pop("awaiting_focus_selection", None)
        state.pop("focus_index_map", None)
        _respond_with_order_detail(state, candidate)
        return True

    # No single candidate — provide a shortlist
    sections = _sections_from_context(context)
    if not sections:
        sections = [("Active orders:", active)] if active else [("Draft orders:", drafts)]
    entries, index_map, option_map = _gather_focus_entries(sections)
    _present_focus_options(state, entries, index_map, option_map)
    return True

# -----------------------------------------------------------------------------
# External (WABA) Utility
# -----------------------------------------------------------------------------
def upload_media_from_path( file_path: str, mime_type: str = "image/jpeg") -> str:
    url = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/media"
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
    files = {"file": (os.path.basename(file_path), open(file_path, "rb"), mime_type)}
    data = {"messaging_product": "whatsapp"}
    r = requests.post(url, headers=headers, files=files, data=data)
    r.raise_for_status()
    print("rocurement Agent::: upo;ad media from path :::Status",r)
    return r.json()["id"]

# -----------------------------------------------------------------------------
# Context Helpers
# -----------------------------------------------------------------------------
CHIT_CHAT_PROMPT = """
"You are Bab.ai — a smart, friendly WhatsApp assistant built for builders and construction professionals. "
    "Read the conversation trail carefully and reply in the same language and tone as the user. "
    "Be natural, concise (1–2 short sentences, ≤120 characters, max one emoji), and sound like a trusted teammate on site. "
    "Your primary role is to help builders share their material requirements — by explaining them what you can do and what they can do"
    "and then collect the best quotations from trusted OEMs, distributors, and manufacturers. "
    "Whenever relevant, smoothly guide the conversation toward useful actions like sharing a requirement, "
    "checking prices, or exploring pay-later credit for materials. " 
    "Explain Bab.ai’s abilities in a helpful, human tone — never like a sales pitch. "
    "Keep every response warm, context-aware, and conversational. "
    "If the topic is off-track, gently bring the user back by reminding how Bab.ai can assist with procurement or credit. "
    "Never ask for sensitive personal data unless the user is clearly in a verified credit/KYC flow."
"""

async def handle_chit_chat(state: dict, llm: Optional[ChatOpenAI] = None) -> dict:
    """
    Generate a concise, friendly nudge into the procurement flow
    based on the last two user messages. Updates state with a
    one-line response and CTA buttons.
    """
    # Prepare LLM
    llm = llm or ChatOpenAI(
        model="gpt-4o-mini",
        temperature=0.2,
        openai_api_key=os.getenv("OPENAI_API_KEY"),
    )

    prev_msg, last_msg = _last_two_user_msgs(state)
    user_blob = f"Previous: {prev_msg}\nLast: {last_msg}".strip()

    # LLM response (async, non-blocking)
    try:
        resp = await llm.ainvoke([
            SystemMessage(content=CHIT_CHAT_PROMPT),
            HumanMessage(content=user_blob or "User sent a short/unclear message."),
        ])
        line = (resp.content or "").strip()
    except Exception:
        line = "I can set up your order—share material, qty, units, location, and needed-by. 🙂"

    # Enforce UX constraints
    line = _one_emoji(_cap_len(line, 120))

    # Update state with reply + procurement CTAs
    state["latest_respons"] = line
    state["uoc_next_message_type"] = "button"
    state["uoc_question_type"] = "procurement_new_user_flow"
    state["needs_clarification"] = True
    state["last_known_intent"] = "procurement"  # keep lane sticky
    state["uoc_next_message_extra_data"] = [
        {"id": "rfq", "title": "📷 Share Requirement"},
        {"id": "credit_use", "title": "⚡ Buy with Credit"},
    ]
   
    return state
async def handle_help(state: AgentState) -> AgentState:
    """
    Handle the help intent — sends tutorial MP4 as header and useful CTAs.
    """
    print("Procurement Agent::::: handle_help:::::  --Handling help intent --")

    try:
        # Path to your ready MP4 file
        media_path = r"C:\Users\koppi\OneDrive\Desktop\Bab.ai\Marketing\Quotations_tutorial.mp4"

        # Upload to WABA
        media_id = upload_media_from_path(media_path, mime_type="video/mp4")

        help_message = (
            "🎥 Here's a quick tutorial on how to request quotations and place your order.\n\n"
            "You can explore the options below to continue."
        )

        state.update(
            intent="help",
            latest_respons=help_message,
            uoc_next_message_type="button",
            uoc_question_type="procurement_help",
            needs_clarification=True,
            uoc_next_message_extra_data={
                "buttons": [
                    {"id": "procurement", "title": "📷 Share Requirement"},
                   # {"id": "credit_use", "title": "💳 Use Credit"},
                    {"id": "main_menu", "title": "🏠 Main Menu"}
                ],
                "media_id": media_id,
                "media_type": "video",
            },
            agent_first_run=False
        )

    except Exception as e:
        print("❌ Procurement Agent:::: handle_help : Error sending tutorial:", e)
        state.update(
            latest_respons="Sorry, I couldn't fetch the tutorial right now. Please try again later.",
            uoc_next_message_type="plain",
            uoc_question_type="procurement_help",
            needs_clarification=True
        )

    return state
# -----------------------------------------------------------------------------
# Extraction (LLM) Core
# -----------------------------------------------------------------------------
async def extract_materials(text: str = "", img_b64: str = None) -> list:
    timeout = 120        # seconds
    retries = 3         # total attempts
    backoff_base = 0.6

    sys_prompt = """
You are Bab.ai, an expert AI for construction procurement.

Your ONLY job: extract construction material line items into a clean JSON array.

STRICT RULES:
- Always return JSON only, never text or explanations.
- Each item = separate JSON object.
- Omit fields if missing/unclear, never hallucinate.

Schema:
{items: 
[
  {
    "material": "string",
    "sub_type": "string",
    "dimensions": "string",
    "dimension_units": "string",
    "quantity": number,
    "quantity_units": "string"
  }, 
  {...},
  {...},
  ...
  ...
]}

Rules:
- Your response MUST be a single JSON array at the top level. Do not wrap it inside an object.
- If there is only one item, still return it as an array with one object.
-If any field in a row is uncertain and you are not very confident about it (uncless you can logically deduce with solid reaasoning),   prepend a * this to the matreial name . Do not hallucinate values.
- Include only materials; ignore names, phone numbers, totals, costs, dates.
- Each variation (different size/grade) = new entry.
- Handle English, Telugu, Hinglish, mixed handwriting.
    """.strip() 

    print("Procurement Agent:: extract_materials ---Starting to extract materials")

    user_payload = []
    if text:
        user_payload.append({"type": "text", "text": text})
    if img_b64:  # allow BOTH text and image
        user_payload.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}})
    if not user_payload:
        user_payload = [{"type": "text", "text": "Extract any construction material details from this input."}]

    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_payload}
    ]

    async def _call_llm():
        
        resp = await llm.ainvoke(messages)
        print("Procurement Agent:: extract_materials ---Calling LLM",resp)
        raw = getattr(resp, "content", "") or "[]"

        parsed = safe_json(raw, default=[])
        # Normalize to list of dicts no matter what
        items = []

        if isinstance(parsed, dict): 
            # Case: {"items":[...]} or a single object
            if "items" in parsed and isinstance(parsed["items"], list):
                items = parsed["items"]
            else:
                # Single object → wrap
                items = [parsed]

        elif isinstance(parsed, list):
            items = parsed 

        else:
            items = []

        # If someone returned [{"items":[...]}] as first element, flatten that as well
        if len(items) == 1 and isinstance(items[0], dict) and "items" in items[0] and isinstance(items[0]["items"], list):
            items = items[0]["items"]

        # Final shape: list of dicts with at least "material" if present
        cleaned = []
        for it in items:
            if isinstance(it, dict):
                # keep only expected keys; do not hallucinate
                kept = {}
                for k in ("material", "sub_type", "dimensions", "dimension_units", "quantity", "quantity_units"):
                    if k in it:
                        kept[k] = it[k]
                # tolerate string-only objects like {"material":"cement"} or {"material":"*cement"}
                if "material" in kept and isinstance(kept["material"], str):
                    kept["material"] = kept["material"].strip()
                cleaned.append(kept)
            # tolerate bare strings like "cement" (some models do this)
            elif isinstance(it, str) and it.strip():
                cleaned.append({"material": it.strip()})

        print("Procurement Agent:::Extracted Materials::", cleaned)
        return cleaned
            

    # Retry with timeout + exponential backoff
    for attempt in range(retries):
        try:
            print("Retrying *******", attempt)
            return await asyncio.wait_for(_call_llm(), timeout=timeout)
        except asyncio.TimeoutError:
            print(f"Procurement Agent:: extract_materials ---Timeout (attempt {attempt+1}/{retries})")
            if attempt == retries - 1:
                return []
            await asyncio.sleep(backoff_base * (attempt + 1))
        except Exception as e:
            print(f"Material extraction error (attempt {attempt+1}/{retries}): {e}")
            if attempt == retries - 1:
                return []
            await asyncio.sleep(backoff_base * (attempt + 1))

    return []

# -----------------------------------------------------------------------------
# Button Handlers
# -----------------------------------------------------------------------------
async def handle_siteops(state: AgentState, crud: ProcurementCRUD, uoc_next_message_extra_data=None ) -> AgentState:
    #handle a message here 
    state.update(
        intent="siteops",
        latest_respons="Got it! Please share a photo of your site so I can assist you better.", 
        uoc_next_message_type="button",
        uoc_question_type="siteops_welcome",
        needs_clarification=True,  
        uoc_next_message_extra_data=[uoc_next_message_extra_data],
        agent_first_run=True
    )
    print("Siteops Agent::::: handle_siteops:::::  --Handling siteops intent --", state)
    return state    

async def handle_main_menu(state: AgentState, crud: ProcurementCRUD,  uoc_next_message_extra_data=None) -> AgentState:
    state.update(
        intent="random",
        latest_respons="Welcome back! How can I assist you today?",
        uoc_next_message_type="button",
        uoc_question_type="siteops_welcome",
        needs_clarification=True,   
        uoc_next_message_extra_data=uoc_next_message_extra_data,
    )
    print("Random Agent::::: handle_main_menu:::::  --Handling main menu intent --", state)
    return state

async def handle_procurement(state: AgentState, crud: ProcurementCRUD,  uoc_next_message_extra_data=None) -> AgentState:
    """
    Handle the procurement intent by updating the state and returning it.
    """
    state.update(
        intent="procurement",
        latest_respons="Got it! What materials are you looking for? You can send a message or an image.",
        uoc_next_message_type="button",
        uoc_question_type="procurement",
        needs_clarification=True,
        uoc_next_message_extra_data=[uoc_next_message_extra_data],
        agent_first_run=False
    )
    print("Procurement Agent::::: handle_procurement:::::  --Handling procurement intent --", state)
    return state

async def handle_rfq(state: AgentState, crud: ProcurementCRUD, latest_response: str, uoc_next_message_extra_data=None) -> AgentState:
    """
    Handle the RFQ intent by updating the state and returning it.
    """
    print("Procurement Agent::::: handle_rfq:::::  state recieved --", state)
    material_request_id = state["active_material_request_id"] if "active_material_request_id" in state else None

    base = os.getenv("REVIEW_ORDER_URL_BASE")
    data = {
        "sender_id" : state.get("sender_id", ""),
        "uuid": state["active_material_request_id"]
    }
    encoded_data = quote(json.dumps(data))
    review_order_url = f"{base}?data={encoded_data}"
    review_order_url_response = """*Choose Vendors and proceed to place order*"""

    state.update(
        intent="rfq",
        latest_respons=review_order_url_response,
        uoc_next_message_type="link_cta",
        uoc_question_type="procurement_new_user_flow",
        needs_clarification=True,
        uoc_next_message_extra_data= {"display_text": "Choose Vendors Quotes", "url": review_order_url},
        agent_first_run=False  
    )
    print("Procurement Agent::::: handle_rfq:::::  --Handling rfq intent --", state)
    return state

async def handle_credit(state: AgentState, crud: ProcurementCRUD,  uoc_next_message_extra_data=None) -> AgentState:
    """
    Handle the credit intent by updating the state and returning it.
    """    
    print("Procurement Agent::::: handle_credit:::::  --Handling credit intent --")
    try:        
            async with AsyncSessionLocal() as session:
                       crud = DatabaseCRUD(session)
                       return await run_credit_agent(state, config={"configurable": {"crud": crud}})
    except Exception as e:
                    print("Webhook :::::: whatsapp_webhook::::: Error calling run_credit_agent", e)
                    import traceback; traceback.print_exc()
    return state 

async def handle_order_edit(state: AgentState, crud: ProcurementCRUD, latest_response: str, uoc_next_message_extra_data=None) -> AgentState:
    """
    Handle the RFQ edit-order intent by updating the state and returning it.
    """
    material_request_id = state.get("active_material_request_id")
    print("Procurement Agent::::: handle_order_edit:::::  edit order active_material_request_id : ", material_request_id)

    base = os.getenv("REVIEW_ORDER_URL_BASE")
    data = {
        "sender_id": state.get("sender_id", ""),
        "uuid": state.get("active_material_request_id")
    }
    encoded_data = quote(json.dumps(data))
    review_order_url = f"{base}?data={encoded_data}"

    review_order_url_response = "🔎 *Edit your Order Here*"

    state.update(
        intent="rfq",
        latest_respons=review_order_url_response,
        uoc_next_message_type="link_cta",
        uoc_question_type="procurement_new_user_flow",
        needs_clarification=True,
        uoc_next_message_extra_data={"display_text": "Review Order", "url": review_order_url},
        agent_first_run=False
    )
    print("Procurement Agent::::: handle_order_edit:::::  --Handling order edit intent --")
    return state

_HANDLER_MAP = {
    "siteops": handle_siteops,
    "procurement": handle_procurement,
    "main_menu": handle_main_menu,
    "rfq": handle_rfq,
    "credit_use": handle_credit,
    "edit_order": handle_order_edit,
    ADD_MORE_PHOTOS_BUTTON_ID: handle_add_more_photos,
    GENERATE_ORDER_BUTTON_ID: handle_generate_order,
}

# -----------------------------------------------------------------------------
# Orchestration Flows
# -----------------------------------------------------------------------------
async def new_user_flow(state: AgentState, crud: ProcurementCRUD  ) -> AgentState:
    intent =state["intent"]
    latest_msg_intent =state.get("intent")
    last_msg = state["messages"][-1]["content"] if state.get("messages") else ""
    normalized_last = last_msg.strip().lower() if last_msg else ""
    user_name = state.get("user_full_name", "There")
    sender_id = state["sender_id"]
    uoc_next_message_extra_data = state.get("uoc_next_message_extra_data", [])
    latest_response = state.get("latest_respons", None)
    # Handle vendor acknowledgement buttons without changing webhook
    if last_msg in ("vendor_confirm", "vendor_cannot_fulfill"):
        ctx = state.get("vendor_ack_context", {}) or {}
        req_id = ctx.get("request_id")
        ven_id = ctx.get("vendor_id")
        if not req_id or not ven_id:
            state.update({
                "latest_respons": "Context missing for this action. Please try again later.",
                "uoc_next_message_type": "plain",
                "needs_clarification": False,
            })
            return state

        try:
            async with AsyncSessionLocal() as session:
                pcrud = ProcurementCRUD(session)
                if last_msg == "vendor_confirm":
                    await pcrud.mark_vendor_confirmation(
                        request_id=str(req_id),
                        vendor_id=str(ven_id),
                    )
                    user_id = await pcrud.get_sender_id_from_request(str(req_id))
                    if user_id:
                        await notify_user_vendor_confirmed(user_id=user_id, request_id=str(req_id))
                    state.update({
                        "latest_respons": "Thanks! Order confirmed. We will coordinate delivery.",
                        "uoc_next_message_type": "plain",
                        "needs_clarification": False,
                    })
                else:  # vendor_cannot_fulfill
                    await pcrud.vendor_decline_and_reopen(request_id=str(req_id), vendor_id=str(ven_id))
                    user_id = await pcrud.get_sender_id_from_request(str(req_id))
                    if user_id:
                        await notify_user_vendor_declined(user_id=user_id, request_id=str(req_id))
                    state.update({
                        "latest_respons": "Acknowledged. We’ve informed the buyer you can’t fulfill.",
                        "uoc_next_message_type": "plain",
                        "needs_clarification": False,
                    })
        except Exception as e:
            print("procurement_agent ::::: vendor ack flow exception:", e)
            state.update({
                "latest_respons": "Sorry, something went wrong processing your response.",
                "uoc_next_message_type": "plain",
                "needs_clarification": False,
            })
        return state
    print("Procurement Agent:::: new_user_flow : last_msg is: -", last_msg)
    # print("Procurement Agent:::: new_user_flow : procurment conversation log  is: -", state.get("siteops_conversation_log", []))
    print("Procurement Agent:::: new_user_flow : the state received here is : -", state)
    response = dict()
    material_request_id = ""

    caption_text = state.get("caption", "")
    img_path = state.get("image_path")

    if last_msg.startswith("merge_draft_"):
        request_id = last_msg.split("merge_draft_", 1)[-1]
        state["image_path"] = None
        state["bulk_target_request_id"] = request_id
        return await _process_pending_photo(state, request_id)

    if last_msg == NEW_ORDER_PHOTO_BUTTON_ID:
        state["image_path"] = None
        state["bulk_target_request_id"] = None
        return await _process_pending_photo(state, None)

    if last_msg.startswith("focus_order_"):
        request_id = last_msg.split("focus_order_", 1)[-1]
        return await _handle_focus_selection(state, request_id)

    if last_msg == STATUS_NEW_ORDER_BUTTON_ID:
        state.pop("pending_focus_options", None)
        state.pop("awaiting_focus_selection", None)
        state.pop("focus_index_map", None)
        state.pop("focus_entry_queue", None)
        state.pop("focus_request_id", None)
        state.update(
            latest_respons="Sure—share a photo or describe the materials you need, and I’ll start a new order.",
            uoc_next_message_type="plain",
            needs_clarification=True,
        )
        return state

    if img_path:
        _store_pending_photo(state, img_path, caption_text)
        state["image_path"] = None

        photos = state.pop("batched_photos", None)
        if not photos:
            current = state.get("pending_photo")
            photos = [current] if current else []

        if last_msg == NEW_ORDER_PHOTO_BUTTON_ID:
            state["bulk_target_request_id"] = None
            state["awaiting_photo_merge_decision"] = False
            state["pending_photo_options"] = {}
            target_request = None
        elif last_msg.startswith("merge_draft_"):
            target_request = last_msg.split("merge_draft_", 1)[-1]
            state["bulk_target_request_id"] = target_request
            state["awaiting_photo_merge_decision"] = False
            state["pending_photo_options"] = {}
        else:
            recent_drafts = await _fetch_recent_drafts(sender_id)
            if recent_drafts and not state.get("bulk_mode_active"):
                message, buttons, option_map = _compose_draft_prompt(recent_drafts)
                state.update(
                    latest_respons=message,
                    uoc_next_message_type="button",
                    uoc_question_type="procurement_new_user_flow",
                    uoc_next_message_extra_data={"buttons": buttons},
                    needs_clarification=True,
                )
                state["pending_photo_options"] = option_map
                state["batched_photos"] = photos
                state["awaiting_photo_merge_decision"] = True
                return state
            target_request = state.get("bulk_target_request_id")

        state["batched_photos"] = photos
        return await _process_pending_photo(state, target_request)

    img_b64 = None
    img_path = state.get("image_path")
    if img_path:
        try:
            img_b64 = encode_image_base64(img_path)
        except FileNotFoundError:
          print("⚠  Image file not found:", img_path)
          print("Procurement Agent:::: new_user_flow : called")
        #   state["siteops_conversation_log"].append({
        #         "role": "user", "content": img_b64 if img_b64 else last_msg + "\n" + state.get("caption", "")
        #     })
    if(state.get("agent_first_run", True)):
        print("Procurement Agent:::: new_user_flow : agent first run is true")
        if(last_msg == ""):
            print("Procurement Agent:::: new_user_flow : last_msg is empty and no image, setting up welcome message")
            greeting_message = (
                f"👋 Hi {user_name}! I'm your procurement assistant.\n"
"I’ll help you connect directly with manufacturers.\n\n"
"Here’s how it works:\n"
"1️⃣ Share a photo or BOQ of your material requirement.\n"
"2️⃣ Bab.ai collects quotations directly from OEMs & distributors.\n"
"3️⃣ You compare and choose the best offer.\n"
"4️⃣ (Optional) Use Pay-Later Credit for easy purchase 💳\n\n"
"What would you like to do now?"
            )
           
            state["latest_respons"] = greeting_message
            state["uoc_next_message_type"] = "button"
            state["uoc_question_type"] = "procurement_new_user_flow"
            state["uoc_confidence"]="low"
            state["needs_clarification"] = True
            state["agent_first_run"] = False
            state["user_verified"] = True
            state["uoc_next_message_extra_data"] = [
                {"id": "procurement_start", "title": "📷 Share Requirement"},
                {"id": MY_ORDERS_BUTTON_ID, "title": "📋 My Orders"},
                {"id": "main_menu", "title": "🏠 Main Menu"},
            ]
            return state
             
        else:
            print("Procurement Agent:::: new_user_flow : Last message/ Image is found")
            caption = state.get("caption", "")
            if img_b64:
                combined = (caption or "").strip()
            else:
                combined = (last_msg or "").strip()

            print("Procurement Agent:::: new_user_flow : combined text:", combined)
 
            # PREMIUM WAIT FLOW: one instant receipt + one heartbeat if still processing
            items = await extract_materials(combined, img_b64)
         
        state.setdefault("procurement_details", {})["materials"] = items
        print("Procurement Agent:::: new_user_flow : extracted materials:", state["procurement_details"]["materials"])
        
        try:
            async with AsyncSessionLocal() as session:
                procurement_mgr = ProcurementManager(session)
            print("Procurement Agent:::: new_user_flow :::: calling persist_procurement for material : ", state["procurement_details"]["materials"])
            await procurement_mgr.persist_procurement(state)
            # material_request_id ="Dummy"
            print("Procurement Agent:::: new_user_flow : persist_procurement completed: ", state.get("active_material_request_id", None))
        except Exception as e:
            print("Procurement Agent:::: new_user_flow : Error in persist_procurement:", e)
            state["latest_respons"] = "Sorry, there was an error saving your procurement request. Please try again later."
            return state
        try:
            await _prepare_review_response(
                state,
                state.get("active_material_request_id"),
                items,
            )
        except Exception as e:
            print("Procurement Agent:::: new_user_flow : Error preparing review response:", e)
            state.update(
                latest_respons="Your request is ready. Please tap the link to review it.",
                uoc_next_message_type="plain",
                needs_clarification=True,
                agent_first_run=False,
            )
        return state
    else:
        print("Procurement Agent:::: new_user_flow : agent first run is false, not setting it to false")
        if last_msg in _HANDLER_MAP:
            #Main menu for new user
            if last_msg =="main_menu":
                print("Procurement Agent:::: new_user_flow : last_msg is main_menu, setting up main menu")
                latest_response = "Welcome back! How can I assist you today?"
                uoc_next_message_extra_data =[{"id": "siteops", "title": "🏗 Manage My Site"},
                                          {"id": "procurement", "title": "⚡ Get Quick Quotes"},
                                          {"id": "credit",      "title": "💳 Get Credit Now"}] 
                return await _HANDLER_MAP[last_msg](state, crud, uoc_next_message_extra_data)
        else: 
                print("Procurement Agent:::: new_user_flow : last_msg is not main_menu, handling it as a specific intent")
                state["last_known_intent"] = "procurement"
                state = await route_and_respond(state)
                return state
        
        ###########################################    
        latest_msg_intent= state["intent"]
        latest_msg_context = state.get("intent_context", {})

        if latest_msg_intent == "random":
                    from agents.random_agent import classify_and_respond
                    return await classify_and_respond(state, config={"configurable": {"crud": crud}})
        elif latest_msg_intent == "siteops":
                    latest_response = "📷 Ready to check your site? Let's continue!"
                    state["latest_respons"]=latest_response
                    state["uoc_next_message_extra_data"] = [{"id": "siteops", "title": "📁 Site Setup"}]
                    state["uoc_question_type"] = "siteops_welcome"
                    state["needs_clarification"] =True
                    return state
        elif latest_msg_intent == "procurement":
                    latest_response = "🧱 Tell me what materials you're looking for, and I'll fetch quotes!"
                    state["latest_respons"]=latest_response
                    state["uoc_next_message_type"]="button"
                    state["uoc_next_message_extra_data"] = [{"id": "procurement", "title": "📦 Continue Procurement"}]
                    state["uoc_question_type"] = "siteops_welcome"
                    state["needs_clarification"] =True
        elif latest_msg_intent == "credit":
                 
                    #state["messages"][-1]["content"] ="routed_from_other_agent" # its sub route
                    latest_response= "This is credit section"
                    state["latest_respons"]=latest_response
                    state["uoc_next_message_type"]="button"
                    state["uoc_next_message_extra_data"] = [{"id": "routed_from_other_agent", "title": "Buy With Credit"}] # This is treated as the last message in credit agent
                    state["uoc_question_type"] = "credit_start"
                    state["needs_clarification"] =True
                 
        else:   
                    state["latest_respons"] = (
                        "🤔 I'm not sure what you're looking for. "
                        "Please choose an option below."
                    )
                    state["uoc_next_message_type"] = "button"
                    state["uoc_question_type"] = "main_menu"
                    state["needs_clarification"] = True
                    state["uoc_next_message_extra_data"] = [
                        {"id": "siteops", "title": "🏗 Manage My Site"},
                        {"id": "procurement", "title": "⚡ Get Quick Quotes"},
                        {"id": "credit", "title": "💳 Get Credit Now"}
                    ]
                    return state

async def collect_procurement_details_interactively(state: dict) -> dict:
    """
    Interactive loop to collect procurement details over WhatsApp:
      • Sends chat history + current procurement details to the LLM
      • Receives procurement update and control JSON
      • Merges result, updates state, and returns
    """
    chat_history = state.get("messages", [])
    procurement_details = state.get("procurement_details", {
        "materials": [],
        "vendor": "",
        "price": "",
        "delivery_date": "",
        "location": "",
        "notes": ""
    })

    # SYSTEM PROMPT — clear strategy, clarify vague input, ask for missing info
    system_prompt = (
                """
        You are a **smart, friendly procurement assistant** who speaks in a soft, warm tone. You're here to **gently guide users** through placing construction material requests — whether they start with a casual message, upload a photo, or provide structured input.

        ---------------------------
        Known Procurement Details:
        ---------------------------
        <insert JSON-dump of state["procurement_details"]>

        =================== GOAL ===================
        Help the user complete a material procurement request with these fields:
        - Material name (brand/type like "ACC Cement", "Vizag TMT")
        - Sub-type or grade (e.g., "OPC 53", "Fly Ash", "53 Grade")
        - Dimensions (e.g., "20", "4x8", "10", "50")
        - Dimension unit (e.g., mm, kg, inch, ft)
        - Quantity (numeric or range like 100, 50, 10–20)
        - Quantity unit (e.g., units, bags, tons, meters)
        - Delivery urgency/date
        - Preferred vendor (or "Any")
        - Optional notes

        You may get:
        - Vague text: “Need cement and TMT”
        - Structured lists: “Vizag TMT 8mm – 200 kg, Deccan OPC – 50 bags”
        - Mixed messages over multiple replies
        - Photos (BOQ, handwritten notes, invoices)

        ================ EXAMPLE SCENARIO ================

        🧾 **1. Text-Only Message (Partial Info):**
        User: “Need Vizag TMT and ACC cement”
        
        You reply warmly:
        
        Got it! Just checking:
        - Vizag TMT: what size (e.g., 8mm, 10mm)? And how many kg?
        - ACC Cement: is it OPC 53 Grade or something else? How many bags?

        Example:
        - Vizag TMT 10mm – 300 kg
        - ACC OPC 53 – 50 bags
        

        🖼 **2. Photo of Material List:**
        You detect image + caption, extract known materials:
        
        Looks like you need:
        1. Deccan TMT 20mm – 150 units
        2. ACC Cement OPC 53 Grade – 50 bags

        Shall I proceed with these? Or would you like to adjust quantities or specs?
        

        📋 **3. Structured Entry Already Present:**
        If all fields are present and clear:
        
        Here’s what I have so far:
        - Deccan Cement OPC 53 – 50 kg – 40 bags
        - Vizag TMT 8mm – 200 kg
        - CenturyPly Plywood 8 ft × 3½ ft × 2 in – 20 sheets

        ✅ Confirm to proceed or let me know if you'd like to edit anything.
        

        🕒 **4. Missing Delivery Info:**
        
        When would you like these materials delivered?

        For example:
        - “ASAP”
        - “Within 2 days”
        - “Before Friday”
        

        🛍 **5. Vendor Selection:**
        
        Do you have a preferred vendor?

        You can say:
        - “Srinivas Traders”
        - “Any” — and I’ll fetch quotes from available suppliers.
        

        🧠 **6. Confusing Response:**
        If the message is unclear:
        
        Hmm… I didn’t quite get that. Could you help me with a few more details?

        For example:
        - "Vizag TMT 10mm – 200 kg"
        - "ACC OPC 53 Cement – 50 bags"
        

        ================ STRATEGY ================
        1. Speak warmly and professionally. Be empathetic and clear.
        2. Ask ONE thing at a time unless summarizing.
        3. If any material is unclear to you, may be you can try to find out the category of the material based on name or dimensions, and try to extract the material name and quantity from it.
        4. Most general types of construction materials are:
            - Cement (OPC, PPC, etc.)
            - TMT Bars (Deccan TMT, Vizag TMT, etc.)
            - Aggregates (Coarse, Fine, etc.)
            - Bricks (Red, Fly Ash, etc.)
            - Sand (River, Manufactured, etc.)
            - Plumbing Materials (Pipes, Fittings, etc.)
            - Electrical Materials (Wires, Switches, etc.)
            - Paints (Interior, Exterior, etc.)
            - Roofing Materials (Tiles, Sheets, etc.)
            - Flooring Materials (Tiles, Marble, etc.)
            - Hardware (Doors, Windows, etc.)
            - Miscellaneous (Tools, Safety Gear, etc.)
            - Carpentry Materials (Wood, Plywood, etc.)
            - Glass (Float, Toughened, etc.)
            - Insulation Materials (Thermal, Acoustic, etc.)
            - Waterproofing Materials (Membranes, Coatings, etc.)
            - Scaffolding Materials (Planks, Props, etc.)
        5. Based on the above types, you can try to extract the material name and quantity from the text or image.
        6. Use buttons where helpful (like "ASAP", "Any vendor", "Confirm Order").
        7. Be patient. Never rush the user.
        8. Give concrete examples always.
        9. Assume the user has minimal context — make it simple.
        10. Use might provide data in text or image in English or Telugu, Don't translate, extract as-is.
        11. You should be able to understand written Telugu or English, but do not translate it. Just extract the material details as-is. 
         
        ============= OUTPUT FORMAT ============
        At the end of every interaction, respond ONLY in this strict JSON format:

        {
          "latest_respons": "<your next WhatsApp message here>",
          "next_message_type": "button",      // 'plain' for text-only, 'button' for interactive options
          "next_message_extra_data": [        // optional — only if next message has buttons
            { "id": "<kebab-case-id>", "title": "<Short Button Title ≤20 chars>" }
          ],
          "procurement_details": {
            "materials": [
              {
                "material": "ACC Cement",
                "sub_type": "OPC 53 Grade",
                "dimensions": "50",
                "dimension_units": "kg",
                "quantity": 40,
                "quantity_units": "bags"
              },
              {
                "material": "Vizag TMT",
                "dimensions": "8",
                "dimension_units": "mm",
                "quantity": 200,
                "quantity_units": "kg"
              }
            ],
            "delivery_date": "2025-07-29",
            "vendor": "Any"
          },
          "uoc_confidence": "low",     // set to "high" only when all needed fields are present
          "uoc_question_type": "procurement"
        }
        
        At the end of your reasoning, ALWAYS respond in this exact JSON format:
            {
              "latest_respons": "<your next WhatsApp message here>",
              "next_message_type": "button",  // 'plain' for text-only, 'button' for buttons
              "next_message_extra_data": [{ "id": "<kebab-case>", "title": "<≤20 chars>" }, "{ "id": "<kebab-case>", "title": "<≤20 chars>" }", "{ "id": "main_menu", "title": "📋 Main Menu" }],
              "procurement_details": { <updated procurement_details so far> },
              "needs_clarification": true,  // false if user exited
              "uoc_confidence": "low",      // 'high' only when structure is complete
              "uoc_question_type": "procurement"
            }

        =============== RULES =================
        - DO NOT include markdown or formatting syntax.
        - DO NOT wrap the JSON in  or markdown fences.
        - Output ONLY the raw JSON above, nothing else.
        """

    )

    # BUILD LLM MESSAGE HISTORY
    messages = [SystemMessage(content=system_prompt)]
    messages += [HumanMessage(content=m["content"]) for m in chat_history]

    if procurement_details:
        messages.append(HumanMessage(content="Current known procurement details:\n" + json.dumps(procurement_details)))

    # CALL LLM
    try:
        llm_raw = await llm.ainvoke(messages)
        llm_clean = llm_raw.content.strip().replace("json", "").replace("", "")
        parsed = json.loads(llm_clean)
    except Exception:
        state.update({
            "needs_clarification": True,
            "proc_confidence": "low",
            "latest_respons": "Sorry, I couldn’t read that. Could you please re-phrase?"
        })
        return state

    # UPDATE PROCUREMENT DETAILS
    updated_details = parsed.get("procurement_details")
    if updated_details:
        state["procurement_details"] = updated_details

    # COPY CONTROL FIELDS
    state.update({
        "latest_respons": parsed["latest_respons"],
        "proc_next_message_type": parsed.get("next_message_type", "plain"),
        "proc_next_message_extra_data": parsed.get("next_message_extra_data"),
        "needs_clarification": parsed.get("needs_clarification", True),
        "uoc_confidence": parsed.get("uoc_confidence", "low"),
        "uoc_question_type":  "procurement",
    })
    
   
    print("procurement_agent :::: collect_procurement_details_interactively :::: Parsed state:", parsed)
    
    user_message = (
        state.get("messages", [])[-1].get("content", "").strip().lower()
        if state.get("messages") else "")
    if user_message == "main_menu" or not state["needs_clarification"]:
        print("procurement_agent :::: collect_procurement_details_interactively :::: User exited or confirmed procurement details.")
        sender_id = state.get("sender_id")
        quick_msg = parsed.get("latest_respons", "Procurement details completed. You can now proceed with your order.")
        whatsapp_output(sender_id, quick_msg, message_type="plain")
        state["needs_clarification"] = False
        state["uoc_confidence"] = "high" if updated_details else "low"
        state["uoc_question_type"] = "procurement"
        # Save to DB or trigger next workflow here if needed
        if state.get("uoc_confidence") == "high":
            print("procurement_agent :::: collect_procurement_details_interactively :::: Procurement details are complete.")
            try:
                async with AsyncSessionLocal() as session:
                    procurement_mgr = ProcurementManager(session)
                    request_id = state.get("active_material_request_id")
                    if request_id:
                        print("procurement_agent :::: collect_procurement_details_interactivley :::: high uoc confidence :::: Updating procurement request with interactive details.")
                        await procurement_mgr.update_procurement_request(request_id, state)
                        print("procurement_agent :::: collect_procurement_details_interactively :::: Procurement request updated successfully.")
            except Exception as e:
                print("❌ Error while updating procurement after interactive confirmation:", e)
            print("procurement_agent :::: collect_procurement_details_interactively :::: Sending WhatsApp output, Saved state:", state)
            
            print("procurement_agent :::: collect_procurement_details_interactively :::: Sending quote request to vendor.")
           
    
    return state

# -----------------------------------------------------------------------------
# Vendor Outreach
# -----------------------------------------------------------------------------
async def send_quote_request_to_vendor(state: dict):
    vendor_phone_number = state["sender_id"]  # Vendor WhatsApp number (without +)
    
    # Mock: Materials this vendor can supply
    vendor_supported_materials = ["KCP 53 grade cement", "Deccan TMT 20mm", "ACC Cement 50kg bags"]

    # Get full material list from procurement
    materials = state.get("procurement_details", {}).get("materials", [])

    # Filter materials vendor can supply
    relevant_items = [
        item for item in materials
        if any(mat.lower() in item["material"].lower() for mat in vendor_supported_materials)
    ]

    if not relevant_items:
        print(f"No matching materials for vendor {vendor_phone_number}")
        return

    # Format WhatsApp message
    message_lines = ["📦 New Quote Request\n\nHere are the materials we need:"]
    for idx, item in enumerate(relevant_items, 1):
        message_lines.append(f"{idx}. {item['material']} – {item['quantity']}")

    message_lines.append("\nPlease reply with your quote and delivery estimate. ✅")
    message = "\n".join(message_lines)

    # Send WhatsApp message
    whatsapp_output(vendor_phone_number, message, message_type="plain")
    print(f"✅ Quote request sent to vendor {vendor_phone_number}")

# -----------------------------------------------------------------------------
# Main Entry Point
# -----------------------------------------------------------------------------
async def run_procurement_agent(state: dict,  config: dict) -> dict:
    print("Procurement Agent:::: run_procurement_agent : called")
    print("Procurement Agent:::: run_procurement_agent : state received =>", state)
    print("Procurement Agent:::: run_procurement_agent : config received =>", config)
    intent_context=""
    try:
        crud = config["configurable"]["crud"]
        procurement_mgr = ProcurementManager(crud)
    except Exception as e:
        print("Procurement Agent:::: run_procurement_agent : failed to initialize crud or UOCManager:", e)
        state["latest_respons"] = "Sorry, there was a system error. Please try again later."
        return state
    
    last_msg = state["messages"][-1]["content"] if state.get("messages") else ""
    print("Procurement Agent:::: run_procurement_agent : last_msg:", last_msg)     
    user_stage = state.get("user_stage", {})
    print("Procurement Agent:::: run_procurement_agent : user_stage:", user_stage)

    normalized_last = last_msg.strip().lower() if isinstance(last_msg, str) else ""
      
    intent_context = state.get("intent_context","")
    if state.get("focus_index_map"):
         choice = _parse_focus_selection(last_msg, state["focus_index_map"])
         if choice:
             return await _handle_focus_selection(state, choice)
    if intent_context.lower() == "chit-chat":
         print("Procurement Agent:::: run_procurement_agent : The user is trying to chit-chat")
         state = await handle_chit_chat(state)
         state["intent_context"]="" #clear context after consuming it 
         return state
    if intent_context.lower() == "help":
         print("Procurement Agent:::: run_procurement_agent : The user is trying to get help")
         state = await handle_help(state)
         state["intent_context"]="" #clear context after consuming it 
         return state

    if intent_context.lower() in {"order_followup", "track_order"}:
         handled = await _handle_order_status_query(state, last_msg)
         if handled:
             state["intent_context"] = ""
             return state

    if normalized_last in {MY_ORDERS_BUTTON_ID, "my orders", "orders", "my order"}:
         handled = await _handle_order_status_query(state, MY_ORDERS_BUTTON_ID)
         if handled:
             state["intent_context"] = ""
             return state
    if last_msg == FOCUS_MORE_BUTTON_ID:
         queue = state.get("focus_entry_queue") or []
         option_map = state.get("pending_focus_options") or {}
         index_map = state.get("focus_index_map") or {}
         if queue:
             _present_focus_options(state, queue, index_map, option_map)
             return state
         handled = await _handle_order_status_query(state, MY_ORDERS_BUTTON_ID)
         if handled:
             state["intent_context"] = ""
             return state
    if state.get("focus_request_id") and _looks_like_followup(last_msg):
         handled = await _handle_order_status_query(state, last_msg)
         if handled:
             return state
    if last_msg.isdigit() and state.get("order_context_cache"):
         sections = _sections_from_context(state["order_context_cache"])
         entries, index_map, option_map = _gather_focus_entries(sections)
         choice = _parse_focus_selection(last_msg, index_map)
         if choice:
             state["pending_focus_options"] = option_map
             return await _handle_focus_selection(state, choice)
        # ---------- 0 · Button click (id) ---------------------------
    if last_msg.lower() in _HANDLER_MAP:
        return await _HANDLER_MAP[last_msg.lower()](state,  config, state.get("uoc_next_message_extra_data", []))

    try:
        async with AsyncSessionLocal() as session:
            procurement_mgr = ProcurementManager(session)
    except Exception as e:
        print("Procurement Agent:::: run_procurement_agent : failed to initialize session:", e)
        state["latest_respons"] = "Sorry, there was a system error. Please try again later."
        return state
    if user_stage == "new":
        print("Procurement agent :::: run_procurement_agent :::: User is new, setting up procurement stage")
        await new_user_flow(state, crud)
        if state.get("uoc_confidence") == "high":
            print("Procurement Agent:::: run_procurement_agent : Procurement confirmed — updating DB")
            try:
                request_id = state.get("active_material_request_id")
                if request_id:
                    await procurement_mgr.update_procurement_request(request_id, state)
            except Exception as e:
                print("Procurement Agent:::: run_procurement_agent : Failed to update procurement after confirmation:", e)
    

        # Add additional stages or fallback logic here if needed
    return state
