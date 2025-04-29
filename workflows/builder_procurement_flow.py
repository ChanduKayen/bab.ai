from langgraph.graph import StateGraph
from agents.procurement_agent import run_procurement_agent
#from agents.credit_agent import run_credit_agent
#from agents.siteops_agent import run_siteops_agent
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
import os
from dotenv import load_dotenv
from typing import TypedDict, List, Optional
load_dotenv()  # lodad environment variables from .env file


class AgentState(TypedDict):
    messages: List[dict]
    sender_id: str
    intent: Optional[str]

builder_graph = StateGraph(AgentState)
#llm = ChatOpenAI(model="gpt-4", temperature=0)
llm = ChatOpenAI(
    model="gpt-4",
    temperature=0,
    openai_api_key=os.getenv("OPENAI_API_KEY")  # safely pulls from env
)

async def infer_intent_node(state: AgentState) -> AgentState:
    last_msg = state["messages"][-1]["content"]
    #print("Last Message -", last_msg)
    system_prompt = (
        "You are an intent router for a construction procurement assistant.\n"
        "Given the user message, return the name of the agent that should handle it.\n"
        "Possible agents: procurement, credit, transport, siteops\n"
        "Respond ONLY with one of: procurement, credit, transport, siteops"
    )
    
    chat_response = await llm.ainvoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=last_msg)
    ])
    
    intent = chat_response.content.strip().lower()
    #print("Found this intent -", intent)
    state["intent"] = intent
    return state

def intent_router(state: AgentState) -> str:
    print("returning state:", state)
    #intnt = state["intent"]
    
    return state["intent"]


# Add nodes
builder_graph.add_node("infer_intent", infer_intent_node)
builder_graph.add_node("procurement", run_procurement_agent)
#builder_graph.add_node("credit", run_credit_agent)
#builder_graph.add_node("siteops", run_siteops_agent)

# Flow setup
builder_graph.set_entry_point("infer_intent")
builder_graph.add_conditional_edges(
    source="infer_intent",
    path=intent_router,  # LLM node that returns agent label
    path_map={
        "procurement": "procurement",
        #"credit": "credit",
        #"transport": "siteops",  # fallback
        #"siteops": "siteops"
    }
)

builder_graph = builder_graph.compile()
