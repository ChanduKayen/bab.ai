# quote_handler.py

import os
from dotenv import load_dotenv
from whatsapp.builder_out import whatsapp_output  # Changed to use your provided function

load_dotenv()

VENDOR_QUOTE_URL_BASE = os.getenv("VENDOR_QUOTE_URL_BASE")
QUOTE_SUMMARY_URL = os.getenv("QUOTE_SUMMARY_URL")

async def send_quote_request_to_vendor(vendor_id: str, request_id: str):
    quote_page_url = f"{VENDOR_QUOTE_URL_BASE}?request_id={request_id}&vendor_id={vendor_id}"
    message = f"You have a new material quote request. Please review and respond:"
    cta_button = {
        "display_text": "Review & Respond",
        "url": quote_page_url
    }
    whatsapp_output(to_number=vendor_id, message_text=message, message_type="link_cta", extra_data=cta_button)

async def notify_user_quote_ready(user_id: str, request_id: str):
    quote_summary_url = f"{QUOTE_SUMMARY_URL}?request_id={request_id}"
    message = f"Vendor quotes are ready for your review:"
    cta_button = {
        "display_text": "View Quotes",
        "url": quote_summary_url
    }
    whatsapp_output(to_number=user_id, message_text=message, message_type="link_cta", extra_data=cta_button)

async def handle_quote_flow(state: dict, vendor_uuids: list, request_id: str, items: list):
    print(f"Requesting quotes from vendors: {vendor_uuids} for request {request_id} with items: {items}")
    for vendor_id in vendor_uuids:
        await send_quote_request_to_vendor(vendor_id, request_id)

    state["latest_response"] = f"Quote requests sent to vendors: {', '.join(vendor_uuids)}. You will be notified once all vendors respond."
    state["uoc_next_message_type"] = "plain"
    state["uoc_question_type"] = "quote_request"

    # Notify the user that quotes are being requested
    await notify_user_quote_ready(state.get("user_id"), request_id)
    
    return state
