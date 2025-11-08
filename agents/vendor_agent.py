# agents/vendor_agent.py
"""Vendor-facing conversational agent.

The vendor agent mirrors the procurement agent orchestration pipeline but keeps
its domain specific to supplier onboarding, quote follow ups, and order
acknowledgements.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Iterable, List, Optional

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.db import get_sessionmaker
from database.procurement_crud import ProcurementCRUD
from models.chatstate import AgentState
from users import user_onboarding_manager
from whatsapp.builder_out import whatsapp_output

# ---------------------------------------------------------------------------
# Environment / LLM bootstrap
# ---------------------------------------------------------------------------
load_dotenv()

AsyncSessionLocal = get_sessionmaker()

_MODEL = os.getenv("OPENAI_VENDOR_MODEL", "gpt-5")


def _chat_llm(model: str = _MODEL) -> ChatOpenAI:
    """Factory that keeps JSON-mode safety switches aligned with procurement."""
    safe_kwargs = {"model_kwargs": {"response_format": {"type": "json_object"}}}
    return ChatOpenAI(model=model, openai_api_key=os.getenv("OPENAI_API_KEY"), **safe_kwargs)


llm = _chat_llm()

# ---------------------------------------------------------------------------
# JSON helpers (ported from procurement agent)
# ---------------------------------------------------------------------------
_CODE_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE | re.MULTILINE)


def _extract_json_blobs(text: str) -> List[str]:
    blobs: List[str] = []
    stack: List[str] = []
    start = -1
    in_string = False
    escape = False

    for idx, ch in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
            continue

        if ch in "{[":
            if not stack:
                start = idx
            stack.append(ch)
            continue

        if ch in "}]" and stack:
            opener = stack[-1]
            if (opener == "{" and ch == "}") or (opener == "[" and ch == "]"):
                stack.pop()
                if not stack and start >= 0:
                    blobs.append(text[start : idx + 1])
                    start = -1
            else:
                # mismatched braces; reset search
                stack.clear()
                start = -1
    return blobs


def safe_json(text: Optional[str], default: Optional[Any] = None) -> Any:
    if text is None:
        return {} if default is None else default

    cleaned = _CODE_FENCE.sub("", text).strip()
    try:
        return json.loads(cleaned)
    except Exception:
        pass

    parsed: List[Any] = []
    for blob in _extract_json_blobs(cleaned):
        try:
            val = json.loads(blob)
        except Exception:
            continue
        if isinstance(val, list):
            parsed.extend(val)
        else:
            parsed.append(val)

    if not parsed:
        return {} if default is None else default
    return parsed[0] if len(parsed) == 1 else parsed


# ---------------------------------------------------------------------------
# Static scaffolding
# ---------------------------------------------------------------------------
_DEFAULT_VENDOR_PROFILE: Dict[str, Any] = {
    "company_name": "",
    "contact_name": "",
    "contact_phone": "",
    "contact_email": "",
    "gst_number": "",
    "materials": [],
    "service_regions": [],
    "delivery_speed": "",
    "credit_terms": "",
    "catalog_links": [],
    "notes": "",
}

_REQUIRED_VENDOR_FIELDS: Iterable[str] = (
    "company_name",
    "contact_name",
    "contact_phone",
    "materials",
    "service_regions",
)

_VENDOR_WELCOME_CTA: List[Dict[str, str]] = [
   # {"id": "vendor_onboarding", "title": "\U0001f3ed Vendor Onboarding"},
    {"id": "vendor_portal", "title": "\U0001f4cb Assigned RFQs"},
    {"id": "vendor_support", "title": "\U0001f198 Help"},
]

_VENDOR_PRIMARY_CTA: List[Dict[str, str]] = [
    {"id": "vendor_portal", "title": "\U0001f6e0 Manage Quotes"},
    {"id": "vendor_support", "title": "\U0001f4e9 Contact Support"},
]


def _merge_profile(profile: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    merged = dict(_DEFAULT_VENDOR_PROFILE)
    if profile:
        for key, value in profile.items():
            merged[key] = value
    return merged


async def _ensure_vendor_role(state: AgentState) -> None:
    if (state.get("user_category") or "").lower() == "vendor":
        return
    state["user_category"] = "vendor"
    try:
        async with AsyncSessionLocal() as session:
            await user_onboarding_manager.set_user_role(
                session,
                sender_id=state.get("sender_id", ""),
                role="vendor",
            )
    except Exception as exc:
        print("Vendor Agent:::: failed to persist user role:", exc)


def _last_user_message(state: AgentState) -> str:
    if not state.get("messages"):
        return ""
    for message in reversed(state["messages"]):
        if message.get("role") == "user":
            return (message.get("content") or "").strip()
    return (state["messages"][-1].get("content") or "").strip()


def _missing_vendor_fields(profile: Dict[str, Any]) -> List[str]:
    missing: List[str] = []
    for key in _REQUIRED_VENDOR_FIELDS:
        value = profile.get(key)
        if isinstance(value, list):
            if not value:
                missing.append(key)
        elif not value:
            missing.append(key)
    return missing


# ---------------------------------------------------------------------------
# Interactive flows
# ---------------------------------------------------------------------------
async def collect_vendor_profile_interactively(state: AgentState) -> AgentState:
    profile = _merge_profile(state.get("vendor_profile"))
    state["vendor_profile"] = profile

    system_prompt = (
        "You are Thirtee's Vendor Success AI. Gather supplier onboarding details. "
        "Always reply with JSON containing:\n"
        "  vendor_profile (object with the fields you know),\n"
        "  latest_respons (string message for WhatsApp),\n"
        "  next_message_type (plain, button, link_cta),\n"
        "  next_message_extra_data (list or object for CTA payload),\n"
        "  needs_clarification (bool),\n"
        "  uoc_confidence (low/medium/high).\n"
        "Clarify missing required fields: company_name, contact_name, contact_phone, "
        "materials, service_regions. Keep responses short and actionable."
    ) 

    messages: List[HumanMessage | SystemMessage] = [SystemMessage(content=system_prompt)]
    for item in state.get("messages", [])[-12:]:  # trim noise while keeping context
        text = (item.get("content") or "").strip()
        if text:
            messages.append(HumanMessage(content=text))

    messages.append(
        HumanMessage(
            content="CURRENT_VENDOR_PROFILE\n" + json.dumps(profile, ensure_ascii=False)
        )
    )

    try:
        llm_raw = await llm.ainvoke(messages)
    except Exception as exc:
        print("Vendor Agent:::: collect_vendor_profile_interactively ::: LLM error", exc)
        state.update(
            latest_respons="Sorry, I could not record that. Could you share the vendor details once more?",
            uoc_next_message_type="plain",
            uoc_question_type="vendor_onboarding",
            needs_clarification=True,
            uoc_confidence="low",
        )
        return state

    parsed = safe_json(llm_raw.content, {})
    if isinstance(parsed, list):
        parsed = parsed[0] if parsed else {}
    if not isinstance(parsed, dict):
        parsed = {}

    updated_profile = parsed.get("vendor_profile")
    if isinstance(updated_profile, dict):
        state["vendor_profile"] = _merge_profile(updated_profile)

    state.update(
        latest_respons=parsed.get("latest_respons")
        or "Noted. Could you confirm the remaining vendor details?",
        uoc_next_message_type=parsed.get("next_message_type", "plain"),
        uoc_next_message_extra_data=parsed.get("next_message_extra_data") or [],
        needs_clarification=parsed.get("needs_clarification", True),
        uoc_confidence=parsed.get("uoc_confidence", "low"),
        uoc_question_type="vendor_onboarding",
        agent_first_run=False,
    )

    if not isinstance(state["uoc_next_message_extra_data"], list):
        state["uoc_next_message_extra_data"] = [state["uoc_next_message_extra_data"]]

    missing = _missing_vendor_fields(state["vendor_profile"])
    if missing and not state.get("needs_clarification", True):
        # force clarification if LLM missed key slots
        prompt = ", ".join(missing[:3])
        state.update(
            needs_clarification=True,
            latest_respons=(
                f"I still need *{prompt}* to finish onboarding. Share those details?"
            ),
            uoc_next_message_type="plain",
        )

    if not missing and state.get("uoc_confidence") == "high":
        state["vendor_onboarded"] = True
        state["needs_clarification"] = False
        state.setdefault("uoc_next_message_type", "button")
        if not state.get("uoc_next_message_extra_data"):
            state["uoc_next_message_extra_data"] = _VENDOR_PRIMARY_CTA
        congrats = (
            "\U0001f389 You are all set! We'll notify you whenever a new RFQ is assigned."
        )
        state["latest_respons"] = parsed.get("latest_respons") or congrats

    return state


# ---------------------------------------------------------------------------
# Handler functions for CTAs
# ---------------------------------------------------------------------------
async def handle_vendor_onboarding(
    state: AgentState,
    crud: Optional[ProcurementCRUD],
    extra_data: Optional[Any] = None,
) -> AgentState:
    state["intent"] = "vendor_onboarding"
    state["agent_first_run"] = False
    return await collect_vendor_profile_interactively(state)
    

async def handle_vendor_portal(
    state: AgentState,
    crud: Optional[ProcurementCRUD],
    extra_data: Optional[Any] = None,
) -> AgentState:
    base = os.getenv("VENDOR_PORTAL_URL_BASE") or "https://vendor.thirtee.app/dashboard"
    sender_id = state.get("sender_id", "")
    query = f"?senderId={sender_id}" if sender_id else ""
    url = f"{base}{query}"
    state.update(
        intent="vendor_portal",
        latest_respons="Open your live vendor workspace to view assigned RFQs.",
        uoc_next_message_type="link_cta",
        uoc_next_message_extra_data={"display_text": "Open Vendor Console", "url": url},
        needs_clarification=False,
        agent_first_run=False,
        uoc_question_type="vendor_new_user_flow",
    )
    return state


async def handle_vendor_quotes(
    state: AgentState,
    crud: Optional[ProcurementCRUD],
    extra_data: Optional[Any] = None,
) -> AgentState:
    state.update(
        intent="vendor_quotes",
        latest_respons=(
            "Here are the latest quote requests linked to you."
            " Use the portal to submit prices or update availability."
        ),
        uoc_next_message_type="button",
        uoc_next_message_extra_data=_VENDOR_PRIMARY_CTA,
        needs_clarification=False,
        agent_first_run=False,
        uoc_question_type="vendor_new_user_flow",
    )
    return state


async def handle_vendor_support(
    state: AgentState,
    crud: Optional[ProcurementCRUD],
    extra_data: Optional[Any] = None,
) -> AgentState:
    support_contact = os.getenv("VENDOR_SUPPORT_NUMBER", "9988776655")
    state.update(
        intent="vendor_support",
        latest_respons=(
            "No problem. Our vendor success team is available on WhatsApp "
            f"*{support_contact}*. Describe your issue and we'll step in."
        ),
        uoc_next_message_type="button",
        uoc_next_message_extra_data=_VENDOR_PRIMARY_CTA,
        needs_clarification=False,
        agent_first_run=False,
        uoc_question_type="vendor_new_user_flow",
    )
    return state


async def handle_main_menu(
    state: AgentState,
    crud: Optional[ProcurementCRUD],
    extra_data: Optional[Any] = None,
) -> AgentState:
    state.update(
        latest_respons="What would you like to do next?",
        uoc_next_message_type="button",
        uoc_next_message_extra_data=_VENDOR_WELCOME_CTA,
        needs_clarification=True,
        uoc_question_type="vendor_new_user_flow",
    )
    return state


_VENDOR_HANDLER_MAP = {
    "vendor_onboarding": handle_vendor_onboarding,
    "vendor_portal": handle_vendor_portal,
    "vendor_quotes": handle_vendor_quotes,
    "vendor_support": handle_vendor_support,
    "main_menu": handle_main_menu,
}


# ---------------------------------------------------------------------------
# Vendor acknowledgement buttons (confirm / decline)
# ---------------------------------------------------------------------------
async def _handle_vendor_ack(state: AgentState, action: str) -> AgentState:
    ctx = state.get("vendor_ack_context") or {}
    req_id = ctx.get("request_id")
    vendor_id = ctx.get("vendor_id")

    if not req_id or not vendor_id:
        state.update(
            latest_respons="Sorry, the order context expired. Please ask the team to resend the link.",
            uoc_next_message_type="plain",
            needs_clarification=False,
        )
        return state

    try:
        async with AsyncSessionLocal() as session:
            crud = ProcurementCRUD(session)
            buyer_id = await crud.get_sender_id_from_request(str(req_id))
    except Exception as exc:
        print("Vendor Agent:::: vendor acknowledgement failed:", exc)
        state.update(
            latest_respons="We could not record your response. Please try again shortly.",
            uoc_next_message_type="plain",
            needs_clarification=False,
        )
        return state

    if action == "vendor_confirm":
        if buyer_id:
            whatsapp_output(
                buyer_id,
                f"\U00002705 Vendor confirmed fulfilment for request {req_id}.",
                message_type="plain",
            )
        state.update(
            latest_respons="Thanks! Order confirmation recorded. We will coordinate logistics next.",
            uoc_next_message_type="plain",
            needs_clarification=False,
        )
    else:
        try:
            async with AsyncSessionLocal() as session:
                crud = ProcurementCRUD(session)
                await crud.vendor_decline_and_reopen(req_id, vendor_id)
        except Exception as exc:
            print("Vendor Agent:::: vendor decline flow failed:", exc)
        if buyer_id:
            whatsapp_output(
                buyer_id,
                f"\U000026A0 Vendor cannot fulfill request {req_id}. We are reopening it.",
                message_type="plain",
            )
        state.update(
            latest_respons="Understood. We have let the buyer know you cannot fulfill this order.",
            uoc_next_message_type="plain",
            needs_clarification=False,
        )
    return state


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------
async def new_vendor_flow(
    state: AgentState,
    crud: Optional[ProcurementCRUD],
) -> AgentState:
    last_msg = _last_user_message(state).lower()

    if last_msg in {"vendor_confirm", "vendor_cannot_fulfill"}:
        return await _handle_vendor_ack(state, last_msg)

    if state.get("agent_first_run", True) :
        state.update(
            latest_respons=(
                "\U0001f3ed *Welcome to Thirtee Supplier Hub!*\n\n"
                "Share a quick profile so we can match the right RFQs:\n"
                "1. Company & GST\n2. Materials you supply\n3. Service regions\n4. Primary contact\n"
            ),
            uoc_next_message_type="button",
            uoc_next_message_extra_data=_VENDOR_WELCOME_CTA,
            needs_clarification=True,
            agent_first_run=False,
            uoc_question_type="vendor_new_user_flow",
        )
        return state

    profile = _merge_profile(state.get("vendor_profile"))
    missing = _missing_vendor_fields(profile)

    if missing and state.get("intent") != "vendor_onboarding":
        prompt = ", ".join(missing[:3])
        state.update(
            latest_respons=(
                f"I still need *{prompt}* to finish your onboarding. Could you share those?"
            ),
            uoc_next_message_type="plain",
            needs_clarification=True,
            uoc_question_type="vendor_onboarding",
        )
        return state

    state.update(
        latest_respons="Great! You're onboarded. Choose what you'd like to do next.",
        uoc_next_message_type="button",
        uoc_next_message_extra_data=_VENDOR_PRIMARY_CTA,
        needs_clarification=True,
        uoc_question_type="vendor_new_user_flow",
    ) 
    return state


async def run_vendor_agent(state: AgentState, config: Dict[str, Any]) -> AgentState:
    print("Vendor Agent:::: run_vendor_agent called")
    print("Vendor Agent:::: state =>", state)
    print("Vendor Agent:::: config =>", config)

    await _ensure_vendor_role(state)

    crud: Optional[ProcurementCRUD] = None
    try:
        crud = config.get("configurable", {}).get("crud")
    except Exception:
        crud = None

    last_msg = _last_user_message(state).lower()
    if last_msg in _VENDOR_HANDLER_MAP:
        return await _VENDOR_HANDLER_MAP[last_msg](state, crud, state.get("uoc_next_message_extra_data"))

    return await new_vendor_flow(state, crud)

