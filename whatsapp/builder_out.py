# whatsapp/builder_out.py

import os
import time
import requests
from typing import Any, Dict, List, Optional, Union
from dotenv import load_dotenv

"""
WhatsApp output helpers with optional image headers.

USAGE PATTERNS (state → whatsapp_output):

1) Plain text only:
   whatsapp_output(to, "Hello!", "plain")

   Plain with image (text becomes caption):
   whatsapp_output(to, "Here is your invoice.", "plain", {"image_url": "https://..."} )
   # or {"media_id": "123456..."}

2) Buttons:
   # Lightweight (no image):
   extra = [
       {"id": "rfq", "title": "Get Quotations"},
       {"id": "credit_use", "title": "Buy with Credit"},
   ]

   # With image: wrap into a dict and add image_url/media_id
   extra = {
       "buttons": [
           {"id": "rfq", "title": "Get Quotations"},
           {"id": "credit_use", "title": "Buy with Credit"},
       ],
       "image_url": "https://example.com/banner.jpg"
   }

3) List:
   # Lightweight (no image): pass simple list of section titles
   extra = ["Cement", "Steel", "RMC"]

   # Advanced (structured rows) without image:
   extra = [{
       "title": "Categories",
       "rows": [
           {"id": "cement", "title": "Cement"},
           {"id": "steel", "title": "Steel"},
       ]
   }]

   # With image:
   extra = {
       "sections": [{
           "title": "Categories",
           "rows": [
               {"id": "cement", "title": "Cement"},
               {"id": "steel", "title": "Steel"},
           ]
       }],
       "image_url": "https://example.com/categories.jpg"
   }

4) Link CTA:
   # Default (no image), dict with display_text + url:
   extra = {"display_text": "Open Order", "url": "https://www.bab-ai.com/orders/review-order?uuid=..."}

   # With image header:
   extra = {
       "display_text": "Open Order",
       "url": "https://www.bab-ai.com/orders/review-order?uuid=...",
       "image_url": "https://example.com/order-hero.png"
   }
"""

load_dotenv(override=True)
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
WHATSAPP_API_URL = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"
#ACCESS_TOKEN = "EAAIMZBw8BqsgBO4ZAdqhSNYjSuupWb2dw5btXJ6zyLUGwOUE5s5okrJnL4o4m89b14KQyZCjZBZAN3yZBCRanqLC82m59bGe4Rd2BPfRe3A3pvGFZCTf2xB7a6insIzesPDVMLIw4gwlMkkz7NGl3ZBLvP5MU8i3mZBMmUBShGeQkSlAyRhsXJtlsg8uGaAfYwTid8PZAGBKnbOR3LFpCgBD8ZCIMJh9xI0sHWy"  

ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
WABA_HEADER_TEXT_LIMIT = 60

def _has_media(extra_data: Optional[Dict[str, Any]]) -> bool:
    if not extra_data or not isinstance(extra_data, dict):
        return False
    return any(k in extra_data for k in ("image_url", "video_url", "document_url", "media_id"))

def _detect_media_type(extra_data: Dict[str, Any]) -> Optional[str]:
    """
    Decide media type for 'plain' messages and headers.
    Priority by explicit keys; fallback to media_type or image.
    """
    if "video_url" in extra_data:
        return "video"
    if "document_url" in extra_data:
        return "document"
    if "image_url" in extra_data:
        return "image"
    # Uploaded media: rely on media_type hint (default image)
    return (extra_data.get("media_type") or "image").lower()

def _media_obj_from_extra(extra_data: Dict[str, Any], kind: str) -> Dict[str, Any]:
    """
    Build the {<kind>: {id/link, [caption]}} node’s inner dict.
    kind in {"image","video","document"}.
    """
    node: Dict[str, Any] = {}
    caption = extra_data.get("caption")
    if extra_data.get("media_id"):
        node = {"id": extra_data["media_id"]}
        if caption and kind in ("video", "document"):
            node["caption"] = caption
        return node
    # URL mode
    url_key = f"{kind}_url"
    if extra_data.get(url_key):
        node = {"link": extra_data[url_key]}
        if caption and kind in ("video", "document"):
            node["caption"] = caption
        return node
    # Fallback empty
    return {}

def _extract_header_media(extra_data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Build an interactive header payload.
    Supports:
      - Text:     {"header_text": "..."}
      - Image:    {"image_url": "..."} or {"media_id": "...", "media_type": "image"}
      - Video:    {"video_url": "..."} or {"media_id": "...", "media_type": "video", "caption": "..."}
      - Document: {"document_url": "..."} or {"media_id": "...", "media_type": "document", "caption": "..."}
    """
    if not extra_data or not isinstance(extra_data, dict):
        return None

    # Text header
    if extra_data.get("header_text"):
        text = str(extra_data["header_text"])
        text = text if len(text) <= WABA_HEADER_TEXT_LIMIT else text[:WABA_HEADER_TEXT_LIMIT - 1] + "…"
        return {"type": "text", "text": text}

    if not _has_media(extra_data):
        return None

    kind = _detect_media_type(extra_data)
    if kind not in ("image", "video", "document"):
        return None

    inner = _media_obj_from_extra(extra_data, kind)
    if not inner:
        return None

    return { "type": kind, kind: inner }

# ---------- Public Entry ----------
def whatsapp_output(
    to_number: str,
    message_text: str,
    message_type: str = "plain",
    extra_data: Optional[Union[Dict[str, Any], List[Dict[str, str]], List[str]]] = None
):
    """
    Dispatch a WhatsApp message.
    - message_type: "plain" | "button" | "list" | "link_cta"
    - extra_data is optional and can be lightweight (lists) or dicts when image headers are needed.
    """
    if message_type == "plain":
        send_plain_message(to_number, message_text, extra_data=extra_data)
    elif message_type == "button":
        send_button_message(to_number, message_text, extra_data=extra_data)
    elif message_type == "list":
        send_list_message(to_number, message_text, extra_data=extra_data)
    elif message_type == "link_cta":
        send_link_cta_message(to_number, message_text, extra_data=extra_data)
    elif message_type == "template":
        send_template_message(to_number, extra_data)
    # extra_data must contain: template_name, language_code, and params
    
    else:
        raise ValueError(f"Unknown message_type: {message_type}")


# ---------- Message Senders ----------


def send_template_message(to_number: str, extra_data: Dict[str, Any]):
    """
    extra_data must include:
      - template_name: str
      - language_code: str (e.g., "en")
      - body_params: List[str]   # ordered for {{1}}..{{N}}
    Optional (for dynamic URL button):
      - button_param: str        # text appended to the base URL set in template
      - button_index: int        # default 0 if first button
    """
    headers = _get_headers()

    name = extra_data["template_name"]
    lang = extra_data.get("language_code", "en")
    body_params = extra_data.get("body_params", [])

    components = []
    if body_params:
        components.append({
            "type": "body",
            "parameters": [{"type": "text", "text": str(p)} for p in body_params]
        })

    if "button_param" in extra_data and extra_data["button_param"] is not None:
        components.append({
            "type": "button",
            "sub_type": "url",
            "index": str(extra_data.get("button_index", 0)),
            "parameters": [{"type": "text", "text": str(extra_data["button_param"])}]
        })

    payload = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "template",
        "template": {
            "name": name,
            "language": {"code": lang},
            "components": components
        }
    }
    _post_message(headers, payload)


def send_plain_message(
    to_number: str,
    message_text: str,
    extra_data: Optional[Dict[str, Any]] = None
):
    """
    If extra_data contains image_url/media_id, send IMAGE with caption=message_text.
    Else, send TEXT.
    """
    headers = _get_headers()

    if _has_image(extra_data):
        image_obj = _image_obj_from_extra(extra_data)
        caption = (extra_data or {}).get("caption", message_text) if message_text else (extra_data or {}).get("caption", "")
        payload = {
            "messaging_product": "whatsapp",
            "to": to_number,
            "type": "image",
            "image": {**image_obj, **({"caption": caption} if caption else {})}
        }
    else:
        payload = {
            "messaging_product": "whatsapp",
            "to": to_number,
            "type": "text",
            "text": {"body": message_text}
        }

    _post_message(headers, payload)


def send_button_message(
    to_number: str,
    message_text: str,
    extra_data: Optional[Union[Dict[str, Any], List[Dict[str, str]]]] = None
):
    """
    Accepts:
      - Lightweight (no image): extra_data is a list of {"id","title"}.
      - With image: extra_data is a dict containing:
          {"buttons": [...], "image_url"|"media_id": "..."}
    """
    headers = _get_headers()

    # Determine buttons + header
    if isinstance(extra_data, list):
        buttons = extra_data
        header_media = None
    else:
        buttons = (extra_data or {}).get("buttons")
        # Also accept dicts that are only the list (edge case)
        if buttons is None and isinstance(extra_data, dict) and "id" in extra_data or "title" in extra_data:
            # Single button dict mistakenly passed—normalize to list
            buttons = [extra_data]  # type: ignore
        header_media = _extract_header_media(extra_data)

    if not buttons:
        raise ValueError("send_button_message requires a list of buttons (either directly or under extra_data['buttons']).")

    button_objects = [{"type": "reply", "reply": {"id": b["id"], "title": b["title"]}} for b in buttons]

    interactive = {
        "type": "button",
        "body": {"text": message_text},
        "action": {"buttons": button_objects}
    }
    if header_media:
        interactive["header"] = header_media

    payload = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "interactive",
        "interactive": interactive
    }
    _post_message(headers, payload)


def send_list_message(
    to_number: str,
    message_text: str,
    extra_data: Optional[Union[Dict[str, Any], List[str], List[Dict[str, Any]]]] = None
):
    """
    Accepts:
      - Lightweight (no image): extra_data is a list of titles (["Cement","Steel"]) -> auto-sections.
      - Advanced (no image): extra_data is a list of sections with rows.
      - With image: extra_data is a dict containing:
          {"sections": <list or simple list>, "image_url"|"media_id": "..."}
    """
    headers = _get_headers()

    sections = None
    header_media = None

    if isinstance(extra_data, list):
        # Could be simple list of titles or already sections
        sections = _normalize_sections(extra_data)
    elif isinstance(extra_data, dict):
        header_media = _extract_header_media(extra_data)
        ed_sections = extra_data.get("sections")
        if ed_sections is None:
            # Allow passing sections directly as a list even if inside dict
            # e.g., {"sections": ["A","B"], "image_url":"..."}
            # If omitted, treat as error.
            raise ValueError("send_list_message requires sections (list) in extra_data['sections'] when dict is provided.")
        sections = _normalize_sections(ed_sections)
    else:
        raise ValueError("send_list_message requires extra_data as list or dict with 'sections'.")

    interactive = {
        "type": "list",
        "body": {"text": message_text},
        "footer": {"text": "Please select one"},
        "action": {"button": "View Options", "sections": sections}
    }

    # Default header when no image: WhatsApp permits omitting header entirely; keep it minimal
    if header_media:
        interactive["header"] = header_media

    payload = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "interactive",
        "interactive": interactive
    }
    _post_message(headers, payload)


def send_link_cta_message(
    to_number: str,
    message_text: str,
    extra_data: Optional[Dict[str, Any]] = None
):
    """
    Accepts:
      - Default (no image): extra_data = {"display_text": "...", "url": "..."}
      - With image: same dict plus "image_url"|"media_id"
    """
    if not extra_data or "display_text" not in extra_data or "url" not in extra_data:
        raise ValueError("send_link_cta_message requires extra_data['display_text'] and extra_data['url'].")

    headers = _get_headers()

    interactive = {
        "type": "cta_url",
        "body": {"text": message_text},
        "action": {
            "name": "cta_url",
            "parameters": {
                "display_text": extra_data["display_text"],
                "url": extra_data["url"]
            }
        }
    }

    header_media = _extract_header_media(extra_data)
    if header_media:
        interactive["header"] = header_media

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_number,
        "type": "interactive",
        "interactive": interactive
    }
    _post_message(headers, payload)





# ---------- Internal Helpers ----------
def _get_headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
def mark_read(inbound_wamid: str):
    """
    Mark the incoming WhatsApp message as 'read' (recommended UX).
    Safe to call only when you have a valid inbound message_id (wamid).
    """
    print(f"💬 Marking message {inbound_wamid} as read...")
    headers = _get_headers()
    payload = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": inbound_wamid
    }
    resp = requests.post(WHATSAPP_API_URL, headers=headers, json=payload, timeout=10)
    print(f"📥 Mark read response: {resp.status_code} {resp.text}")

def send_typing_indicator_meta(to_message_id: str):
    """
    Sends an official 'typing indicator' + 'read' status to WhatsApp Cloud API.
    Requires Graph API v21.0+.
    """
    PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
    ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")

    url = f"https://graph.facebook.com/v21.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }

    payload = {
        "messaging_product": "whatsapp",
        "status": "read",  # marks the inbound message as read
        "message_id": to_message_id,
        "typing_indicator": {
            "type": "text"  # tells Meta to display "typing…" for text reply
        },
    }

    print(f"💬 Sending typing indicator for message_id={to_message_id}...")
    response = requests.post(url, headers=headers, json=payload, timeout=10)
    print(f"📥 Response: {response.status_code} {response.text}")

    if not (200 <= response.status_code < 300):
        raise Exception(f"Failed to send typing indicator: {response.status_code} {response.text}")

def _post_message(headers: Dict[str, str], payload: Dict[str, Any]):
    print("📤 Sending payload to WhatsApp:")
    print(payload)
    response = requests.post(WHATSAPP_API_URL, headers=headers, json=payload)
    print(f"📥 WhatsApp API response: {response.status_code} {response.text}")
    if not (200 <= response.status_code < 300):
        raise Exception(f"Failed to send message: {response.status_code} {response.text}")


def _has_image(extra_data: Optional[Dict[str, Any]]) -> bool:
    return bool(extra_data and (extra_data.get("image_url") or extra_data.get("media_id")))


def _image_obj_from_extra(extra_data: Optional[Dict[str, Any]]) -> Dict[str, str]:
    if not extra_data:
        return {}
    if extra_data.get("media_id"):
        return {"id": extra_data["media_id"]}
    if extra_data.get("image_url"):
        return {"link": extra_data["image_url"]}
    return {}


def _extract_image_header(extra_data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Build an interactive header payload if image provided:
      {"type": "image", "image": {"link": "..."} }
    """
    if not _has_image(extra_data):
        return None
    return {"type": "image", "image": _image_obj_from_extra(extra_data)}


def _normalize_sections(
    sections: Union[List[str], List[Dict[str, Any]]]
) -> List[Dict[str, Any]]:
    """
    Accept:
      - Simple list of titles: ["Cement","Steel"] -> single section with rows
      - Structured sections: [{"title": "...","rows":[{"id":"cement","title":"Cement"}, ...]}]
    """
    if not isinstance(sections, list) or len(sections) == 0:
        raise ValueError("Sections must be a non-empty list.")

    if isinstance(sections[0], str):
        # Convert simple list of titles into rows with auto IDs
        rows = [{"id": s.lower().replace(" ", "_"), "title": s} for s in sections]  # type: ignore
        return [{"title": "Options", "rows": rows}]
    else:
        # Validate presence of rows in first section (best-effort)
        if "rows" not in sections[0]:
            raise ValueError("Invalid list structure. Expected 'rows' in first section.")
        return sections  # already structured
