# agents/credit_agent.py

import os, re, json, asyncio
from typing import Optional, List, Dict, Any
from dotenv import load_dotenv

from whatsapp.builder_out import whatsapp_output
from database._init_ import AsyncSessionLocal
from langchain_core.messages import SystemMessage, HumanMessage

# OPTIONAL: if you’ll use LLM anywhere (not strictly required for credit flow)
from langchain_openai import ChatOpenAI

# App managers (you’ll implement CreditManager, VendorCRUD as needed)
# from database.credit_crud import CreditCRUD
# from database.vendor_crud import VendorCRUD
# from managers.credit_manager import CreditManager
# from managers.uoc_manager import UOCManager
from whatsapp import apis

load_dotenv()

# ------- CONFIG / CLIENTS -------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
llm = ChatOpenAI(model="gpt-4o", temperature=0, openai_api_key=OPENAI_API_KEY)

# ------- UTILS -------
_JSON_PATTERN = re.compile(r"\{.*\}", re.S)

def safe_json(text: str, default=None):
    txt = text.strip()
    if txt.startswith("```"):
        txt = txt.strip("`").lstrip("json").strip()
    try:
        return json.loads(txt)
    except Exception:
        match = _JSON_PATTERN.search(txt)
        if match:
            try:
                return json.loads(match.group(0))
            except Exception:
                pass
    return default if default is not None else {}

def mask_id(s: str, keep_last=4) -> str:
    if not s or len(s) <= keep_last:
        return "****"
    return "****" + s[-keep_last:]

# ------- STATE HELPERS -------
def _set_buttons(state, buttons: List[Dict[str, str]]):
    state["uoc_next_message_type"] = "button"
    state["uoc_next_message_extra_data"] = buttons

def _link_cta(state, text: str, url: str):
    state["uoc_next_message_type"] = "link_cta"
    state["uoc_next_message_extra_data"] = {"display_text": text, "url": url}

# ------- INTENTS / HANDLERS MAP -------
async def handle_credit_entry(state, crud, latest_response: str, extra=None):
    """
    Initial landing for credit flow: decides if user needs onboarding or can proceed.
    """
    sender_id = state.get("sender_id")
    state.update(
        intent="credit",
        latest_respons=latest_response,
        uoc_question_type="credit",
        needs_clarification=True,
    )

    # Fetch credit snapshot
    try:
        async with AsyncSessionLocal() as session:
            # credit_mgr = CreditManager(session)
            credit_profile = await _get_credit_profile_mock(state)  # replace with credit_mgr.get_profile(sender_id)
    except Exception as e:
        print("Credit Agent:::: handle_credit_entry error:", e)
        state["latest_respons"] = "Sorry, I couldn’t check your credit right now. Please try again."
        return state

    if credit_profile.get("status") == "approved":
        limit = credit_profile["limit"]
        used = credit_profile["used"]
        msg = (f"✅ Credit is active.\n"
               f"Available: ₹{limit - used:,} (Used ₹{used:,} / Limit ₹{limit:,})\n\n"
               f"Proceed to choose a vendor and complete this order.")
        state["latest_respons"] = msg
        _set_buttons(state, [
            {"id": "credit_view_portal", "title": "View Credit & Vendors"},
            {"id": "main_menu", "title": "🏠 Main Menu"}
        ])
        return state

    # not approved → start onboarding
    msg = ("Let’s get you approved for Bab.ai Credit.\n\n"
           "We’ll need your Aadhaar, PAN, GST and your consent to verify.\n"
           "Your data is encrypted and shared only with our regulated credit partner.")
    state["latest_respons"] = msg
    _set_buttons(state, [
        {"id": "credit_onboard_start", "title": "Start Credit Check"},
        {"id": "main_menu", "title": "🏠 Main Menu"}
    ])
    return state

async def handle_credit_onboard_start(state, crud, latest_response: str, extra=None):
    """
    WhatsApp Flows collection—done one-by-one to reduce friction.
    """
    sender_id = state.get("sender_id")
    state.update(
        intent="credit",
        latest_respons=latest_response or "Please share your Aadhaar number.",
        uoc_question_type="credit_onboard_aadhaar",
        needs_clarification=True,
    )
    _set_buttons(state, [{"id": "cancel", "title": "Cancel"}, {"id": "main_menu", "title": "🏠 Main Menu"}])
    return state

async def handle_collect_aadhaar(state, crud, latest_response: str, extra=None):
    aadhaar = state.get("messages", [])[-1].get("content", "").replace(" ", "")
    # Basic sanity (you’ll add proper validators / VID support)
    if not (aadhaar.isdigit() and 8 < len(aadhaar) <= 16):
        state["latest_respons"] = "That doesn’t look like a valid Aadhaar/VID. Please re-enter."
        state["uoc_question_type"] = "credit_onboard_aadhaar"
        return state

    state.setdefault("credit_profile", {})["aadhaar"] = aadhaar
    state.update(
        latest_respons=f"Aadhaar received: {mask_id(aadhaar)}\nNow, please share your PAN.",
        uoc_question_type="credit_onboard_pan",
        needs_clarification=True,
    )
    return state

async def handle_collect_pan(state, crud, latest_response: str, extra=None):
    pan = state.get("messages", [])[-1].get("content", "").strip().upper()
    if not re.match(r"^[A-Z]{5}\d{4}[A-Z]$", pan):
        state["latest_respons"] = "That PAN doesn’t look right. Please re-enter (e.g., ABCDE1234F)."
        state["uoc_question_type"] = "credit_onboard_pan"
        return state

    state.setdefault("credit_profile", {})["pan"] = pan
    state.update(
        latest_respons=f"PAN received: {mask_id(pan)}\nPlease share your GST number.",
        uoc_question_type="credit_onboard_gst",
        needs_clarification=True,
    )
    return state

async def handle_collect_gst(state, crud, latest_response: str, extra=None):
    gst = state.get("messages", [])[-1].get("content", "").strip().upper()
    if not re.match(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$", gst):
        state["latest_respons"] = "That GST number doesn’t look valid. Please re-enter."
        state["uoc_question_type"] = "credit_onboard_gst"
        return state

    state.setdefault("credit_profile", {})["gst"] = gst
    msg = ("Last step: please provide consent to share and verify your details with our regulated credit partner.\n\n"
           "Reply 'I CONSENT' to proceed.")
    state.update(
        latest_respons=msg,
        uoc_question_type="credit_onboard_consent",
        needs_clarification=True,
    )
    return state

async def handle_collect_consent(state, crud, latest_response: str, extra=None):
    text = state.get("messages", [])[-1].get("content", "").strip().upper()
    if text not in ("I CONSENT", "I CONSENT."):
        state["latest_respons"] = "Please reply exactly 'I CONSENT' to proceed."
        state["uoc_question_type"] = "credit_onboard_consent"
        return state

    # Submit to credit partner (async)
    sender_id = state.get("sender_id")
    profile = state.get("credit_profile", {})
    try:
        async with AsyncSessionLocal() as session:
            # credit_mgr = CreditManager(session)
            # await credit_mgr.submit_kyc(sender_id, profile)
            pass
    except Exception as e:
        print("Credit Agent:::: submit_kyc error:", e)
        state["latest_respons"] = "We couldn’t submit your application. Please try again."
        return state

    state.update(
        latest_respons="Thanks! Your application is submitted. This usually takes 2–5 minutes. I’ll update you here.",
        uoc_question_type="credit_onboard_pending",
        needs_clarification=True,
    )
    # Optional: schedule a short poll / set a task—here we just simulate
    return state

async def handle_poll_approval(state, crud, latest_response: str, extra=None):
    """
    Poll the credit partner. If approved → show buttons.
    """
    try:
        async with AsyncSessionLocal() as session:
            # credit_mgr = CreditManager(session)
            # status = await credit_mgr.check_status(state.get("sender_id"))
            status = await _mock_check_status()
    except Exception as e:
        print("Credit Agent:::: poll_approval error:", e)
        state["latest_respons"] = "Still checking… I’ll notify you as soon as there’s an update."
        return state

    if status.get("status") == "approved":
        limit = status["limit"]
        used = status["used"]
        state["latest_respons"] = (f"🎉 Credit Approved!\n"
                                   f"Available: ₹{limit - used:,} (Used ₹{used:,} / Limit ₹{limit:,})")
        _set_buttons(state, [
            {"id": "credit_view_portal", "title": "View Credit & Vendors"},
            {"id": "main_menu", "title": "🏠 Main Menu"}
        ])
        state["uoc_question_type"] = "credit"
        return state

    state["latest_respons"] = "Your application is still under review. I’ll keep you posted."
    state["uoc_question_type"] = "credit_onboard_pending"
    return state

async def handle_credit_portal(state, crud, latest_response: str, extra=None):
    """
    Deep link to ONE mobile webview that has:
    - Tab 1: Credit Info (limit, used, Trust Score, NBFC logo)
    - Tab 2: Vendors (credit-eligible vendors for the active PO)
    """
    sender_id = state.get("sender_id")
    # Build a signed URL with session_id / request_id
    try:
        credit_url = apis.get_credit_portal_url(
            base_url="https://babai-ui.vercel.app/credit-portal",
            params={},
            query={"sid": sender_id, "request_id": state.get("active_material_request_id")}
        )
    except Exception as e:
        print("Credit Agent:::: credit_portal URL error:", e)
        state["latest_respons"] = "Couldn’t open the credit portal. Please try again."
        return state

    state["latest_respons"] = "Opening your credit & vendors page…"
    _link_cta(state, "View Credit & Vendors", credit_url)
    state["uoc_question_type"] = "credit"
    return state

async def handle_vendor_confirm(state, crud, latest_response: str, extra=None):
    """
    After user picks vendor in webview → we confirm vendor and request invoice/e-waybill via WhatsApp.
    """
    vendor_id = state.get("selected_vendor_id")  # set by your webview callback
    if not vendor_id:
        state["latest_respons"] = "Please select a vendor in the portal to continue."
        _set_buttons(state, [{"id": "credit_view_portal", "title": "Open Credit Portal"}])
