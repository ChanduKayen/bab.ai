# quote_handler.py

import os
from typing import Dict, List, Optional
from dotenv import load_dotenv
from whatsapp.builder_out import whatsapp_output  # Changed to use your provided function

load_dotenv()

VENDOR_QUOTE_URL_BASE = os.getenv("VENDOR_QUOTE_URL_BASE")
QUOTE_SUMMARY_URL = os.getenv("QUOTE_SUMMARY_URL")
VENDOR_ORDER_CONFIRMATION_URL_BASE = os.getenv("VENDOR_ORDER_CONFIRMATION_URL_BASE")

async def send_quote_request_to_vendor(vendor_id: str, request_id: str, contact_number: Optional[str]):
    if not contact_number:
        print(f"quotation_handler ::::: send_quote_request_to_vendor ::::: missing contact for vendor {vendor_id}")
        return

    quote_page_url = f"{VENDOR_QUOTE_URL_BASE}?uuid={request_id}&vendorId={vendor_id}"
    message = "You have a new material quote request. Please review and respond:"
    cta_button = {
        "display_text": "Review & Respond",
        "url": quote_page_url
    }
    whatsapp_output(to_number=contact_number, message_text=message, message_type="link_cta", extra_data=cta_button)

async def notify_user_quote_ready(user_id: str, request_id: str):
    quote_summary_url = f"{QUOTE_SUMMARY_URL}?uuid={request_id}"
    message = f"Vendor quotes are ready for your review:"
    cta_button = {
        "display_text": "View Quotes",
        "url": quote_summary_url
    }
    whatsapp_output(to_number=user_id, message_text=message, message_type="link_cta", extra_data=cta_button)

async def handle_quote_flow(state: dict, user_id: str, vendors: List[Dict[str, Optional[str]]], request_id: str, items: List[Dict[str, Optional[str]]]):
    print(f"Requesting quotes from vendors: {vendors} for request {request_id} with items: {items}")
    print(f"quotation_handler ::::: handle_quote_flow ::::: vendors count : {len(vendors)}")

    notified_labels: List[str] = []
    notified_ids: List[str] = []
    for vendor in vendors:
        vendor_id = vendor.get("vendor_id")
        contact_number = vendor.get("phone")
        vendor_label = vendor.get("name") or vendor_id

        if not vendor_id:
            print(f"quotation_handler ::::: handle_quote_flow ::::: skipping vendor entry without vendor_id: {vendor}")
            continue

        try:
            print(f"quotation_handler ::::: handle_quote_flow ::::: notifying vendor {vendor_id} on {contact_number}")
            await send_quote_request_to_vendor(vendor_id, request_id, contact_number)
            notified_labels.append(vendor_label or vendor_id)
            notified_ids.append(vendor_id)
            print(f"quotation_handler ::::: handle_quote_flow : notified vendor {vendor_id}")
        except Exception as e:
            print(f"quotation_handler ::::: handle_quote_flow ::::: vendor {vendor_id} notification failed : {e}")

    print("quotation_handler ::::: handle_quote_flow ::::: notified vendors : ", notified_ids)
    state["uoc_next_message_type"] = "plain"
    state["uoc_question_type"] = "quote_request"

    if notified_labels:
        state["latest_response"] = f"Quote requests sent to vendors: {', '.join(notified_labels)}. You will be notified once all vendors respond."
    else:
        state["latest_response"] = "We could not reach any vendors for this request yet. We will notify you once we are able to send the quote requests."

    print("quotation_handler ::::: handle_quote_flow :::: notify user", user_id)
    print("quotation_handler ::::: handle_quote_flow :::: request id", request_id)
    try:
        await notify_user_quote_ready(user_id=user_id, request_id=request_id)
    except Exception as e:
        print("quotation_handler ::::: handle_quote_flow ::::: exception in notifying user : ", e)
        return state

    print("quotation_handler ::::: handle_quote_flow :::: successfully notified the user")
    return state


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