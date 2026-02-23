# agents/credit_agent.py

import os, re, json, asyncio
from typing import Optional, List, Dict, Any
from dotenv import load_dotenv

from whatsapp.builder_out import whatsapp_output
#from database._init_ import AsyncSessionLocal
from app.db import get_sessionmaker
AsyncSessionLocal = get_sessionmaker()
from langchain_core.messages import SystemMessage, HumanMessage

# OPTIONAL: if you’ll use LLM anywhere (not strictly required for credit flow)
from langchain_openai import ChatOpenAI

# App managers (you’ll implement CreditManager, VendorCRUD as needed)
# from database.credit_crud import CreditCRUD
# from database.vendor_crud import VendorCRUD
from managers.credit_manager import CreditManager
from managers.trust_module import BabaiTrustModule
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
async def run_credit_agent(state: dict,  config: dict):
    last_msg   = (state["messages"][-1]["content"] or "").strip()
    sender_id = state.get("sender_id")
    print("Credit Agent:::: run_credit_agent : starting credit flow")
    try:
        crud = config["configurable"]["crud"]
        credit_mgr = CreditManager(crud) 
        
    except Exception as e:
        print("PCredit Agent:::: run_credit_agent : failed to initialize crud ", e)
        state["latest_respons"] = "Sorry, there was a system error. Please try again later."
        return state
    try:
        async with AsyncSessionLocal() as session:
            credit_mgr = CreditManager(session)
            credit_profile = await credit_mgr.get_profile(sender_id)  
            print("Credit Agent:::: run_credit_agent : fetched credit profile:", credit_profile)
    except Exception as e:
        print("Credit Agent:::: handle_credit_entry error:", e)
        state["latest_respons"] = "Sorry, I couldn’t check your credit right now. Please try again."
        return state
    print("last Message", last_msg)
    
    state.update(
        intent="credit", 
        latest_respons="Welcome to Thirtee  Credit! How can I assist you today?",
        uoc_question_type="credit",
        needs_clarification=True,
    )
    if last_msg =="routed_from_other_agent":
        state["latest_respons"] =  ( "💳 Thirtee  Credit — India's first credit system for builders.\n\n"
                                    "Turn a material need into purchasing power in minutes:\n\n"
                                    "① Send your requirement — photo, invoice, or a simple message.\n"
                                    "② Check eligibility instantly\n"
                                    "③ Choose your vendor — Thirtee  pays.\n\n"
                                    "Reply now with your requirement to begin.\n")
        state["needs_clarification"] = True
        state["uoc_question_type"] ="credit_start"
        if credit_profile.get("status") == "pending":
            _set_buttons(state, [
            {"id": "credit_start", "title": "Check Eligibility"},
            {"id": "main_menu", "title": "🏠 Main Menu"}
        ])
        _set_buttons(state, [
            {"id": "main_menu", "title": "🏠 Main Menu"}  ]) 
        return state
    if last_msg== "rfq":
        state.update(
                uoc_question_type="procurement_new_user_flow",
                needs_clarification=True,
                agent_first_run=False  
            )
        return state

    if last_msg =="credit_start":
        print("Credit Agent:::: run_credit_agent : last_msg is credit_start, calling handle_credit_onboard_start")
        return await handle_credit_onboard_start(state, crud)
    if last_msg == "application_status":
        print("Credit Agent:::: run_credit_agent : last_msg is application_status")
        return await handle_poll_approval(state, crud)
    # if last_msg == "select_vendors_for_credit":
    #     active_material_request_id = state["active_material_request_id"] if "active_material_request_id" in state else None
    #     review_order_url = apis.get_review_order_url("https://bab-ai.com/review-order", {}, {"uuid": state["active_material_request_id"]})
    #     review_order_url_response = f"Please review your order carefully"
    #     state.update(
    #             latest_respons=review_order_url_response,
    #             uoc_next_message_type="link_cta",
    #             uoc_question_type="credit_start",
    #             needs_clarification=True,
    #             uoc_next_message_extra_data= {"display_text": "Select Vendors", "url": review_order_url},
    #             agent_first_run=False  
    #         )
    #     print("Procurement Agent::::: handle_rfq:::::  --Handling rfq intent --", state)
    #     return state
    # if last_msg in _HANDLER_MAP:
    #     print("Credit Agent:::: run_credit_agent : last_msg is in _HANDLER_MAP, calling handler")
    #     if last_msg =="main_menu":
    #                 print("Procurement Agent:::: new_user_flow : last_msg is main_menu, setting up main menu")
    #                 latest_response = "Welcome back! How can I assist you today?"
    #                 uoc_next_message_extra_data =[{"id": "siteops", "title": "🏗 Manage My Site"},
    #                                         {"id": "procurement", "title": "⚡ Get Quick Quotes"},
    #                                         {"id": "credit",      "title": "💳 Get Credit Now"}] 
    #                 return await _HANDLER_MAP[last_msg](state, crud, latest_response, uoc_next_message_extra_data)
    # Fetch credit snapshot
    

    if credit_profile.get("status") == "approved":
        limit = credit_profile["limit"]
        used = credit_profile["used"]
        msg = (" *💳 Your credit summary (🟢 Active)* \n\n"
              
               f"Credit Issuer: HDFC Bank \nAvailable Amounts: ₹ {limit - used:,} \nUsed Amount ₹{used:,}\n\n\n"
               "Pick a verified local supplier, lock your quote, and Thirtee  handles payment with bank-grade security.\n"
               #"Choose vendor 🛒 → Confirm Invoice🧾→ Thirtee  pays securely💳 →  Split into EMIs 🔄"
               )
        active_material_request_id = state["active_material_request_id"] if "active_material_request_id" in state else None
        
        #review_order_url = apis.get_review_order_url("https://bab-ai.com/review-order", {}, {"uuid": state["active_material_request_id"]})
        review_order_url = apis.get_review_order_url(os.getenv("ONBOARDING_URL"))
        print("Credit Agent:::: run_credit_agent :review_order_url", review_order_url)
        state["latest_respons"] = msg
        state.update(
                uoc_next_message_type="link_cta",
                uoc_question_type="credit_start",
                needs_clarification=True,
                uoc_next_message_extra_data= {"display_text": "Select Vendors", "url": review_order_url},
                agent_first_run=False  
            )
        #state["uoc_next_message_type"]="onboarding" #Dummy Send control to random agent temporarily
        return state 
    #Add status conditions ..find out intent form message and call the right handler
    # Add reject conditions
    if credit_profile.get("status") == "pending":
        print("Credit Agent:::: handle_credit_entry : status is pending, calling handle_poll_approval")
        await handle_poll_approval(state, crud)
        state["uoc_question_type"] = "credit_start"
        _set_buttons(state, [
        {"id": "application_status", "title": "Application Status"},
        {"id": "main_menu", "title": "🏠 Main Menu"}
    ]) 
        return state
    # not approved start onboarding
    msg = ("💳 Let’s help you access Thirtee  Credit.\n"
           "India’s First Virtual Credit Card, Engineered for Builders.\n\n"
           "To get started, we’ll securely collect your Aadhaar, PAN, and GST details, along with your consent for verification.\n"
           "🔒 Your information will be encrypted end-to-end and shared only with our licensed and regulated credit partners, strictly for the purpose of assessing your eligibility.\n\n"
           "📜 This process follows RBI-compliant and international data protection standards")
    state["latest_respons"] = msg 
    state["uoc_question_type"] = "credit_start"
    state["needs_clarification"] = True   
    _set_buttons(state, [
        {"id": "credit_start", "title": "Check Eligibility"},
        {"id": "main_menu", "title": "🏠 Main Menu"}
    ])
    return state

async def handle_credit_onboard_start(state, crud):
    """
    WhatsApp Flows collection—done one-by-one to reduce friction.
    """
    print("Credit Agent:::: handle_credit_onboard_start : starting credit onboarding")
    sender_id = state.get("sender_id")
    
    state.update(
        intent="credit",
        latest_respons= "Please share your Aadhaar number.",
        uoc_question_type="credit_onboard_aadhaar",
        needs_clarification=True,
    )
    _set_buttons(state, [{"id": "kyc_cancel", "title": "Cancel"}, {"id": "main_menu", "title": "🏠 Main Menu"}])
    return state

async def handle_collect_aadhaar(state):
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

async def handle_collect_pan(state):
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

async def handle_collect_gst(state):
    gst = state.get("messages", [])[-1].get("content", "").strip().upper()
    if not re.match(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$", gst):
        state["latest_respons"] = "That GST number doesn’t look valid. Please re-enter."
        state["uoc_question_type"] = "credit_onboard_gst"
        return state

    state.setdefault("credit_profile", {})["gst"] = gst
    msg = ("Last step: please provide consent to share and verify your details with our regulated credit partner.\n\n"
           "Select 'I CONSENT' option to proceed.")
    state.update(
        latest_respons=msg,
        uoc_question_type="credit_onboard_consent",
        needs_clarification=True,
    )
    _set_buttons(state, [{"id": "consent", "title": "I Consent"}, {"id": "consent_reject", "title": "Reject"}, {"id": "main_menu", "title": "🏠 Main Menu"}])

    return state
 
async def handle_collect_consent(state):
    text = state.get("messages", [])[-1].get("content", "").strip().upper()
    if text not in ("I CONSENT", "CONSENT"):
        state["latest_respons"] = "Please reply exactly 'I CONSENT' to proceed."
        state["uoc_question_type"] = "credit_onboard_consent"
        _set_buttons(state, [{"id": "consent", "title": "I Consent"}, {"id": "consent_reject", "title": "Reject"}, {"id": "main_menu", "title": "🏠 Main Menu"}])
        return state

    # Submit to credit partner (async)
    sender_id = state.get("sender_id")
    profile = state.get("credit_profile", {})
    try:
        async with AsyncSessionLocal() as session:
            credit_mgr = CreditManager(session)
            await credit_mgr.submit_kyc(sender_id, profile)
    except Exception as e:
        print("Credit Agent:::: submit_kyc error:", e)
        state["latest_respons"] = "We couldn’t submit your application. Please try again."
        return state
    latest_response=(
        "✅ Application received!\n\n"
        "Your approval will be ready in just 2–5 minutes\n.\n"
        "While we process it, you can start getting quotations from our trusted partner vendors — "
        "the same local suppliers you already know and buy from, now available instantly through Thirtee ."
    )
    state.update(
        latest_respons=latest_response,
        uoc_next_message_extra_data=[{"id": "application_status", "title": "Application Status"},{"id": "rfq", "title": "Get Quotations"}],
        uoc_question_type="credit_start", # Send the context back to procurement agent
        needs_clarification=True,
    ) 
  
    async def _bg_poll_and_notify(sender_id: str):
        print("Credit Agent:::: background poll started for sender_id:", sender_id)
        try:
            async with AsyncSessionLocal() as session:
                mgr = CreditManager(session)
                snap = await mgr.poll_until_approved(sender_id, max_wait_seconds=300, interval_seconds=20)

                if snap.get("status") == "approved":
                    limit = float(snap.get("limit", 0.0))
                    used  = float(snap.get("used", 0.0))
                    available = max(0.0, limit - used)
                    msg = (
                        "🎉 Credit Approved!\n" 
                        f"Available: ₹{available:,.0f} (Used ₹{used:,.0f} / Limit ₹{limit:,.0f})\n"
                        f"Tap to continue with your vendors."
                    )
                    # Proactive push to the user on WhatsApp: 
                    await apis.send_message(sender_id, msg, buttons=[
                        {"id": "credit_view_portal", "title": "View Credit & Vendors"},
                        {"id": "rfq", "title": "Get Material Quotations"}
                    ])
                else:
                    # Optional: gentle nudge if still pending after timeout
                    await apis.send_message(
                        sender_id,
                        "Still reviewing your application. This can take a bit longer sometimes. I’ll notify you as soon as it’s approved."
                    )
        except Exception as e:
            print("Credit Agent:::: background poll failed:", e)

    # schedule without awaiting (don't block the user flow)
    asyncio.create_task(_bg_poll_and_notify(sender_id))
    return state
 
async def handle_poll_approval(state, crud):
    """ 
    Poll the credit partner. If approved ensure Trust Score snapshot is fresh,
    then show credit & portal CTA.
    """
    sender_id = state.get("sender_id")
    print("Credit Agent:::: handle_poll_approval : polling credit status for sender_id:", sender_id)
    try:
        async with AsyncSessionLocal() as session:
            credit_mgr = CreditManager(session)

            # 1) Ask our DB/business layer for current status snapshot
            status = await credit_mgr.check_status(sender_id)   # 
            print("Credit Agent:::: handle_poll_approval : fetched status:", status)
            # Optional: also poll the partner API, do it inside CreditManager
            # status = await credit_mgr.refresh_partner_status(sender_id)

            # 2) If partner has approved, ensure Trust Score is computed and fresh
            if status.get("status") == "approved":
                # make sure we have a recent trust score and snapshot it on CreditProfile
                #ts_snapshot = await credit_mgr.ensure_trust_score_fresh(sender_id, max_age_minutes=720)  # 12h, tune as needed
                #trust_score = ts_snapshot.get("score", status.get("trust_score", 0))

                limit = float(status.get("limit", 0.0)) 
                used  = float(status.get("used", 0.0))
                available = max(0.0, limit - used)

                state["latest_respons"] = (
                    "🎉🎉Congralutions! Your Credit Request Approved!\n\n"
                    f"Available Limit: ₹{available:,.0f} \n\n"
                    "You may now proceed to pick a verified local supplier, lock your quote, and Thirtee  handles payment with bank-grade security."
                   # f"Thirtee  Trust Score: {trust_score:.0f}"
                )
                active_material_request_id = state["active_material_request_id"] if "active_material_request_id" in state else None
                #review_order_url = apis.get_review_order_url("https://bab-ai.com/review-order", {}, {"uuid": state["active_material_request_id"]})
                review_order_url = apis.get_review_order_url(os.getenv("ONBOARDING_URL"))

                state.update(
                        uoc_next_message_type="link_cta",
                        uoc_question_type="credit_start",
                        needs_clarification=True,
                        uoc_next_message_extra_data= {"display_text": "Select Vendors", "url": review_order_url},
                        agent_first_run=False  
                    )
                return state

            # 3) Not yet approved → keep them in pending
            state["latest_respons"] = "Your application is still under review. I’ll keep you posted."
            state["uoc_question_type"] = "credit_onboard_pending"
            return state

    except Exception as e:
        print("Credit Agent:::: poll_approval error:", e)
        state["latest_respons"] = "Still checking… I’ll notify you as soon as there’s an update."
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

async def handle_kyc_cancel(state, crud):
    """
    User cancelled KYC onboarding. Reset state and return to main menu.
    """
    state.update(
        intent="main_menu",
        latest_respons="KYC onboarding cancelled. How can I assist you today?",
        uoc_question_type="main_menu",
        needs_clarification=False,
    )
    _set_buttons(state, [
        {"id": "siteops", "title": "🏗 Manage My Site"},
        {"id": "procurement", "title": "⚡ Get Quick Quotes"},
        {"id": "credit",      "title": "💳 Get Credit Now"}
    ])
    return state

_HANDLER_MAP = {
    "credit_start": handle_credit_onboard_start,
    "kyc_cancel": handle_kyc_cancel
}