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
        msg_type = msg["type"]

        state = {
            "sender_id": sender_id,
            "messages": []  # We'll fill this based on type
        }

        if msg_type == "text":
            text = msg["text"]["body"]
            state["messages"].append({"role": "user", "content": text})

        elif msg_type == "image":
            media_id = msg["image"]["id"]
            caption = msg["image"].get("caption", "")

            # Ideally: Download the image here using media_id
            # But for now, we just pass the media_id and caption to the agent

            state["messages"].append({
                "role": "user",
                "content": f"[Image ID: {media_id}] {caption}"  # Or you could pass separately
            })
            state["media_id"] = media_id  # optional
            state["caption"] = caption

        else:
            return {"status": "ignored", "reason": f"Unsupported message type {msg_type}"}

        # Run the LangGraph orchestrator
        result = await builder_graph.ainvoke(state)

        # Return the agent's response back to WhatsApp
        response_msg = result["messages"][-1]["content"]
       
        logger.info("Received message from WhatsApp")
        logger.info(f"User said: {state['messages'][-1]['content']}")
        return {"status": "done", "reply": response_msg}
 
    except Exception as e:
        logger.error("Error in WhatsApp webhook:", str(e))
        return {"status": "error", "message": str(e)}
