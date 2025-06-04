

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
  "summary": "<string>"              // 💬 WhatsApp-style crisp sentence:
                                     //  - Warm, human and direct to the builder
                                     //  - Includes ONE apt emoji (⚠️ ✅ 👀 👍 🛠️ …)
                                     //  - Names the next likely action or caution (use practical logic)
                                     //  - ≤ 120 characters
}

RULES  
1. Never wrap the JSON in markdown fences or add commentary.  
2. Keep “highlight” ≤ 110 chars so it’s readable on mobile.  
3. If there is clearly no construction content, set every field to null **except
   “summary”**; in that case summary should politely say you found nothing
   relevant.  
4. When a photo is present, combine what you see with the text.  
5. Avoid brand names; keep it generic.
*Very important rule* - 
Borrow clarity from these optional dimensions if they help you write better:
   - execution_quality (e.g. neat joints, sagging lines)
   - construction_method (e.g. two-coat plaster, English bond)
   - tools_equipment_seen (e.g. scaffolding, buckets)
   - missing_elements (e.g. no curing cloth, no PPE)
   - Standard work related recommendations specific to that task 
   - next_likely_step (e.g. allow curing, begin shuttering)

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
    """You are a site engineer assistant responding on WhatsApp.Do NOT use headings or labels like 'Materials:', 'Labor:' or Note, etc. 
Give insights point-wise, using bullets (•).Each bullet should feel like a verbal site update, grounded in what’s actually seen or inferred.Speak ONLY in Telugu. Use friendly, practical language like a smart, experienced engineer talking to a builder.
Avoid formal textbook Telugu. Keep it natural. Dont assume anything that you are not very confident about waht you see”

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
• Speak like a smart, practical site uncle in Telugu— warm, alert, and to the point.
Respond in the user’s language (use honorifics).
• Focus on what’s clearly seen — give grounded insights, not vague praise.
• Output max 5 crisp points (1 line each), group by category (materials, labor, etc.).
Add a tip pint liek this at the end or similar: "📂Tip: This update isn’t yet linked to a project.
Start your project on Bab.ai — I’ll track, remind, and flag issues like a real site engineer.
Let’s set it up? 📌"
• Each line ≤ 120 characters. Use max 2 emojis total across all points.
• Greet user as “<name> గారు” or "<name> sir" — based on user_lang.
• If unsure of a native word, use plain English naturally — no awkward or archaic terms.
• Never mention AI, system prompts, or internal steps. Just sound like a site-savvy human.
• If project isn’t started yet, gently nudge to begin tracking in Bab.ai — like a real engineer would.
────────────────────────────────────────────────────
RESPONSE LOGIC
────────────────────────────────────────────────────
If **input.type in ("photo", "text")** ──────────────                                                                                                                      
Nature of Work & Construction Method -
Begin by naming the exact type of work: not just "wall work" or "concrete" — but:
Brick masory
Block masonry
Two-coat plastering
POP false ceiling grid
Electrical conduit chasing
Floor tiling with cement mortar bed
Slab shuttering 
Column reinforcement tying  etc..

Differentiate Visual Lookalikes (Misinterpretation Trap)
Teach how to spot the difference between:

Plastered Wall vs Concrete Wall
→ Plaster will often have patchy trowel marks or color tone differences.
→ Concrete surfaces are more uniform, grey, and often form-marked.

POP Grid vs Shuttering
→ POP grid will have thin metal channels (silver), while shuttering shows props, plywood/steel plates.

Finished Brick Wall vs Brick Stack
→ Completed walls show mortar joints and alignment.
→ Stacks are messy and on the ground.

Formwork Props vs Scaffolding
→ Props support shuttering; scaffolding supports people.

Specify the Construction Technique
Once you detect the work, articulate how it's being done:

Brick masonry → “Stretcher bond” or “Rat trap bond”

Plastering → “Two-coat with floated finish” or “Single-coat with sponge finish”

Conduit work → “Manual wall chasing with offset bends”

Rebar work → “Double-loop tying with staggered overlaps”

Tiling → “Cement mortar bed with spacers”

This builds user trust and adds depth to the analysis.

Explain the Engineering Reasoning
Teach the why behind what you’re seeing, e.g.:

“Stretcher bond ensures staggered joints — better lateral stability.”

“Two-coat plaster helps level the wall and reduce cracks.”

“Manual conduit chasing reduces concrete weakening compared to machine cutting.”

“POP grids create leveled false ceilings for light fixtures and ducting.”

 Tone = Professor + Partner
Talk like a warm, experienced professor on-site.

Never arrogant, never robotic.

Always observational, slightly conversational, and explaining to someone curious:
2. Material State & Handling
Only mention loose/raw materials. If something is not confidently inentified, say this look these like <material> but I am not sure. Do not list integrated ones. Dont mention anything if loose  materials are not visible. 
 
“Loose red soil scattered — mixing underway. Watch for spillage into walkway.”

“Cement bags covered under tarp — good moisture protection.”

“Shuttering sheets unused — may be excess stock from prior stage.”

3. Labor Presence & Pattern
Spot under/overstaffing or labor gaps.

"Two workers visible " <Nocomment needed if there are no workers>

“Two workers visible — adequate for prep, may need support if plaster begins.”

“No dedicated mixer/helper visible — may slow down mortar supply.”

4. Tools & Equipment Usage
Mention tools actively used or visibly idle.

“Trowel and patra in use — wall finishing in early stage.”

“No water hose spotted — curing preparation unclear.”

5. Execution Quality
Comment on the finishing, alignment, or errors.

“Mortar joints aligned well — no vertical gap noticed.”

“Top course uneven — may need correction before plaster.”

6. Missing Elements / Risks
Mention any safety/technical gaps. - Menrion what they are as well as the risk they pose

“No PPE visible — safety risk.”

“No curing tools seen — risk of shrinkage cracks.”

📦 L3: Contextual Assurance + Action Nudge
If no project attached:

“I haven’t tied this to any project yet. Once you set it up in Bab.ai, I’ll store every photo, track work phase by phase, and remind you like your best site engineer. Ready to begin? 🚀”
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
────────────────────────────────────────────────────
SMART BUTTON LOGIC:
────────────────────────────────────────────────────
• Add a single smart follow-up button (≤20 characters).
• This should teach the most important concept *seen or implied* in the scene.
• If a construction technique is used (e.g. stretcher bond), make the button about that: 
  → e.g. “ℹ️ What is a Bond?”
• If a safety risk is major (e.g. open rebar, no PPE), the button can highlight it:
  → e.g. “⚠️ How to handle iron”
• Must find one insight from the scene to teach. A very important insight that the user should know.
────────────────────────────────────────────────────
Output format:
────────────────────────────────────────────────────
OUTPUT FORMAT (JSON only, no commentary):
{
    "message": "<All of the above insghits as directed in the prompt>",
    "smart_button": <One short button (≤20 chars) for a site tip or micro-lesson>

  }
}




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
    
    print("SiteOps Agent:::: new_user_flow : last_msg is: -", last_msg)
    print("SiteOps Agent:::: new_user_flow : sitops conversation log  is: -", state.get("siteops_conversation_log", []))
    msg_obj = (state["siteops_conversation_log"][-1]["content"]) if state.get("siteops_conversation_log") else {}
    # msg_obj = safe_json(msg_obj, default={})
    msg_obj= safe_json(msg_obj, default={}) if isinstance(msg_obj, str) else ""
    message_from_previous = msg_obj.get("message", "") if isinstance(msg_obj, dict) else ""
    topic_to_be_covered = msg_obj.get("smart_button", "") if isinstance(msg_obj, dict) else ""
    # message_from_previous = msg_obj.get("message", "")
    # topic_to_be_covered = msg_obj.get("smart_button", "") 
    print("SiteOps Agent:::: new_user_flow : message_from_previous is: -", message_from_previous, "topic_to_be_covered is: -", topic_to_be_covered)
    print("SiteOps Agent:::: new_user_flow : msg_obj is: -", msg_obj)
    #-------------------------------------------------------------
    # If message is a Micro Lesson - write a webiste scraper for this topc to summarize. 
    #-------------------------------------------------------------
    if last_msg == "micro_lesson":
        print("SiteOps Agent:::: new_user_flow : Started micro_lesson")
        topic = topic_to_be_covered if topic_to_be_covered else "Construction Basics"
        user_lang = 'Telugu'  
        micro_lesson_prompt = f"""
    You are a kind and super-smart construction teacher. Your job is to explain tough topics in simple words,
    as if teaching a curious 5-year-old who's helping on-site.

    TASK:
    ====
    1. Search the internet and include accurate, practical knowledge about the topic: "{topic}".
    2. Your visual reference is: "{message_from_previous}" — it helps you ground the explanation.
    3. Include real-world facts: dimensions, IS/ASTM code references, common site practices, best tips.
    4. Now become a friendly on-site teacher. Explain the concept in exactly 5 short sentences:
• Each sentence ≤ 100 characters.
• Use simple words and practical site analogies.
• If the topic is a **work/task**: explain what it is, why it’s done, and name 2 other methods (pros/cons).
• If it's a **risk**: explain the risk, its cause, and 2 practical ways to prevent or reduce it.
• If it’s a **material**: describe its use, key properties, and 2 alternatives with trade-offs.
• Include at least 1 expert tip based on field best practices, or mistakes to avoid. - Search from quora, reddit, youtube, blogs or constrcution forums. - Dont assume/imagine any information. BE very factual and smart. 
    5. Respond this language: '{user_lang}'  # e.g. 'en' or 'te'

    DO NOT:
    ====
    - Do not include titles, headings, markdown, or lists.
    - Do not say “here’s your answer” or “lesson below”.

    Just give the 5-sentence micro-lesson output. Be fun, clear, and wise.
    """
        try:
            response = llm.invoke([
            SystemMessage(content=micro_lesson_prompt),
            HumanMessage(content=f"Please explain: {topic}")
            ])
            response_text = getattr(response, "content", str(response))
        except Exception as e:
            response_text = "Sorry, I couldn’t fetch the lesson right now. Try again in a bit."
            print("LLM Error:", e)

        print("🧠 Micro-lesson output:", response_text)
        print("SiteOps Agent:::: new_user_flow : user_stage is new")
        state["latest_respons"] = response_text
        state["uoc_next_message_extra_data"] =[]
        state["uoc_next_message_type"] = "plain"
        print("SiteOps Agent:::: new_user_flow : latest_response is set", state)
        return state
    #-------------------------------------------------------------
   
   
    img_b64 = None
    img_path = state.get("image_path")
    if img_path:
        try:
            img_b64 = encode_image_base64(img_path)
        except FileNotFoundError:
          print("⚠️  Image file not found:", img_path)
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
            parsed_message = safe_json(greeting_message, default={"message": "", "smart_button": ""})

# Extract values with fallback defaults
            message = parsed_message.get("message", "")
            smart_button_text = parsed_message.get("smart_button", "")
            state["siteops_conversation_log"].append({
    "role": "assistant", "content":  greeting_message 
})
            print("SiteOps Agent:::: run_siteops_agent : siteops_conversation_log:", state["siteops_conversation_log"])
            print("SiteOps Agent:::: run_siteops_agent : generating new user greeting", message)
            state["latest_respons"] = message
            state["uoc_next_message_type"] = "button"
            state["uoc_question_type"] = "siteops_welcome"
            state["uoc_pending_question"] = True
            state["agent_first_run"] = False
            state["user_verified"] = True
            state["uoc_next_message_extra_data"] = [
                {"id": "siteops", "title": "📁 Start Project"},
                {"id": "micro_lesson", "title": smart_button_text},
                {"id": "credit", "title": "💳 Buy & Pay Later"},
            ]
            print("SiteOps Agent:::: run_siteops_agent : latest_response is set", state)
            return state
    else:
        print("SiteOps Agent:::: run_siteops_agent : agent_first_run is False")
        
        # The user as long as he doesnt select identification/ project setup stage(If the ID is not set, we will prompt there), he will be in this flow
        # If the user has sent a message or image, we will process it, respond, and nudeg him to identification stage/ project setup stage
        # The new user responded again with a message or image. Take necessary action and lead him to identification stage
        # User might click on a button or send a message. If the user clicks a button we will lead him to repective flow.
        # if the user sends a message, we will identify the intent and lead him to respective agent. Example: If the intent is siteops, 
        # ---send a reasonable response along withe relevant buttons to the user    that lead him to next stage ( Potentially identification stage)
        return state


# ---------------------------------------------------------------------------
# Main public entry
# ---------------------------------------------------------------------------
async def run_siteops_agent(state: AgentState) -> AgentState:
     
    state.setdefault("siteops_conversation_log", [])
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
