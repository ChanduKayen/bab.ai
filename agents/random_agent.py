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
from agents.procurement_agent import run_procurement_agent
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
        .lstrip("json")
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

The feature should feel like a perfectly timed suggestion, as if it emerged directly from the user’s own context or curiosity. The value should be so well integrated that its importance feels self-evident — requiring no hard sell, just a soft nudge that resonates.
   ─ siteops      → cta.id "siteops",     cta.title "🏗 Manage My Site"
   ─ procurement  → cta.id "procurement", cta.title "⚡Quick Quotes"
   ─ credit       → cta.id "credit",      cta.title "💳 Pay-Later Credit"
3. “message” ≤ 120 characters.
4. Return ONLY the JSON. No markdown, no extra text.
5. Preferably respond in local lanuage. I fuser uses ENglish proceed with whaterver language user is using.

EXAMPLE
User: “Bro, what’s Bab.ai?"
→
{"internal_msg_intent":"random","message":"👋 I’m Bab.ai — track site progress, get quotes, even credit when you need.","cta":{"id":"siteops","title":"🏗 Manage My Site"}}
"""
#-------------------------------------------------------------------------------

#----------------------------User onboarding prompts-----------------------------
NEW_USER_PROMPT = """
You are Bab.ai — a world-class, emotionally intelligent assistant for construction professionals on WhatsApp.

The user has just joined (or returned). Your job is to make them feel welcomed, seen, and curious.

Write a short 2–3 line message that does the following:

1. Greet them by name using culturally appropriate honorifics:
   - Use "garu" after the name if the user’s language is Telugu
   - Use "ji" after the name if the user’s language is Hindi
   - Like wise for other languages, e.g. "sahib" in Urdu, etc.
2. Briefly introduce what Bab.ai can do, in a warm, trustworthy tone:
   - Track site progress from photos 📸
   - Get quotes for cement, steel, etc. from trusted vendors 🧱
   - Unlock pay-later material credit instantly 💳
3. End with a helpful and upbeat invitation to start — don’t sound robotic.

Tone: magical, confident, and regionally personalized.  Respond in the user’s telugu language.
Use natural phrasing in the user's language. Keep it concise (max 3 lines).  
Output ONLY the message — no buttons, no metadata.
"""

IDENTIFIED_USER_PROMPT = """  """
ENGAGED_USER_PROMPT = """  """
TRUSTED_USER_PROMPT = """  """

#----------------------------------------------------------

# ------------------------------------------------------------------
# Placeholder downstream handlers (async)
# ------------------------------------------------------------------Flatest
async def handle_siteops(state: AgentState, latest_response: str, config:dict, uoc_next_message_extra_data=None ) -> AgentState:
    
    state["messages"][-1]["content"] ="" # Setting this empty to make sure that the site opsn first run new sttae user 
    state.update(
        intent="siteops",
        latest_respons=latest_response, 
        uoc_next_message_type="button",
        uoc_question_type="onboarding",
        needs_clarification=True,  
        uoc_next_message_extra_data=[uoc_next_message_extra_data],
        agent_first_run=True
    )
    print("Random Agent::::: handle_siteops:::::  --Handling siteops intent --", state)
    from agents.siteops_agent import run_siteops_agent
    return await run_siteops_agent(state, config)



async def handle_procurement(state: AgentState, latest_response: str, config:dict, uoc_next_message_extra_data=None) -> AgentState:
    """
    Handles the procurement intent by updating the state for procurement flow.
    """
    state.update(
        intent="procurement",
        latest_respons=latest_response,
        uoc_next_message_type="button",
        uoc_question_type="procurement",
        needs_clarification=True,
        uoc_next_message_extra_data=[uoc_next_message_extra_data] if uoc_next_message_extra_data else [],
        agent_first_run=True
    )
    print("Random Agent::::: handle_procurement:::::  --Handling procurement intent --", state)
    return await run_procurement_agent(state, config)

async def handle_credit(state: AgentState, latest_response: str, config:dict, uoc_next_message_extra_data=None) -> AgentState:
    #state.update(latest_respons="Let’s see if you’re eligible for credit.")
    sender_id = state["sender_id"]
    whatsapp_output(
            sender_id,
            "బిల్డింగ్ మ్యాటీరియల్స్‌ కొనుగోలుకు కావలసిన credit support త్వరలో అందుబాటులోకి వస్తుంది. అప్పటివరకు, site manage చేయడానికి ఉన్న options‌ని ఓసారి explore చేయండి — మీ site పనులకు చక్కగా ఉపయోగపడతాయి.",
            "button",
            [{"id": "siteops",     "title": "🏗 Manage My Site"},{"id": "main_menu", "title": "⚡Main Menu"}])
    state.update(
        uoc_question_type="onboarding",
        needs_clarification=True,  
    )
    print("Random Agent::::: handle_credit:::::  --Handling credit intent --", state)
    return await state
async def handle_main_menu(state: AgentState, latest_response: str, config:dict, uoc_next_message_extra_data=None) -> AgentState:
    state.update(
        intent="random",
        latest_respons="Welcome back! How can I assist you today?",
        uoc_next_message_type="button",
        uoc_question_type="onboarding",
        needs_clarification=True,   
        uoc_next_message_extra_data=[{"id": "siteops",     "title": "🏗 Manage My Site"},{"id": "procurement", "title": "⚡ Get Quick Quotes"},{"id": "credit",      "title": "💳 Get Credit Now"}],
    )
    print("Random Agent::::: handle_main_menu:::::  --Handling main menu intent --", state)
    return state
_HANDLER_MAP = {
    "siteops": handle_siteops,
    "procurement": handle_procurement,
    "credit": handle_credit,
    "main_menu": handle_main_menu,
}

DEFAULT_CTA = {
    "siteops":     {"id": "siteops",     "title": "🏗 Manage My Site"},
    "procurement": {"id": "procurement", "title": "⚡ Get Quick Quotes"},
    "credit":      {"id": "credit",      "title": "💳 Get Credit Now"},
}



def generate_new_user_greeting(user_name: str) -> str:
    system = SystemMessage(content=NEW_USER_PROMPT)
    user = HumanMessage(content=f"The user's name is {user_name}.")
    result = llm.invoke([system, user])
    return result.content

def generate_identified_user_greeting(user_name: str) -> str:
    system = SystemMessage(content=IDENTIFIED_USER_PROMPT)
    user = HumanMessage(content=f"The user's name is {user_name}.")
    result = llm.invoke([system, user])
    return result.content
def generate_engaged_user_greeting(user_name: str) -> str:
    system = SystemMessage(content=ENGAGED_USER_PROMPT)
    user = HumanMessage(content=f"The user's name is {user_name}.")
    result = llm.invoke([system, user])
    return result.content
def generate_trusted_user_greeting(user_name: str) -> str:
    system = SystemMessage(content=TRUSTED_USER_PROMPT)
    user = HumanMessage(content=f"The user's name is {user_name}.")
    result = llm.invoke([system, user])
    return result.content


async def classify_and_respond(state: AgentState,  config: dict) -> AgentState: 
    last_msg   = (state["messages"][-1]["content"] or "").strip()
    last_lower = last_msg.lower()
    uoc_next_message_extra_data = state.get("uoc_next_message_extra_data", [])
    latest_response = state.get("latest_respons", None)
    print("Random Agent::::: classify_and_respond:::::  --Last message --", last_lower)

    # because when we call the orchstrator it correctly extracts the inteded message,
    # but since this state is passed after an image analysis, the image pathis still found - 
    # what we are actually doing when her eis we are inculding this obtained message along with the image path -
    #  that is why we are seeing the sitops intent. To overcoem this issue we are passing a new state with path set to ""
    state_for_intent_match = state.copy()
    state_for_intent_match["image_path"]="" if last_msg else state.get("image_path","")
    from orchastrator.core import infer_intent_node
    latest_msg_intent = (await infer_intent_node(state_for_intent_match)).get("intent")
    
    # ---------- 0 · Button click (id) ---------------------------
    if last_lower in _HANDLER_MAP:
        return await _HANDLER_MAP[last_lower](state,  latest_response, config, uoc_next_message_extra_data)
    
    print("Rando Agent:::: new_user_flow:::: Button is note selected")
    if latest_msg_intent == "siteops":
        latest_response = "📷 Ready to check your site? Let's continue!"
        uoc_next_message_extra_data = {"id": "siteops", "title": "📁 Continue Site Setup"}
        return await handle_siteops(state, latest_response, config, uoc_next_message_extra_data)
    elif latest_msg_intent == "procurement":
        latest_response = "🧱 Tell me what materials you're looking for, and I'll fetch quotes!"
        uoc_next_message_extra_data = {"id": "procurement", "title": "📦 Get Material"}
        return await handle_procurement(state, latest_response, config, uoc_next_message_extra_data)
    # elif latest_msg_intent == "credit":
    #     latest_response = "💳 Let's explore credit options suitable for your site."
    #     return await handle_credit(state, latest_response)

    elif latest_msg_intent == "random":
        user_stage = state.get("user_stage", "new")
        print("Random Agent::::: classify_and_respond:::::  --user Stage --",user_stage)
        # ---------- 1 · First-time greeting ------------------------
        if state.get("agent_first_run", True):
            if user_stage == "new":
                username = state.get("user_full_name", "there")
                print("Random Agent::::: classify_and_respond:::::  --First time user --", state.get("user_full_name"))
                sender_id = state["sender_id"]
                greeting_message = generate_new_user_greeting(username)
                state["latest_respons"] = greeting_message
                state["uoc_next_message_type"] = "button"
                state["uoc_question_type"] = "onboarding"
                state["needs_clarification"] = True
                state["agent_first_run"] = False
                state["user_verified"] = True
                state["uoc_next_message_extra_data"] = [
                    {"id": "siteops", "title": "📷 Share Site"},
                    {"id": "procurement", "title": "📦 Material Rates"},
                    {"id": "credit", "title": "💰 Get Credit"}
                ]

                
                return state
            elif user_stage == "curious":
                state["user_stage"] = "identified"
            elif user_stage == "identified":
                state["user_stage"] = "engaged"
            elif user_stage == "engaged":
                state["user_stage"] = "trusted"
            else:
                state["user_stage"] = "new"
            

            


        
        # ---------- 2 · Empty / emoji-only nudge -------------------
        if not re.search(r"\w", last_msg):
            state.update(
                latest_respons="🙂 Need Site updates, quotations or credit? Try Bab.ai!",
                uoc_next_message_type="button",
                uoc_next_message_extra_data=[
                    {"id": "siteops", "title": "🏗 Manage my site"},
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
        print("Router::::::- Classify_and_respond:::::  --LLM response --", data)
        internal_msg_intent   = data.get("internal_msg_intent", "random")
        message  = data.get("message") or "Got it!"
        print("Router::::::- Classify_and_respond:::::  --Intent found: --", internal_msg_intent)
        if internal_msg_intent in {"siteops", "procurement", "credit"}:
            raw_cta = data.get("cta", {})
            title = raw_cta.get("title", DEFAULT_CTA[internal_msg_intent]["title"])[:20]
            
            cta = {"id": internal_msg_intent, "title": title}
            print("Router:- Preparing for cta button-:", cta)
        else:
            if internal_msg_intent not in {"siteops", "procurement", "credit", "random"}:
                internal_msg_intent = "random"
            cta = DEFAULT_CTA.get(internal_msg_intent, DEFAULT_CTA["siteops"])
        print("Random Agnet:::: Classify_and_respond:::::  --FIna CTA --", cta)
        # ---------- 4 · Route if needed ---------------------------
        if internal_msg_intent in _HANDLER_MAP:
            state["messages"].append({"role": "assistant", "content": message})
            return await _HANDLER_MAP[internal_msg_intent](state,  message, config)
        print("Random Agnet:::: Classify_and_respond:::::  --FIna CTA  at last--", cta)
        print("Router::::::- Classify_and_respond:::::  --Intent found at last --", internal_msg_intent)
        
        
        
        state.update(
            intent=internal_msg_intent,
            latest_respons=message,
            uoc_next_message_type="button",
            uoc_question_type="onboarding",
            needs_clarification=True,  
            uoc_next_message_extra_data=[cta],
        )
        return state  
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
