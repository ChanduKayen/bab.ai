

import os, json, base64, openai
from typing import Dict, Tuple, Optional
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
import re        
from models.chatstate import AgentState
from managers.uoc_manager import UOCManager
from whatsapp.builder_out import whatsapp_output
from database.uoc_crud import DatabaseCRUD
#from database._init_ import AsyncSessionLocal
from app.db import get_sessionmaker
AsyncSessionLocal = get_sessionmaker()

from database.procurement_crud import ProcurementCRUD
  # <-- Add this import, adjust path as needed
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
    - Strips json fences
    - Tries a raw json.loads
    - Fallback: regex find first {...}
    - On failure returns default (dict() if not supplied)
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
Return *ONE single-line JSON object* and nothing else.  
Keys (always include all four; use null if unknown):

{
  "component": "<string|null>",     // top-level element (e.g. Bathroom, Rebar, Wall Plaster)
  "highlight": "<string|null>",     // one crisp sentence of what is happening now, with quick analysis
  "risk": "<string|null>",          // main immediate risk 
  "summary": "<string>"              // 💬 WhatsApp-style crisp sentence:
                                     //  - Warm, human and direct to the builder
                                     //  - Includes ONE apt emoji (⚠ ✅ 👀 👍 🛠 …)
                                     //  - Names the next likely action or caution (use practical logic)
                                     //  - ≤ 120 characters
}

RULES  
1. Never wrap the JSON in markdown fences or add commentary.  
2. Keep “highlight” ≤ 110 chars so it’s readable on mobile.  
3. If there is clearly no construction content, set every field to null **except
   “summary”; in that case summary should politely say you found nothing
   relevant.  
4. When a photo is present, combine what you see with the text.  
5. Avoid brand names; keep it generic.
Very important rule - 
Borrow clarity from these optional dimensions if they help you write better:
   - execution_quality (e.g. neat joints, sagging lines)
   - construction_method (e.g. two-coat plaster, English bond)
   - tools_equipment_seen (e.g. scaffolding, buckets)
   - missing_elements (e.g. no curing cloth, no PPE)
   - Standard work related recommendations specific to that task 
   - next_likely_step (e.g. allow curing, begin shuttering)

EXAMPLE  
*User text:*  
“Two masons are applying the first coat of waterproofing in the master bathroom.”  

*Expected model reply (single line):*  
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
    """
────────────────────────────────────
ROLE
• You're a calm, observant “smart site brain” who replies on WhatsApp.
• Tone: confident, warm, and quietly impressive — like Apple meets a sharp site engineer.
• You don’t oversell. You simply notice, log, and assist.

INPUT
• User may send a photo or short message about site progress.

OUTPUT
• 1 crafted message.
    – First, react naturally to what’s visible or described.
    – Mention the exact work observed (e.g., floor tiling, slab prep).
    – If workers or materials are seen, note them subtly.
    – Then explain, in one calm line, what you can do if they keep sending updates.
    – Optionally, end with a soft CTA: “Want me to save this under a project?”

STYLE RULES
✓ Never boast. Quietly amaze.
✓ Feel helpful and personal — like you're watching out for them.
✓ Don’t list features. Don’t explain how the system works.
✓ Say “I can track everything from just photos & messages” only once, if at all.
✓ If work is unclear, guess gently or ask — never fake confidence.

EXAMPLE OUTPUT

Looks like floor tiles were laid and a few cement bags went in. Logged that for you. ✅
But I don’t stop there — I can follow up tomorrow, nudge your supervisor, and even spot patterns over time.

Just send a photo or message here — no apps, no effort.

I track everyone, every task, every day — like a site log that builds itself.

📂 Want me to save this under a project name, so I can track all future work in one place?




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
            print("⚠  Image file not found:", img_path)

    # ----------- summarise (safe) -----------
    try:
        note = summarise_update(combined, img_b64) or {}
    except Exception as e:
        print("⚠  summarise_update failed:", e)
        note = {}

    # Mandatory keys with defaults
    note.setdefault("component",  None)
    note.setdefault("highlight",  None)
    note.setdefault("risk",       None)
    note.setdefault(
        "summary",
        "Sorry, I couldn’t grasp that update. Could you re-phrase?"
    )

    # store quick-grasp *string* for WhatsApp reply
    state["siteops_quick_grasp"] = note["summary"]
    print("SiteOps Agent:::: get_context_and_tags : summary:", note["summary"])
    # ----------- vector tags (safe) -----------
    # try:
    #     query = f"{note['component'] or ''} {note['highlight'] or ''}".strip()
    #     raw_tags   = vector_search(query) if query else []
    #     tags_pretty = filter_tags(raw_tags)
    # except Exception as e:
    #     print("⚠  vector_search failed:", e)
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



async def handle_siteops(state: AgentState, crud: DatabaseCRUD,latest_response: str, uoc_next_message_extra_data=None ) -> AgentState:
    #handle a message here 
    state.update(
        intent="siteops",
        latest_respons=latest_response, 
        uoc_next_message_type="button",
        uoc_question_type="siteops_welcome",
        needs_clarification=True,  
        uoc_next_message_extra_data=[uoc_next_message_extra_data],
        agent_first_run=True
    )
    print("Siteops Agent::::: handle_siteops:::::  --Handling siteops intent --", state)
    return state    

async def handle_procurement(state: AgentState, crud: DatabaseCRUD,latest_response: str, uoc_next_message_extra_data=None ) -> AgentState:
    #handle a message here 
    state.update(
        intent="procurement",
        latest_respons=latest_response, 
        uoc_next_message_type="button",
        uoc_question_type="procurement_welcome",
        needs_clarification=True,  
        uoc_next_message_extra_data=[uoc_next_message_extra_data],
        agent_first_run=True
    )
    print("Siteops Agent::::: handle_siteops:::::  --Handling procurement intent --", state)
    return state    

def handle_main_menu(state: AgentState, crud: DatabaseCRUD, latest_response: str, uoc_next_message_extra_data=None) -> AgentState:
    state.update(
        intent="random",
        latest_respons=latest_response,
        uoc_next_message_type="button",
        uoc_question_type="siteops_welcome",
        needs_clarification=True,   
        uoc_next_message_extra_data=uoc_next_message_extra_data,
    )
    print("Random Agent::::: handle_main_menu:::::  --Handling main menu intent --", state)
    return state

async def handle_micro_lesson(state:AgentState, crud: DatabaseCRUD, latest_response:str, uoc_next_message_extra_data= None) -> AgentState:
    msg_obj = (state["siteops_conversation_log"][-1]["content"]) if state.get("siteops_conversation_log") else {}
    # msg_obj = safe_json(msg_obj, default={})
    msg_obj= safe_json(msg_obj, default={}) if isinstance(msg_obj, str) else ""
    message_from_previous = msg_obj.get("message", "") if isinstance(msg_obj, dict) else ""
    topic_to_be_covered = msg_obj.get("smart_button", "") if isinstance(msg_obj, dict) else ""
    print("SiteOps Agent:::: new_user_flow : Started micro_lesson")
    topic = topic_to_be_covered if topic_to_be_covered else "Construction Basics"
    user_lang = 'Telugu'
    micro_lesson_prompt = f"""
You are a master builder-mentor. Explain *{topic}* so that even a curious
20-year-old helper and a seasoned contractor both say “aha!”.

RULES OF ENGAGEMENT
===================
⿡  Search credible sources (IS/ASTM, field handbooks, failure reports,
    expert YouTube demos, high-quality threads). Quote numbers ONLY if verifiable.

⿢  Deliver *exactly 6 bullets* – each ≤ 140 chars.
    • Bullets 1-3  = BASICS (what, why, 1 everyday detail + 1 common slip-up).
    • Bullets 4-6  = ADVANCED (killer fact / failure story / code clause /
                      pro hack / cost metric). End with *[TRY]* or *[CHECK]*
                      action tag the reader can do next shift.

⿣  ✨ Use 1 “wow” emoji max (⚠, 💡, 🔍, 🚧, 🔑). No other fluff.

⿤  No headings, no markdown, no numbering – just six crisp lines.

DON’TS
======
• No invented data. Skip if uncertain.
• No brand names or sales pitch.
• No “here’s your answer” filler.

Language: {user_lang}
"""
    try:
        response =await llm.ainvoke([
            SystemMessage(content=micro_lesson_prompt),
            HumanMessage(content=f"Please explain: {topic}")
        ])
        response_text = getattr(response, "content", str(response))
    except Exception as e:
        response_text = "Sorry, I couldn’t fetch the lesson right now. Try again in a bit." 
        print("LLM Error:", e)

    print("Micro-lesson output:", response_text)
    print("SiteOps Agent:::: new_user_flow : user_stage is new")
    state["latest_respons"] = response_text
    state["uoc_next_message_extra_data"] = []
    state["uoc_next_message_type"] = "button"
    state["uoc_next_message_extra_data"] = [
    {"id": "project_onboarding", "title": "📁 Add to Project"},
    {"id": "main_menu", "title": "🏠 Main Menu"}
]
    print("SiteOps Agent:::: new_user_flow : latest_response is set", state)
    return state
 

async def handle_project_onboarding(state:AgentState,  crud: DatabaseCRUD, latest_response:str, uoc_next_message_extra_data= None) -> AgentState:
    uoc_last_called_by =  "siteops"
    uoc_mgr = UOCManager(crud)
    return await uoc_mgr.resolve_uoc(state,uoc_last_called_by)

async def handle_project_overview(state:AgentState,  crud: DatabaseCRUD, latest_response:str, uoc_next_message_extra_data= None):
        message=""" SiteOps Daily Pulse — ASM Elite Apartments (Stilt + G + 5 Floors)
📍 Pratap Nagar, Kakinada, Andhra Pradesh

Yesterday
✅ Concreting completed — 2nd floor bathrooms
✅ Masonry work started — 3rd floor

ACTION NOW
1️⃣ Crew Efficiency Alert — 1 crew member idle. 8 masons on Floor-2; productivity data shows 7 can complete the same scope. (Avoid ₹2,300/day idle cost)
2️⃣ Cement Shortfall — Stock: 85 bags | Next pour: 120 bags. Short by 35 bags. 🧱 Order now

Why this matters:
Every insight here is AI-generated from your site logs, BOQ plans, and daily productivity patterns — so you act fast, save cost, and stay ahead."""
        print("Siteops Agent:: Handle_project_overview:: ")
        state["latest_respons"] = message
        state["uoc_next_message_type"] = "button"
        state["needs_clarification"]=True
        state["uoc_next_message_type"]="procurement_new_user_flow"
        state["agent_first_run"]=False
        extra_data = [
        {"id": "Order_materials", "title": "Order Cement"},
        {"id": "main_menu", "title": "🏠 Main Menu"}
    ]
        sender_id= state.get("sender_id")
        whatsapp_output(sender_id, message, message_type="button", extra_data=extra_data)
        return state


async def handle_order_materials(state:AgentState,  crud: DatabaseCRUD, latest_response:str, uoc_next_message_extra_data= None): 
    state["messages"][-1]["content"] = "I need cement"
    state["uoc_next_message_type"]="procurement_new_user_flow"
    state["agent_first_run"]=True
    state["image_path"]=""
    async with AsyncSessionLocal() as session:
            
            crud = ProcurementCRUD(session)
            from agents.procurement_agent import run_procurement_agent
            return await run_procurement_agent(state, config={"configurable": {"crud": crud}})



_HANDLER_MAP = {
      "siteops": handle_siteops,
    "procurement": handle_procurement,
    #"credit": handle_credit,
    "main_menu": handle_main_menu,
    "micro_lesson": handle_micro_lesson,
    "project_onboarding" : handle_project_onboarding,
    "project_overview": handle_project_overview,
    "Order_materials": handle_order_materials
}




#---------------- First run user stage flows--------------
#--------------------------------------------------------- 

async def new_user_flow(state: AgentState,latest_msg_intent:str, crud: DatabaseCRUD) -> AgentState:
   
    
    last_msg = state["messages"][-1]["content"] if state.get("messages") else ""
    user_name = state.get("user_full_name", "There")
    sender_id = state["sender_id"]
    uoc_next_message_extra_data = state.get("uoc_next_message_extra_data", [])
    latest_response = state.get("latest_respons", None)
    print("SiteOps Agent:::: new_user_flow : last_msg is: -", last_msg)
    print("SiteOps Agent:::: new_user_flow : sitops conversation log  is: -", state.get("siteops_conversation_log", []))
    print("SiteOps Agent:::: new_user_flow : the state received here is : -", state)

    img_b64 = None
    img_path = state.get("image_path")
    if img_path:
        try:
            img_b64 = encode_image_base64(img_path)
        except FileNotFoundError:
          print("⚠  Image file not found:", img_path)
          print("SiteOps Agent:::: run_siteops_agent : called")
          state["siteops_conversation_log"].append({
    "role": "user", "content": img_b64 if img_b64 else last_msg + "\n" + state.get("caption", "")
})
    
    if state.get("agent_first_run", True):
        if last_msg == "":
            print("SiteOps Agent:::: run_siteops_agent : latest_response is not set")


            greeting_message = generate_new_user_greeting(user_name)
            print("SiteOps Agent:::: run_siteops_agent : generating new user greeting", greeting_message)
            state["latest_respons"] = greeting_message
            state["uoc_next_message_type"] = "button"
            state["uoc_question_type"] = "siteops_welcome"
            state["needs_clarification"] = True
            state["agent_first_run"] = False
            state["user_verified"] = True
            state["uoc_next_message_extra_data"] = [
                {"id": "siteops", "title": "🏗Start with my site"},
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
                    f"Hey 👋\n\nGot your photo. Give me a sec — scanning this carefully. 🔍",
                    message_type="plain",
                )
                combined = caption if caption else ""
            else:
                combined = last_msg
            combined = combined.strip()
            print("SiteOps Agent:::: run_siteops_agent : combined text:", combined)

            greeting_message = generate_new_user_greeting(user_name, combined, img_b64)
            parsed_message = safe_json(greeting_message, default={"message": "", "smart_button": ""})
            print("SiteOps Agent:::: run_siteops_agent : parsed_message:", parsed_message, greeting_message)
            message = parsed_message.get("message", "")
            smart_button_text = parsed_message.get("smart_button", "")
            state["siteops_conversation_log"].append({"role": "assistant", "content":  greeting_message })
            print("SiteOps Agent:::: run_siteops_agent : siteops_conversation_log:", state["siteops_conversation_log"])
            print("SiteOps Agent:::: run_siteops_agent : generating new user greeting", message)
            state["latest_respons"] = greeting_message
            state["uoc_next_message_type"] = "button"
            state["uoc_question_type"] = "siteops_welcome"
            state["needs_clarification"] = True
            state["agent_first_run"] = False
            state["user_verified"] = True
            state["uoc_next_message_extra_data"] = [
                #{"id": "micro_lesson", "title": "ℹ Learn More"}, 
                {"id": "project_onboarding", "title": "📁 Add to Project"},
                {"id": "project_overview", "title": "Project Overview"},
                {"id": "main_menu", "title": "🏠 Main Menu"}
            ]
            print("SiteOps Agent:::: run_siteops_agent : latest_response is set", state)
            return state
    #This becomes true from second message onwards.
    else:
        print("SiteOps Agent:::: run_siteops_agent : agent_first_run is False")
        if last_msg in _HANDLER_MAP:
            #The main menu for new user.
            if last_msg =="main_menu":
                latest_response = "Welcome back! How can I assist you today?"
                uoc_next_message_extra_data =[{"id": "siteops", "title": "🏗 Manage My Site"},
                                          {"id": "procurement", "title": "⚡ Get Quick Quotes"},
                                          {"id": "credit",      "title": "💳 Get Credit Now"}] 
            return await _HANDLER_MAP[last_msg](state, crud, latest_response, uoc_next_message_extra_data)
        else:
            print("SiteOps Agent:::: new_user_flow:::: Button is note selected")
            if latest_msg_intent == "random":
                from agents.random_agent_backup import classify_and_respond
                return await classify_and_respond(state, config={"configurable": {"crud": crud}})
            elif latest_msg_intent == "siteops":
                latest_response = "📷 Ready to check your site? Let's continue!"
                uoc_next_message_extra_data = {"id": "siteops", "title": "📁 Continue Site Setup"}
                return await handle_siteops(state, crud, latest_response,  uoc_next_message_extra_data)
            elif latest_msg_intent == "procurement":
                latest_response = "🧱 Tell me what materials you're looking for, and I'll fetch quotes!"
                return await handle_procurement(state, crud, latest_response)
            # elif latest_msg_intent == "credit":
            #     latest_response = "💳 Let's explore credit options suitable for your site."
            #     return await handle_credit(state, latest_response)
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

        # The user a s long as he doesnt select identification/ project setup stage(If the ID is not set, we will prompt there), he will be in this flow
        # If the user has sent a message or image, we will process it, respond, and nudeg him to identification stage/ project setup stage
        # The new user responded again with a message or image. Take necessary action and lead him to identification stage
        # User might click on a button or send a message. If the user clicks a button we will lead him to repective flow.
        # if the user sends a message, we will identify the intent and lead him to respective agent. Example: If the intent is siteops, 
        # ---send a reasonable response along withe relevant buttons to the user    that lead him to next stage ( Potentially identification stage)
        return state


# ---------------------------------------------------------------------------
# Main public entry
# ---------------------------------------------------------------------------
async def run_siteops_agent(state: AgentState, config: dict) -> AgentState:
    print("SiteOps Agent:::: run_siteops_agent : called")
    print("SiteOps Agent:::: run_siteops_agent : config received =>", config)
    try:
        crud = config["configurable"]["crud"]
        uoc_mgr = UOCManager(crud)
    except Exception as e:
        print("SiteOps Agent:::: run_siteops_agent : failed to initialize crud or UOCManager:", e)
        state["latest_respons"] = "Sorry, there was a system error. Please try again later."
        return state
    
    state.setdefault("siteops_conversation_log", [])
    
    last_msg = state["messages"][-1]["content"] if state.get("messages") else ""
    print("SiteOps Agent:::: run_siteops_agent : last_msg:", last_msg)     
    user_stage = state.get("user_stage", {})
    print("SiteOps Agent:::: run_siteops_agent : user_stage:", user_stage)
    
    
    # because when we call the orchstrator it correctly extracts the inteded message,
    # but since this state is passed after an image analysis, the image pathis still found - 
    # what we are actually doing when her eis we are inculding this obtained message along with the image path -
    #  that is why we are seeing the sitops intent. To overcoem this issue we are passing a new state with path set to ""
    state_for_intent_match = state.copy()
    state_for_intent_match["image_path"]="" if last_msg else state.get("image_path","")
    from orchastrator.core import infer_intent_node
    latest_msg_intent = (await infer_intent_node(state_for_intent_match)).get("intent")

    print("SiteOps Agent:::: run_siteops_agent - Intent of latest message is - ", latest_msg_intent)

    if user_stage == "new":
         print("SiteOps Agent:::: run_siteops_agent : user_stage is new")
         return await new_user_flow(state, latest_msg_intent, crud)
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
    state = await uoc_mgr.resolve_uoc(state, "siteops")

    if state.get("uoc_confidence") == "low":
        state["agent_first_run"] = False
        return state

    state["agent_first_run"] = False
    return state
  