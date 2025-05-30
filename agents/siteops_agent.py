# agents/siteops_agent.py

import os
import json
import base64
import openai
from typing import Dict
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from tools.lsie import _local_sku_intent_engine
from tools.context_engine import filter_tags, vector_search
from models.chatstate import AgentState
from unitofconstruction.uoc_manager import UOCManager

load_dotenv()

llm_reasoning = ChatOpenAI(model="gpt-4o", temperature=0, openai_api_key=os.getenv("OPENAI_API_KEY"))
llm_context = ChatOpenAI(model="gpt-3.5-turbo", temperature=0, openai_api_key=os.getenv("OPENAI_API_KEY"))

def encode_image_base64(image_path: str) -> str:
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")

async def run_siteops_agent(state: AgentState) -> AgentState:
    print("SiteOps Agent::::: run_siteops_agent::::: -- Siteops agent called -- ")

    if state.get("agent_first_run", True):
        uoc_manager = UOCManager()
        state = await uoc_manager.resolve_uoc(state, "siteops")
        context = get_context(state)
        print("SiteOps Agent::::: run_siteops_agent::::: <is_first_time Yes> --Stage 1: Context extracted --", context)
        state["context"] = context

        if state.get("uoc_confidence") == "low":
            print("SiteOps Agent::::: run_siteops_agent::::: <uoc_confidence Low> -- Returning state --")
            state["agent_first_run"] = False
            return state

    else:
        print("SiteOps Agent::::: run_siteops_agent::::: <is_first_time No> -- Continuing with existing state")

    reasoning_input = format_reasoning_input(state)
    state["uoc_confidence"] = "high"
    state["uoc_pending_question"] = False
    print("SiteOps Agent::::: run_siteops_agent::::: -- Stage 2: Reasoning input prepared --", reasoning_input)

    result = get_reason(state, reasoning_input)
    print("SiteOps Agent::::: run_siteops_agent::::: -- Stage 2: Reasoning result --", result)

    state["latest_response"] = result
    state["messages"].append({"role": "assistant", "content": result})
    state["agent_first_run"] = False
    return state

def get_reason(state: dict, reasoning_input: str) -> str:
    system_prompt_text = reasoning_prompt()
    context = state.get("context_tags", "")
    uoc_summary = json.dumps(state.get("uoc", {}).get("data", {}), indent=2)

    chat_response = llm_reasoning.invoke([
        SystemMessage(content=system_prompt_text),
        HumanMessage(content=f"User message:\n{reasoning_input}\n\nContext:\n{context}\n\nUOC State:\n{uoc_summary}")
    ])
    return chat_response.content.strip()

def get_context(state: dict) -> str:
    last_msg = state["messages"][-1]["content"]
    image_path = state.get("image_path")
    image_caption = state.get("caption", "")
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
    return (
        """
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
    )

def reasoning_prompt() -> str:
    return (
        """
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
    )

def format_reasoning_input(state: dict) -> str:
    return state["messages"][-1]["content"]