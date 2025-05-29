import requests

WHATSAPP_API_URL = "https://graph.facebook.com/v19.0/651218151406174/messages"
ACCESS_TOKEN = "EAAIMZBw8BqsgBOzp2wEbgEHYfjZC66ZC7awz0X8MOKdDOlXHZBrHeQLYDrm86BxJoliZBMv6asJbFMtOwGemZAxujYw3Vsqzr1APZCfdc0C9WIeW9pnskZCSkDI95Mw1XGsxtxxZCzodcAzKJp0mpnQmOjAvpKbZC2SlnZBiQMZC9ObDvZB3cZCzZBaZCQLoBb3pMG3iBffmlPzU6ZC9oyBHPZALicbolC4dDVc81QmoMZD"  


def whatsapp_output(to_number: str, message_text: str, message_type="plain", extra_data=None):
    if message_type == "plain":
        send_plain_message(to_number, message_text)
    elif message_type == "button":
        send_button_message(to_number, message_text, extra_data)
    elif message_type == "list":
        send_list_message(to_number, message_text, extra_data)
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
    response = requests.post(WHATSAPP_API_URL, headers=headers, json=payload)
    print(f"WhatsApp API response: {response.status_code} {response.text}")
    if response.status_code != 200:
        raise Exception(f"Failed to send message: {response.status_code} {response.text}")