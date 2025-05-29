# agents/siteops_agent.py

from tools.lsie import _local_sku_intent_engine
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from tools.context_engine import filter_tags, vector_search
from models.chatstate import AgentState
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

async def run_siteops_agent(state: AgentState) -> AgentState:
    print("SiteOps Agent::::: run_siteops_agent::::: -- Siteops agent called -- ")

    # if state.get("uoc_pending_question", False):
    #     print("UOCManager still clarifying — skipping agent reasoning.")
    #     return state

    is_first_time = state.get("agent_first_run", True)

    if is_first_time:
       # I think we need to get use the messaage to and get the struture and details of the UOC first. The UOC hanndles the state with the found project structure else it will ask the user for the missing details.
       #    Get the message user sent 
       #    send the message back to the UOC manager to get the project structure and details
       #    The UOC manager sends teh state back with the found or created project.
       #    Now work on that particular project until a new project work is called 
        uoc_manager = UOCManager()
        state = await uoc_manager.resolve_uoc(state, "siteops" )
        context = get_context(state)
        print("SiteOps Agent::::: run_siteops_agent::::: <is_first_time Yes>  --Stage 1: Context extracted after resolution from UOC Manager: --", context)
        state["context"] = context
        #state = await UOCManager. run(state, called_by="siteops")
        
        
        if state.get("uoc_confidence") == "low":
            print("SiteOps Agent::::: run_siteops_agent::::: <is_first_time Yes>::::: <uoc_confidence Low> --Stage 1:  Confience low, returing state without reasoning, state : --", state)
            state["agent_first_run"] = False
            #print("SiteOps Agent::::: State from UOC manager:", state)
            return state
    else:
        print("SiteOps Agent::::: run_siteops_agent::::: <is_first_time NO > -- Not first run, using existing state")
    reason_input = format_reasoning_input(state)
    state["uoc_confidence"]= "high"  # Assuming high confidence for reasoning stage
    state["uoc_pending_question"] = False  # Reset pending question flag for reasoning stage
    print("SiteOps Agent::::: run_siteops_agent::::: -- Stage 2: Preparing reasoning stage, Attributes -- ", reason_input)

    result = get_reason(state, reason_input)
    print("SiteOps Agent::::: run_siteops_agent::::: -- Stage 2: Reasoning Result: -- ", result)

    state["latest_response"] = result
    state["messages"].append({"role": "assistant", "content": result})
    state["agent_first_run"] = False
    return state

def get_reason(state: dict, reasoning_input: str) -> str:
    system_prompt = reasoning_prompt()
    context = state.get("context_tags", "")
    uoc_summary = json.dumps(state.get("uoc", {}).get("data", {}), indent=2)

    chat_response = llm_reasoning.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"User message:\n{reasoning_input}\n\nContext:\n{context}\n\nUOC State:\n{uoc_summary}")
    ])

    return chat_response.content.strip()

def get_context(state: dict):
    last_msg = state["messages"][-1]["content"]
    image_path = state.get("image_path")
    image_caption = state.get("caption", None)
    combined_input = f"Message: {last_msg}\nCaption: {image_caption}".strip()

    if image_path:
        image_base64 = encode_image_base64(image_path)

        vision_response = openai.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt()},
                {"role": "user", "content": [
                    {"type": "text", "text": combined_input},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}}
                ]}
            ],
            max_tokens=300
        )
        return vision_response.choices[0].message.content.strip()

    query_response = llm_context.invoke([
        SystemMessage(content=system_prompt()),
        HumanMessage(content=combined_input)
    ])

    return query_response.content.strip()

def system_prompt() -> str:
    return """
You are a construction site quality assistant trained to analyze BOTH photos and text updates from Indian construction sites. Your job is to summarize the work and enrich the builder with best practices, realistic insights, and possible risks. Follow this structure:
 Understanding
- Analyze photos and text together.

Describe Work
- Component:
- Stage of Work:
- Location Hints:
- Observation:

Enrich with Contextual Knowledge
A) Guidelines (IS codes + standard practices)
B) Realistic Understanding (Field practice)
C) Common Problems / Complaints
D) Possible Risks

Rules:
- Be factual, detailed, informative and structured.
- Avoid assumptions beyond input + verified knowledge.
- If nothing is found, say "No meaningful construction work or update is detected in this input."
"""

def reasoning_prompt() -> str:
    return """
You are a construction site reasoning assistant. You are provided with:
1. User message (site update)
2. Context (IS codes, complaints, field tips)
3. UOC State (site meta info)

Your task:
- Compare the site update with the context + UOC state.
- Analyze and suggest if work reported matches expected work (yes/no + why).
- List any gaps, risks, or anomalies.
- Suggest actionable next steps.
- Provide recommendations for quality, safety, or sequencing.

Format:
Risks: (One line)
Actionable Items: (Up to 3 bullet points)
Next Stage Preparations: (Up to 2 bullet points)
Potential Financial Impact: (One line)

Respond concisely. If no data is sufficient, say 'No relevant comparison possible'.
"""

def format_reasoning_input(state: dict) -> str:
    last_msg = state["messages"][-1]["content"]
    return last_msg
