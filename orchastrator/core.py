from langgraph.graph import StateGraph
from agents.procurement_agent import run_procurement_agent
#from agents.credit_agent import run_credit_agent
from agents.siteops_agent import run_siteops_agent
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
import os
from dotenv import load_dotenv
from models.chatstate import AgentState
from typing import TypedDict, List, Optional
import base64  
import openai  # Import the OpenAI module
load_dotenv()  # lodad environment variables from .env file




builder_graph = StateGraph(AgentState)
#llm = ChatOpenAI(model="gpt-4", temperature=0)
llm = ChatOpenAI(
    model="gpt-4o",
    temperature=0,
    openai_api_key=os.getenv("OPENAI_API_KEY")  # safely pulls from env
)

def encode_image_base64(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode("utf-8")
    
def build_vision_prompt(image_path, message_text=""):
    image_base64 = encode_image_base64(image_path)

    return [
        {
            "role": "system",
            "content": (
                "You are an intent router for a construction assistant. "
                "Given a user message and an image (like a bill, site photo, or plan), classify the intent. "
                "Respond with only one of: procurement, credit, siteops, transport, random"
            )
        },
        {
            "role": "user",
            "content": [
                {"type": "text", "text": message_text},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{image_base64}"
                    }
                }
            ]
        }
    ]

    
async def infer_intent_node(state: AgentState) -> AgentState:
    print("$$$$$$$$$$Orchestrator called - infer_intent_node$$$$$$$$$$$")
    print("State -", state)
    last_msg = state["messages"][-1]["content"]
    image_caption = state.get("caption", None)
    image_path = state.get("image_path", None)
    #print("Last Message -", last_msg)
    combined_input = f"Message: {last_msg}\ncaption: {image_caption}".strip()

    
    if image_path:
        print("Image path found -", image_path)
        messages = build_vision_prompt(image_path=image_path, message_text=combined_input)
        response = openai.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            max_tokens=10
        )
        state["intent"] = response.choices[0].message.content.strip().lower()
        print("Intent of image found -", state["intent"])
        return state


    system_prompt = (
        "You are an intent router for a construction procurement assistant.\n"
        "Given the user message, return the name of the agent that should handle it.\n"
        #"or if its a random content irrelvant to construction say random"
       "Possible agents: procurement, credit, transport, siteops, random\n"
       "Respond ONLY with one of: procurement, credit, transport, siteops, random"
    )
    
    chat_response = await llm.ainvoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=last_msg)
    ])
    
    

    intent = chat_response.content.strip().lower()
    print("Found this intent -", intent)
    state["intent"] = intent
    print("++++++++++++++State after intent inference in orchestrator", state)

    return state

def intent_router(state: AgentState) -> str:
     return state["intent"]


#Added the cleanup to clear the state to avoid data leakage between next calls
# This is a temporary solution, ideally we should have a better way to handle state management
async def cleanup_node(state: AgentState) -> AgentState:
    print("$$$$$$$$$$Orchestrator called - cleanup_node$$$$$$$$$$$")
    state.pop("image_path", None)
    state.pop("caption", None)
    state.pop("media_id", None)
    state.pop("context_tags", None) 
    state.pop("context", None)
    state.pop("uoc", None)  
    state.pop("uoc_confidence", None)
    state.pop("uoc_pending_question", None) 
    state.pop("uoc_last_called_by", None)
    state.pop("agent_first_run", None)  
    state.pop("latest_response", None)
    state.pop("messages", None)
    state.pop("sender_id", None)
    state.pop("intent", None)
    state.pop("image_path", None)
    state.pop("caption", None)
    state.pop("media_id", None)
    state.pop("context_tags", None)
    state.pop("context", None)   
    return state

# Add node
builder_graph.add_node("infer_intent", infer_intent_node)
builder_graph.add_node("procurement", run_procurement_agent)
#builder_graph.add_node("credit", run_credit_agent)
builder_graph.add_node("siteops", run_siteops_agent)
builder_graph.add_node("cleanup", cleanup_node)

# Flow setup
builder_graph.set_entry_point("infer_intent")
builder_graph.add_conditional_edges(
    source="infer_intent",
    path=intent_router,  # LLM node that returns agent label
    path_map={
        "procurement": "procurement",
        #"credit": "credit",
        #"transport": "siteops",  # fallback
        "siteops": "siteops"
    }
)


builder_graph.add_edge("procurement", "cleanup")
builder_graph.add_edge("siteops", "cleanup")

builder_graph = builder_graph.compile()
