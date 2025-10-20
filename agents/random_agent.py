# agents/random_router_agent.py
# ------------------------------------------------------------------
# WhatsApp concierge for Thirtee .
# Now delegates FIRST to Convo Router (fast deterministic),
# and only falls back to LLM when needed.
#
# Key upgrades:
# - Async LLM calls (ainvoke) + strict JSON extraction
# - Convo Router integration (route_and_respond) before LLM
# - Handles image-only / empty messages
# - Enforces single emoji & ≤120 chars message rule
# - Clean button routing and safe state updates
# - Local language preference respected
# - Privacy-safe (no PII asks here)
# ------------------------------------------------------------------

import os, json, re, logging, asyncio
from typing import Dict, Tuple, Any, Optional
from dotenv import load_dotenv 
#from database._init_ import AsyncSessionLocal
from app.db import get_sessionmaker
AsyncSessionLocal = get_sessionmaker()

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from models.chatstate import AgentState
from database.credit_crud import CreditCRUD
from agents.procurement_agent import run_procurement_agent
from agents.siteops_agent import run_siteops_agent
from agents.credit_agent import run_credit_agent
# >>> NEW: use your Convo Router
from utils.convo_router import route_and_respond 
from users import user_onboarding_manager
import fastapi
import requests

load_dotenv()

# WhatsApp / Facebook access token used for media uploads; set via environment variable
ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN") or os.getenv("ACCESS_TOKEN") or ""

log = logging.getLogger("bab.random_router")

llm = ChatOpenAI(
    model="gpt-4o",
    temperature=0.2,
    openai_api_key=os.getenv("OPENAI_API_KEY")
)

# ------------------------------------------------------------------
# Strict JSON helper (balanced braces, code-fence tolerant)
# ------------------------------------------------------------------
_JSON_ANY = re.compile(r"\{.*?\}", re.S)
def upload_media_from_path( file_path: str, mime_type: str = "image/jpeg") -> str:
    url = f"https://graph.facebook.com/v19.0/712076848650669/media"
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
    files = {"file": (os.path.basename(file_path), open(file_path, "rb"), mime_type)}
    data = {"messaging_product": "whatsapp"}
    r = requests.post(url, headers=headers, files=files, data=data)
    r.raise_for_status()
    print("rocurement Agent::: upo;ad media from path :::Status",r)
    return r.json()["id"]

def strict_json(text: str) -> Dict[str, Any]:
    raw = (text or "").strip()
    # Strip code fences like ```json ... ```
    if raw.startswith("```"):
        raw = raw.strip("`")
        nl = raw.find("\n")
        raw = raw[nl + 1:] if nl != -1 else raw

    # Find first balanced {...}
    start = raw.find("{")
    if start == -1:
        return {}

    depth = 0
    in_str = False
    escape = False
    end = -1

    for i in range(start, len(raw)):
        ch = raw[i]

        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue

        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                candidate = raw[start:end]
                try:
                    return json.loads(candidate)
                except Exception:
                    # If this slice isn't valid JSON, keep scanning in case there's another object later
                    # Reset to search after this '{'
                    next_start = raw.find("{", start + 1)
                    if next_start == -1:
                        break
                    i = next_start - 1
                    start = next_start
                    depth = 0
                    in_str = False
                    escape = False

    # Last-chance: try the whole string
    try:
        return json.loads(raw)
    except Exception:
        return {}


# ------------------------------------------------------------------
# LLM routing prompt (global standard)
# ------------------------------------------------------------------
ROUTER_PROMPT = """You are Thirtee ’s WhatsApp concierge.

OUTPUT
Return ONE single-line JSON object and nothing else:
{
 "internal_msg_intent":  "<siteops | procurement | credit | random>",
 "message": "<friendly 1-sentence reply, with more human touch>",
 "cta":     { "id": "<kebab-case>", "title": "<≤20 chars>" }
}

GUIDE
• siteops      – progress photos, quality updates, site queries
• procurement  – material quotes (message or photos), list of material (message or photos), prices, transport
• credit       – finance, payment terms, “credit days”
• random       – greetings, jokes, unrelated chatter

RULES
1. Warm, concise, professional. One emoji max.
2. If internal_msg_intent = random: respond playfully or empathetically to match the user's tone — then gracefully transition into showcasing one Thirtee  feature in a way that feels natural and almost magical.
   ─ siteops      → cta.id "siteops",     cta.title "🏗 Manage My Site"
   ─ procurement  → cta.id "procurement", cta.title "⚡Quick Quotes"
   ─ credit       → cta.id "credit",      cta.title "💳 Pay-Later Credit"
3. “message” ≤ 120 characters.
4. Return ONLY the JSON. No markdown, no extra text.
5. Preferably respond in local language. If user uses English proceed with whatever language user is using.
"""

# ---------------------------- User onboarding prompts --------------------------
NEW_USER_PROMPT = """
You are Thirtee  — a world-class, emotionally intelligent assistant for construction professionals on WhatsApp.
The user has just joined (or returned). Your job is to make them feel welcomed, seen, and curious.

Write a short 2–3 line message that does the following:
1) Greet them by name using culturally appropriate tone.
2) Briefly introduce what Thirtee  can do in a warm, trustworthy way:
   • Track site progress from photos 📸
   • Get quotes for cement, steel, etc. from trusted vendors 🧱
   • Unlock pay-later material credit instantly 💳
3) End with a helpful invitation to start.

Keep it natural in the user’s language (or English if they use English). Max 3 lines. Output ONLY the message.
"""

IDENTIFIED_USER_PROMPT = "Write a warm 2-line message that welcomes back an identified user and suggests one smart next step. Output ONLY the message."
ENGAGED_USER_PROMPT   = "Write a concise 2-line nudge for an engaged user, suggesting a high-value action. Output ONLY the message."
TRUSTED_USER_PROMPT   = "Write a short 2-line message for a trusted user that offers a pro tip and a quick next step. Output ONLY the message."

# ---------------------------- Conversational prompts --------------------------
CONVERSATION_SYSTEM_PROMPT = (
    "You are Thirtee  — a smart, friendly WhatsApp assistant built for builders and construction professionals. "
    "Read the conversation trail carefully and reply in the same language and tone as the user. "
    "Be natural, concise (1–2 short sentences, ≤120 characters, max one emoji), and sound like a trusted teammate on site. "
    "Your primary role is to help builders share their material requirements — by explaining them what you can do and what they can do"
    "and then collect the best quotations from trusted OEMs, distributors, and manufacturers. "
    "Whenever relevant, smoothly guide the conversation toward useful actions like sharing a requirement, "
    "checking prices, or exploring pay-later credit for materials. " 
    "Explain Thirtee ’s abilities in a helpful, human tone — never like a sales pitch. "
    "Keep every response warm, context-aware, and conversational. "
    "If the topic is off-track, gently bring the user back by reminding how Thirtee  can assist with procurement or credit. "
    "Never ask for sensitive personal data unless the user is clearly in a verified credit/KYC flow."
)


CONVERSATION_JSON_PROMPT = (
    "Return ONLY a JSON object with this schema and nothing else:\n"
    "{\n"
    "  \"message\": \"<concise reply per constraints>\",\n"
    "  \"cta\": { \n"
    "    \"id\": \"<siteops|procurement|credit>\",\n"
    "    \"title\": \"<≤20 chars, can include emoji>\"\n"
    "  }\n"
    "}\n"
    "Rules: 1 emoji max; ≤120 chars; pick the most relevant CTA from context; use user's language."
)

# ------------------------------------------------------------------
# Default CTAs
# ------------------------------------------------------------------
DEFAULT_CTA = {
    #"siteops":     {"id": "siteops",     "title": "🏗 Manage My Site"},
    "procurement": {"id": "procurement", "title": "📷 Share Requirement"},
    #"credit":      {"id": "credit",      "title": "💳 Pay-Later Credit"},
}

# ------------------------------------------------------------------
# Helpers: greeting generators (async for consistency)
# ------------------------------------------------------------------
async def _ainvoke(llm, messages):
    return await llm.ainvoke(messages)

async def generate_new_user_greeting(user_name: str) -> str:
    res = await _ainvoke(llm, [SystemMessage(content=NEW_USER_PROMPT),
                               HumanMessage(content=f"The user's name is {user_name}.")])
    return res.content.strip()

async def generate_identified_user_greeting(user_name: str) -> str:
    res = await _ainvoke(llm, [SystemMessage(content=IDENTIFIED_USER_PROMPT),
                               HumanMessage(content=f"The user's name is {user_name}.")])
    return res.content.strip()

async def generate_engaged_user_greeting(user_name: str) -> str:
    res = await _ainvoke(llm, [SystemMessage(content=ENGAGED_USER_PROMPT),
                               HumanMessage(content=f"The user's name is {user_name}.")])
    return res.content.strip()

async def generate_trusted_user_greeting(user_name: str) -> str:
    res = await _ainvoke(llm, [SystemMessage(content=TRUSTED_USER_PROMPT),
                               HumanMessage(content=f"The user's name is {user_name}.")])
    return res.content.strip()

# ------------------------------------------------------------------
# Button handlers (downstream)
# ------------------------------------------------------------------
async def handle_siteops(state: AgentState, latest_response: str, config: dict,
                         uoc_next_message_extra_data: Optional[Dict[str, str]]=None) -> AgentState:
    # Clear user text so SiteOps agent treats next turn as fresh
    if state.get("messages"):
        state["messages"][-1]["content"] = ""
    state.update(
        intent="siteops",
        latest_respons=latest_response,
        uoc_next_message_type="button",
        uoc_question_type="onboarding",
        needs_clarification=True,
        uoc_next_message_extra_data=[uoc_next_message_extra_data] if uoc_next_message_extra_data else [{"id":"siteops","title":"📁 Continue Site Setup"}],
        agent_first_run=True
    )
    return await run_siteops_agent(state, config)

async def handle_procurement(state: AgentState, latest_response: str, config: dict,
                             uoc_next_message_extra_data: Optional[Dict[str, str]]=None) -> AgentState:
    if state.get("messages"):
        state["messages"][-1]["content"] = ""
    state.update(
        intent="procurement",
        latest_response=latest_response,
        uoc_next_message_type="button",
        uoc_question_type="procurement",
        needs_clarification=True,
        uoc_next_message_extra_data=[uoc_next_message_extra_data] if uoc_next_message_extra_data else [{"id":"procurement","title":"📷 Share Requirement"}],
        agent_first_run=True
    )
    return await run_procurement_agent(state, config)
async def handle_rfq(state: AgentState, latest_response: str, config: dict,
                             uoc_next_message_extra_data: Optional[Dict[str, str]]=None) -> AgentState:
    if state.get("messages"):
        state["messages"][-1]["content"] = "guided_photo_upload"
    state.update(
        intent="procurement",
        latest_response=latest_response,
        uoc_next_message_type="button",
        uoc_question_type="procurement",
        needs_clarification=True,
        uoc_next_message_extra_data=[uoc_next_message_extra_data] if uoc_next_message_extra_data else [{"id":"procurement","title":"📷 Share Requirement"}],
        agent_first_run=True
    )
    return await run_procurement_agent(state, config)

async def handle_credit(state: AgentState, latest_response: str, config: dict,
                        uoc_next_message_extra_data: Optional[Dict[str, str]]=None) -> AgentState:
    if state.get("messages"):
        state["messages"][-1]["content"] = "routed_from_random_agent"
    state.update(
        intent="credit",
        latest_respons=latest_response,
        uoc_next_message_type="plain",
        uoc_question_type="credit",
        needs_clarification=True,
        uoc_next_message_extra_data=[uoc_next_message_extra_data] if uoc_next_message_extra_data else [{"id":"credit","title":"⚡ Check Eligibility"}],
        agent_first_run=True
    )
    try:
        async with AsyncSessionLocal() as session:
            crud = CreditCRUD(session)
            return await run_credit_agent(state, config={"configurable": {"crud": crud}})
    except Exception as e:
        log.error("random_router: error in run_credit_agent: %s", e)
        return state

async def handle_main_menu(state: AgentState, latest_response: str, config: dict,
                           uoc_next_message_extra_data: Optional[Dict[str, str]]=None) -> AgentState:
    state.update(
        intent="random",
        latest_respons=latest_response or "Welcome back! How can I assist you today?",
        uoc_next_message_type="button",
        uoc_question_type="onboarding",
        needs_clarification=True,
        uoc_next_message_extra_data=[
            {"id": "siteops", "title": "🏗 Manage My Site"},
            {"id": "procurement", "title": "⚡ Quick Quotes"},
            {"id": "credit", "title": "💳 Pay-Later Credit"},
        ],
    )
    return state

_HANDLER_MAP = {
    "siteops": handle_siteops,
    "procurement": handle_procurement,
    "credit": handle_credit,
    "main_menu": handle_main_menu,
    "rfq": handle_rfq,
}
 
# ------------------------------------------------------------------
# Utilities
# ------------------------------------------------------------------
def _one_emoji(msg: str) -> str:
    """Ensure at most one emoji in the message."""
    # very light-touch: if multiple emoji-like chars, keep first; strip others
    # (You can make this stricter with emoji lib if needed.)
    seen = 0
    out = []
    for ch in msg:
        if ord(ch) > 0x1F000:  # rough emoji-ish cutoff
            seen += 1
            if seen > 1:
                continue
        out.append(ch)
    return "".join(out)

def _cap_len(msg: str, limit: int = 120) -> str:
    return msg if len(msg) <= limit else msg[:limit-1] + "…"

 
def _last_user_text(state: AgentState) -> str: 
    if not state.get("messages"):
        return "" 
    return (state["messages"][-1].get("content") or "").strip()

async def _ainvoke_json(llm, messages):
    """Prefer JSON-structured responses; fallback to plain if unsupported."""
    try:
        bound = llm.bind(response_format={"type": "json_object"})
        return await bound.ainvoke(messages)
    except Exception:
        return await llm.ainvoke(messages)

def _history_snippet(state: AgentState, limit: int = 4) -> str:
    msgs = state.get("messages") or []
    if not msgs:
        return ""
    hist = msgs[:-1]
    lines = []
    for m in hist[-limit:]:
        text = (m.get("content") or "").strip()
        if not text:
            continue
        lines.append(f"- {_cap_len(text, 160)}")
    return "\n".join(lines)

async def generate_conversational_reply_with_cta(state: AgentState) -> Dict[str, Any]:
    last = _last_user_text(state)
    history = _history_snippet(state)
    print("Random agent::: Generate_conversational_reply_with_cta history snippet:::: ", history)
    prompt = (
        f"Recent conversation (most recent last):\n{history}\n\n"
        f"User's latest message:\n\"\"\"{last}\"\"\"\n\n"
        "Follow the schema strictly."
    )
    res = await llm.ainvoke([
        SystemMessage(content=CONVERSATION_SYSTEM_PROMPT + "\n\n" + CONVERSATION_JSON_PROMPT),
        HumanMessage(content=prompt)

    ])
    print("LLM Response :", res)
    data = strict_json(res.content) or {}
    message = (data.get("message") or "").strip()
    print("Random agent::: Generate_conversational_reply_with_cta LLM  message:::: ", repr(message))
    cta = data.get("cta") or {}
    cta_id = str(cta.get("id") or "").strip().lower()
    if cta_id not in {"siteops","procurement","credit"}: 
        low = (last or "").lower()
        if any(k in low for k in ["photo","progress","site","work","crew","stock","update"]):
            cta_id = "siteops"
        elif any(k in low for k in ["price","quote","cement","steel","sand","order","boq","invoice"]):
            cta_id = "procurement"
        elif any(k in low for k in ["credit","limit","pay","kyc","loan"]):
            cta_id = "credit"
        else:
            cta_id = "procurement"
    default_title = DEFAULT_CTA[cta_id]["title"]
    title = cta.get("title") or default_title
    title = _cap_len(title, 20)
    return {"message": message, "cta": {"id": cta_id, "title": title}}

def _first_name(full: str) -> str:
    s = (full or "there").strip()
    return s.split()[0] if s else "there"

def _quick_cta_from_text(last: str, state: AgentState) -> Dict[str, str]:
    lk = (state.get("last_known_intent") or "").lower()
    if lk in DEFAULT_CTA:
        return DEFAULT_CTA[lk]
    low = (last or "").lower()
    if any(k in low for k in ["photo","progress","site","work","crew","stock","update"]):
        return DEFAULT_CTA["siteops"]
    if any(k in low for k in ["price","quote","cement","steel","sand","order","boq","invoice"]):
        return DEFAULT_CTA["procurement"]
    if any(k in low for k in ["credit","limit","pay","kyc","loan"]):
        return DEFAULT_CTA["credit"]
    return DEFAULT_CTA["procurement"]


# ------------------------------------------------------------------
# Main entry
# ------------------------------------------------------------------ 
async def classify_and_respond(state: AgentState, config: Optional[Dict[str, Any]] = None, **kwargs) -> AgentState:
    config = config or {}
    last_msg = _last_user_text(state)
    last_lower = last_msg.lower()
    log.debug("random_router:last_message: %s", last_lower)
    intent = state.get("intent") 
    if not intent:
        intent = "random"
    print("Random Agent::: Classify and respond ::: Called --------- ", intent)
    if last_lower in _HANDLER_MAP:
        return await _HANDLER_MAP[last_lower](state, latest_response=state.get("latest_respons", ""), config=config)
    elif last_msg.lower() == "builder_user" or last_msg.lower() == "vendor_user":
        state["user_category"] = "builder" if last_msg.lower() == "builder_user" else "vendor"
        print(f"Random Agent:::: run_radom_agent : User category set to {state['user_category']}")
        try:
            async with AsyncSessionLocal() as session:
                    #crud = ProcurementCRUD(session)
                    await user_onboarding_manager.set_user_role(session, sender_id= state.get("sender_id", ""), role= state["user_category"])

        except Exception as e:
            print("Random Agent::: Classify and respond  : failed to update user category in DB:", e)
            state["latest_respons"] = "Sorry, there was a system error. Please try again later."
        
        state["uoc_next_message_type"] = "button"
        state["uoc_question_type"] = "onboarding"
        if state["user_category"] == "builder":
            image_path = "C:/Users/koppi/OneDrive/Desktop/Thirtee/Marketing/builder_welcome.png"
            media_id = upload_media_from_path( image_path, "image/jpeg")
            state["latest_respons"] = """👋 *Welcome to Thirtee, Builder!*  
Here I help builders like you connect with manufacturers effortlessly, instantly, and right at your fingertips.

You’re now set up as a *Builder*. Let’s get your first requirement rolling.
            """
            state["uoc_next_message_extra_data"] = {"buttons":  [
                     {"id": "rfq", "title": "📷 Share Requirement"}
    
                ],
                "media_id": media_id,   
                "media_type": "image",
                }

            
            #"media_id": media_id,   --Temporarily disabling content media
                #"media_type": "image",
            
            media_id = upload_media_from_path( image_path, "image/jpeg")
            
            state["needs_clarification"] = True
            
        else:
            state["latest_respons"] = """👋 *Welcome to Thirtee, Supplier!* — where vendors connect directly with builders"""
            state["uoc_question_type"] = "vendor_new_user_flow"
            state["uoc_next_message_extra_data"] = [
                {"id": "vendor_onboarding", "title": "🏭 Vendor Onboarding"}
            ]
        state["needs_clarification"] = True
        return state
    

     #########Identifiying user category###################
    if state.get("user_category", "") == None or state.get("user_category", "") == "USER":
        print("Random Agent::: Classify and respond  : User category not set")
        message = """👋 *Hola!* I am Thirtee, your smart assistant for construction procurement and credit.

Before we proceed would you let me know if you are a *Builder* looking for materials or a *Supplier* supplying them?

_This information helps me personalise your experience_"""

        state["latest_respons"] = message
        state["uoc_next_message_type"] = "button"
        state["uoc_question_type"] = "onboarding"
        state["needs_clarification"] = True
        state["uoc_next_message_extra_data"] = [
            {"id": "builder_user", "title": "👷‍♂️ I'm a Builder"},
            {"id": "vendor_user", "title": "🏭 I'm a Supplier"},
        ]
        # whatsapp_output(state.get("sender_id", ""), message, message_type="button",extra_data= [
        #     {"id": "builder_user", "title": "👷‍♂️ Builder"},
        #     {"id": "vendor_user",  "title": "🏭 Supplier"},
        # ])
        return state

    image_present = bool(state.get("image_path"))
    if (not last_msg and not re.search(r"\w", last_msg or "")) and not image_present:
        state.update(
            latest_respons="🙂 Need material quotes or site help? Just share a photo — Thirtee  will collect quotations directly from manufacturers.",
            uoc_next_message_type="button",
            uoc_next_message_extra_data=[{"id": "siteops", "title": "🏗 Manage My Site"}],
        )
        return state
    if intent == "random":
        if state.get("agent_first_run")== True:
            print("Random Agent::: Classify and respond ::: First Run ", intent)
            username = state.get("user_full_name", "there")
            greeting_message = f"Hello {username}! 👋 Just share a photo of what you need — Thirtee  will get quotations directly from manufacturers for you." # --- NO need of LLM Call here
            state.update(
                latest_respons= greeting_message,
                uoc_next_message_type="button",
                uoc_question_type="onboarding",
                needs_clarification=True,
                agent_first_run=False,
                user_verified=True,
                uoc_next_message_extra_data=[
                    #{"id": "siteops", "title": "🏗 Manage My Site"},
                    {"id": "procurement", "title": "⚡ Quick Quotes"},
                   # {"id": "credit", "title": "💳 Pay-Later Credit"},
                ],
            )
            return state
        else:
        # Agent second run and beyond — build a contextual reply from trail + latest
            print("Random Agent::: Classify and respond ::: Second Run ")

            try:
                convo = await generate_conversational_reply_with_cta(state) or {}
                msg = convo.get("message", "").strip()
                cta = convo.get("cta") or {}
                cta_id = (cta.get("id") or "").strip().lower()
                cta_title = (cta.get("title") or "").strip()

                # Fallbacks if LLM didn't return a valid CTA
                if cta_id not in {"siteops", "procurement", "credit"}:
                    cta_choice = _quick_cta_from_text(_last_user_text(state), state)
                    cta_id = cta_choice["id"]
                    cta_title = cta_title or cta_choice["title"]

                # Safety caps: one emoji + ≤120 chars, title ≤20 chars
                #msg = _clean_message(msg) or "Got it. What would you like to do next?"
                cta_title = _cap_len(cta_title or DEFAULT_CTA[cta_id]["title"], 20)

                # Update state for WhatsApp UI (button with one clear action)
                state.update(
                    latest_respons=msg,
                    uoc_next_message_type="button",
                    uoc_question_type="onboarding",              
                    needs_clarification=True,
                    uoc_next_message_extra_data=[{"id": cta_id, "title": cta_title}],
                )
                return state

            except Exception as e:
                log.error("random_router: second-run convo build failed: %s", e)
                # Heuristic-only fallback (no LLM)
                last = _last_user_text(state)
                cta_choice = _quick_cta_from_text(last, state)
                state.update(
                    latest_respons="Noted. Try this next?",
                    uoc_next_message_type="button",
                    uoc_question_type=cta_choice["id"],
                    needs_clarification=True,
                    uoc_next_message_extra_data=[cta_choice],
                )
                return state

    try:
        state = await route_and_respond(state)
    except Exception as e:
        log.error("random_router: route_and_respond failed: %s", e)
    return state
