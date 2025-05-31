# agents/random_router_agent.py
# ------------------------------------------------------------------
# Entry-point “concierge” for Bab.ai WhatsApp.  Classifies the very
# first user message (or any free-form message later) into one of our
# three main flows: SiteOps, Procurement, Credit – else Random.
#
# - Always returns a single-line JSON reply with keys
#   intent, message, cta.
# - Handles button clicks, greetings, emojis, empty messages.
# - Plays along with random chatter while advertising a core feature.
# ------------------------------------------------------------------

import os, json, re, logging, asyncio
from typing import Dict, Tuple
from dotenv import load_dotenv

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from models.chatstate import AgentState
from whatsapp.builder_out import whatsapp_output

load_dotenv()
log = logging.getLogger("bab.random_router")

llm = ChatOpenAI(
    model="gpt-4o",
    temperature=0.2,
    openai_api_key=os.getenv("OPENAI_API_KEY")
)

# ------------------------------------------------------------------
# Strict JSON helper: tries raw json, then first {...} block
# ------------------------------------------------------------------
_JSON_BLOCK = re.compile(r"\{.*\}", re.S)

def strict_json(text: str) -> Dict:
    txt = (
        text.strip()
        .lstrip("```json")
        .rstrip("```")
        .strip()
    )
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        m = _JSON_BLOCK.search(txt)
        return json.loads(m.group(0)) if m else {}

# ------------------------------------------------------------------
# LLM routing prompt (global standard)
# ------------------------------------------------------------------
ROUTER_PROMPT = """You are Bab.ai’s WhatsApp concierge.

OUTPUT
Return ONE single-line JSON object and nothing else:
{
 "intent":  "<siteops | procurement | credit | random>",
 "message": "<friendly 1-sentence reply>",
 "cta":     { "id": "<kebab-case>", "title": "<≤20 chars>" }
}

GUIDE
• siteops      – progress photos, quality updates, site queries
• procurement  – material quotes, prices, transport
• credit       – finance, payment terms, “credit days”
• random       – greetings, jokes, unrelated chatter

RULES
1. Warm, concise, professional. One emoji max.
2. If intent=random: play along, then *subtly advertise ONE Bab.ai feature*:
   ─ siteops      → cta.id "siteops",     cta.title "🏗️ Manage My Site"
   ─ procurement  → cta.id "procurement", cta.title "⚡Quick Quotes"
   ─ credit       → cta.id "credit",      cta.title "💳 Pay-Later Credit"
3. “message” ≤ 120 characters.
4. Return ONLY the JSON. No markdown, no extra text.

EXAMPLE
User: “Bro, what’s Bab.ai?"
→
{"intent":"random","message":"👋 I’m Bab.ai — track site progress, get quotes, even credit when you need.","cta":{"id":"siteops","title":"🏗️ Manage My Site"}}
"""

# ------------------------------------------------------------------
# Placeholder downstream handlers (async)
# ------------------------------------------------------------------
async def handle_siteops(state: AgentState) -> AgentState:
    state["agent_first_run"] = True
    from agents.siteops_agent import run_siteops_agent
    return await run_siteops_agent(state)

async def handle_procurement(state: AgentState) -> AgentState:
    state.update(latest_respons="Let’s get you today’s quotes from vendors.")
    return state

async def handle_credit(state: AgentState) -> AgentState:
    state.update(latest_respons="Let’s see if you’re eligible for credit.")
    return state

_HANDLER_MAP = {
    "siteops": handle_siteops,
    "procurement": handle_procurement,
    "credit": handle_credit,
}

# ------------------------------------------------------------------
# Main router
# ------------------------------------------------------------------
async def classify_and_respond(state: AgentState) -> AgentState:
    last_msg   = (state["messages"][-1]["content"] or "").strip()
    last_lower = last_msg.lower()

    # ---------- 0 · Button click (id) ---------------------------
    if last_lower in _HANDLER_MAP:
        return await _HANDLER_MAP[last_lower](state)

    # ---------- 1 · First-time greeting ------------------------
    if state.get("agent_first_run", True):
        sender_id = state["sender_id"]
        whatsapp_output(
            sender_id,
            "👋 Hi, I’m *Bab.ai* — your smart, pocket-sized assistant for building projects.\n\nI help you track site progress, get material quotes, and even buy now–pay later — all from this chat.",
            message_type="plain"
        )

        # 1️⃣ Site Management
        whatsapp_output(
            sender_id,
            "📸 Got a photo or update from your site?\nI’ll instantly tell you what’s happening, flag risks, and help you track progress like a pro.",
            message_type="button",
            extra_data=[{"id": "siteops", "title": "🏗️ Manage My Site"}]
        )

        # 2️⃣ Get Quotes from Vendors
        whatsapp_output(
            sender_id,
            "📦 Need prices for cement, steel, or any building material?\nI’ll send your requirement to verified vendors and get quotes in minutes.",
            message_type="button",
            extra_data=[{"id": "procurement", "title": "⚡ Get Quick Quotes"}]
        )

        # 3️⃣ Pay-Later Credit
        whatsapp_output(
            sender_id,
            "💳 Want to buy materials without paying upfront?\nI’ll check your eligibility and offer instant pay-later credit — like a virtual credit card for construction.",
            message_type="button",
            extra_data=[{"id": "credit", "title": "💳 Get Credit Now"}]
        )

        # Update state only after sending all messages
        state.update(
            agent_first_run=False,
            user_verified=True,
            uoc_pending_question=False
        )

    # ---------- 2 · Empty / emoji-only nudge -------------------
    if not re.search(r"\w", last_msg):
        state.update(
            latest_respons="🙂 Need an update, quote or credit? Choose below!",
            uoc_next_message_type="button",
            uoc_next_message_extra_data=[
                {"id": "siteops", "title": "🏗 SiteOps"},
            ],
        )
        return state

    # ---------- 3 · LLM classification ------------------------
    prompt  = ROUTER_PROMPT + f"\nUSER_MESSAGE: {last_msg}"
    try:
        llm_resp = llm.invoke([SystemMessage(content=prompt)])
        data     = strict_json(llm_resp.content)
    except Exception as e:
        log.error("Router LLM failure: %s", e)
        data = {}

    intent   = data.get("intent", "random")
    message  = data.get("message") or "Got it!"
    cta      = data.get("cta") or {"id": "siteops", "title": "🏗 SiteOps"}
    cta["title"] = cta["title"][:20]        # hard limit

    # ---------- 4 · Route if needed ---------------------------
    if intent in _HANDLER_MAP:
        # send the reply first, then hand off
        state["messages"].append({"role": "assistant", "content": message})
        return await _HANDLER_MAP[intent](state)

    # ---------- 5 · Random / fallback -------------------------
    state.update(
        intent="random",
        latest_respons=message,
        uoc_next_message_type="button",
        uoc_question_type="onboarding",
        uoc_pending_question=True,
        uoc_next_message_extra_data=[cta],
    )
    return state
