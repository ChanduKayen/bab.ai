# agents/random_router_agent.py
# ------------------------------------------------------------------
# WhatsApp concierge for Bab.ai.
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

load_dotenv()
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

def strict_json(text: str) -> Dict[str, Any]:
    raw = (text or "").strip()
    # strip code fences
    if raw.startswith("```"):
        raw = raw.strip("`")
        idx = raw.find("\n")
        raw = raw[idx+1:] if idx != -1 else raw
    matches = list(_JSON_ANY.finditer(raw))
    for m in reversed(matches):
        try:
            return json.loads(m.group(0))
        except Exception:
            continue
    return {}

# ------------------------------------------------------------------
# LLM routing prompt (global standard)
# ------------------------------------------------------------------
ROUTER_PROMPT = """You are Bab.ai’s WhatsApp concierge.

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
2. If internal_msg_intent = random: respond playfully or empathetically to match the user's tone — then gracefully transition into showcasing one Bab.ai feature in a way that feels natural and almost magical.
   ─ siteops      → cta.id "siteops",     cta.title "🏗 Manage My Site"
   ─ procurement  → cta.id "procurement", cta.title "⚡Quick Quotes"
   ─ credit       → cta.id "credit",      cta.title "💳 Pay-Later Credit"
3. “message” ≤ 120 characters.
4. Return ONLY the JSON. No markdown, no extra text.
5. Preferably respond in local language. If user uses English proceed with whatever language user is using.
"""

# ---------------------------- User onboarding prompts --------------------------
NEW_USER_PROMPT = """
You are Bab.ai — a world-class, emotionally intelligent assistant for construction professionals on WhatsApp.
The user has just joined (or returned). Your job is to make them feel welcomed, seen, and curious.

Write a short 2–3 line message that does the following:
1) Greet them by name using culturally appropriate tone.
2) Briefly introduce what Bab.ai can do in a warm, trustworthy way:
   • Track site progress from photos 📸
   • Get quotes for cement, steel, etc. from trusted vendors 🧱
   • Unlock pay-later material credit instantly 💳
3) End with a helpful invitation to start.

Keep it natural in the user’s language (or English if they use English). Max 3 lines. Output ONLY the message.
"""

IDENTIFIED_USER_PROMPT = "Write a warm 2-line message that welcomes back an identified user and suggests one smart next step. Output ONLY the message."
ENGAGED_USER_PROMPT   = "Write a concise 2-line nudge for an engaged user, suggesting a high-value action. Output ONLY the message."
TRUSTED_USER_PROMPT   = "Write a short 2-line message for a trusted user that offers a pro tip and a quick next step. Output ONLY the message."

# ------------------------------------------------------------------
# Default CTAs
# ------------------------------------------------------------------
DEFAULT_CTA = {
    "siteops":     {"id": "siteops",     "title": "🏗 Manage My Site"},
    "procurement": {"id": "procurement", "title": "⚡ Quick Quotes"},
    "credit":      {"id": "credit",      "title": "💳 Pay-Later Credit"},
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
        latest_respons=latest_response,
        uoc_next_message_type="button",
        uoc_question_type="procurement",
        needs_clarification=True,
        uoc_next_message_extra_data=[uoc_next_message_extra_data] if uoc_next_message_extra_data else [{"id":"procurement","title":"📦 Start Order"}],
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

def _clean_message(msg: str) -> str:
    return _cap_len(_one_emoji(msg.strip()))
 
def _last_user_text(state: AgentState) -> str: 
    if not state.get("messages"):
        return "" 
    return (state["messages"][-1].get("content") or "").strip()

# ------------------------------------------------------------------
# Main entry
# ------------------------------------------------------------------ 
async def classify_and_respond(state: AgentState, config: Optional[Dict[str, Any]] = None, **kwargs) -> AgentState:
    config = config or {}
    last_msg = _last_user_text(state)
    last_lower = last_msg.lower()
    log.debug("random_router:last_message: %s", last_lower)
    print("Random Agent::: Classify and respond ::: Called ")
    # --- 0) Button click direct routing (id equals handler key) ---
    if last_lower in _HANDLER_MAP:
        return await _HANDLER_MAP[last_lower](state, latest_response=state.get("latest_respons", ""), config=config)

    # --- 1) Image-only or empty message: nudge with single CTA ---
    image_present = bool(state.get("image_path"))
    if (not last_msg and not re.search(r"\w", last_msg or "")) and not image_present:
        state.update(
            latest_respons="🙂 Need site updates, quotations or credit? Try Bab.ai!",
            uoc_next_message_type="button",
            uoc_next_message_extra_data=[{"id": "siteops", "title": "🏗 Manage My Site"}],
        )
        return state

    # --- 2) Delegate to Convo Router FIRST (fast + deterministic) ---
    # We pass the state; router will set intent/context/missing slots/etc.
    try:
        routed_state = await route_and_respond(dict(state))  # pass a shallow copy
        # If Convo Router produced a concrete intent (not random/help), fast-route
        intent = routed_state.get("latest_msg_intent") or routed_state.get("intent")
        context = routed_state.get("intent_context")
        resp_text = routed_state.get("latest_respons")
        buttons = routed_state.get("uoc_next_message_extra_data") or []
        # Concrete intents we can immediately hand off to downstream agents:
        if intent in {"siteops", "procurement", "credit"} and resp_text:
            msg = _clean_message(resp_text)
            if intent == "siteops":
                return await handle_siteops(state, msg, config, buttons[0] if buttons else None)
            if intent == "procurement":
                return await handle_procurement(state, msg, config, buttons[0] if buttons else None)
            if intent == "credit":
                return await handle_credit(state, msg, config, buttons[0] if buttons else None)

        # If router says random/help, keep going and try the LLM concierge below.
        # But keep the router's helpful text/buttons as fallback UI if LLM fails.
        router_help_text = routed_state.get("latest_respons")
        router_help_buttons = buttons
    except Exception as e:
        log.error("random_router: Convo Router delegation failed: %s", e)
        router_help_text, router_help_buttons = None, None

    # --- 3) LLM concierge fallback classification --- 
    prompt = ROUTER_PROMPT + f"\nUSER_MESSAGE: {last_msg}"
    try:
        llm_resp = await llm.ainvoke([SystemMessage(content=prompt)])
        data = strict_json(llm_resp.content)
    except Exception as e:
        log.error("random_router: LLM routing failure: %s", e)
        data = {}

    internal_msg_intent = data.get("internal_msg_intent", "random")
    message = _clean_message(data.get("message") or "Got it!")
    raw_cta = data.get("cta") or {}
    cta_id = raw_cta.get("id") or internal_msg_intent
    cta_title = (raw_cta.get("title") or DEFAULT_CTA.get(internal_msg_intent, DEFAULT_CTA["siteops"])["title"])[:20]
    cta = {"id": cta_id, "title": cta_title}

    # --- 4) Route if we have a concrete intent ---
    if internal_msg_intent in _HANDLER_MAP:
        # Log assistant message to history (keeps convo natural)
        state.setdefault("messages", []).append({"role": "assistant", "content": message})
        if internal_msg_intent == "siteops":
            return await handle_siteops(state, message, config)
        if internal_msg_intent == "procurement":
            return await handle_procurement(state, message, config)
        if internal_msg_intent == "credit":
            return await handle_credit(state, message, config)

    # --- 5) Onboarding / random path ---
    user_stage = state.get("user_stage", "new")
    if state.get("agent_first_run", True):
        username = state.get("user_full_name", "there")
        try:
            if user_stage == "new":
                greeting_message = await generate_new_user_greeting(username)
            elif user_stage == "identified":
                greeting_message = await generate_identified_user_greeting(username)
            elif user_stage == "engaged":
                greeting_message = await generate_engaged_user_greeting(username)
            elif user_stage == "trusted":
                greeting_message = await generate_trusted_user_greeting(username)
            else:
                greeting_message = await generate_new_user_greeting(username)
            state.update(
                latest_respons=_clean_message(greeting_message),
                uoc_next_message_type="button",
                uoc_question_type="onboarding",
                needs_clarification=True,
                agent_first_run=False,
                user_verified=True,
                uoc_next_message_extra_data=[
                    {"id": "siteops", "title": "🏗 Manage My Site"},
                    {"id": "procurement", "title": "⚡ Quick Quotes"},
                    {"id": "credit", "title": "💳 Pay-Later Credit"},
                ],
            )
            return state
        except Exception as e:
            log.error("random_router: greeting generation failed: %s", e)

    # If everything else fails, use router help (if any) else LLM result
    state.update(
        intent="random",
        latest_respons=router_help_text or message,
        uoc_next_message_type="button",
        uoc_question_type="onboarding",
        needs_clarification=True,
        uoc_next_message_extra_data=(router_help_buttons or [cta]),
    )
    return state
