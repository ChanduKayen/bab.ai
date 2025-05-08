from typing import Dict, Optional, List
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
import os
from dotenv import load_dotenv
import json
from datetime import datetime

load_dotenv()

llm = ChatOpenAI(
    model="gpt-4o",
    temperature=0,
    openai_api_key=os.getenv("OPENAI_API_KEY")
)

class UOCManager:
    #confident_state = {}
    @staticmethod
    async def run(state: Dict, prompt: Optional[str] = None, called_by: Optional[str] = None) -> Dict:
        print("$$$$$$$$$$UOCManager called $$$$$$$$$$$")
        """
        Tries to infer or build a UOC (Unit of Construction).
        If confident, stores UOC to state.
        If not, asks follow-up question via assistant message and flags pending state.
        """
        chat_history = state.get("messages", [])
        uoc_state = state.get("uoc", {}).get("data") if state.get("uoc") else None
    
        system_prompt = prompt or (
            "You are an assistant that manages construction units (UOCs - Unit of Construction). "
            "From the user's messages, identify or clarify the site, block, flat, or project being referred to. "
            "Ask only the most relevant, minimal questions if UOC is unclear. "
            "If confident, return the UOC as a JSON object with fields like: project_name, block, flat_number, floor, zone, or site_name."
            "Ask freindly, if you know their name call them using thier name - be personal and supportive, very minimal occasional fun is allowed"
        )

        # Construct message chain
        messages = [SystemMessage(content=system_prompt)]
        for msg in chat_history:
            messages.append(HumanMessage(content=msg["content"]))
        if uoc_state:
            messages.append(HumanMessage(content=f"Current known UOC state:\n{uoc_state}"))

        # Call LLM
        response = await llm.ainvoke(messages)
        result = response.content.strip()
        
        try:
            if result.startswith("{") and "project" in result.lower():  # simple sanity check
                parsed_uoc = eval(result)  # Use json.loads(result) if you're strict on input
                state["uoc"] = {
                    "data": parsed_uoc,
                    "confidence": "high",
                    "last_updated": datetime.utcnow().isoformat()
                }
                state["uoc_confidence"] = "high"
                state["uoc_pending_question"] = False
                state["uoc_last_called_by"] = called_by
                print("UOC Updated with high confidence:", state)
                #return state  #Send the state back tp calling agent
                #UOCManager.confident_state = state
            else:
                # Could not confidently infer UOC → ask user
                state["uoc_pending_question"] = True
                state["uoc_confidence"] = "low"
                state["uoc_last_called_by"] = called_by
                state["messages"].append({
                    "role": "assistant",
                    "content": result
                })
                print("UOC in low confidence:", state)
        except Exception as e:
            # LLM reply was malformed or failed
            state["uoc_pending_question"] = True
            state["uoc_confidence"] = "error"
            state["uoc_last_called_by"] = called_by
            state["messages"].append({
                "role": "assistant",
                "content": f" Couldn't process UOC: {e}"
            })

        return state
