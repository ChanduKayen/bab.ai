# quote_handler.py

import os
from dotenv import load_dotenv
from whatsapp.builder_out import whatsapp_output  # Changed to use your provided function

load_dotenv()

VENDOR_QUOTE_URL_BASE = os.getenv("VENDOR_QUOTE_URL_BASE")
QUOTE_SUMMARY_URL = os.getenv("QUOTE_SUMMARY_URL")

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
