# agents/siteops_agent.py
# --------------------------------------------------
# Collects one site update (text and/or image),
# ➊ makes a single-sentence “site note”,
# ➋ pulls context tags,
# ➌ passes control to UOCManager,
# ➍ runs the reasoning LLM.
# --------------------------------------------------

import os, json, base64, openai, random
from typing import Dict, Tuple, Optional
from datetime import datetime
from dotenv import load_dotenv

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

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


# ---------------------------------------------------------------------------
# Helper 0 · encode image
# ---------------------------------------------------------------------------
def encode_image_base64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


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
                                    //   • includes ONE fitting emoji (👍, 👀, ⚠️, ✅, 🛠️ … ..choose uniwuley and sublte)
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

# ---------------------------------------------------------------------------
# Main public entry
# ---------------------------------------------------------------------------
async def run_siteops_agent(state: AgentState) -> AgentState:
    print("SiteOps Agent:::: run_siteops_agent : called")

    # ---------- 1 · Summarise update & build context ----------
    ctx_block = get_context_and_tags(state)
    print("SiteOps Agent:::: run_siteops_agent : ctx_block:", ctx_block)
    state["context"] = ctx_block
    #state["context_tags"] = ctx_tags

    # ---------- 2 · UOC resolution (first run only) ----------
    if state.get("agent_first_run", True):
        print("SiteOps Agent:::: run_siteops_agent : agent_first_run is True")
        #note = state.get("siteops_quick_grasp", "")
        quick_msg = state["siteops_quick_grasp"]
        sender_id = state["sender_id"]

        print("SiteOps Agent:::: run_siteops_agent : quick_msg:", quick_msg)
        whatsapp_output(sender_id, quick_msg, message_type="plain")
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
