from langgraph.graph import StateGraph
from agents.procurement_agent import run_procurement_agent
from agents.random_agent import classify_and_respond
#from agents.credit_agent import run_credit_agent
from agents.siteops_agent import run_siteops_agent
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
import os
from dotenv import load_dotenv
from models.chatstate import AgentState
import base64
import openai  # Import the OpenAI module
import json  # Import the JSON module
import re  # Import the re module for regular expressions
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
def build_intent_prompt(image_path=None, message_text=""):
    print("Orchestrator::::: build_intent_prompt::::: --Building intent prompt -- ", message_text, image_path)
    if image_path:
        image_base64 = encode_image_base64(image_path)
        return [
            {"role": "system", "content": (
                "You are an intent router for a construction assistant.\n"
                "Given a user message and an image (like a bill, site photo, or plan), classify the intent.\n"
                "Respond with only one of: procurement, credit, siteops, transport, random"
            )},
            {"role": "user", "content": [
                {"type": "text", "text": message_text},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}}
            ]}
        ]
    else:
        return [
            SystemMessage(content=(
                "You are an intent router for a construction procurement assistant.\n"
                "Classify the user message into one of: procurement, credit, transport, siteops, random"
            )),
            HumanMessage(content=message_text)
        ]    
def build_insight_prompt(image_path, message_text=""):
    image_base64 = encode_image_base64(image_path)
    print("Orchestrator::::: build_insight_prompt::::: --Building insight prompt -- ", message_text, image_path)
    return [
        {
            "role": "system",
            "content": (
    "You are an advanced construction intelligence assistant.\n"
    "You are reviewing an image sent by a builder, site engineer, or vendor.\n"
    "The image may be a photo of a construction site, building component, document (like a bill or quote), or handwritten note.\n\n"

    "Your responsibilities:\n"
    "1. Classify the overall intent of the image. Choose ONLY one from:\n"
    "   - procurement (materials, bills, price lists, quotes, vendor info)\n"
    "   - siteops (site status, worker activity, construction stage, safety)\n"
    "   - credit (invoices, GST, payments, financial docs)\n"
    "   - transport (delivery photos, vehicles, loading, unloading)\n"
    "   - random (anything else)\n\n"

    "2. Extract ALL useful structured information under `insights`. You must think like a site manager, planner, and quality inspector — and include both **visible facts** and **intelligent inferences**:\n\n"

    "  - materials: List all materials visible or implied (e.g. bricks, cement, steel, tiles, pipes).\n"
    "              These should be actual materials from the image, not generic lists.\n\n"
    "  - quantities: Try to estimate or extract quantities only if visible (e.g. 3 bags, 6 rods).\n"
    "                Format as objects with name and estimate.\n\n"
    "  - components: Building components involved — like wall, slab, column, beam, ceiling, footing.\n"
    "                These must be clearly part of the construction image.\n\n"
    "  - measurements: Include real or inferred dimensions (e.g. wall height, slab thickness), only if extractable.\n"
    "                  Format units if possible (e.g. 9 ft, 4 inches).\n\n"
    "  - bill_info: Fill only if the image is a bill or invoice. Include:\n"
    "       - vendor_name\n"
    "       - invoice_number\n"
    "       - gstin\n"
    "       - total_amount\n"
    "       - date\n\n"
    "  - risks: Any visible safety or quality issues. Examples: no PPE, unsafe scaffolding, poor finishing.\n"
    "           Include only if truly seen or strongly inferred.\n\n"
    "  - progress_stage: Describe which construction stage is captured — e.g., shuttering ongoing, slab ready.\n"
    "                    Be as specific as the image allows.\n\n"
    "  - next_likely_step: What’s the logical next construction activity that should follow this stage?\n"
    "                      Base it on standard site practices.\n\n"
    "  - construction_method: Describe the method/technique used — not just the activity.\n"
    "                         E.g., ‘English bond’, ‘two-coat plaster’, ‘staggered joints’.\n\n"
    "  - execution_quality: Evaluate workmanship — signs of good or bad execution (e.g. uneven joints, clean lines).\n"
    "                       Be visual and specific.\n\n"
    "  - tools_equipment_seen: Any tools, machinery, or equipment visible (e.g. trowel, lift, bucket, drill).\n\n"
    "  - labor_seen: Estimate workers and their likely roles (e.g. 2 masons, 1 helper).\n\n"
    "  - weather_context: Infer weather from light, shadows, or background (e.g. sunny, cloudy, rainy).\n\n"
    "  - hidden_dependencies: List things that must be completed/verified before this step (e.g. waterproofing, curing).\n\n"
    "  - missing_elements: What’s expected but missing? (e.g. safety net, curing cloth, corner ties).\n\n"
    "  - General notes. Keep neutral and realistic. If unsure, say: 'Details unclear or not visible..\n"
    "                 These should reflect a site supervisor’s interpretation.\n\n"

    "⚠️ Format response as **pure JSON** with no markdown or commentary.\n"
    "If any field doesn’t apply, set it as an empty array `[]` or null — do not leave it out.\n\n"

    "🚫 DO NOT copy examples from this instruction into your final output.\n"
    "These are provided only for your understanding.\n"
    "In your actual response:\n"
    "- Use only details observed or inferred from the image and caption.\n"
    "- Never reuse example phrases like 'wall height 9 ft' or 'plastering halfway done' unless they are true.\n"
    "- Think like a site engineer. Ground your response in the **specific image and context**, not the prompt.\n"

    "⚠️ Output strictly as clean JSON. No markdown, no extra explanation. If a field doesn’t apply, set it to `[]` or `null`.\n\n"

    "Format:\n"
    "{\n"
    "  \"intent\": \"...\",\n"
    "  \"insights\": {\n"
    "    \"materials\": [...],\n"
    "    \"quantities\": [...],\n"
    "    \"components\": [...],\n"
    "    \"measurements\": [...],\n"
    "    \"bill_info\": {\n"
    "      \"vendor_name\": \"...\",\n"
    "      \"invoice_number\": \"...\",\n"
    "      \"gstin\": \"...\",\n"
    "      \"total_amount\": \"...\",\n"
    "      \"date\": \"...\"\n"
    "    },\n"
    "    \"risks\": [...],\n"
    "    \"progress_stage\": \"...\",\n"
    "    \"next_likely_step\": \"...\",\n"
    "    \"construction_method\": \"...\",\n"
    "    \"execution_quality\": [...],\n"
    "    \"tools_equipment_seen\": [...],\n" 
    "    \"labor_seen\": [...],\n"
    "    \"weather_context\": [...],\n"
    "    \"hidden_dependencies\": [...],\n"
    "    \"missing_elements\": [...],\n"
    "    \"observations\": \"...\"\n"
    "  }\n"
    "}"
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

async def run_insight_background(image_path: str, message_text: str, state: AgentState):
    try:
        messages = build_insight_prompt(image_path, message_text)
        response = await llm.ainvoke(messages)
        cleaned_response = re.sub(r"^```json\s*|```$", "", response.content.strip(), flags=re.IGNORECASE)
        
        parsed_json = json.loads(cleaned_response)
        state["insights"] = parsed_json.get("insights", {})
        print("Orchestrator:::::: Insights updated in background:", state["insights"])
       
    except Exception as e:
        print("Insight background task failed:", e) 

async def infer_intent_node(state: AgentState) -> AgentState:
    print()
    print("Orchestrator::::: infer_intent_node::::: -- Orchestrator called -- ", state)
    last_msg = state["messages"][-1]["content"]
    print("Orchestrator::::: infer_intent_node::::: -- last message in orchestrator - ", state)
    print("ORchestrator::::infre intent node:::: - messges found - ", last_msg)
    image_caption = state.get("caption", None)
    image_path = state.get("image_path", None)
    #print("Last Message -", last_msg)
    combined_input = f"Message: {last_msg}\ncaption: {image_caption}".strip()
    
    print("Orchestrator::::: infer_intent_node:::::  --If image path: --", image_path, combined_input)
    if image_path:
        print("Orchestrator::::: infer_intent_node:::::  --Image path found: --", image_path)
        messages = build_intent_prompt(image_path=image_path, message_text=combined_input)
        
        response = openai.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            max_tokens=500 # changes 10 to 500
            #New add - Preprocessnig the insight
                     )
        print("Orchestrator::::: infer_intent_node:::::  --Intent Response from LLM: --", response.choices[0].message.content.strip())
        state["intent"]= response.choices[0].message.content.strip().lower()
        #Asynchronously process the image for insights
        
        
        #-------------------------------------------------------------------------------------------------
        # I wnated to get the insights from the image and caption before sending the intent to the next agent pralallelly. But Commenting it due to perfromance issues.
        #-------------------------------------------------------------------------------------------------
        
        # if state["intent"] in {"siteops", "procurement", "credit", "transport"}:
        #     asyncio.create_task(run_insight_background(image_path, combined_input, state))   

        return state

    print("Orchestrator::::: infer_intent_node::::: Before LLM Call")
    system_prompt = (
        "You are an intent router for a construction procurement assistant.\n"
        "Given the user message, return the name of the agent that should handle it.\n"
        #"or if its a random content irrelvant to construction say random"
       "Possible agents: procurement, credit, transport, siteops, random\n"
       "Respond ONLY with one of: procurement, credit, transport, siteops, random"
    ) 
    
    try:
        chat_response = await llm.ainvoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=last_msg)
        ])
    except Exception as e:
        print("Orchestrator::::: infer_intent_node::::: LLM call failed:", e)
        state["intent"] = "random"
        return state
    print("Orchestrator::::: infer_intent_node::::: After LLM Call")
    

    intent = chat_response.content.strip().lower()
    
    print("Orchestrator::::: infer_intent_node:::::  --Intent of text found: --", intent)
    state["intent"] = intent
    #print("Orchestrator::::: ++++++++++++++State after intent inference in orchestrator", state)
    
    return state

def intent_router(state: AgentState) -> str:
     print("Orchestrator::::: intent_router:::::")
     return state["intent"]


#Added the cleanup to clear the state to avoid data leakage between next calls
# This is a temporary solution, ideally we should have a better way to handle state management
async def cleanup_node(state: AgentState) -> AgentState:
    keys_to_remove = [
        "image_path", "caption", "media_id", "context_tags", "context",
        "uoc", "uoc_confidence", "needs_clarification", "uoc_last_called_by",
        "agent_first_run", "latest_response", "messages", "sender_id", "intent",
        "insights"
    ]
    for key in keys_to_remove:
        state.pop(key, None)
    print("Orchestrator::::: cleanup_node::::: --Cleaned up state -- ", state)
    return state

# Add node
try:
    builder_graph.add_node("infer_intent", infer_intent_node)
    builder_graph.add_node("procurement", run_procurement_agent)
    #builder_graph.add_node("credit", run_credit_agent)
    builder_graph.add_node("siteops", run_siteops_agent)
    builder_graph.add_node("random", classify_and_respond)
    builder_graph.add_node("cleanup", cleanup_node)
except Exception as e:
    print("Error adding nodes to builder_graph:", e)

# Flow setup
try:
    builder_graph.set_entry_point("infer_intent")
    builder_graph.add_conditional_edges(
        source="infer_intent",
        path=intent_router,  # LLM node that returns agent label
        path_map={
            "procurement": "procurement",
            #"credit": "credit",
            "random": "random",  # fallback
            "siteops": "siteops"
        }
    )

    builder_graph.add_edge("procurement", "cleanup")
    builder_graph.add_edge("siteops", "cleanup")
    #builder_graph.add_edge("random", "cleanup")

    builder_graph = builder_graph.compile()
except Exception as e:
    print("Error setting up builder_graph flow:", e)


