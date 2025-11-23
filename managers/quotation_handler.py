"""Utilities for notifying supervisors and vendors about quote status."""

import os
import json
from typing import Dict, List, Optional
from dotenv import load_dotenv
from urllib.parse import quote

from whatsapp.builder_out import whatsapp_output

load_dotenv()

VENDOR_QUOTE_URL_BASE = os.getenv("VENDOR_QUOTE_URL_BASE")
QUOTE_SUMMARY_URL = os.getenv("QUOTE_SUMMARY_URL")
VENDOR_ORDER_CONFIRMATION_URL_BASE = os.getenv("VENDOR_ORDER_CONFIRMATION_URL_BASE")


def _format_project_line(name: Optional[str], location: Optional[str]) -> str:
    label = name or "your project"
    if location:
        return f"{label} – {location}"
    return label


def _quote_summary_url(request_id: str) -> Optional[str]:
    if not QUOTE_SUMMARY_URL:
        return None
    return f"{QUOTE_SUMMARY_URL}?uuid={request_id}"


def _vendor_quote_url(request_id: str, vendor_id: str) -> str:
    base = VENDOR_QUOTE_URL_BASE or "https://example.com/vendor/quotes"
    data = {"uuid": request_id, "vendor_id": vendor_id}
    encoded_data = quote(json.dumps(data, separators=(",", ":")))
    return f"{base}?data={encoded_data}"


def _vendor_order_url(request_id: str, vendor_id: str) -> str:
    base = VENDOR_ORDER_CONFIRMATION_URL_BASE or "https://example.com/vendor/order"
    data = {"uuid": request_id, "vendor_id": vendor_id}
    encoded_data = quote(json.dumps(data, separators=(",", ":")))
    return f"{base}?data={encoded_data}"

def _vendor_quote_button_param(request_id: str, vendor_id: str) -> str:
    """URL-encode the JSON payload for the template’s dynamic URL button."""
    import json
    from urllib.parse import quote
    data = {"uuid": request_id, "vendor_id": vendor_id}
    # compact JSON to keep URL short
    return quote(json.dumps(data, separators=(",", ":")))

async def send_quote_request_to_vendor(
    vendor_id: str,
    request_id: str,
    contact_number: Optional[str],
    *,
    project_name: Optional[str] = None,
    project_location: Optional[str] = None,
    item_count: Optional[int] = None,

    # (recommended so your template gets rich, correct values)
    vendor_display_name: Optional[str] = None,   # for {{1}}
    builder_name: Optional[str] = None,          # for {{2}}
    company_name: Optional[str] = None           # for {{3}}
) -> None:
    """Send an initial quote request to a vendor via WhatsApp Template (Utility)."""
    if not contact_number:
        print("quotation_handler ::::: send_quote_request_to_vendor ::::: missing contact for vendor", vendor_id)
        return

    # --- Compose body params for the approved template ---
    # Template body:
    # Hi Mr. {{1}},
    # *{{2}}* garu has requested a quotation from you. He sent this message from Thirtee platform.
    #
    # Here are the request details:
    # • *Company*: {{3}}
    # • *Project*: {{4}}
    # • *Delivery location*: {{5}}
    #
    # Button: dynamic URL → https://www.thirtee.in/orders/send-quote?data={{1}}   (this {{1}} is button param)

    # Safe fallbacks (kept neutral to preserve Utility/non-promotional tone)
    v_name = vendor_display_name or "Vendor"
    b_name = builder_name or (company_name or "Builder")
    c_name = company_name or b_name
    proj   = project_name or "Project"
    loc    = project_location or "Location"

    # Build the dynamic button param (URL-encoded JSON)
    encoded = _vendor_quote_button_param(request_id, vendor_id)

    # Try template first; fall back to link_cta if anything goes wrong
    try:
        whatsapp_output(
            to_number=contact_number,
            message_text="",  # not used for template
            message_type="template",
            extra_data={
                "template_name": "vendor_quote_request_notification",
                "language_code": "en",
                "body_params": [
                    v_name,  # {{1}} Vendor display name (renders after "Hi Mr.")
                    b_name,  # {{2}} Builder name (… garu)
                    c_name,  # {{3}} Company
                    proj,    # {{4}} Project
                    loc      # {{5}} Delivery location
                ],
                "button_param": encoded,   # appended to base URL set in the template
                "button_index": 0          # first (only) button
            }
        )
        return
    except Exception as e:
        print("Template send failed, falling back to link CTA:", e)

    # --- Fallback: your existing link_cta path (kept as-is) ---
    project_line = _format_project_line(project_name, project_location)
    message_lines = [
        "👷 Thirtee procurement request",
        f"Project: {project_line}",
    ]
    if item_count is not None:
        message_lines.append(f"Materials requested: {item_count}")
    message_lines.append("Tap below to review the details and share your prices.")

    whatsapp_output(
        to_number=contact_number,
        message_text="\n".join(message_lines),
        message_type="link_cta",
        extra_data={
            "display_text": "Review & Respond",
            "url": _vendor_quote_url(request_id, vendor_id),
        },
    )



async def notify_user_quote_ready(
    state: dict,
    user_id: str,
    request_id: str,
    *,
    project_name: Optional[str] = None,
    project_location: Optional[str] = None,
    vendor_labels: Optional[List[str]] = None,
) -> dict:
    """Let the supervisor know that their request has been sent to vendors."""
    if not user_id:
        return

    project_line = _format_project_line(project_name, project_location)
    vendors_text = ", ".join(vendor_labels) if vendor_labels else "your vendor list"
    message_lines = [
        f"✅ Request logged for {project_line}.",
        f"Quotes requested from: {vendors_text}.",
        "We'll notify you as each vendor responds. Track progress below.",
    ]

    url = _quote_summary_url(request_id)
    state.update(
        intent="rfq",
        latest_respons= "\n".join(message_lines),
        uoc_next_message_type="link_cta",
        uoc_question_type="quote_request",
        uoc_next_message_extra_data= {"display_text": "Choose Vendors Quotes", "url": url} if url else None,
    )

    return state
    # if url:
    #     whatsapp_output(
    #         to_number=user_id,
    #         message_text="\n".join(message_lines),
    #         message_type="link_cta",
    #         extra_data={"display_text": "View Quotes", "url": url},
    #     )
    # else:
    #     whatsapp_output(
    #         to_number=user_id,
    #         message_text="\n".join(message_lines),
    #         message_type="plain",
    #     )


async def handle_quote_flow(
    state: dict,
    user_id: str,
    vendors: List[Dict[str, Optional[str]]],
    request_id: str,
    items: List[Dict[str, Optional[str]]],
    *,
    project_name: Optional[str] = None,
    project_location: Optional[str] = None,
) -> dict:
    """Notify vendors and supervisor after a request is submitted."""
    print(
        f"Requesting quotes from vendors: {vendors} for request {request_id} with items: {items}"
    )
    print(
        "quotation_handler ::::: handle_quote_flow ::::: vendors count :",
        len(vendors),
    )

    notified_labels: List[str] = []
    notified_ids: List[str] = []
    item_count = len(items) if items is not None else None

    for vendor in vendors:
        vendor_id = vendor.get("vendor_id")
        contact_number = vendor.get("phone")
        vendor_label = vendor.get("name") or vendor_id

        if not vendor_id:
            print(
                "quotation_handler ::::: handle_quote_flow ::::: skipping vendor entry without vendor_id:",
                vendor,
            )
            continue

        try:
            print(
                "quotation_handler ::::: handle_quote_flow ::::: notifying vendor",
                vendor_id,
                "on",
                contact_number,
            )
            await send_quote_request_to_vendor(
                vendor_id,
                request_id,
                contact_number,
                project_name=project_name,
                project_location=project_location,
                item_count=item_count,
                #Dummy data for template params
                vendor_display_name="Likhitha",
                builder_name="Chandu Babu",
                company_name="Briklay Constructions",

            )
            notified_labels.append(vendor_label or vendor_id)
            notified_ids.append(vendor_id)
            print(
                "quotation_handler ::::: handle_quote_flow : notified vendor",
                vendor_id,
                vendor_display_name="Likhitha",
                builder_name="Chandu Babu",
                company_name="Briklay Constructions",

            )
            notified_labels.append(vendor_label or vendor_id)
            notified_ids.append(vendor_id)
            print(
                "quotation_handler ::::: handle_quote_flow : notified vendor",
                vendor_id,
            )
        except Exception as exc:  # pragma: no cover - notification best effort
            print(
                "quotation_handler ::::: handle_quote_flow ::::: vendor",
                vendor_id,
                "notification failed :",
                exc,
            )

    print("quotation_handler ::::: handle_quote_flow ::::: notified vendors :", notified_ids)
    state["uoc_next_message_type"] = "plain"
    state["uoc_question_type"] = "quote_request"

    if notified_labels:
        state["latest_respons"] = (
            "Quote requests sent for "
            f"{_format_project_line(project_name, project_location)} to: "
            f"{', '.join(notified_labels)}. We'll let you know as responses arrive."
        )
    else:
        state["latest_respons"] = (
            "We could not reach any vendors for this request yet. "
            "We'll notify you as soon as we do."
        )

    print("quotation_handler ::::: handle_quote_flow :::: notify user", user_id)
    print("quotation_handler ::::: handle_quote_flow :::: request id", request_id)
    try:
       state = await notify_user_quote_ready(
            state,
            user_id=user_id,
            request_id=request_id,
            project_name=project_name,
            project_location=project_location,
            vendor_labels=notified_labels,
        )
    except Exception as exc:  # pragma: no cover - notification best effort
        print(
            "quotation_handler ::::: handle_quote_flow ::::: exception in notifying user :",
            exc,
        )
        return state

    print("quotation_handler ::::: handle_quote_flow :::: successfully notified the user")
    return state


async def send_vendor_order_confirmation(
    request_id: str,
    vendor_id: str,
    order_summary: dict,
    phone: Optional[str] = None,
) -> None:
    """Notify the selected vendor that an order has been confirmed."""
    if not phone:
        print("quotation_handler ::::: send_vendor_order_confirmation ::::: no phone number available")
        return

    print("quotation_handler ::::: send_vendor_order_confirmation ::::: using phone :", phone)
    total_val = order_summary.get("order_total")
    project_line = _format_project_line(
        order_summary.get("project_name"),
        order_summary.get("project_location") or order_summary.get("delivery_location"),
    )
    expected_date = order_summary.get("expected_delivery_date")
    items = order_summary.get("items", [])

    item_lines: List[str] = []
    for item in items[:5]:
        name = item.get("material_name") or "Material"
        qty = item.get("quantity")
        unit = item.get("quantity_units") or "units"
        item_lines.append(f"• {name} – {qty} {unit}")
    if len(items) > 5:
        item_lines.append("• …")

    message_lines = [
        "✅ Thirtee  order confirmed",
        f"Project: {project_line}",
    ]
    if expected_date:
        message_lines.append(f"Deliver by: {expected_date}")
    if total_val is not None:
        message_lines.append(f"Order total: ₹{total_val}")
    if item_lines:
        message_lines.append("Items:")
        message_lines.extend(item_lines)
    message_lines.append("Open the link below for full details and next steps.")

    whatsapp_output(
        to_number=phone,
        message_text="\n".join(message_lines),
        message_type="link_cta",
        extra_data={"display_text": "View Order Details", "url": _vendor_order_url(request_id, vendor_id)},
    )

    buttons = [
        {"id": "vendor_confirm", "title": "Confirm Order"},
        {"id": "vendor_cannot_fulfill", "title": "Cannot Fulfill"},
    ]
    whatsapp_output(
        to_number=phone,
        message_text="Please choose an option:",
        message_type="button",
        extra_data=buttons,
    )

    from whatsapp.webhook import save_state  # noqa: WPS433  (lazy import to avoid cycle)

    vendor_state = {
        "sender_id": phone,
        "messages": [],
        "agent_first_run": False,
        "needs_clarification": True,
        "uoc_last_called_by": None,
        "uoc_confidence": "low",
        "uoc": {},
        "uoc_question_type": "procurement_new_user_flow",
        "uoc_next_message_type": "button",
        "uoc_next_message_extra_data": buttons,
        "vendor_ack_context": {
            "request_id": request_id,
            "vendor_id": vendor_id,
            "order_total": total_val,
        },
    }
    save_state(phone, vendor_state)


async def notify_user_vendor_confirmed(user_id: str, request_id: str) -> None:
    try:
        whatsapp_output(
            to_number=user_id,
            message_text="Vendor confirmed your order. Preparing for delivery.",
            message_type="plain",
        )
    except Exception as exc:  # pragma: no cover
        print("quotation_handler ::::: notify_user_vendor_confirmed ::::: exception :", exc)


async def notify_user_vendor_declined(user_id: str, request_id: str) -> None:
    try:
        url = _quote_summary_url(request_id)
        if url:
            whatsapp_output(
                to_number=user_id,
                message_text="Selected vendor can’t fulfill. Please choose another vendor.",
                message_type="link_cta",
                extra_data={"display_text": "View Other Quotes", "url": url},
            )
        else:
            whatsapp_output(
                to_number=user_id,
                message_text="Selected vendor can’t fulfill. Please choose another vendor.",
                message_type="plain",
            )
    except Exception as exc:  # pragma: no cover
        print("quotation_handler ::::: notify_user_vendor_declined ::::: exception :", exc)


async def notify_user_vendor_quote_update(
    user_id: str,
    vendor_name: Optional[str],
    request_id: str,
    *,
    project_name: Optional[str] = None,
    project_location: Optional[str] = None,
    is_update: bool = False,
) -> None:
    """Ping the supervisor when a vendor submits or updates prices."""
    if not user_id:
        print("quotation_handler ::::: notify_user_vendor_quote_update ::::: missing user id")
        return

    project_line = _format_project_line(project_name, project_location)
    vendor_label = vendor_name or "A vendor"
    verb = "updated" if is_update else "submitted"
    icon = "🔁" if is_update else "📩"

    message_lines = [
        f"{icon} {vendor_label} has {verb} prices for {project_line}.",
        "Review all quotes below to compare vendors.",
    ]

    url = _quote_summary_url(request_id)
    if url:
        whatsapp_output(
            to_number=user_id,
            message_text="\n".join(message_lines),
            message_type="link_cta",
            extra_data={"display_text": "Review Quotes", "url": url},
        )
    else:
        whatsapp_output(
            to_number=user_id,
            message_text="\n".join(message_lines),
            message_type="plain",
        )


async def send_vendor_order_confirmation(
    request_id: str,
    vendor_id: str,
    order_summary: dict,
    phone: Optional[str] = None,
) -> None:
    """Notify the selected vendor that an order has been confirmed."""
    if not phone:
        print("quotation_handler ::::: send_vendor_order_confirmation ::::: no phone number available")
        return

    print("quotation_handler ::::: send_vendor_order_confirmation ::::: using phone :", phone)
    total_val = order_summary.get("order_total")
    project_line = _format_project_line(
        order_summary.get("project_name"),
        order_summary.get("project_location") or order_summary.get("delivery_location"),
    )
    expected_date = order_summary.get("expected_delivery_date")
    items = order_summary.get("items", [])

    item_lines: List[str] = []
    for item in items[:5]:
        name = item.get("material_name") or "Material"
        qty = item.get("quantity")
        unit = item.get("quantity_units") or "units"
        item_lines.append(f"• {name} – {qty} {unit}")
    if len(items) > 5:
        item_lines.append("• …")

    message_lines = [
        "✅ Thirtee  order confirmed",
        f"Project: {project_line}",
    ]
    if expected_date:
        message_lines.append(f"Deliver by: {expected_date}")
    if total_val is not None:
        message_lines.append(f"Order total: ₹{total_val}")
    if item_lines:
        message_lines.append("Items:")
        message_lines.extend(item_lines)
    message_lines.append("Open the link below for full details and next steps.")

    whatsapp_output(
        to_number=phone,
        message_text="\n".join(message_lines),
        message_type="link_cta",
        extra_data={"display_text": "View Order Details", "url": _vendor_order_url(request_id, vendor_id)},
    )

    buttons = [
        {"id": "vendor_confirm", "title": "Confirm Order"},
        {"id": "vendor_cannot_fulfill", "title": "Cannot Fulfill"},
    ]
    whatsapp_output(
        to_number=phone,
        message_text="Please choose an option:",
        message_type="button",
        extra_data=buttons,
    )

    from whatsapp.webhook import save_state  # noqa: WPS433  (lazy import to avoid cycle)

    vendor_state = {
        "sender_id": phone,
        "messages": [],
        "agent_first_run": False,
        "needs_clarification": True,
        "uoc_last_called_by": None,
        "uoc_confidence": "low",
        "uoc": {},
        "uoc_question_type": "procurement_new_user_flow",
        "uoc_next_message_type": "button",
        "uoc_next_message_extra_data": buttons,
        "vendor_ack_context": {
            "request_id": request_id,
            "vendor_id": vendor_id,
            "order_total": total_val,
        },
    }
    save_state(phone, vendor_state)


async def notify_user_vendor_confirmed(user_id: str, request_id: str) -> None:
    try:
        whatsapp_output(
            to_number=user_id,
            message_text="Vendor confirmed your order. Preparing for delivery.",
            message_type="plain",
        )
    except Exception as exc:  # pragma: no cover
        print("quotation_handler ::::: notify_user_vendor_confirmed ::::: exception :", exc)


async def notify_user_vendor_declined(user_id: str, request_id: str) -> None:
    try:
        url = _quote_summary_url(request_id)
        if url:
            whatsapp_output(
                to_number=user_id,
                message_text="Selected vendor can’t fulfill. Please choose another vendor.",
                message_type="link_cta",
                extra_data={"display_text": "View Other Quotes", "url": url},
            )
        else:
            whatsapp_output(
                to_number=user_id,
                message_text="Selected vendor can’t fulfill. Please choose another vendor.",
                message_type="plain",
            )
    except Exception as exc:  # pragma: no cover
        print("quotation_handler ::::: notify_user_vendor_declined ::::: exception :", exc)


async def notify_user_vendor_quote_update(
    user_id: str,
    vendor_name: Optional[str],
    request_id: str,
    *,
    project_name: Optional[str] = None,
    project_location: Optional[str] = None,
    is_update: bool = False,
) -> None:
    """Ping the supervisor when a vendor submits or updates prices."""
    if not user_id:
        print("quotation_handler ::::: notify_user_vendor_quote_update ::::: missing user id")
        return

    project_line = _format_project_line(project_name, project_location)
    vendor_label = vendor_name or "A vendor"
    verb = "updated" if is_update else "submitted"
    icon = "🔁" if is_update else "📩"

    message_lines = [
        f"{icon} {vendor_label} has {verb} prices for {project_line}.",
        "Review all quotes below to compare vendors.",
    ]

    url = _quote_summary_url(request_id)
    if url:
        whatsapp_output(
            to_number=user_id,
            message_text="\n".join(message_lines),
            message_type="link_cta",
            extra_data={"display_text": "Review Quotes", "url": url},
        )
    else:
        whatsapp_output(
            to_number=user_id,
            message_text="\n".join(message_lines),
            message_type="plain",
        )


async def send_vendor_order_confirmation(request_id: str, vendor_id: str, order_summary: dict, phone: Optional[str] = None):
    """
    Notify selected vendor with order confirmation CTA + decision buttons.
    Requires a target phone number from the vendor record.
    """
    try:
        if not phone:
            print("quotation_handler ::::: send_vendor_order_confirmation ::::: no phone number available")
            return

        print(f"quotation_handler ::::: send_vendor_order_confirmation ::::: using phone : {phone}")
        total_val = order_summary.get("order_total")
        vendor_name = order_summary.get("vendor_name", "Vendor")

        # Build link
        if VENDOR_ORDER_CONFIRMATION_URL_BASE:
            url = f"{VENDOR_ORDER_CONFIRMATION_URL_BASE}?uuid={request_id}&vendor_id={vendor_id}"
        else:
            url = f"https://example.com/vendor/order?uuid={request_id}&vendor_id={vendor_id}"

        # Message body
        message = (
            f"You have been selected for order {request_id}.\n"
            f"Total: ₹{total_val}\n"
            f"Please review and confirm."
        )

        # 1) Send CTA link to view order details
        whatsapp_output(
            to_number=phone,
            message_text=message,
            message_type="link_cta",
            extra_data={"display_text": "View Order Details", "url": url},
        )

        # 2) Send decision buttons (simple IDs; context seeded in state)
        buttons = [
            {"id": "vendor_confirm", "title": "Confirm Order"},
            {"id": "vendor_cannot_fulfill", "title": "Cannot Fulfill"},
        ]
        whatsapp_output(
            to_number=phone,
            message_text="Please choose an option:",
            message_type="button",
            extra_data=buttons,
        )

        # 3) Seed vendor chat state so webhook routes reply to procurement agent without changes
        # Lazy import to avoid circular import during app startup
        from whatsapp.webhook import save_state  # noqa: WPS433

        vendor_state = {
            "sender_id": phone,
            "messages": [],
            "agent_first_run": False,
            "needs_clarification": True,
            "uoc_last_called_by": None,
            "uoc_confidence": "low",
            "uoc": {},
            # Route into procurement agent flow
            "uoc_question_type": "procurement_new_user_flow",
            # In case we want to resend buttons on follow-ups
            "uoc_next_message_type": "button",
            "uoc_next_message_extra_data": buttons,
            # Context for vendor acknowledgement handlers
            "vendor_ack_context": {
                "request_id": request_id,
                "vendor_id": vendor_id,
                "order_total": total_val,
            },
        }
        save_state(phone, vendor_state)
    except Exception as e:
        print("quotation_handler ::::: send_vendor_order_confirmation ::::: exception :", e)
        raise

async def notify_user_vendor_confirmed(user_id: str, request_id: str):
    try:
        whatsapp_output(
            to_number=user_id,
            message_text="Vendor confirmed your order. Preparing for delivery.",
            message_type="plain",
        )
    except Exception as e:
        print("quotation_handler ::::: notify_user_vendor_confirmed ::::: exception :", e)

async def notify_user_vendor_declined(user_id: str, request_id: str):
    try:
        url = f"{QUOTE_SUMMARY_URL}?uuid={request_id}" if QUOTE_SUMMARY_URL else None
        if url:
            whatsapp_output(
                to_number=user_id,
                message_text="Selected vendor can’t fulfill. Please choose another vendor.",
                message_type="link_cta",
                extra_data={"display_text": "View Other Quotes", "url": url},
            )
        else:
            whatsapp_output(
                to_number=user_id,
                message_text="Selected vendor can’t fulfill. Please choose another vendor.",
                message_type="plain",
            )
    except Exception as e:
        print("quotation_handler ::::: notify_user_vendor_declined ::::: exception :", e)