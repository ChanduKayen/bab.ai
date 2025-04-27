from fastapi import APIRouter, Request
from  workflows.builder_procurement_flow import builder_graph 
import sys
from fastapi.responses import PlainTextResponse
#from whatsapp.intent_router import route_message

router = APIRouter()
from app.logging_config import logger
logger.info("[STARTUP] webhook.py loaded successfully.")
logger.info("Now testing the webhook route.")

@router.get("/webhook")
async def verify(request: Request):
    print("GET /webhook called")
    sys.stdout.flush()
    params = dict(request.query_params)
    if params.get("hub.verify_token") == "babai":
        return PlainTextResponse(params.get("hub.challenge", "0"))
    return PlainTextResponse("Invalid token", status_code=403)

@router.post("/webhook")
async def whatsapp_webhook(request: Request):
    try:
        logger.info("Entered Webhook route")
        data = await request.json()
        msg = data["entry"][0]["changes"][0]["value"]["messages"][0]
        sender_id = msg["from"]
        text = msg["text"]["body"]

        #Define the state expected by LangGraph
        state = {
            "messages": [{"role": "user", "content": text}],
            "sender_id": sender_id,
            # add additional keys if needed (e.g., memory, session, etc.)
        }

        #Run the LangGraph orchestrator
        result = await builder_graph.ainvoke(state)

        #Return the agent's response back to WhatsApp
        response_msg = result["messages"][-1]["content"]
       
        logger.info("Received message from WhatsApp")
        logger.info(f"User said: {text}")
        return {"status": "done", "reply": response_msg}
 
    except Exception as e:
        return {"status": "error", "message": str(e)}