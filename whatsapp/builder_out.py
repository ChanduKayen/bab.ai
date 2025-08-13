import os
import requests
from dotenv import load_dotenv

load_dotenv(override=True)
WHATSAPP_API_URL = "https://graph.facebook.com/v19.0/712076848650669/messages"
#ACCESS_TOKEN = "EAAIMZBw8BqsgBO4ZAdqhSNYjSuupWb2dw5btXJ6zyLUGwOUE5s5okrJnL4o4m89b14KQyZCjZBZAN3yZBCRanqLC82m59bGe4Rd2BPfRe3A3pvGFZCTf2xB7a6insIzesPDVMLIw4gwlMkkz7NGl3ZBLvP5MU8i3mZBMmUBShGeQkSlAyRhsXJtlsg8uGaAfYwTid8PZAGBKnbOR3LFpCgBD8ZCIMJh9xI0sHWy"  

ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")



def whatsapp_output(to_number: str, message_text: str, message_type="plain", extra_data=None):
    if message_type == "plain": 
        send_plain_message(to_number, message_text)
    elif message_type == "button":
        send_button_message(to_number, message_text, extra_data)
    elif message_type == "list":
        send_list_message(to_number, message_text, extra_data)
    elif message_type == "link_cta":
        send_link_cta_message(to_number, message_text, extra_data)
    else:
        raise ValueError(f"Unknown message_type: {message_type}")

def send_plain_message(to_number, message_text):
    headers = _get_headers()
    payload = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "text",
        "text": {"body": message_text}
    }
    _post_message(headers, payload) 

def send_button_message(to_number, message_text, buttons):
    headers = _get_headers()
    button_objects = [{"type": "reply", "reply": {"id": btn["id"], "title": btn["title"]}} for btn in buttons]
    payload = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "interactive",
        "interactive": {
            "type": "button", 
            "body": {"text": message_text},
            "action": {"buttons": button_objects}
        } 
    }
    _post_message(headers, payload)

def send_link_cta_message(to_number, message_text, cta_button):
    
    headers = _get_headers()
    print(f"Sending link CTA message to {to_number} with text: {message_text} and button: {cta_button}")
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_number,
        "type": "interactive",
        "interactive": {
            "type": "cta_url",
            "body": {
                "text": message_text
            },
            "action": {
                "name": "cta_url",
                "parameters": 
                    {
                        "display_text": cta_button["display_text"],
                        "url": cta_button["url"]
                    }
                
            }
        }
    }

    _post_message(headers, payload)

def send_list_message(to_number, message_text, sections): 
    headers = _get_headers()

    if isinstance(sections, list) and sections and isinstance(sections[0], str):
        sections = [{
            "title": "Options",
            "rows": [ 
                {"id": opt.lower().replace(" ", "_"), "title": opt}
                for opt in sections
            ]
        }]
    elif isinstance(sections, list) and sections and "rows" not in sections[0]:
        raise ValueError("Invalid list message structure. 'rows' missing in section.")

    payload = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {"type": "text", "text": "Options"},
            "body": {"text": message_text},
            "footer": {"text": "Please select one"},
            "action": {"button": "View Options", "sections": sections}
        }
    }
    _post_message(headers, payload)








def _get_headers():
    return {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json" 
    }

def _post_message(headers, payload):
    print("📤 Sending payload to WhatsApp:")
    response = requests.post(WHATSAPP_API_URL, headers=headers, json=payload)
    print(f"📥 WhatsApp API response: {response.status_code} {response.text}")
    if response.status_code != 200:
        raise Exception(f"Failed to send message: {response.status_code} {response.text}")



def send_typing_indicator(to_number: str, duration: int = 5):
    """
    Sends a typing indicator to the user for a set duration in seconds.
    Note: WhatsApp typing indicator usually lasts a few seconds by default.
    """
    headers = _get_headers()
    payload = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "typing",
        "typing": {
            "status": "typing"  # You can also send 
        }
    }

    print(f"💬 Sending typing indicator to {to_number} for {duration} seconds...")
    response = requests.post(WHATSAPP_API_URL, headers=headers, json=payload)
    print(f"📥 Typing indicator response: {response.status_code} {response.text}")

    # Optionally wait 
    if duration > 0:
        import time
        time.sleep(duration)
        payload["typing"]["status"] = "paused"
        requests.post(WHATSAPP_API_URL, headers=headers, json=payload)
