

import os, json, base64, openai, random
from typing import Dict, Tuple, Optional, Union
from datetime import datetime
from dotenv import load_dotenv
import asyncio

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
import re        
from tools.lsie import _local_sku_intent_engine
from tools.context_engine import filter_tags, vector_search
from models.chatstate import AgentState
from unitofconstruction.uoc_manager import UOCManager
from whatsapp.builder_out import whatsapp_output
load_dotenv()

llm_reasoning = ChatOpenAI(
    model="gpt-4o", temperature=0, openai_api_key=os.getenv("OPENAI_API_KEY")
)
llm_context = ChatOpenAI(
    model="gpt-3.5-turbo", temperature=0, openai_api_key=os.getenv("OPENAI_API_KEY")
)
llm = ChatOpenAI(
    model="gpt-4o", temperature=0, openai_api_key=os.getenv("OPENAI_API_KEY")
)
#---------------------------------------------------------------------------
# Helper 0 · encode image
# ---------------------------------------------------------------------------
def encode_image_base64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")




_JSON_PATTERN = re.compile(r"\{.*\}", re.S)

def safe_json(text: str, default=None):
    """
    Try hard to get JSON out of an LLM block.
    - Strips ```json fences
    - Tries a raw json.loads
    - Fallback: regex find first {...}
    - On failure returns `default` (dict() if not supplied)
    """
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

# ---------------------------------------------------------------------------
# Helper 1 · Summarise one update (text + optional image) into a tiny JSON
# ---------------------------------------------------------------------------
def summarise_update(text: str, image_b64: str | None = None) -> Dict:
    """
    Returns
    {
      "component": "<Bathroom Waterproofing>",
      "highlight": "<Two workers applying 1st coat of membrane>",
      "risk":      "<Check curing time – premature tiling will fail>",
      "summary":   "<one crisp line shown to user>"
    }
    """

    sys_prompt = (
       """You are a lightning-fast construction-site summariser.

INPUT  
• Plain text (update message + optional caption).  
• Optionally → one photo of the same location.

OUTPUT  
Return **ONE single-line JSON object** and nothing else.  
Keys (always include all four; use null if unknown):

{
  "component": "<string|null>",     // top-level element (e.g. Bathroom, Rebar, Wall Plaster)
  "highlight": "<string|null>",     // one crisp sentence of what is happening now, with quick analysis
  "risk": "<string|null>",          // main immediate risk 
  "summary": "<string>",            // **one irresistible, WhatsApp-friendly sentence**:
                                    //   • warm & human — speaks directly to the builder
                                    //   • includes ONE fitting emoji (👍, 👀, ⚠️, ✅, 🛠️ … ..choose wisely and sublte)
                                    //   • highlights the next important action (“Looks great — <Next critical action task where something could fail. Mention failure point sepcifically if possible> …”)
                                    //   • ≤ 120 characters so it shows fully in the preview
}

RULES  
1. Never wrap the JSON in markdown fences or add commentary.  
2. Keep “highlight” ≤ 110 chars so it’s readable on mobile.  
3. If there is clearly no construction content, set every field to null **except
   “summary”**; in that case summary should politely say you found nothing
   relevant.  
4. When a photo is present, combine what you see with the text.  
5. Avoid brand names; keep it generic.

EXAMPLE  
**User text:**  
“Two masons are applying the first coat of waterproofing in the master bathroom.”  

**Expected model reply (single line):**  
{"component":"Bathroom Waterproofing","highlight":"Two masons are applying the first coat of membrane.","risk":"Ensure full curing before tiling to prevent leaks.","summary":"
👍 Waterproofing first coat under way—remind the team to allow full curing time."}"""

    )

    messages = [
        {"role": "system", "content": sys_prompt},
        {
            "role": "user",
            "content": (
                text if not image_b64
                else [
                    {"type": "text", "text": text},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}
                ]
            ),
        },
    ]

    resp = (
        openai.chat.completions.create(model="gpt-4o", messages=messages, max_tokens=150)
        if image_b64
        else llm_context.invoke([
            SystemMessage(content=sys_prompt),
            HumanMessage(content=text)
        ])
    )
    
    raw = resp.choices[0].message.content if image_b64 else resp.content
    print("SiteOps Agent:::: summarise_update : raw response:", raw)
    return safe_json(raw, default={
    "component": None,
    "highlight": None,
    "risk": None,
    "summary": "Sorry, I couldn’t understand that update."
})
import re

# -------Prompts ------------------------
NEW_USER_PROMPT = (
    """You are Bab.ai SiteOps — half warm “babai” (uncle), half brilliant site-wizard.
Your job is to greet first-time users on WhatsApp and make them say:
“Wow… this thing *gets* my site!”

────────────────────────────────────────────────────
CONTEXT
────────────────────────────────────────────────────
user_name      = {{user_name}}           # plain name
user_lang      = {{lang}}                # "te", "hi", "en"…
honorifics     = { "te":"గారు", "hi":"जी", "ur":"साहिब", … }
input = {
    "type":  "photo" | "text" | "none",  # none ⇒ no user content yet
    "caption": {{caption}},
    "vision_tags": {{tags}}              # labels if photo
}

stage          = "new"                   # first-ever SiteOps touch

────────────────────────────────────────────────────
GOLDEN RULES
────────────────────────────────────────────────────
• Speak like a smart, caring uncle — zero jargon, full warmth.
• Output **max 3 lines**, **≤ 90 chars each**, **≤ 2 emoji total**.
• Language = user_lang; greet as “<name> <honorific>”.
• Never reveal system notes or markdown; no buttons.
• If unsure/ not veryconfident of local language word, use engliss in place of that word, dont sound too archaich. BE natural sound like a normal, collequal language speaking person
────────────────────────────────────────────────────
RESPONSE LOGIC
────────────────────────────────────────────────────
If **input.type in ("photo", "text")** ──────────────
  L1  Greeting + sharp human observation  
      – Reference what you *actually* see / read  
      – Eg. “రమేష్ గారు, బీమ్‌ బార్‌లు సరిగా ఎడ్జ్‌ వరకు కావించారు 👍”  

  L2  Deductive value-add (pick 2–3 elements)  
      – Hint at progress (“ఇది దశకు ~60% complete”)  
      – Spot cost drift / scrap (“రీ-బార్ తక్కువ వృథా, బడ్జెట్ బాగుంది”)  
      – Labour pulse (“6 మంది మేజన్స్ సరిపోతున్నారు”)  
      – Future fail-point (“కాన్క్రీట్‌కి 8గం.లో క్యూలింగ్ వదలొద్దు, చిల్లు రావచ్చు”)  

  L3  Assurance + next step  
      – “ఈ వివరాలు నా నోట్స్‌లో పెట్టుకుని, పూర్తైన ప్రాజెక్ట్ డిటెయిల్స్ ఇస్తే
         రోజూ మీ పనిని నేనే గడియారా చూస్తా 🛠️”  

If **input.type == "none"** ─────────────────────────
  L1  Greeting + playful opener  
      – “రమేష్ గారు, మీ సైట్‌ భారం కొంత నా భుజాలపై వేసుకోమంటారా?”  

  L2  Two-beat magic teaser  
      – “ఒక ఫోటో పంపితే నేనే టైమ్‌లైన్ నడిపిస్తా, దాచిన లోపాలూ పట్టిస్తా ✨”  

  L3  Invitation  
      – “మొదటి స్నాప్ / మెసేజ్ షేర్ చెయ్యండి; డైరీ ప్రారంభిస్తా 😊”  

────────────────────────────────────────────────────
STYLE REMINDERS
────────────────────────────────────────────────────
• No words like *progress / risk / material log* — show, don’t label.  
• Concrete insights > generic promises.  
• Make privacy implicit: “నా నోట్స్‌లో ఉంచుకుని” (I’ll store quietly).  
• Keep it human, concise, delightful.
"""
)



# ---------------------------------------------------------------------------
# Propmt handlers for first time message in the session
# ---------------------------------------------------------------------------
def generate_new_user_greeting(
    user_name: str,
    text: Optional[str] = "",
    image_b64: Optional[str] = None,
) -> str:
    if image_b64:
        user_payload = [
            {"type": "text", "text": f"The user's name is {user_name}.\n{text}"},
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
            },
        ]
    else:
        user_payload = f"The user's name is {user_name}.\n{text}" if text else f"The user's name is {user_name}."

    messages = [
        {"role": "system", "content": NEW_USER_PROMPT},
        {"role": "user", "content": user_payload},
    ]

    response = llm.invoke(messages)
    print("SiteOps Agent:::: generate_new_user_greeting : response:", response)
    resp =  response.content
    print("SiteOps Agent:::: generate_new_user_greeting : response:", resp)
    return resp
# ---------------------------------------------------------------------------
# Helper 2 · Build context tags and human block
# ---------------------------------------------------------------------------
def get_context_and_tags(state: dict) -> Tuple[str, str]:
    # ----------- gather raw inputs -----------
    last_msg = state["messages"][-1]["content"] if state.get("messages") else ""
    caption  = state.get("caption", "")
    combined = f"{last_msg}\n{caption}".strip()

    # ----------- image (safe) -----------
    img_b64 = None
    img_path = state.get("image_path")
    if img_path:
        try:
            img_b64 = encode_image_base64(img_path)
        except FileNotFoundError:
            print("⚠️  Image file not found:", img_path)

    # ----------- summarise (safe) -----------
    try:
        note = summarise_update(combined, img_b64) or {}
    except Exception as e:
        print("⚠️  summarise_update failed:", e)
        note = {}

    # Mandatory keys with defaults
    note.setdefault("component",  None)
    note.setdefault("highlight",  None)
    note.setdefault("risk",       None)
    note.setdefault(
        "summary",
        "Sorry, I couldn’t grasp that update. Could you re-phrase?"
    )

    # store quick-grasp **string** for WhatsApp reply
    state["siteops_quick_grasp"] = note["summary"]
    print("SiteOps Agent:::: get_context_and_tags : summary:", note["summary"])
    # ----------- vector tags (safe) -----------
    # try:
    #     query = f"{note['component'] or ''} {note['highlight'] or ''}".strip()
    #     raw_tags   = vector_search(query) if query else []
    #     tags_pretty = filter_tags(raw_tags)
    # except Exception as e:
    #     print("⚠️  vector_search failed:", e)
    #     tags_pretty = ""

    # # ----------- human context block -----------
    ctx_block = (
        f"Component : {note['component']}\n"
        f"Highlight : {note['highlight']}\n"
        f"Risk      : {note['risk']}\n\n"
        f"Summary   : {note['summary']}\n\n"
    )

    return ctx_block





# async def wait_for_insights(state, max_retries=25, delay=1):
    
#     for a in range(max_retries):
#         print("SiteOps Agent:::: retrying : waiting for insights---",a)
#         if state.get("insights"):
#             print("SiteOps Agent:::: retrying : insights found")
#             return state["insights"]
#         await asyncio.sleep(delay)
#     return None 


#---------------- First run user stage flows--------------
#---------------------------------------------------------
def new_user_flow(state: AgentState) -> AgentState:
    last_msg = state["messages"][-1]["content"] if state.get("messages") else ""
    user_name = state.get("user_full_name", "There")
    sender_id = state["sender_id"]

    print("SiteOps Agent:::: new_user_flow : user_stage is new")
    
    img_b64 = None
    img_path = state.get("image_path")
    if img_path:
        try:
            img_b64 = encode_image_base64(img_path)
        except FileNotFoundError:
          print("⚠️  Image file not found:", img_path)
          print("SiteOps Agent:::: run_siteops_agent : called")
    if state.get("agent_first_run", True):
        if last_msg == "":
            print("SiteOps Agent:::: run_siteops_agent : latest_response is not set")

            greeting_message = generate_new_user_greeting(user_name)
            print("SiteOps Agent:::: run_siteops_agent : generating new user greeting", greeting_message)
            state["latest_respons"] = greeting_message
            state["uoc_next_message_type"] = "button"
            state["uoc_question_type"] = "siteops_welcome"
            state["uoc_pending_question"] = True
            state["agent_first_run"] = False
            state["user_verified"] = True
            state["uoc_next_message_extra_data"] = [
                {"id": "siteops", "title": "🏗️Start with my site"},
                {"id": "procurement", "title": "⚡ Get Quotes"},
                {"id": "credit", "title": "💳 Credit Options"},
            ]

            return state
        else:
            print("SiteOps Agent:::: run_siteops_agent : Last message/ Image is found")
            caption = state.get("caption", "")
            if img_b64:
                whatsapp_output(
                    sender_id,
                    f"👷‍♂️ హాయ్ {user_name} గారు! 📸 మీరు పంపిన ఫోటో అందింది.\n\nఇప్పుడు మీ site ఫోటో ని చూస్తూ, ముఖ్యమైన విషయాలు గమనిస్తున్నాను. ఇంకొద్ది సేపట్లో మీకు పూర్తి అప్డేట్ ఇస్తా! 🔍🧱",
                    message_type="plain",
                )
                combined = caption if caption else ""
            else:
                combined = last_msg
            combined = combined.strip()
            print("SiteOps Agent:::: run_siteops_agent : combined text:", combined)

            greeting_message = generate_new_user_greeting(user_name, combined, img_b64)
            print("SiteOps Agent:::: run_siteops_agent : generating new user greeting", greeting_message)
            state["latest_respons"] = greeting_message
            state["uoc_next_message_type"] = "button"
            state["uoc_question_type"] = "siteops_welcome"
            state["uoc_pending_question"] = True
            state["agent_first_run"] = False
            state["user_verified"] = True
            state["uoc_next_message_extra_data"] = [
                {"id": "siteops", "title": "🏗️Start with my site"},
                {"id": "procurement", "title": "⚡ Get Quotes"},
                {"id": "credit", "title": "💳 Credit Options"},
            ]
            print("SiteOps Agent:::: run_siteops_agent : latest_response is set", state)
            return state
    else:
        print("SiteOps Agent:::: run_siteops_agent : agent_first_run is False")
        return state


# ---------------------------------------------------------------------------
# Main public entry
# ---------------------------------------------------------------------------
async def run_siteops_agent(state: AgentState) -> AgentState:
     
    
    print("SiteOps Agent:::: run_siteops_agent : called")
    last_msg = state["messages"][-1]["content"] if state.get("messages") else ""
    print("SiteOps Agent:::: run_siteops_agent : last_msg:", last_msg)

    # ------ ---- 1 · Summarise update & build context ----------
    #ctx_block = get_context_and_tags(state)
    #print("SiteOps Agent:::: run_siteops_agent : ctx_block:", ctx_block)
    #state["context"] = ctx_block
    #state["context_tags"] = ctx_tags


    # ---------- 2 · UOC resolution (first run only) ----------
  
        
    user_stage = state.get("user_stage", {})
        
    print("SiteOps Agent:::: run_siteops_agent : user_stage:", user_stage)
        
    if user_stage == "new":
         print("SiteOps Agent:::: run_siteops_agent : user_stage is new")
         return new_user_flow(state)
    elif user_stage == "identified":
          # existing_user_flow(sender_id, last_msg, state, user_name, img_b64)
          pass
    elif user_stage == "engaged":   
        # engaged_user_flow(sender_id, last_msg, state, user_name, img_b64)
         pass
    elif user_stage == "trusted":
         # trusted_user_flow(sender_id, last_msg, state, user_name, img_b64)
         pass

        # This is an existing code that checks with UOC manager. We have to place this code in relevant user stage
    print("SiteOps Agent:::: run_siteops_agent : agent_first_run is True")
    uoc_mgr = UOCManager()
    state = await uoc_mgr.resolve_uoc(state, "siteops")

    if state.get("uoc_confidence") == "low":
        state["agent_first_run"] = False
        return state

    # ---------- 3 · Reasoning --------------------------------
    reasoning_input = state["messages"][-1]["content"]
    result = _get_reason(state, reasoning_input)

    # ---------- 4 · Save response to chat state --------------
    state["latest_response"] = result

    state["messages"].append({"role": "assistant", "content": result})
    state["agent_first_run"] = False
    return state


# ---------------------------------------------------------------------------
# Helper 3 · Reasoning prompt & call
# ---------------------------------------------------------------------------
def _get_reason(state: dict, user_update: str) -> str:
    prompt = (
        "You are a construction-site reasoning assistant.\n"
        "Given:\n"
        "1. User update text.\n"
        "2. Site note (single highlight).\n"
        "3. Context tags / guidelines.\n"
        "4. UOC snapshot (project meta).\n\n"
        "Compare the update with expectations.\n"
        "Output concisely:\n"
        "Risks: <one line>\n"
        "Actionable Items:\n"
        " - bullet 1\n"
        " - bullet 2 (max 3 bullets)\n"
        "Next Stage Preparations:\n"
        " - bullet 1\n"
        "Potential Financial Impact: <one line>\n"
        "If info is insufficient → 'No relevant comparison possible'."
    )

    note = state.get("latest_site_note", {})
    tags = state.get("context_tags", "")
    uoc_snapshot = json.dumps(state.get("uoc", {}).get("data", {}), indent=2)

    chat = llm_reasoning.invoke(
        [
            SystemMessage(content=prompt),
            HumanMessage(
                content=(
                    f"User update:\n{user_update}\n\n"
                    f"Site note:\n{note}\n\n"
                    f"Tags:\n{tags}\n\n"
                    f"UOC snapshot:\n{uoc_snapshot}"
                )
            ),
        ]
    )
    return chat.content.strip()
