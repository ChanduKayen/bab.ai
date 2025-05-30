import os
import json
import re
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from models.chatstate import AgentState

load_dotenv()

llm = ChatOpenAI(model="gpt-4o", temperature=0.3, openai_api_key=os.getenv("OPENAI_API_KEY"))

# --------------------------------------------------
# Utility to parse JSON safely
# --------------------------------------------------

def strict_json(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        fixed = re.sub(r'}\s*{', '},{', text)
        return json.loads(fixed)

# --------------------------------------------------
# Async placeholder handlers (replace later)
# --------------------------------------------------
async def handle_siteops(state: AgentState) -> AgentState:
    state["agent_first_run"] = True
    from agents.siteops_agent import run_siteops_agent
    return await run_siteops_agent(state) 

async def handle_procurement(state: AgentState) -> AgentState:
    state["latest_respons"] = "Let’s get you quotes from your vendors."
    return state

async def handle_credit(state: AgentState) -> AgentState:
    state["latest_respons"] = "Let’s check if you're eligible for credit."
    return state

# --------------------------------------------------
# Core agent
# --------------------------------------------------
async def classify_and_respond(state: AgentState) -> AgentState:
    user_verified = state.get("user_verified", False)
    last_msg = state["messages"][-1]["content"].strip()

    # Map buttons (titles ≤ 20 chars)
    button_map = {
        "siteops": handle_siteops,
        "procurement": handle_procurement,
        "credit": handle_credit,
    }
    if last_msg.lower() in button_map:
        return await button_map[last_msg.lower()](state)

    # First‑time greeting
    if not user_verified and state.get("agent_first_run", True):
        state.update(
            latest_respons=(
                "Hey 👋 Welcome to Bab.ai — your smart construction companion.\n\n"
                "We help you with:\n"
                "🏗 Track site updates\n"
                "📦 Get vendor quotes\n"
                "💳 Apply for credit\n\n"
                "What would you like to try?"
            ),
            uoc_next_message_type="button",
            uoc_question_type="onboarding",
            uoc_pending_question=True,
            uoc_next_message_extra_data=[
                {"id": "siteops", "title": "🏗 SiteOps"},
                {"id": "procurement", "title": "📦 Quotes"},
                {"id": "credit", "title": "💳 Credit"},
            ],
            agent_first_run=False,
            user_verified=True,
        )
        return state

    # Quick menu if user sends empty msg
    # if user_verified and not last_msg and len(state["messages"]) <= 2:
    #     state.update(
    #         latest_respons="Welcome back 👷‍♂️ How can I help today?",
    #         uoc_next_message_type="button",
    #         uoc_question_type="onboarding",
    #         uoc_pending_question=True,
    #         uoc_next_message_extra_data=[
    #             {"id": "siteops", "title": "🏗 SiteOps"},
    #             {"id": "procurement", "title": "📦 Quotes"},
    #             {"id": "credit", "title": "💳 Credit"},
    #         ],
    #     )
    #     return state

    # LLM free‑form classification
    prompt = (
         "You are WhatsApp assistant. Return ONLY JSON with keys intent,message,cta. "
        "intent must be siteops, procurement, credit, or random. "
        "cta.title ≤ 20 chars. Example: {\"intent\":\"credit\",\"message\":\"OK\",\"cta\":{\"id\":\"credit\",\"title\":\"Get Credit\"}}} "
        f"USER_MESSAGE: {last_msg}"
    )
    try:
        llm_resp = llm.invoke([SystemMessage(content=prompt)])
        cleaned = re.sub(r"^```json|```$", "", llm_resp.content.strip(), flags=re.MULTILINE)
        data = strict_json(cleaned)
        intent = data.get("intent", "random")

        if intent not in {"siteops", "procurement", "credit"}:
            state.update(
                latest_respons="Not sure I got that. Try SiteOps 👇",
                uoc_next_message_type="button",
                uoc_next_message_extra_data=[{"id": "siteops", "title": "🏗 SiteOps"}],
            )
            return state

        state.update(
            intent=intent,
            latest_respons=data.get("message", "Got it!"),
            uoc_question_type="onboarding",
            uoc_pending_question=True,
            uoc_next_message_type="button",
            uoc_next_message_extra_data=[data.get("cta")],
        )
        return state

    except Exception as err:
        print("Random Agent LLM classification failed:", err)
        state.update(
            latest_respons="Hmm… I didn’t get that. Try SiteOps 👇",
            uoc_next_message_type="button",
            uoc_question_type="onboarding",
            uoc_pending_question=True,
            uoc_next_message_extra_data=[{"id": "siteops", "title": "🏗 SiteOps"}],
        )
        return state
