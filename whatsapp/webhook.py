from fastapi import APIRouter, Request
from orchastrator.core import builder_graph 
import sys
from fastapi.responses import PlainTextResponse
import os
from unitofconstruction.uoc_manager import UOCManager
router = APIRouter()
from app.logging_config import logger
import requests

WHATSAPP_API_URL = "https://graph.facebook.com/v19.0/651218151406174/messages"
ACCESS_TOKEN = "EAAIMZBw8BqsgBO0L6UPZCmS48FShZCeE6oHuIhycRPhRWObZC808vGV80fDez1lhfcI5RkPjd82ZCjsLq4ZBHYw4BZBbtg9iMbI2ODn8RxTffMsU0nwoZCY9dZCdvZBByFDuLNEgHXTh1m8qFZCTYZBbj7n30x2CZCByssZB9AK0t1WAZAT6LXJwYq2gXWrxwwZBxqp4PFBlzuAicEsZCgItNe3BVomNzgtSogaS1LwZDZD"  
#ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")

def send_whatsapp_message(to_number: str, message_text: str):
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "text",
        "text": {"body": message_text}
    }

    response = requests.post(WHATSAPP_API_URL, headers=headers, json=payload)
    print(f"Sent message to {to_number}. Response:", response.status_code, response.text)

logger.info("[STARTUP] webhook.py loaded successfully.")
logger.info("Now testing the webhook route.")

def download_whatsapp_image(media_id: str) -> str:
    #Get media URL
    media_info_url = f"https://graph.facebook.com/v19.0/{media_id}"
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
    res = requests.get(media_info_url, headers=headers)
    
    if res.status_code != 200:
        print("Failed to get media URL:", res.text)
        return None
    
    media_url = res.json().get("url")
    
    #Downloading media content
    image_data = requests.get(media_url, headers=headers).content
    filename = f"C:/Users/koppi/OneDrive/Desktop/Bab.ai/{media_id}.jpg"
    
    with open(filename, "wb") as f:
        f.write(image_data)
    
    print(f"Saved image to {filename}")
    return filename
      
async def run_agent_by_name(agent_name: str, state: dict) -> dict:
    """
    Routes the state to the correct agent based on the name.
    """
    if agent_name == "siteops":
        from agents.siteops_agent import run_siteops_agent
        return await run_siteops_agent(state)

    elif agent_name == "procurement":
        from agents.procurement_agent import run_procurement_agent
        return await run_procurement_agent(state)

    elif agent_name == "credit":
        from agents.credit_agent import run_credit_agent
        return await run_credit_agent(state)

    else:
        raise ValueError(f"Unknown agent name: {agent_name}")
    

@router.get("/webhook")
async def verify(request: Request):
    print("GET /webhook called")
    sys.stdout.flush()
    params = dict(request.query_params)

    expected_token = "babai"

    if params.get("hub.verify_token") == expected_token:
        return PlainTextResponse(params.get("hub.challenge", "0"))
    
    return PlainTextResponse("Invalid token", status_code=403)


@router.post("/webhook")
async def whatsapp_webhook(request: Request):
    try:
        logger.info("Entered Webhook route")
        data = await request.json()
        #msg = data["entry"][0]["changes"][0]["value"]["messages"][0]

        entry = data["entry"][0]["changes"][0]["value"]
        if "messages" not in entry:
            logger.info("No messages found in the entry.")
            return {"status": "ignored", "reason": "No messages found"}
        msg = entry["messages"][0]


        print("Received message:", msg)
        sender_id = msg["from"]
        msg_type = msg["type"]

        state = {
            "sender_id": sender_id,
            "messages": [],  # We'll fill this based on type
            #"uoc_last_called_by": None,  # Without declaring this variable here, it will be undefined in the first call to UOCManager
            #"uoc_pending_question": False,
        }
        
        if msg_type == "text":
            text = msg["text"]["body"]
            state["messages"].append({"role": "user", "content": text})

        elif msg_type == "image":
            media_id = msg["image"]["id"]
            caption = msg["image"].get("caption", "")

            image_path = download_whatsapp_image(media_id)

            state["messages"].append({
                "role": "user",
                "content": f"[Image ID: {media_id}] {caption}"  # Or you could pass separately
            })
            state["media_id"] = media_id  
            state["image_path"] = image_path  
            state["caption"] = caption

            print("#################################Image downloaded and saved at:", image_path)

        else:
            return {"status": "ignored", "reason": f"Unsupported message type {msg_type}"}

        # we are checking what the message is about and whom to call - orchastrator or agent; we call orcha strator first and if the respone is regarding an ongoin converstainpn to gain more context intiated by agetns the respone is direclty routed to agetns insted of orchestrator
        #result = await builder_graph.ainvoke(state)
                # First message, go to orchestrator
        # if not state.get("uoc_pending_question", False):
        #     result = await builder_graph.ainvoke(state)
        # The above flow has logic flaw becuase the followup question in set high by the uoc manager but this is interpreted as a frost time message and routed to orchestrated.
        
        #if not state.get("uoc_last_called_by") and state.get("uoc_pending_question", False):
        print("UOC pending question:", state.get("uoc_pending_question", False))
        print("UOC last called by:", state.get("uoc_last_called_by", "unknown"))

        if  state.get("uoc_last_called_by") is None:  # checinkg it any agent called from uoc manager whic imokies it is a followup task 
            print("Calling agent directly")
            result = await builder_graph.ainvoke(state)
            #state["uoc_last_called_by"] = result.get("agent_name", None)  # Set the agent name for future reference
    
                 
        # Agent returned with UOC pending
        #if state.get("uoc_pending_question", False):
        if  state.get("uoc_last_called_by") is not None:
            # Call UOC Manager
            print("Calling UOC Manager")
            state = await UOCManager.run(state, called_by=state.get("uoc_last_called_by", "unknown"))

            # Still unclear, ask user
            if state.get("uoc_pending_question", False):
                send_whatsapp_message(sender_id, state["messages"][-1]["content"])
                return {"status": "waiting_for_user"}
            
            # UOC is now confident, resume agent
            agent_name = state.get("uoc_last_called_by", "unknown")
            result = await run_agent_by_name(agent_name, state)
            #state["uoc_last_called_by"] = result.get("agent_name", None)

        # Send final reply
        response_msg = result["messages"][-1]["content"]
        send_whatsapp_message(sender_id, response_msg)
        logger.info("Final response sent to WhatsApp")
        return {"status": "done", "reply": response_msg}

    
    except Exception as e:
        logger.error("Error in WhatsApp webhook:{e}")
        #logger.error(e, exc_info=True)
        return {"status": "error", "message": str(e)}
