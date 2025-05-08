# agents/procurement_agent.py

from tools.lsie import _local_sku_intent_engine
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from tools.context_engine import filter_tags, vector_search
import os
from dotenv import load_dotenv
import json  
import base64
import openai
from unitofconstruction.uoc_manager import UOCManager

load_dotenv()  
llm_reasoning = ChatOpenAI(
    model="gpt-4o",
    temperature=0,
    openai_api_key=os.getenv("OPENAI_API_KEY")
)   

llm_context = ChatOpenAI(
    model="gpt-3.5-turbo",
    temperature=0,
    openai_api_key=os.getenv("OPENAI_API_KEY")
)   

def encode_image_base64(image_path: str) -> str:
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")

async def run_siteops_agent(state: dict) -> dict:
    print("$$$$$$$$$$Siteops agent called $$$$$$$$$$$")

    if state.get("uoc_pending_question", False):
        print("UOCManager still clarifying — skipping agent reasoning.")
        return state

    is_first_time = state.get("agent_first_run", True) 

    if is_first_time:    #next return happens only if there is a high confidence from the UOC mananger. Until the manager satisifies the loop continues. This can later re senstiivised for practical purposes like if the customer wont respond to the followup prompt.
        context = get_context(state)
        state["context_tags"] = context

        state = await UOCManager.run(state, called_by="siteops")
        if state.get("uoc_confidence") == "low":
            print("UOCManager still clarifying — skipping agent reasoning.")
            state["agent_first_run"] = False #setting here makes sense because, even if the return is for one time it needs to be checked as false so that the next return will be  taken care of.  
            return state

        #state["agent_first_run"] = False # This can be missed and it will not be set because the if condition retuns the state to webhook. 
    else:
        print("Follow-up context — skipping extraction")

    reason_input = format_reasoning_input(state)
    result = get_reason(state["context_tags"], reason_input) #CHANGE THIS - THIS IS INACURATE. KEPT FOR TIME BEING

    state["messages"].append({"role": "assistant", "content": result})
    return state

def get_reason(context: list, last_msg: str) -> str:
    system_prompt = (
        "You are a construction reasoning assistant. "
        "You are provided with a site update and supporting context (like IS codes, complaints, tips). "
        "Use the context only as background grounding — do not blindly repeat or enforce everything from it. "
        "Reason independently but draw upon relevant context where it strengthens your conclusion. "
        "Respond concisely in the following format:\n"
        "Risks -if any, one line only;\n"
        "Actionable Items -bullet points, max 3;\n"
        "Preparations for Next Stage -if required, bullet points, max 2."
        "Potentail financial impact -if any, one line only ; \n"
        "No verbose answers"
        "Respond in regional language like telugu if you feel necessary, else respond in english.\n"
        "If the context is not relevant to the site update, say 'No relevant context found'.\n"   
    )

    chat_response = llm_reasoning.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=last_msg),
        HumanMessage(content="Relevant context (for your reference):\n" + "\n- " + "\n- ".join(context))
    ])

    return chat_response.content.strip()

def get_context(state: dict):
    last_msg = state["messages"][-1]["content"]
    image_path = state.get("image_path")
    image_caption = state.get("caption", None)
    combined_input = f"Message: {last_msg}\ncaption: {image_caption}".strip()

    if image_path:
        image_base64 = encode_image_base64(image_path)

        vision_response = openai.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an assistant analyzing construction site photos.\n"
                        "Describe the visible work, including the component, stage of work, and location hints.\n"
                        "Be concise and objective. If nothing meaningful is visible, say so."
                    )
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": combined_input},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_base64}"
                            }
                        }
                    ]
                }
            ],
            max_tokens=300
        )
        gained_context = vision_response.choices[0].message.content.strip() 
        print("Vision response:", gained_context)
        return gained_context
    else:
        query_params = llm_context.invoke([
            SystemMessage(
                content=(
                    "Extract the following information from the user's message and return it in this JSON format: "
                    "{\"component\": \"...\", \"stage\": \"...\", \"zone\": \"...\"}. "
                    "Definitions:\n"
                    "- component: the physical item the work is being done on, such as a wall, slab, column, beam, footing, shaft, or roof.\n"
                    "- stage: the specific activity being performed on the component, such as plastering, curing, brickwork, etc. Avoid generic actions like 'working' or 'doing'. If no stage is clearly mentioned, leave it blank.\n"
                    "- zone: the location where the work is happening, such as ground floor, first floor, second floor, flat numbers, directions (e.g., south-east corner), or blocks.\n"
                    "Return only the JSON."
                )
            ),
            HumanMessage(content=combined_input)
        ]).content.strip()
        return query_params

def format_reasoning_input(state: dict):
    last_msg = state["messages"][-1]["content"]
    uoc_summary = json.dumps(state.get("uoc", {}).get("data", {}), indent=2)
    return f"{last_msg}\n\nUOC Context (This is the unit of construction we are referring to):\n{uoc_summary}"
