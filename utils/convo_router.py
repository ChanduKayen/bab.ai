import json, re, os
from typing import Dict, Tuple, Optional, Union, Any, List
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from dotenv import load_dotenv
from database.uoc_crud import DatabaseCRUD
from database.procurement_crud import ProcurementCRUD
from database.credit_crud import CreditCRUD 
#from database._init_ import AsyncSessionLocal 
from app.db import get_sessionmaker
AsyncSessionLocal = get_sessionmaker()

load_dotenv()
_llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, openai_api_key=os.getenv("OPENAI_API_KEY"))

ALLOWED = {
  "procurement": [
    "start_order",        # ask/confirm materials, qty, units, location, need-by
    "order_followup",     # track / modify / compare / issue — all post-order actions
    "upload",             # photo/invoice/BOQ intake that drafts an order
    "help"                # quick Qs about process/pricing without details yet
  ],
  "credit": [
    "limit_or_kyc",       # check eligibility/limit or start KYC (same entry path)
    "pay_vendor",         # make/arrange vendor payment
    "status_or_repay",    # application status / statement / repayment info
    "help"
  ],
  "siteops": [
    "setup",              # create/select site (name + location)
    "progress",           # log progress / upload photo (same entry path)
    "summary",            # risks/follow-ups / site summary
    "help"
  ],
  "ambiguous": ["help", # unclear but meaningful inetent realted ot bab-ai; router uses last_known_intent
                "chit-chat" # unrealted chatter
                ]   
}
FALLBACK = {"intent": "ambiguous", "context": "help"}

# Non-greedy JSON; supports code fences; grabs the last valid JSON object if multiple appear.
_JSON_ANY = re.compile(r"\{.*?\}", re.S)

REQUIRED_SLOTS = {
  # Procurement
  ("procurement","start_order"):   ["materials","quantity","units","location","needed_by_date"],
  ("procurement","order_followup"):["order_id"],
  ("procurement","upload"):        ["doc_present","doc_type"],   # {"photo","invoice","boq","other"}
  ("procurement","help"):          [],

  # Credit
  ("credit","limit_or_kyc"):       [],                           # ask PAN/GST only inside KYC flow
  ("credit","pay_vendor"):         ["vendor_name","amount","order_id"],
  ("credit","status_or_repay"):    ["application_id"],           # if unknown, agent can ask/lookup
  ("credit","help"):               [],

  # SiteOps
  ("siteops","setup"):             ["site_name","location"],
  ("siteops","progress"):          ["task","floor_or_area"],     # or just photo → doc_present/doc_type
  ("siteops","summary"):           ["site_name"],
  ("siteops","help"):              [],

  # Ambiguous
  ("ambiguous","help"):            []
}

TEMPLATES = {
    ("procurement","new_request"): {
        "text": "Got it. I’ll set this up. Share details like *material, qty, units, location, needed-by*.\nExample: `OPC 53, 120, bags, Kukatpally, Friday`",
        "buttons": [{"id":"proc_upload","title":"📎 Upload BOQ/Photo"}],
        "qtype": "procurement_new_user_flow"
    },
    ("procurement","get_quotes"): {
        "text": "💬 I’ll fetch quotes. Confirm *material, qty, units, location*.",
        "buttons": [{"id":"proc_quotes","title":"⚡ Get Quotes"}],
        "qtype": "procurement_new_user_flow"
    },
    ("procurement","compare_select"): {
        "text": "🧮 Comparing options for your order. Share your *Order ID* to proceed.",
        "buttons": [{"id":"proc_compare","title":"🔍 Compare Quotes"}],
        "qtype": "procurement_new_user_flow"
    },
    ("procurement","track_order"): {
        "text": "📍 Track your delivery with your *Order ID*.",
        "buttons": [{"id":"proc_track","title":"📍 Track Now"}],
        "qtype": "procurement_new_user_flow"
    },
    ("procurement","modify_order"): {
        "text": "✏️ Tell me the *Order ID* and what to change.",
        "buttons": [{"id":"proc_edit","title":"✏️ Edit Order"}],
        "qtype": "procurement_new_user_flow"
    },
    ("procurement","upload_doc"): {
        "text": "📎 Send a *photo/BOQ/invoice* and I’ll parse it into an order draft.",
        "buttons": [{"id":"proc_upload","title":"📎 Upload Now"}],
        "qtype": "procurement_new_user_flow"
    },
    ("procurement","issue_report"): {
        "text": "⚠️ Let’s fix this. Share *Order ID* and the issue (*short-supply/quality/other*).",
        "buttons": [{"id":"proc_issue","title":"🛠 Raise Issue"}],
    },

    ("credit","eligibility_check"): {
        "text": "💳 Checking credit eligibility takes seconds. Ready?",
        "buttons": [{"id":"credit_check","title":"⚡ Check Eligibility"}],
    },
    ("credit","application_status"): {
        "text": "📊 Share your *Application ID* to see live status.",
        "buttons": [{"id":"credit_status","title":"📊 View Status"}],
    },
    ("credit","kyc_start"): {
        "text": "🪪 Let’s finish KYC. I’ll ask only what’s needed and keep it secure.",
        "buttons": [{"id":"kyc_start","title":"🪪 Start KYC"}],
    },
    ("credit","limit_check"): {
        "text": "💳 I can show your available limit and options to increase it.",
        "buttons": [{"id":"credit_limit","title":"📈 Check Limit"}],
    },
    ("credit","vendor_payment"): {
        "text": "💸 Pay a vendor via Bab.ai Credit. Share *vendor, amount, order ID*.",
        "buttons": [{"id":"credit_pay","title":"💳 Pay Vendor"}],
    },
    ("credit","repayment_info"): {
        "text": "🧾 I can show EMIs due and repayment schedule.",
        "buttons": [{"id":"credit_repay","title":"📅 Repayment"}],
    },
    ("credit","trust_score"): {
        "text": "🔎 Your Bab.ai Trust Score speeds up approvals. Want to see it and how to improve?",
        "buttons": [{"id":"trust_score","title":"🔎 View Score"}],
    },

    ("siteops","site_setup"): {
        "text": "📁 Let’s set up your site. Share *site name* and *location*.",
        "buttons": [{"id":"siteops","title":"📁 Site Setup"}],
    },
    ("siteops","log_progress"): {
        "text": "📝 Log an update. Example: `Concreting done, Floor-2 bathrooms`",
        "buttons": [{"id":"siteops_log","title":"📝 Log Update"},{"id":"siteops_photo","title":"📷 Add Photo"}],
    },
    ("siteops","upload_photo"): {
        "text": "📷 Send a clear site photo; I’ll attach it to today’s log.",
        "buttons": [{"id":"siteops_photo","title":"📷 Upload Photo"}],
    },
    ("siteops","crew_update"): {
        "text": "👷 Share *role* and *count* to adjust the crew. Example: `Masons, 8`",
        "buttons": [{"id":"siteops_crew","title":"👷 Update Crew"}],
    },
    ("siteops","inventory_update"): {
        "text": "📦 Share *material, stock level, units*. Example: `Cement, 85, bags`",
        "buttons": [{"id":"siteops_stock","title":"📦 Update Stock"}],
    },
    ("siteops","alerts_followup"): {
        "text": "🔔 I’ll pull today’s follow-ups and risks for your site.",
        "buttons": [{"id":"siteops_summary","title":"📈 View Follow-ups"}],
    },
    ("siteops","site_summary"): {
        "text": "📈 I’ll compile your site summary. Share *site name*.",
        "buttons": [{"id":"siteops_summary","title":"📈 Get Summary"}],
    },

    # ("random","greeting"): {
    #     "text": "👋 Hey! I can help with *Procurement*, *Credit*, or *Site Ops*.",
    #     "buttons": [{"id":"procurement","title":"⚡ Get Quotes"},{"id":"credit_start","title":"💳 Use Credit"}],
    #     "qtype": "onboarding"
    # },
    # ("random","help"): {
    #     "text": "Try one:\n• “Need 120 bags OPC 53 Friday”\n• “Check credit limit”\n• “Concreting done floor-2 bathrooms”",
    #     "buttons": [{"id":"procurement","title":"⚡ Get Quotes"},{"id":"siteops","title":"🏗 Manage Site"}],
    #     "qtype": "onboarding"
    # },
    # ("random","pricing_plan"): {
    #     "text": "We’ll share pricing after your first pilot. Want to start a quick demo?",
    #     "buttons": [{"id":"demo_start","title":"▶️ Start Demo"}],
    #     "qtype": "onboarding"
    # },
    # ("random","demo_request"): {
    #     "text": "I’ll walk you through a guided demo. Which area?",
    #     "buttons": [{"id":"procurement","title":"🧱 Procurement"},{"id":"credit_start","title":"💳 Credit"}],
    #     "qtype": "onboarding"
    # },
    # ("random","human_help"): {
    #     "text": "I can connect you to support. Meanwhile, want to try a quick action?",
    #     "buttons": [{"id":"procurement","title":"⚡ Get Quotes"},{"id":"siteops","title":"🏗 Manage Site"}],
    #     "qtype": "onboarding"
    # },
}

CLASSIFY_PROMPT = """You are a strict WhatsApp message classifier for builders.
Output JSON ONLY with:
{
  "intent": "<procurement|credit|siteops|ambiguous>",
  "context": "<one valid context for that intent>",
  "slots": {
    "materials": null|string|array,
    "quantity": null|number,
    "units": null|string,
    "needed_by_date": null|string,
    "location": null|string,
    "vendor_name": null|string,
    "amount": null|number,
    "order_id": null|string,
    "application_id": null|string,
    "gst": null|string,
    "doc_type": null|"photo"|"invoice"|"boq"|"other",
    "doc_present": null|boolean,

    "site_name": null|string,
    "task": null|string,
    "floor_or_area": null|string,
    "crew_role": null|string,
    "crew_count": null|number,
    "material_name": null|string,
    "stock_level": null|number,
    "issue_type": null|string
  }
}

Allowed contexts:
- procurement: %PROC%
- credit: %CRED%
- siteops: %SITE%
- random: %RAND%

Rules:
- Choose exactly ONE intent and ONE context from the allowed lists.
- Use intent="ambiguous" ONLY if the message provides no clear cues.
- If intent="ambiguous", set context="help" if the users's message is unclear but is related to Procurement or credit or Siteops
- If intent="ambiguous", set context="chit-chat" if the users's message is unrelated and feels like a casual conversation, joking or light-hearted banter or anything else.
- Prefer the most actionable context.
- Infer obvious slots from the message; leave absent ones as null.
- If uncertain, use intent="random", context="help".
- Output JSON ONLY.

User message:
\"\"\"%MSG%\"\"\""""

def _format_prompt(user_text: str) -> str:
    return (CLASSIFY_PROMPT
        .replace("%PROC%", ", ".join(ALLOWED["procurement"]))
        .replace("%CRED%", ", ".join(ALLOWED["credit"]))
        .replace("%SITE%", ", ".join(ALLOWED["siteops"]))
        .replace("%MSG%", (user_text or "").strip())
    )

def _extract_json(text: str) -> Optional[dict]:
    """
    Extract the last valid JSON object from a model response.
    Handles code fences and extra prose; ignores braces in strings.
    """
    if not text:
        return None
    raw = (text or "").strip()

    # Strip code fences like ```json ... ``` or ``` ...
    if raw.startswith("```"):
        # remove leading ```
        raw = raw.lstrip("`")
        # drop optional language tag line
        nl = raw.find("\n")
        raw = raw[nl+1:] if nl != -1 else raw
        # remove trailing ```
        raw = raw.rstrip("`").rstrip()

    # Fast path: the whole thing is JSON
    try:
        return json.loads(raw)
    except Exception:
        pass

    # Scan for balanced {...} blocks
    last_obj = None
    i, n = 0, len(raw)
    while True:
        start = raw.find("{", i)
        if start == -1:
            break
        depth = 0
        j = start
        in_str = False
        esc = False
        while j < n:
            ch = raw[j]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == "\"":
                    in_str = False
            else:
                if ch == "\"":
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        candidate = raw[start:j+1]
                        try:
                            last_obj = json.loads(candidate)
                        except Exception:
                            pass
                        i = j + 1
                        break
            j += 1
        else:
            # no closing brace found
            break

    return last_obj

def _is_missing(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip()) or (isinstance(v, list) and len(v) == 0)

def _coerce_number(v: Any) -> Optional[Union[int, float]]:
    if v is None: return None
    if isinstance(v, (int, float)): return v
    try:
        # allow "120", "120.0", "1,200"
        s = str(v).replace(",", "").strip()
        return int(s) if s.isdigit() or re.fullmatch(r"-?\d+", s) else float(s)
    except Exception:
        return None

def _normalize_slots(slots: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(slots or {})
    for k in ["quantity","crew_count","stock_level"]:
        out[k] = _coerce_number(out.get(k))
    out["amount"] = _coerce_number(out.get("amount"))
    # Materials can be string or list; normalize "" -> None
    if isinstance(out.get("materials"), str) and not out["materials"].strip():
        out["materials"] = None
    # Doc type normalization
    if out.get("doc_type") is not None:
        dt = str(out["doc_type"]).lower()
        out["doc_type"] = dt if dt in {"photo","invoice","boq","other"} else "other"
    return out

def _merge_slots(old: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
    """Non-destructive merge preferring 'new' when set."""
    merged = dict(old or {})
    for k, v in (new or {}).items():
        if not _is_missing(v):
            merged[k] = v
    return merged

# ----------------------- Public API -----------------------
async def route_and_respond(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Classify the latest user message, extract slots, choose WhatsApp microcopy + buttons,
    mark missing slots, and update state non-destructively.
    """
    print("Convo Router :::::: Route and Respond:::: called")
    # Pull latest message text + metadata
    intent=""
    chosen_intent=""
    last_msg = state["messages"][-1] if state.get("messages") else {}
    user_msg_text = last_msg.get("content", "") or ""
    user_msg_type = last_msg.get("type") or ("image" if state.get("image_path") else "text")
    print("Convo Router :::::: Route and Respond:::: user message ", last_msg)
    if not user_msg_text and user_msg_type != "image":
        intent, context = FALLBACK["intent"], FALLBACK["context"]
        await _apply_state(state, intent, context, {}, required=[])
        return state
    
    # If image/doc present, pre-seed slots
    image_seed = {}
    if user_msg_type == "image" or state.get("image_path"):
         image_seed = {"doc_present": True, "doc_type": "photo"}
    print("Convo Router :::::: Route and Respond:::: Formatting Prompt")
    prompt = _format_prompt(user_msg_text)
    resp = await _llm.ainvoke([
        SystemMessage(content="Classify into intent + context; extract slots. Return JSON only."),
        HumanMessage(content=prompt)
    ])
    print("Convo Router :::::: Route and Respond:::: LLM Output", resp.content)
    data = _extract_json(resp.content) #or {"intent": "random", "context": "help", "slots": {}}
    intent = data.get("intent")
    context = data.get("context")
    print("Convo Router :::::: Route and Respond:::: Found Intetnand context first- ", intent, context)
    # Guardrails on intent/context
    if intent not in ALLOWED or context not in ALLOWED[intent]:
        intent, context = FALLBACK["intent"], FALLBACK["context"]

    # Normalize + merge slots with any prior
    new_slots = _normalize_slots(data.get("slots", {}) or {})
    new_slots = _merge_slots(image_seed, new_slots)
    prior_slots = state.get("extracted_slots", {})
    merged_slots = _merge_slots(prior_slots, new_slots)
    
    
    
  
    chosen_intent = intent 
    if intent == "ambiguous":
         
         chosen_intent = state.get("last_known_intent") or FALLBACK["intent"]
         #context = "help" 

    state["intent_context"] = context
    print("Convo Router :::::: Route and Respond:::: Found chosen_intent and  context - ", chosen_intent, context)
    
    #if state["last_known_intent"] not in {"procurement","credit","siteops"} and chosen_intent =="random":

 
    if chosen_intent =="procurement" and context in {"start_order","order_followup","upload","upload_doc","help", "chit-chat"}:
        print("Convo Router :::::: Route and Respond:::: PRocurement intent - trying_to_understand_process", "get_quotes")
        from agents.procurement_agent import run_procurement_agent
        
        try: 
            async with AsyncSessionLocal() as session:
                crud = ProcurementCRUD(session)
                # NOTE: This returns state; respect that contract
                return await run_procurement_agent(state, config={"configurable": {"crud": crud}})
        except Exception as e:
            print("Convo Router :::::: Error in Procurement run procurement agent:", e)
    #Route directly to the agent for specific contexts
    if chosen_intent =="credit" and context in {"limit_or_kyc","pay_vendor","status_or_repay", "help", "chit-chat"}:
        from agents.credit_agent import run_credit_agent
       
        try:
            async with AsyncSessionLocal() as session:
                crud = CreditCRUD(session)
                # NOTE: This returns state; respect that contract
                return await run_credit_agent(state, config={"configurable": {"crud": crud}})
        except Exception as e:
            print("Convo Router :::::: Error in random classify_and_respond:", e)
   
 
   # Remaining contexts might need additional infomration from user beofre passing tot he agent or its just faster to directly resopnd to them withoug having to touch the agent.
    required = REQUIRED_SLOTS.get((intent, context), [])
    await _apply_state(state, intent, context, merged_slots, required)
    # Persist slots back to state
    state["extracted_slots"] = merged_slots
    return state
 
async def _apply_state(state: Dict[str, Any], intent: str, context: str, slots: Dict[str, Any], required: List[str]) -> None:
    tpl = TEMPLATES.get((intent, context), TEMPLATES[("random","help")])

    missing = [k for k in required if _is_missing(slots.get(k))]

    base_text = tpl["text"]
    if missing:
        ask = ", ".join([f"*{k.replace('_',' ')}*" for k in missing[:3]])
        base_text += f"\n\n➡️ Share {ask} to continue."

    state["latest_msg_intent"] = intent
    state["intent_context"] = context
    state["missing_slots"] = missing
    state["latest_respons"] = base_text
    state["uoc_question_type"] = tpl.get("qtype", state.get("uoc_question_type"))
    # Force explicit next message type for button UIs
    state["uoc_next_message_type"] = "button"
    state["uoc_next_message_extra_data"] = tpl.get("buttons", [])
    state["needs_clarification"] = bool(missing)
