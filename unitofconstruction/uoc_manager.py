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
    @staticmethod
    async def run(state: Dict, prompt: Optional[str] = None, called_by: Optional[str] = None) -> Dict:
        print("$$$$$$$$$$ UOCManager called $$$$$$$$$$$")

        chat_history = state.get("messages", [])
        uoc_state = state.get("uoc", {}).get("data") if state.get("uoc") else None

        system_prompt = prompt or (
            "You are a construction site assistant that identifies the Unit of Construction (UOC) from messages - terxt/ image. Try to deduce what the module is if you can (ex. dedeuce flat no, or work area like bnaathroom , staircase, etc., ).\n\n"
            "A UOC may consist of:\n"
            "- project_name\n"
            "- block\n"
            "- flat_number\n"
            "- floor\n"
            "- site_name\n\n"
            "Your job:\n"
            "1. If you find clear values for any of these fields, extract them into a JSON object.\n"
            "2. If all fields are missing or unclear or if more information is needed develop a clear understading of the unit, reply with a very short friendly clarification question.\n"
            "3. Do NOT ask for confirmation if values are already present.\n"
            "4. Do NOT engage in small talk, jokes, or unnecessary comments.\n"
            "5. Be direct, efficient, and helpful.\n\n"
            "Example output (when confident):\n"
            "{\n"
            '    "project_name": "Bhaskar Heights",\n'
            '    "block": "B",\n'
            '    "flat_number": "504",\n'
            '    "floor": "5th",\n'
            '    "site_name": "Hyderabad"\n'
            "}\n\n"
            "Example output (when unclear):\n"
            '"Could you please tell me which block or flat number you mean?"\n\n'
            "ALWAYS prefer to extract data. Only ask a question if essential."
        )

        # Construct message chain
        messages = [SystemMessage(content=system_prompt)]
        for msg in chat_history:
            messages.append(HumanMessage(content=msg["content"]))

        if uoc_state:
            messages.append(
                HumanMessage(content=f"Current known UOC state:\n{uoc_state}")
            )

        # Call LLM
        response = await llm.ainvoke(messages)
        print("LLM response:", response)
        result = response.content.strip()
        print("LLM response in uoc manager :", result.startswith("{"))
        try:
            if result.startswith("{"):
                # confident extraction
                parsed_uoc = eval(result)  # Replace with json.loads() for strict safety
                state["uoc"] = {
                    "data": parsed_uoc,
                    "confidence": "high",
                    "last_updated": datetime.utcnow().isoformat()
                }
                state["uoc_confidence"] = "high"
                state["uoc_pending_question"] = False
                state["uoc_last_called_by"] = called_by
                print("UOC Updated with high confidence:", state)
            else:
                # not confident → ask user
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
                "content": f"Couldn't process UOC: {e}"
            })

        return state
