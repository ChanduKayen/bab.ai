# agents/procurement_agent.py

import asyncio
import base64, requests
from typing import List
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from managers.uoc_manager import UOCManager
from whatsapp.builder_out import whatsapp_output
import os
from managers.procurement_manager import ProcurementManager
from models.chatstate import AgentState
from database.procurement_crud import ProcurementCRUD
from database.uoc_crud import DatabaseCRUD
from dotenv import load_dotenv
import json  # Import the json module
import re
#from app.db import SessionLocal

from app.db import get_sessionmaker
AsyncSessionLocal = get_sessionmaker()

from whatsapp import apis
from whatsapp.builder_out import whatsapp_output
from agents.credit_agent import run_credit_agent
from whatsapp.engagement import run_with_engagement
from utils.convo_router import route_and_respond
from utils.content_card import generate_review_order_card
from pathlib import Path

# -----------------------------------------------------------------------------
# Environment & Model Setup
# -----------------------------------------------------------------------------
load_dotenv()  # lodad environment variables from .env file
#llm = ChatOpenAI(model="gpt-4", temperature=0)
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
# External (WABA) Utility
# -----------------------------------------------------------------------------
def upload_media_from_path( file_path: str, mime_type: str = "image/jpeg") -> str:
    url = f"https://graph.facebook.com/v19.0/712076848650669/media"
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

async def handle_chit_chat(state: dict, llm: ChatOpenAI | None = None) -> dict:
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
    review_order_url = apis.get_review_order_url(os.getenv("REVIEW_ORDER_URL_BASE"), {}, {"senderId" : state.get("sender_id", ""), "uuid": state["active_material_request_id"]})
    review_order_url_response = """*Choose Vendors and proceed to palce order*"""

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
    Handle the RFQ intent by updating the state and returning it.
    """
     material_request_id = state["active_material_request_id"] if "active_material_request_id" in state else None
     print("Procurement Agent::::: handle_rfq:::::  edit order active_materail_request_id : ", material_request_id)
     review_order_url = apis.get_review_order_url(os.getenv("REVIEW_ORDER_URL_BASE"), {}, {"senderId" : state.get("sender_id", ""), "uuid": state["active_material_request_id"]})
     review_order_url_response = """🔎 *Edit your Order Here*"""

     state.update(
        intent="rfq",
        latest_respons=review_order_url_response,
        uoc_next_message_type="link_cta",
        uoc_question_type="procurement_new_user_flow",
        needs_clarification=True,
        uoc_next_message_extra_data= {"display_text": "Review Order", "url": review_order_url},
        agent_first_run=False  
    )
     print("Procurement Agent::::: handle_rfq:::::  --Handling rfq intent --", state)
     return state

_HANDLER_MAP = {
    "siteops": handle_siteops,
    "procurement": handle_procurement,
    "main_menu": handle_main_menu,
    "rfq": handle_rfq,
    "credit_use": handle_credit,
    "edit_order": handle_order_edit
}

# -----------------------------------------------------------------------------
# Orchestration Flows
# -----------------------------------------------------------------------------
async def new_user_flow(state: AgentState, crud: ProcurementCRUD  ) -> AgentState:
    intent =state["intent"]
    latest_msg_intent =state.get("intent")
    last_msg = state["messages"][-1]["content"] if state.get("messages") else ""
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

        # Define notify_user_vendor_confirmed if not imported
        async def notify_user_vendor_confirmed(user_id: str, request_id: str):
            # Placeholder: send WhatsApp notification to user about vendor confirmation
            message = f"✅ Your order {request_id} has been confirmed by the vendor."
            whatsapp_output(user_id, message, message_type="plain")

        # Define notify_user_vendor_declined if not imported
        async def notify_user_vendor_declined(user_id: str, request_id: str):
            # Placeholder: send WhatsApp notification to user about vendor decline
            message = f"❌ Vendor cannot fulfill your order {request_id}. Please choose another vendor."
            whatsapp_output(user_id, message, message_type="plain")

        try:
            async with AsyncSessionLocal() as session:
                pcrud = ProcurementCRUD(session)
                if last_msg == "vendor_confirm":
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
            items = await run_with_engagement(
                sender_id=sender_id,
                work_coro=extract_materials(combined, img_b64),
                first_nudge_after=8,  # seconds
            )
         
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
            
            review_order_url_response = f"""*Your request is ready.*

Please review unclear items before continuing.

_Next, choose an action:_
            
            """
           
            path = generate_review_order_card(
                out_dir=str(UPLOAD_IMAGES_DIR),
                variant="waba_header2x",  # 1600x836 (2x 800x418)
                brand_name="bab-ai.com Procurement System",
                brand_pill_text="Procurement",
                heading="Review Order",
                site_name="AS Elite, Kakinada",
                order_id="MR-08A972B5",
                items_count_text="3 materials",
                delivery_text="Fri, 22 Aug",
                quotes_text="3 in (best ₹—)",
                payment_text="Credit available",
                items=items,
                total_value="₹ 3,45,600",
                total_subnote="incl. GST • freight extra",
                quotes_ready_count=3,
            )

            media_id = upload_media_from_path( path, "image/jpeg")

            state.update({  
                "latest_respons": review_order_url_response,
                "uoc_next_message_type": "button",
                "uoc_question_type": "procurement_new_user_flow",
                #"uoc_next_message_extra_data": {"display_text": "Review Order", "url": review_order_url},
                "uoc_next_message_extra_data": {"buttons":  [
                     {"id": "edit_order", "title": "Edit Order"},
                    {"id": "rfq", "title": "Confirm & Get Quotes"},
                    {"id": "credit_use", "title": "Buy with Credit"},
                ],
                "media_id": media_id,
                "media_type": "image",
                },
                "needs_clarification": True,
                "active_material_request_id": state["active_material_request_id"],
                "agent_first_run": False,
            })
        except Exception as e:
            print("Procurement Agent:::: new_user_flow : Error in fetching review order:", e)
        
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
        latest_msg_context = state["intent_context"]

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

      
    intent_context = state.get("intent_context","")
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