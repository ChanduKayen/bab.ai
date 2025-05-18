from typing import Dict, Optional
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
import os
from dotenv import load_dotenv
import json
from datetime import datetime
import re

load_dotenv()

llm = ChatOpenAI(
    model="gpt-4o",
    temperature=0,
    openai_api_key=os.getenv("OPENAI_API_KEY")
)

def clean_llm_response(result: str) -> str:
    """Remove markdown formatting and extract JSON block"""
    result = result.strip()
    result = re.sub(r"```(json)?", "", result)
    result = result.replace("```", "").strip()
    return result

class UOCManager:
    @staticmethod
    async def run(state: Dict, prompt: Optional[str] = None, called_by: Optional[str] = None) -> Dict:
        print("$$$$$$$$$$ UOCManager called $$$$$$$$$$$")

        chat_history = state.get("messages", [])
        uoc_state = state.get("uoc", {}).get("data") if state.get("uoc") else None

        system_prompt = prompt or (
            "You are a construction site assistant designed to identify and progressively build the Unit of Construction (UOC) from user messages (text or image).\n\n"
            "A UOC represents a construction project and may include:\n"
            "- project_name\n"
            "- project_type (choose from: Villa, Individual House, Apartment, Gated Community, High Rise)\n"
            "- number_of_blocks\n"
            "- number_of_floors\n"
            "- flats_per_floor\n"
            "- block\n"
            "- flat_number\n"
            "- floor\n"
            "- site_name (zone like: bathroom, staircase, terrace, balcony etc.)\n\n"
            "You must progressively ask only for missing fields. Follow this order:\n"
            "1. If project_type is missing, ask to select project type (use 'button').\n"
            "2. If number_of_floors is missing, ask how many floors (use 'button' for 1,2,3,5,10,20).\n"
            "3. If flats_per_floor is missing, ask for number of flats per floor (use 'plain').\n"
            "4. Once structure is built, ask for missing block / flat / floor / site_name if needed.\n\n"
            "IMPORTANT WhatsApp platform rules:\n"
            "- 'button' allows max 3 buttons. If >3 options, use 'list' instead.\n"
            "- Always prefer button or list over plain text if possible.\n"
            "- Only use plain when expecting a free text response (e.g. numeric value).\n\n"
            "Your response must be strictly JSON in this format:\n"
            "{\n"
            '    "uoc": {},\n'
            '    "latest_respons": "",\n'
            '    "next_message_type": "",\n'
            '    "next_message_extra_data": null\n'
            "}\n\n"
            "Examples:\n\n"
            "1️⃣ Example: Ask for project type\n"
            "{\n"
            '    "uoc": {},\n'
            '    "latest_respons": "Please select the project type.",\n'
            '    "next_message_type": "button",\n'
            '    "next_message_extra_data": [\n'
            '        {"id": "villa", "title": "Villa"},\n'
            '        {"id": "ind_house", "title": "Individual House"},\n'
            '        {"id": "apartment", "title": "Apartment"}\n'
            "    ]\n"
            "}\n\n"
            "2️⃣ Example: Ask for number of floors\n"
            "{\n"
            '    "uoc": {"project_type": "apartment"},\n'
            '    "latest_respons": "How many floors does this building have?",\n'
            '    "next_message_type": "button",\n'
            '    "next_message_extra_data": [\n'
            '        {"id": "1", "title": "1"},\n'
            '        {"id": "2", "title": "2"},\n'
            '        {"id": "3", "title": "3"}\n'
            "    ]\n"
            "}\n\n"
            "3️⃣ Example: Ask for flats per floor\n"
            "{\n"
            '    "uoc": {"project_type": "apartment", "number_of_floors": "5"},\n'
            '    "latest_respons": "How many flats per floor? Please enter a number.",\n'
            '    "next_message_type": "plain",\n'
            '    "next_message_extra_data": null\n'
            "}\n\n"
            "Strict rules:\n"
            "- Never give markdown, code blocks, comments, or explanation.\n"
            "- Only output valid JSON object exactly as per format.\n"
        )

        messages = [SystemMessage(content=system_prompt)]
        for msg in chat_history:
            messages.append(HumanMessage(content=msg["content"]))

        if uoc_state:
            messages.append(
                HumanMessage(content=f"Current known UOC state:\n{json.dumps(uoc_state)}")
            )

        response = await llm.ainvoke(messages)
        result = clean_llm_response(response.content)
        print(f"Raw LLM response:\n{result}")

        try:
            parsed_result = json.loads(result)

            uoc_data = parsed_result.get("uoc", {})
            state["uoc"] = {
                "data": uoc_data,
                "confidence": "high" if uoc_data else "low",
                "last_updated": datetime.utcnow().isoformat()
            }
            state["uoc_confidence"] = "high" if uoc_data else "low"
            state["uoc_pending_question"] = False if uoc_data else True
            state["uoc_last_called_by"] = called_by

            state["latest_respons"] = parsed_result.get("latest_respons", "")
            state["uoc_next_message_type"] = parsed_result.get("next_message_type", "plain")
            state["uoc_next_message_extra_data"] = parsed_result.get("next_message_extra_data", None)

            print("UOCManager updated state:")
            print(state)

        except Exception as e:
            print(f"LLM response parsing failed: {e}")
            state["uoc_pending_question"] = True
            state["uoc_confidence"] = "error"
            state["uoc_last_called_by"] = called_by
            state["latest_respons"] = f"Couldn't process UOC properly: {e}"
            state["uoc_next_message_type"] = "plain"
            state["uoc_next_message_extra_data"] = None

        return state
