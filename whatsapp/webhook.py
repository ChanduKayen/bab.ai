from fastapi import APIRouter, Request, Depends, Request
from agents import procurement_agent
from orchastrator.core import builder_graph 
import sys 
from fastapi.responses import PlainTextResponse
import os
from dotenv import load_dotenv
from managers.uoc_manager import UOCManager
import logging
router = APIRouter()
from app.logging_config import logger
import requests
from pathlib import Path

# Load environment variables
load_dotenv()
APP_SECRET = os.getenv("APP_SECRET", None)
#import redis
import asyncio  
import random
import json
import agents.siteops_agent as siteops_agent
from agents.random_agent import classify_and_respond
from agents.procurement_agent import collect_procurement_details_interactively
from agents import credit_agent
from whatsapp.builder_out import whatsapp_output
from users.user_onboarding_manager import user_status
#from database._init_ import AsyncSessionLocal
from app.db import get_sessionmaker
AsyncSessionLocal = get_sessionmaker()

from database.uoc_crud import DatabaseCRUD
from managers.uoc_manager import UOCManager
from managers.project_intel import TaskHandler
import os, time, json, hashlib
import hmac
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import BackgroundTasks
from fastapi.responses import Response
import json
from hashlib import sha256
from app.db import get_db
from database.whatsapp_crud import first_time_event

#This has to be updated accroding to he phone number you are using for the whatsapp business account.
WHATSAPP_API_URL = "https://graph.facebook.com/v19.0/768446403009450/messages"
#ACCESS_TOKEN = "EAAIMZBw8BqsgBO4ZAdqhSNYjSuupWb2dw5btXJ6zyLUGwOUE5s5okrJnL4o4m89b14KQyZCjZBZAN3yZBCRanqLC82m59bGe4Rd2BPfRe3A3pvGFZCTf2xB7a6insIzesPDVMLIw4gwlMkkz7NGl3ZBLvP5MU8i3mZBMmUBShGeQkSlAyRhsXJtlsg8uGaAfYwTid8PZAGBKnbOR3LFpCgBD8ZCIMJh9xI0sHWy"  

ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")

MEDIA_DOWNLOAD_PATH = os.getenv("MEDIA_DOWNLOAD_DIR")
if not MEDIA_DOWNLOAD_PATH:
    raise RuntimeError("Environment variable `MEDIA_DOWNLOAD_DIR` must be set.")
MEDIA_DOWNLOAD_DIR = Path(MEDIA_DOWNLOAD_PATH)

# implementing a presistnace layer to preseve the chat history tha saves the state of messages for followup questions required by UOC manager 
#r = redis.Redis(host='localhost', port=6379, decode_responses=True)
memory_store = {}

def get_state(sender_id: str): 
    print("Webhook :::::: get_state::::: Getting state for sender_id:", sender_id)
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
    #         "needs_clarification": False,
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
    #         "needs_clarification": False,
    #         "uoc_last_called_by": None,
    #         "uoc_confidence": "low",
    #         "uoc": {},                           
    #     }
    

def save_state(sender_id:str, state:dict):
    memory_store[sender_id] = state
    print("Webhook :::::: save_state::::: State saved method called")
    # r.set(sender_id, json.dumps(state), ex=3600)  # Setting the expiration time to 1 hour

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
    print("Webhook :::::: download_whatsapp_image::::: Got media URL:",media_info_url)
    res = requests.get(media_info_url, headers=headers)
    
    if res.status_code != 200:
        print("Webhook :::::: download_whatsapp_image::::: Failed to get media URL:", res.text)
        return None

    media_url = res.json().get("url")
    if not media_url:
        print("Webhook :::::: download_whatsapp_image::::: Media URL missing in response")
        return None

    try:
        MEDIA_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as err:
        print("Webhook :::::: download_whatsapp_image::::: Failed to ensure download dir:", err)
        return None

    try:
        media_res = requests.get(media_url, headers=headers, stream=True, timeout=30)
        media_res.raise_for_status()
    except Exception as err:
        print("Webhook :::::: download_whatsapp_image::::: Failed to download media:", err)
        return None

    filename = MEDIA_DOWNLOAD_DIR / f"{media_id}.jpg"

    try:
        with open(filename, "wb") as file_obj:
            for chunk in media_res.iter_content(chunk_size=8192):
                if chunk:
                    file_obj.write(chunk)
    except Exception as err:
        print("Webhook :::::: download_whatsapp_image::::: Failed to save image:", err)
        return None

    print(f" Webhook :::::: download_whatsapp_image::::: Saved image to {filename}")
    return str(filename)
      
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
     
@router.get("/webhook/")
@router.get("/webhook")
async def verify(request: Request):
    q = request.query_params
    VERIFY_TOKEN = "babai"
    if q.get("hub.mode") == "subscribe" and q.get("hub.verify_token") == VERIFY_TOKEN:
        return PlainTextResponse(q.get("hub.challenge", "0"))
    return PlainTextResponse("Invalid token", status_code=403)  

# @router.get("/webhook")
# async def verify(request: Request):
#     print("Webhook :::::: verify::::: GET /webhook called")
#     sys.stdout.flush()
#     params = dict(request.query_params)

#     expected_token = "babai"

#     if params.get("hub.verify_token") == expected_token:
#         return PlainTextResponse(params.get("hub.challenge", "0"))
    
#     return PlainTextResponse("Invalid token", status_code=403)


# #(SQLAlchemy) Helper
# async def first_time_event(event_id: str) -> bool:
#     """
#     True  -> first time (inserted)
#     False -> duplicate (conflict)
#     """
#     q = text("""
#         INSERT INTO whatsapp_events (event_id)
#         VALUES (:eid)
#         ON CONFLICT DO NOTHING
#         RETURNING 1
#     """)
#     print("Recording First event-----------")
#     async with AsyncSessionLocal() as session:
#         res = await session.execute(q, {"eid": event_id})
#         await session.commit()
#         return res.scalar() == 1
def extract_event_id(payload: dict) -> str:
    try:
        return payload["entry"][0]["changes"][0]["value"]["messages"][0]["id"]
    except Exception:
        return sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
async def handle_whatsapp_event(data: dict):
    try:
        logger.info("Entered Webhook route")
        #data = await request.json()
        #msg = data["entry"][0]["changes"][0]["value"]["messages"][0]

        entry = data["entry"][0]["changes"][0]["value"]
        if "messages" not in entry:
            logger.info("No messages found in the entry.")
            return {"status": "ignored", "reason": "No messages found"}
        msg = entry["messages"][0]
        
        
        print("Webhook :::::: whatsapp_webhook::::: Received message:", msg)
        sender_id = msg["from"]
        msg_type = msg["type"]
        contacts = entry.get("contacts", [])
        user_name = None

        if contacts and isinstance(contacts[0], dict):
            profile = contacts[0].get("profile", {})
            if isinstance(profile, dict):
                user_name = profile.get("name")
        print("Webhook :::::: whatsapp_webhook::::: usrname:", user_name)
        
        state = get_state(sender_id)  # Retrieve the state from Redis
        #state["user_full_name"] = user_name  
        #user = user_status(sender_id, user_name) # Dummied
        user = {"user_full_name": user_name, "user_stage": "new"}
        user_stage = user["user_stage"]
        
        if state is None:
            state = {
                "sender_id": sender_id,
                "messages": [],  
                "agent_first_run": True,             
                "needs_clarification": False,
                "uoc_last_called_by": None,
                "uoc_confidence": "low",
                "uoc": {}, 
                "user_full_name": user_name,    
                "user_stage": user_stage,    
            }
        else:
            state["user_full_name"] = user_name
            state["user_stage"] = user_stage  
        if msg_type == "text":
            text = msg["text"]["body"]
            state["messages"].append({"role": "user", "content": text})
            state["msg_type"] = "text"
        
        elif msg_type == "image":
            media_id = msg["image"]["id"]
            caption = msg["image"].get("caption", "")
        
            image_path = download_whatsapp_image(media_id)
            print("Webhook :::::: whatsapp_webhook::::: Image downloaded, path:", image_path)
            state["messages"].append({
                "role": "user",
                "content": f"[Image ID: {media_id}] {caption}"  # Or you could pass separately
            })
            state["media_id"] = media_id  
            state["image_path"] = image_path  
            state["caption"] = caption
            state["msg_type"] = "image"
            state["media_url"] = image_path
        
            print("Webhook :::::: whatsapp_webhook::::: Image downloaded and saved at:", image_path)
        elif msg_type == "interactive":
            interactive_type = msg["interactive"]["type"]
            if interactive_type == "button_reply":
                reply_id = msg["interactive"]["button_reply"]["id"]
            elif interactive_type == "list_reply":
                reply_id = msg["interactive"]["list_reply"]["id"]
            else:
                reply_id = "unknown_interactive"
    
            state["messages"].append({"role": "user", "content": reply_id})
            state["msg_type"] = "interactive"
            print(f"Webhook :::::: whatsapp_webhook::::: Captured interactive reply: {reply_id}")

        elif msg_type == "document":
            media_id  = msg["document"]["id"]
            file_name = msg["document"].get("filename", "document")

            # Fetch download URL & metadata
            meta_resp = requests.get(
                f"https://graph.facebook.com/v19.0/{media_id}",
                params={"access_token": ACCESS_TOKEN},
                timeout=10,
            )

            if meta_resp.status_code != 200:
                print("Webhook :::::: Failed to fetch document meta:", meta_resp.text)
                return {"status": "ignored", "reason": "Cannot fetch document"}
            
            media_info = meta_resp.json()
            media_url  = media_info.get("url")
            mime_type  = media_info.get("mime_type", "")
            
            # Decide file type
            file_type = "pdf" if mime_type == "application/pdf" else "document"

            # Download the file
            ext = ".pdf" if file_type == "pdf" else ".bin"
            local_path = MEDIA_DOWNLOAD_DIR / f"{media_id}{ext}"
            try:
                file_data = requests.get(media_url, timeout=10).content
                with open(local_path, "wb") as fp:
                    fp.write(file_data)
                print(f"Webhook :::::: Saved document to {local_path}")
            except Exception as e:
                print("Webhook :::::: Failed to download and save document:", e)
                return {"status": "ignored", "reason": "Failed to download"}

            # Try optional text extraction for PDFs
            if file_type == "pdf":
                try:
                    import fitz  # PyMuPDF
                    doc = fitz.open(str(local_path))
                    pdf_text = "".join(page.get_text() for page in doc)
                    doc.close()
                    state["pdf_text"] = pdf_text
                except Exception as e:
                    print("Webhook :::::: PDF text extraction failed:", e)

            # Add synthetic user message & metadata
            state["messages"].append({
                "role": "user",
                "content": f"[Document ID: {media_id}] {file_name}"
            })

            state["media_id"] = media_id
            state["file_name"] = file_name
            state["msg_type"] = file_type
            state["media_url"] = str(local_path)
        else:
            return {"status": "ignored", "reason": f"Unsupported message type {msg_type}"}






        # we are checking what the message is about and whom to call - orchastrator or agent; we call orcha strator first and if the respone is regarding an ongoin converstainpn to gain more context intiated by agetns the respone is direclty routed to agetns insted of orchestrator
        #result = await builder_graph.ainvoke(state)
                # First message, go to orchestrator
        # if not state.get("needs_clarification", False):
        #     result = await builder_graph.ainvoke(state)
        # The above flow has logic flaw becuase the followup question in set high by the uoc manager but this is interpreted as a frost time message and routed to orchestrated.
        
        #if not state.get("uoc_last_called_by") and state.get("needs_clarification", False):
        print("Webhook :::::: whatsapp_webhook:::::  UOC pending question:", state.get("needs_clarification", False))
        print("Webhook :::::: whatsapp_webhook::::: UOC last called by:", state.get("uoc_last_called_by", "unknown"))

        # if  state.get("uoc_last_called_by") is None:  # checinkg it any agent called from uoc manager whic imokies it is a followup task 
        #     print("Calling agent directly")
        #     result = await builder_graph.ainvoke(state)
            #state["uoc_last_called_by"] = result.get("agent_name", None)  # Set the agent name for future reference
            #save_state(sender_id, result)  # Save the updated state back to Redis
                 
        # Agent returned with UOC pending
        #if state.get("needs_clarification", False):
        PROJECT_FORMATION_MESSAGES = [
    "Okay.",
    "Alright.",
    "Got it.",
    "Noted.",
    "Understood.",
    "Right.",
    "Fine.",
    "All right.",
    "Clear.",
    "Yes.",
]

        PLAN_OR_DOC_MESSAGES = [
            "No problem. Just answer a few quick questions — we’ll set up your project, and auto-link all future updates.",

"OKay! Let’s start from scratch — fast. A few simple answers now, and everything else will connect automatically.",

"That’s fine. You’ll be done in under a minute — we’ll match future updates to this project for you.",

"Sure, we work with whatever you’ve got. Just a few taps now — Bab.ai will keep everything neatly linked from here on.",
        ]

        PROJECT_SELECTION_MESSAGES = [
            "🔍 Hold on... I’m figuring out which project you’re referring to.",
            "📁 Let me check if this matches any existing projects.",
            "🗂️ Matching this conversation to the right project for context.",
        ]

        FIRST_TIME_MESSAGES = [
             "Alright, let’s take a look.",
    "Okay, I’m with you.",
    "Sure, let's get started.",
    "Got it. Let’s take the first step.",
    "Alright. We'll go one thing at a time.",
    "I’m here. Let’s begin.",
    "Alright — starting simple.",
    "Okay, let's figure this out together.",
    "All good. Let me guide you from here.",
    "That’s received. Let’s begin from the basics.",
    "Okay, let’s make this easy.",
    "Alright. Just need a small detail to begin.",
    "Let’s start gently. One quick check first.",
    "Thanks. I’ll take it from here.",
    "Got it. Let’s just set the context right.",
    "With you. Let’s start at the beginning.",
    "Noted. I’ll guide you from here.",
    "Okay, let’s get some clarity first.",
    "Right, let’s set the ground.",
    "Perfect. Let’s walk through it step by step.", 
        ]
        try:
                async with AsyncSessionLocal() as session:
                   crud = DatabaseCRUD(session)
                   uoc_mgr = UOCManager(crud)
                   task_handler = TaskHandler(crud)  # Initialize TaskHandler with the same CRUD instance
                   #uoc_mgr = UOCManager()  # Instantiate the class
        except Exception as e:
                print("Webhook :::::: whatsapp_webhook::::: Error instantiating UOCManager:", e)
                import traceback; traceback.print_exc()
                return {"status": "error", "message": f"Failed to instantiate UOCManager: {e}"}
       
        if  state.get("needs_clarification") is True:  # checking if the uoc manager is pending a question to be answered by the user
            print("Webhook :::::: whatsapp_webhook::::: <needs_clarification>::::: -- Figuring out which method in UOC managet to call, after the question initasked by UOC manager --", state["needs_clarification"])
            q_type = state.get("uoc_question_type", "").strip().lower()
            print("Webhook :::::: whatsapp_webhook::::: <uoc_question_type>::::: -- The set question type is --", repr(q_type))
            
            
            if q_type == "onboarding":
                    
                    print("Webhook :::::: whatsapp_webhook::::: <pending_question True>::::: -- The set question type is random, so calling ??classify_and_respond?? --")
                    try:
                        async with AsyncSessionLocal() as session:
                            crud = DatabaseCRUD(session)
                            followups_state = await classify_and_respond(state, config={"configurable": {"crud": crud}})
                    except Exception as e:
                        print("Webhook :::::: whatsapp_webhook::::: Error calling classify_and_respond in onboarding:", e)
            
            
            elif q_type == "project_formation":
                whatsapp_output(sender_id, random.choice(PROJECT_FORMATION_MESSAGES), message_type="plain")
                print("Webhook :::::: whatsapp_webhook::::: <needs_clarification True>::::: <uoc_question_type>::::: -- The set question type is project_formation so calling ??collect_project_structure_interactively??  --", state["uoc_question_type"])
                #print("State=====", state)
                try:
                    followups_state = await uoc_mgr.collect_project_structure_interactively(state)
                except Exception as e:
                    print("Webhook :::::: whatsapp_webhook::::: Error calling collect_project_structure_interactively:", e)
                    traceback.print_exc()
                

            elif q_type == "has_plan_or_doc":
                    file_url  = state.get("image_path") or state.get("file_local_path")
                    file_type = state.get("msg_type")

                    if file_url and file_type in ("image", "pdf", "document"):
                        print("Webhook :::::: Detected plan upload —", file_type, file_url)
                        try:
                            followups_state = await uoc_mgr.process_plan_file(
                                state, file_url, file_type
                            )
                        except Exception as e:
                            print("Webhook :::::: process_plan_file failed:", e)
                            followups_state = state
                            followups_state.update(
                                latest_respons=(
                                    "Sorry, I couldn’t read that file. "
                                    "Please try a clearer image or a PDF."
                                ),
                                needs_clarification=True,
                            )
                    else:
                        whatsapp_output(
                            sender_id,
                            random.choice(PLAN_OR_DOC_MESSAGES),
                            message_type="plain",
                        )
                        print("Webhook :::::: Awaiting plan upload — sending gentle nudge.")
                        followups_state = await uoc_mgr.collect_project_structure_with_priority_sources(state)
            elif q_type == "project_selection":
                # whatsapp_output(sender_id, random.choice(PROJECT_SELECTION_MESSAGES), message_type="plain")
                # print("Webhook ::::::  whatsapp_webhook::::: <needs_clarification True>::::: <uoc_question_type>::::: -- The set question type is project_selection, so calling ??select_or_create_project??--", state["uoc_question_type"])
                # followups_state = await uoc_mgr.select_or_create_project(state, None)
                if msg.get("type") == "interactive" and msg["interactive"].get("type") == "button_reply":
                    button_id = msg["interactive"]["button_reply"]["id"]
                    button_title = msg["interactive"]["button_reply"]["title"]
                    
                    if button_id == "add_new":
                        # Call your select_or_create_project flow
                        
                        followups_state = await uoc_mgr.select_or_create_project(state, None)
                    else:
                        followups_state = await task_handler.handle_job_update(state)
            elif q_type == "task_region_identification":
                print("Webhook :::::: whatsapp_webhook::::: <needs_clarification True>::::: <uoc_question_type>::::: -- The set question type is task_region_identification, so calling ??get_region_via_llm?? --")
                try:
                    followups_state = await task_handler.get_region_via_llm(state)
                except Exception as e:
                    print("Webhook :::::: whatsapp_webhook::::: Error in get_region_via_llm:", e)
                    import traceback; traceback.print_exc()
                    followups_state = state
                    followups_state.update(
                        latest_respons="Sorry, I couldn't determine the region. Please try again.",
                        needs_clarification=True,
                    ) 
            
            elif q_type == "siteops_welcome":
                print("Webhook :::::: whatsapp_webhook::::: <needs_clarification True>::::: <uoc_question_type>::::: -- The set question type is siteops_welcome, so calling ??siteops_agent.new_user_flow?? --", state["uoc_question_type"])
                try:
                    #followups_state = await siteops_agent.new_user_flow(state)
                    #followups_state=await siteops_agent.run_siteops_agent(state)
                    async with AsyncSessionLocal() as session:
                       crud = DatabaseCRUD(session)
                       followups_state = await siteops_agent.run_siteops_agent(state, config={"configurable": {"crud": crud}})
                except Exception as e:
                    print("Webhook :::::: whatsapp_webhook::::: Error calling siteops_agent.new_user_flow:", e)
                    import traceback; traceback.print_exc()
            
            elif q_type == "procurement":
                print("Webhook :::::: whatsapp_webhook::::: q_type = procurement :::: The set question type is procurement, so calling ??collect_procurement_details_interactively?? --", state["uoc_question_type"])
                followups_state = await collect_procurement_details_interactively(state)
                return {"status": "done", "reply": response_msg}
               
            elif q_type== "procurement_new_user_flow":
                print("Webhook :::::: whatsapp_webhook::::: q_type = procurement_new_user_flow :::: The set question type is procurement_new_user_flow, so calling ??procurement_agent.run_procurement_agent?? --", state["uoc_question_type"])
                try:
                    followups_state = await procurement_agent.run_procurement_agent(state, config={"configurable": {"crud": crud}})
                except Exception as e:
                    print("Webhook ::::: whatsapp_webhook ::::: q_type = procurement_new_user_flow ::::: Exception rasied : ", e)

            elif q_type == "credit_start": 
                print("Webhook :::::: whatsapp_webhook::::: <needs_clarification True>::::: <uoc_question_type>::::: -- The set question type is credit_onboard_start, so calling ??credit_agent.run_credit_agent?? --", state["uoc_question_type"])
                try:
                    followups_state = await credit_agent.run_credit_agent(state, config={"configurable": {"crud": crud}})
                except Exception as e:
                    print("Webhook :::::: whatsapp_webhook::::: Error calling credit_agent.run_credit_agent:", e)
                    import traceback; traceback.print_exc()
            
            elif q_type == "credit_onboard_aadhaar":
                print("Webhook :::::: whatsapp_webhook::::: <needs_clarification True>::::: <uoc_question_type>::::: -- The set question type is credit_onboard_aadhaar, so calling ??credit_agent.handle_collect_aadhaar?? --", state["uoc_question_type"])
                try:
                    followups_state = await credit_agent.handle_collect_aadhaar(state)
                except Exception as e:
                    print("Webhook :::::: whatsapp_webhook::::: Error calling credit_agent.handle_collect_aadhaar:", e)
                    import traceback; traceback.print_exc()
            elif q_type == "credit_onboard_pan":
                print("Webhook :::::: whatsapp_webhook::::: <needs_clarification True>::::: <uoc_question_type>::::: -- The set question type is credit_onboard_pan, so calling ??credit_agent.handle_collect_pan?? --", state["uoc_question_type"])
                try:
                    followups_state = await credit_agent.handle_collect_pan(state)
                except Exception as e:
                    print("Webhook :::::: whatsapp_webhook::::: Error calling credit_agent.handle_collect_pan:", e)
                    import traceback; traceback.print_exc()
            elif q_type == "credit_onboard_gst":
                print("Webhook :::::: whatsapp_webhook::::: <needs_clarification True>::::: <uoc_question_type>::::: -- The set question type is credit_onboard_gst, so calling ??credit_agent.handle_collect_gst?? --", state["uoc_question_type"])
                try:
                    followups_state = await credit_agent.handle_collect_gst(state)
                except Exception as e:
                    print("Webhook :::::: whatsapp_webhook::::: Error calling credit_agent.handle_collect_gst:", e)
                    import traceback; traceback.print_exc()
            elif q_type =="credit_onboard_consent":
                print("Webhook :::::: whatsapp_webhook::::: <needs_clarification True>::::: <uoc_question_type>::::: -- The set question type is credit_onboard_consent, so calling ??credit_agent.handle_collect_consent?? --", state["uoc_question_type"])
                try:
                    followups_state = await credit_agent.handle_collect_consent(state)
                except Exception as e:
                    print("Webhook :::::: whatsapp_webhook::::: Error calling credit_agent.handle_collect_consent:", e)
                    import traceback; traceback.print_exc()
            elif q_type=="credit_status_check":
                print("Webhook :::::: whatsapp_webhook::::: <needs_clarification True>::::: <uoc_question_type>::::: -- The set question type is credit_status_check, so calling ??credit_agent.handle_credit_status_check?? --", state["uoc_question_type"])
                try:
                    followups_state = await credit_agent.handle_poll_approval(state)
                except Exception as e:
                    print("Webhook :::::: whatsapp_webhook::::: Error calling credit_agent.handle_credit_status_check:", e)
                    import traceback; traceback.print_exc()
            else:
                raise ValueError(f"Unknown uoc_question_type: {state['uoc_question_type']}")

        #print("State after classify_and_respond=====", followups_state)
            
            if followups_state.get("needs_clarification") is False and followups_state.get("uoc_confidence") in ["high"]:
                print("Webhook :::::: whatsapp_webhook::::: <needs_clarification True>::::: <needs_clarification>::::: -- UOC is now confident, calling the agent --")
                agent_name = followups_state.get("uoc_last_called_by", "uknown")
                result = await run_agent_by_name(agent_name, state)


            #followups_state = await UOCManager.run(state, called_by=state.get("uoc_last_called_by", "unknown"))
            save_state(sender_id, followups_state)  # Save the updated state back to Redis
            print("Webhook :::::: whatsapp_webhook::::: -- Got result from the called agent, saved the state : --: ", followups_state)
            response_msg= followups_state.get("latest_respons", "No response available.")
            message_type= followups_state.get("uoc_next_message_type", "plain")
            extra_data= followups_state.get("uoc_next_message_extra_data", None)
            print("Webhook :::::: whatsapp_webhook::::: -- ******Sending message to whatsapp****** Attributes: -- ", message_type, extra_data)
            whatsapp_output(sender_id, response_msg, message_type=message_type, extra_data=extra_data)
     
        # This is redundant because the uoc_confidence -> high state is handled in UOC manager and that state is sent to the agent directly. There wont be a response back to the user when the state is high. 
        # elif  state.get("needs_clarification") is False and state.get("uoc_confidence") in ["High"]:
        #      # UOC is now confident, resume agent
        #     agent_name = state.get("uoc_last_called_by", "unknown")
        #     result = await run_agent_by_name(agent_name, state) 

# UOC is now confident, resume the originally intended agent.
# No need to go back to orchestrator (builder_graph) — decision was made earlier.
#The main question may arise from the lack of clairty of who owns the control flow and the return path?  - Which is now addressed by the above code.
           
        elif state.get("needs_clarification") is False:
            print("Webhook :::::: whatsapp_webhook::::: <needs_clarification False>:::::  -- Calling orchestrator, this is a first time message --")
            #whatsapp_output(sender_id, random.choice(FIRST_TI ME_MESSAGES), message_type="plain")
            state["user_full_name"] = user_name  # Update the user's full name in the state
            #result = await builder_graph.ainvoke(state)
            
            print("Calling builder_graph:", builder_graph)
            print("Type of builder_graph:", type(builder_graph))
            #PassingDB Session as a  contextwrapper to Langgraph; dont send crud in a state, it break the serialization. 
            async with AsyncSessionLocal() as session:
             crud = DatabaseCRUD(session)
             result = await builder_graph.ainvoke(input=state, config={"crud": crud})
             
 

            save_state(sender_id, result)
            #print("Webhook :::::: whatsapp_webhook::::: <needs_clarification False>:::::  -- Got result from the Orchestrator, saved the state : --", get_state(sender_id))
            # print("result after saving in condition ", result)
        # Send final reply
        #response_msg = state["latest_response"] if "latest_response" in state else "No response available."
        #response_msg = result["messages"][-1]["content"] if "messages" in result else "No response available."
        response_msg= result.get("latest_respons", "No response available.")
        message_type= result.get("uoc_next_message_type", "plain")
        extra_data= result.get("uoc_next_message_extra_data", None)
        print("Webhook :::::: whatsapp_webhook:::::-- ******Sending message to whatsapp****** Attributes :", message_type, extra_data)
        try:
            whatsapp_output(sender_id, response_msg, message_type=message_type, extra_data=extra_data)
            logger.info("Final response sent to WhatsApp")
        except Exception as send_err:
            logger.error(f"Failed to send WhatsApp response: {send_err}")
        return {"status": "done", "reply": response_msg}
    
       
    except Exception as e:
        logger.error("Error in WhatsApp webhook:{e}")
        #logger.error(e, exc_info=True)
        return {"status": "error", "message": str(e)}

# @router.get("/webhook")
# async def verify(request: Request):
#     q = request.query_params
#     if q.get("hub.mode") == "subscribe" and q.get("hub.verify_token") == "babai":
#         return PlainTextResponse(q.get("hub.challenge", "0"))
#     return PlainTextResponse("Invalid token", status_code=403)

# router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])

@router.post("/webhook")
async def whatsapp_webhook(
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    print("Webhook :::::: whatsapp_webhook::::: ####Webhook Called####")

    # Parse body safely
    try:
        data = await request.json()
        print("Webhook :::::: whatsapp_webhook::::: data", data)
    except Exception:
        try:
            data = json.loads((await request.body()) or b"{}")
        except Exception:
            return PlainTextResponse("OK", status_code=200)

    eid = extract_event_id(data)
    if not eid:
        return PlainTextResponse("OK", status_code=200)

    try:
        print("Webhook :::::: whatsapp_webhook::::: Calling First time event")
        is_first = await first_time_event(session, eid)
    except Exception:
        logging.exception("first_time_event failed")
        return PlainTextResponse("OK", status_code=200)

    if not is_first:
        print("Duplicate/Noise - 200 OK")
        return PlainTextResponse("OK", status_code=200)

    entry = data.get("entry", [{}])[0].get("changes", [{}])[0].get("value", {})
    if not entry.get("messages"):
        print("Statuses/Noise - 200 OK")
        return PlainTextResponse("OK", status_code=200)

    # schedule async work on the running loop
    asyncio.create_task(_safe_handle_whatsapp_event(data))
    return PlainTextResponse("OK", status_code=200)

async def _safe_handle_whatsapp_event(payload: dict):
    try:
        await handle_whatsapp_event(payload)  # <-- your async worker
    except Exception:
        logging.exception("handle_whatsapp_event failed")
