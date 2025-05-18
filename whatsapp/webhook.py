from fastapi import APIRouter, Request
from orchastrator.core import builder_graph 
import sys
from fastapi.responses import PlainTextResponse
import os
from unitofconstruction.uoc_manager import UOCManager
router = APIRouter()
from app.logging_config import logger
import requests
import redis
import asyncio
import json
import agents.siteops_agent as siteops_agent
from whatsapp.builder_out import whatsapp_output
WHATSAPP_API_URL = "https://graph.facebook.com/v19.0/651218151406174/messages"
ACCESS_TOKEN = "EAAIMZBw8BqsgBOZBP0c9k3eITWoOyWIOJLYz2u1DtQcKdpWv6aK36o63j8KQ9qejEcrxCtTRsOV3rePOkKsbKaZBLE0FOnq45QyADtelogEjJK8dcAlYFMPd9lLHYUNuyEY4a3cHGjL0gdm423tTSg1wRyghGymAHspuW5bcK7YorCf9KeUGPaMLkCMf7fQsGqHkjViJr7MmL5YodDSfNMhP4oZD"  
#ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")

# implementing a presistnace layer to preseve the chat history tha saves the state of messages for followup questions required by UOC manager 
#r = redis.Redis(host='localhost', port=6379, decode_responses=True)
memory_store = {}

def send_typing_indicator(sender_id: str):
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": sender_id,
        "type": "typing_on"
    }
    try:
        response = requests.post(WHATSAPP_API_URL, headers=headers, json=payload)
        print("Typing indicator sent:", response.status_code, response.text)
    except Exception as e:
        print("Failed to send typing indicator:", str(e))


def get_state(sender_id: str):
    print("Getting state for sender_id:", sender_id)
    return memory_store.get(sender_id)                      
    
    # try: 
    #     ############### Redis Connection Test ###############
    #     ############### Uncomment for production ###############
    #      r.ping()
    #      print(" Redis connection successful")
    # except redis.ConnectionError:
    #     print(" Redis connection failed. Attempting to load state from backup.")
    #     # Load state from backup or initialize a new state
    #     return {
    #         "sender_id": sender_id,   
    #         "messages": [],
    #         "agent_first_run": True,             
    #         "uoc_pending_question": False,
    #         "uoc_last_called_by": None,
    #         "uoc_confidence": "low",
    #         "uoc": {},                           
    #     }


    # state_json = r.get(sender_id)
    # try:
    #     return json.loads(state_json) 
    # except (TypeError, json.JSONDecodeError):
    #     print("Failed to decode state for sender_id:", sender_id)
    #     return {
    #         "sender_id": sender_id,   
    #         "messages": [],
    #         "agent_first_run": True,             
    #         "uoc_pending_question": False,
    #         "uoc_last_called_by": None,
    #         "uoc_confidence": "low",
    #         "uoc": {},                           
    #     }
    

def save_state(sender_id:str, state:dict):
    memory_store[sender_id] = state
    print("State saved method called")
    # r.set(sender_id, json.dumps(state), ex=3600)  # Setting the expiration time to 1 hour

#########################################################


# def send_whatsapp_message(to_number: str, message_text: str):
#     print("Sending message to WhatsApp:", to_number, message_text)
#     headers = {
#         "Authorization": f"Bearer {ACCESS_TOKEN}",
#         "Content-Type": "application/json"
#     }
#     payload = {
#         "messaging_product": "whatsapp",
#         "to": to_number,
#         "type": "text",
#         "text": {"body": message_text}
#     }

#     response = requests.post(WHATSAPP_API_URL, headers=headers, json=payload)
#     print(f"Sent message to {to_number}. Response:", response.status_code, response.text)

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
    print("####Webhook Called####")
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

        # state = {
        #     "sender_id": sender_id,
        #     "messages": [],  # We'll fill this based on type
        #     #"uoc_last_called_by": None,  # Without declaring this variable here, it will be undefined in the first call to UOCManager
        #     #"uoc_pending_question": False,
        # }
        state = get_state(sender_id)  # Retrieve the state from Redis


        if state is None:
            state = {
                "sender_id": sender_id,
                "messages": [],  
                "agent_first_run": True,             
                "uoc_pending_question": False,
                "uoc_last_called_by": None,
                "uoc_confidence": "low",
                "uoc": {},                           
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
        elif msg_type == "interactive":
            interactive_type = msg["interactive"]["type"]
            if interactive_type == "button_reply":
                reply_id = msg["interactive"]["button_reply"]["id"]
            elif interactive_type == "list_reply":
                reply_id = msg["interactive"]["list_reply"]["id"]
            else:
                reply_id = "unknown_interactive"
    
            state["messages"].append({"role": "user", "content": reply_id})
            print(f"Captured interactive reply: {reply_id}")


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

        # if  state.get("uoc_last_called_by") is None:  # checinkg it any agent called from uoc manager whic imokies it is a followup task 
        #     print("Calling agent directly")
        #     result = await builder_graph.ainvoke(state)
            #state["uoc_last_called_by"] = result.get("agent_name", None)  # Set the agent name for future reference
            #save_state(sender_id, result)  # Save the updated state back to Redis
                 
        # Agent returned with UOC pending
        #if state.get("uoc_pending_question", False):
        if  state.get("uoc_pending_question") is True:  # checking if the uoc manager is pending a question to be answered by the user
            sender_id = state.get("sender_id")
            print("Calling UOC Manager")
            uoc_mgr = UOCManager() #Instantiate the class
            if state["uoc_question_type"] == "project_formation":
                print("*****Project fromation")
                #print("State=====", state)
                try:
                    send_typing_indicator(sender_id)
                    followups_state = await uoc_mgr.collect_project_structure_interactively(state)
                except Exception as e:
                    print("Error calling collect_project_structure_interactively:", e)
                    import traceback; traceback.print_exc()
          


            elif state["uoc_question_type"] == "project_selection":
                print("*****Project selection")
                sender_id = state.get("sender_id")
                send_typing_indicator(sender_id)
                followups_state = await uoc_mgr.select_or_create_project(state, None)
            else:
                raise ValueError(f"Unknown uoc_question_type: {state['uoc_question_type']}")
            
            if followups_state.get("uoc_pending_question") is False and followups_state.get("uoc_confidence") in ["High"]:
                print("9999999999999999999999999999999")
                
                send_typing_indicator(sender_id)
                agent_name = followups_state.get("uoc_last_called_by", "uknown")
                result = await run_agent_by_name(agent_name, state)


            #followups_state = await UOCManager.run(state, called_by=state.get("uoc_last_called_by", "unknown"))
            save_state(sender_id, followups_state)  # Save the updated state back to Redis
            print("State saved")
            print("followup state: ", followups_state)
            
            response_msg= followups_state.get("latest_respons", "No response available.")
            message_type= followups_state.get("uoc_next_message_type", "plain")
            extra_data= followups_state.get("uoc_next_message_extra_data", None)
            print("uoc_next_message_type ", message_type)
            whatsapp_output(sender_id, response_msg, message_type=message_type, extra_data=extra_data)
    
        # This is redundant because the uoc_confidence -> high state is handled in UOC manager and that state is sent to the agent directly. There wont be a response back to the user when the state is high. 
        # elif  state.get("uoc_pending_question") is False and state.get("uoc_confidence") in ["High"]:
        #      # UOC is now confident, resume agent
        #     agent_name = state.get("uoc_last_called_by", "unknown")
        #     result = await run_agent_by_name(agent_name, state) 

# UOC is now confident, resume the originally intended agent.
# No need to go back to orchestrator (builder_graph) — decision was made earlier.
#The main question may arise from the lack of clairty of who owns the control flow and the return path?  - Which is now addressed by the above code.
             
        elif state.get("uoc_pending_question") is False:
            print("Calling orch ")
            send_typing_indicator(sender_id)
            result = await builder_graph.ainvoke(state)
            save_state(sender_id, result)
            print("state afters saving", get_state(sender_id))
            # print("result after saving in condition ", result)
        # Send final reply
        #response_msg = state["latest_response"] if "latest_response" in state else "No response available."
        #response_msg = result["messages"][-1]["content"] if "messages" in result else "No response available."
        response_msg= result.get("latest_respons", "No response available.")
        message_type= result.get("uoc_next_message_type", "plain")
        extra_data= result.get("uoc_next_message_extra_data", None)
        print("extra data", extra_data)
        print("Message type", message_type)
        whatsapp_output(sender_id, response_msg, message_type=message_type, extra_data=extra_data)
        logger.info("Final response sent to WhatsApp")
        return {"status": "done", "reply": response_msg}
    
    
    except Exception as e:
        logger.error("Error in WhatsApp webhook:{e}")
        #logger.error(e, exc_info=True)
        return {"status": "error", "message": str(e)}
