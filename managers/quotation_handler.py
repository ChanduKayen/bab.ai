# quote_handler.py

import os
from dotenv import load_dotenv
from whatsapp.builder_out import whatsapp_output  # Changed to use your provided function

load_dotenv()

VENDOR_QUOTE_URL_BASE = os.getenv("VENDOR_QUOTE_URL_BASE")
QUOTE_SUMMARY_URL = os.getenv("QUOTE_SUMMARY_URL")
VENDOR_ORDER_CONFIRMATION_URL_BASE = os.getenv("VENDOR_ORDER_CONFIRMATION_URL_BASE")

async def send_quote_request_to_vendor(vendor_id: str, request_id: str):
    quote_page_url = f"{VENDOR_QUOTE_URL_BASE}?uuid={request_id}&vendor_id={vendor_id}"
    message = f"You have a new material quote request. Please review and respond:"
    cta_button = {
        "display_text": "Review & Respond",
        "url": quote_page_url
    }
    whatsapp_output(to_number=vendor_id, message_text=message, message_type="link_cta", extra_data=cta_button)

async def notify_user_quote_ready(user_id: str, request_id: str):
    request_id="08a972b5-ac48-4974-ade2-977985101359"
    quote_summary_url = f"{QUOTE_SUMMARY_URL}?uuid={request_id}"
    message = f"Vendor quotes are ready for your review:"
    cta_button = {
        "display_text": "View Quotes",
        "url": quote_summary_url
    }
    whatsapp_output(to_number=user_id, message_text=message, message_type="link_cta", extra_data=cta_button)

async def handle_quote_flow(state: dict, user_id, vendor_uuids: list, request_id: str, items: list):
    print(f"Requesting quotes from vendors: {vendor_uuids} for request {request_id} with items: {items}")
    vendor_uuids=["917036233512"]
    for vendor_id in vendor_uuids:
        try:
            await send_quote_request_to_vendor(vendor_id, request_id)
            print(f"quotation_handler ::::: handle_quote_flow : notified vendor {vendor_id}")
        except Exception as e :
            print(f"quotation_handler ::::: handle_quote_flow ::::: vendors : {vendor_id} notification failed : {e}")

    print("quotation_handler ::::: handle_quote_flow ::::: notified vendors : ", vendor_uuids)

    print("quotation_handler ::::: handle_quote_flow ::::: send material quote to user")
    state["latest_response"] = f"Quote requests sent to vendors: {', '.join(vendor_uuids)}. You will be notified once all vendors respond."
    state["uoc_next_message_type"] = "plain"
    state["uoc_question_type"] = "quote_request"
    print("quotation_handler ::::: handle_quote_flow :::: notify user", user_id)
    print("quotation_handler ::::: handle_quote_flow :::: request id", request_id)
    # Notify the user that quotes are being requested
    try : 
        await notify_user_quote_ready(user_id=user_id, request_id=request_id)
    except Exception as e :
        print("quotation_handler ::::: handle_quote_flow ::::: exception in notifying user : ", e)
        return
    print("quotation_hanlder ::::: handle_quote_flow :::: successfully notified the user")
    return state


HARD_CODED_VENDOR_PHONE = "917036233512"

async def send_vendor_order_confirmation(request_id: str, vendor_id: str, order_summary: dict):
    """
    Notify selected vendor with order confirmation CTA + decision buttons.
    Uses a hardcoded vendor phone for now as per current policy.
    """
    try:
        phone = HARD_CODED_VENDOR_PHONE
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
