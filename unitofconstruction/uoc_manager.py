import os
import json
from typing import Dict, Optional, List
from datetime import datetime
from dotenv import load_dotenv
from rapidfuzz import fuzz
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
load_dotenv()

llm = ChatOpenAI(
    model="gpt-4o",
    temperature=0,
    openai_api_key=os.getenv("OPENAI_API_KEY")
)


def clean_llm_response(raw_text: str) -> str:
    return raw_text.strip().replace("```json", "").replace("```", "")


class ProjectDatabase:
    def __init__(self):
        self.user_projects = {}  # Temporary in-memory DB

    def get_projects_for_user(self, sender_id: str) -> List[Dict]:
        return self.user_projects.get(sender_id, [])

    def save_project_for_user(self, sender_id: str, project_id: str, project_structure: dict):
        print("UOC Manager:::::: save_project_for_user::::: -- Saved Project, details: --", sender_id, "Project ID:", project_id)
        if sender_id not in self.user_projects:
            self.user_projects[sender_id] = []
        self.user_projects[sender_id].append({
            "id": project_id,
            "title": project_structure.get("project_name", "Unnamed Project"),
            "structure": project_structure
        })

    def get_project_structure(self, sender_id: str, project_id: str) -> Optional[dict]:
        for project in self.user_projects.get(sender_id, []):
            if project["id"] == project_id:
                return project["structure"]
        return None


def fuzzy_match_project_name(user_text: str, project_list: List[Dict]) -> Optional[Dict]:
    best_match = None
    highest_score = 0
    for project in project_list:
        score = fuzz.ratio(user_text.lower(), project["title"].lower())
        if score > highest_score:
            highest_score = score
            best_match = project
    return best_match if highest_score >= 85 else None


class UOCManager:
    def __init__(self, openai_api_key: Optional[str] = None):
        self.llm = ChatOpenAI(
            model="gpt-4o",
            temperature=0,
            openai_api_key=openai_api_key or os.getenv("OPENAI_API_KEY")
        )
        self.project_db = ProjectDatabase()

    async def extract_possible_project_name(self, caption_text: str) -> str:
        prompt = "Extract the possible project name or block reference from this text. Return onlly the project name if found, empty string if nothing found. No comments, no explanations, nothing"
        messages = [SystemMessage(content=prompt), HumanMessage(content=caption_text)]
        response = await self.llm.ainvoke(messages)
        print("UOC Manager:::::: extract_possible_project_name::::: --The possible project name's LLM resoponse is --", response.content.strip())
        return response.content.strip()

    async def select_or_create_project(self, state: Dict, possible_project_name: Optional[str]) -> Dict:
        print("UOC Manager:::::: select_or_create_project:::::  -- Called this function with a potential project name  --", possible_project_name)
        sender_id = state["sender_id"]
        user_projects = self.project_db.get_projects_for_user(sender_id)
        user_message = state.get("messages", [])[-1]["content"].strip().lower() if state.get("messages") else ""

    # 1. If user is responding to a fuzzy match confirmation
        if state.get("uoc_pending_question") and "fuzzy_project_suggestion" in state:
            if user_message == "yes":
                project = state["fuzzy_project_suggestion"]
                state["active_project_id"] = project["id"]
                del state["fuzzy_project_suggestion"]
                state["uoc_pending_question"] = False
                return state # Dont return state, respond back the resolve UOC

            elif user_message == "no":
                state["uoc_pending_question"] = True
                state["uoc_question_type"] = "project_selection"
                state["messages"].append({
                    "role": "assistant",
                    "content": "Okay, please select your project:",
                    "next_message_type": "button",
                    "next_message_extra_data": user_projects + [{"id": "add_new", "title": "➕ Add New Project"}]
                    })
                return state

    # 2. If user selected from project list
        if state.get("uoc_pending_question"):
            selected_id = user_message
            if selected_id in [proj["id"] for proj in user_projects]:
                state["active_project_id"] = selected_id
                state["uoc_pending_question"] = False
                return state

            if selected_id == "add_new":
                state["uoc_pending_question"] = False
                return await self.collect_project_structure_interactively(state)

    # 3. First attempt: try fuzzy match from possible name
        if possible_project_name:
            match = self.fuzzy_match_project_name(possible_project_name, user_projects)
            if match:
                state["fuzzy_project_suggestion"] = match
                state["uoc_pending_question"] = True
                state["uoc_question_type"] = "project_selection"
                state["messages"].append({
                    "role": "assistant",
                    "content": f"Did you mean '{match['title']}'?",
                    "next_message_type": "button",
                    "next_message_extra_data": [{"id": "yes", "title": "Yes"}, {"id": "no", "title": "No"}]
                    })
                return state

    # 4. No fuzzy match or possible name → show project list (if any)
        if user_projects:
            state["uoc_pending_question"] = True
            state["uoc_question_type"] = "project_selection"
            state["messages"].append({
                "role": "assistant",
                "content": "Please select your project:",
                "next_message_type": "button",
                "next_message_extra_data": user_projects + [{"id": "add_new", "title": "➕ Add New Project"}]
                })
            return state

    # 5. No projects at all → begin onboarding immediately
        return await self.collect_project_structure_interactively(state)


    async def resolve_uoc(self,  state: Dict, uoc_last_called_by: str ) -> Dict:
        print("UOC Manager:::::: resolve_uoc:::::  -- Called UOC Manager")
        state["uoc_last_called_by"] = uoc_last_called_by
        message = state.get("messages", [])[-1]["content"].strip() if state.get("messages") else ""
        user_input = state.get("caption") or message or ""
        possible_project_name = await self.extract_possible_project_name(user_input)
        state= await self.select_or_create_project(state, possible_project_name)
        print("UOC Manager:::::: resolve_uoc::::: -- State after the project selection/ creation : --", state)


        if state.get("uoc_pending_question"):
            state["UOC Manager:::::: uoc_confidence"] = "low"
            print("UOC Manager:::::: resolve_uoc:::::  <uoc_pending_question Yes> --UOCManager has a question, sending the resonse back to siteops Agent--")
            return state
        active_project_id = state.get("active_project_id")
        print("UOC Manager:::::: resolve_uoc:::::   --Active project Set :-- ", active_project_id)
        
        # if active_project_id not in state:
        #     print("UOC Manager:::::: resolve_uoc::::: <active_project_id No>  Waiting for confirmation on project selection.")
        #     return state
        
        structure = self.project_db.get_project_structure(
        state["sender_id"], state["active_project_id"])

        if not structure:
            return await self.collect_project_structure_interactively(state)
            
        state["project_structure"] = structure

        extracted = await self.extract_candidate_fields(message, state["project_structure"])
        required = ("project_name", "block", "floor", "flat_number", "region_name", "region_type")
        if not extracted or not all(k in extracted for k in required):
            state["uoc_pending_question"] = True
            state["uoc_confidence"] = "low"
            return state

        region_id = self.build_region_id(extracted["block"], extracted["floor"], extracted["flat_number"], extracted["region_type"])
        blocks = state.setdefault("uoc_state", {}).setdefault("blocks", {})
        found = False

        for block_name, block in blocks.items():
            regions = block.get("regions", {})
            for region_name, region in regions.items():
                if region.get("region_id") == region_id:
                    found = True
                    state["uoc"] = {
                        "data": extracted,
                        "confidence": "high",
                        "last_updated": datetime.utcnow().isoformat(),
                        "uoc_found": True,
                        "region_data": region
                    }
                    break
            if found:
                break

        if not found:
            state = self.create_missing_layers(state, extracted)
            state["uoc"] = {
                "data": extracted,
                "confidence": "high",
                "last_updated": datetime.utcnow().isoformat(),
                "uoc_found": True,
                "region_data": state["uoc_state"]["blocks"][extracted["block"]]["regions"][extracted["region_name"]]
            }

        return state
    async def collect_project_structure_interactively(self, state: Dict) -> Dict:
        print("UOC Manager:::::: collect_project_structure_interactively:::::: -- Started collecting details--")
    
        """LLM driven progressive project setup conversation."""
        chat_history = state.get("messages", [])
        project_structure = state.get("project_structure", {})

        system_prompt = (
    "You are an intelligent and conversational assistant guiding a construction professional through setting up a new building project.\n\n"

    "Your job is to make this process feel as natural as a thoughtful back-and-forth conversation — while accurately collecting structured project details step by step.\n\n"

    " Your objective:\n"
    "Build a JSON object named `uoc` containing all relevant project setup information, collected progressively in the following strict order:\n"
    "1. project_name (text)\n"
    "2. project_type (one of: Individual House, Apartment, Gated Community, High Rise)\n"
    "3. number_of_blocks (integer)\n"
    "4. number_of_floors (integer)\n"
    "5. flats_per_floor (integer)\n"
    "6. region_types (list of types like: 1BHK, 2BHK, 3BHK, 4BHK, 5BHK, garden area, etc.)\n\n"

    " How to behave:\n"
    "- Review the entire message history (`messages`) to extract any already-provided details.\n"
    "- Ask the user for ONLY the next missing field — don’t jump ahead or ask multiple things.\n"
    "- Phrase your question clearly and politely, as if you're speaking with a busy site manager. Use a tone that’s smart, helpful, and concise.\n"
    "- Choose the best interaction type:\n"
    "    → Use 'plain' for open-text answers (like names or numbers)\n"
    "    → Use 'button' for simple fixed choices (up to 3 options)\n"
    "    → Use 'list' if the choices are longer than 3\n\n"

    " Once all fields are collected, mark the conversation as complete by setting `uoc_pending_question` to false.\n"
    "Until then, always keep it true.\n\n"

    " Output Format — STRICT JSON only, in the exact shape below. Never add explanations, commentary, or markdown:\n"
    "{\n"
    '  "uoc": { ...fields extracted so far... },\n'
    '  "latest_respons": "The next friendly question to ask the user" | Empty string if the required conditions are met,\n'
    '  "next_message_type": "plain" | "button" | "list",\n'
    '  "next_message_extra_data": [ options list if applicable, else null ],\n'
    '  "uoc_pending_question": true | false\n'
    "}\n"
)



        messages = [SystemMessage(content=system_prompt)]
        for msg in chat_history:
            messages.append(HumanMessage(content=msg["content"]))

        if project_structure:
            messages.append(HumanMessage(content=f"Current known project structure:\n{json.dumps(project_structure)}"))

        response = await self.llm.ainvoke(messages)
        result = response.content.strip().replace("```json", "").replace("```", "")
        
        try:
            parsed = json.loads(result)
            print("UOC Manager:::::: collect_project_structure_interactively::::::  -- Parsed respnose from LLM is :", parsed["latest_respons"])
            
            pending_question = parsed["uoc_pending_question"]
            if not pending_question:
                print("UOC Manager:::::: collect_project_structure_interactively:::::: <pending_question No> -- UOC is confident, Returning state to calling function  --")
                state["uoc_pending_question"] = False
                state["uoc_confidence"] = "high"
                state["uoc_question_type"] = None
                state["latest_respons"] = ""
                return state

            if "uoc" in parsed and isinstance(parsed["uoc"], dict):
                print("UOC Manager:::::: collect_project_structure_interactively:::::: <uoc  found in prased> -- Setting project structure  --")
                project_structure.update(parsed["uoc"])
                state["project_structure"] = project_structure
                
            
            if "latest_respons" in parsed:
                #state["messages"].append({"role": "assistant", "content": parsed["latest_respons"]})
                state["latest_respons"] = parsed["latest_respons"]
                state["uoc_next_message_type"] = parsed.get("next_message_type", "plain")
                state["uoc_next_message_extra_data"] = parsed.get("next_message_extra_data", None)
                state["uoc_pending_question"] = True
                state["uoc_question_type"] = "project_formation"
                if state["uoc_pending_question"]:
                    state["uoc_confidence"] = "low"
                    #state["poject_setup_done"] = False
                else:
                    state["uoc_confidence"] = "high"
                    self.project_db.save_project_for_user(state["sender_id"], state["active_project_id"], project_structure)
                    #state["poject_setup_done"] = True
            #state["uoc_confidence"] = "low" if state["uoc_pending_question"] else "high"
        except Exception as e:
            state["uoc_pending_question"] = True
            state["uoc_confidence"] = "low"
            state["messages"].append({"role": "assistant", "content": "I'm unable to parse that. Could you please provide the project details again?"})
        print("UOC Manager:::::: Collect_project_structure_interactively::::::  -- Final State in this method --", state)
        return state
  
    async def extract_candidate_fields(self, message: str, project_structure: Dict) -> Optional[Dict]:
        """LLM extraction of candidate region fields."""
        system_prompt = "You are a site assistant. Extract: project_name, block, floor, flat_number, region_name, region_type from user input."
        messages = [SystemMessage(content=system_prompt), HumanMessage(content=message)]
        response = await self.llm.ainvoke(messages)
        try:
            if response.content.strip().startswith("{"):
                return json.loads(response.content.strip())
        except Exception:
            pass
        return None
    
    def build_region_id(self, block: str, floor: int, flat_number: int, region_type: str) -> str:
        return f"{block}-{floor}-{flat_number}-{region_type}"
    
    def create_missing_layers(self, state: Dict, extracted: Dict) -> Dict:
        blocks = state.setdefault("uoc_state", {}).setdefault("blocks", {})
        block = blocks.setdefault(extracted["block"], {})
        regions = block.setdefault("regions", {})
        if extracted["region_name"] not in regions:
            regions[extracted["region_name"]] = {
                "region_id": extracted.get("region_id") or self.build_region_id(extracted["block"], extracted["floor"], extracted["flat_number"], extracted["region_type"]),
                "region_type": extracted["region_type"],
                "id_confidence": extracted.get("id_confidence", 0.8),
                "created_by": extracted.get("created_by", "LLM"),
                "expected_departments": [],
                "region_guidelines": [],
                "departments": {}
            }
        return state

    def create_expected_tasks(self, region: Dict):
        if not region["expected_departments"]:
            return
        for dept in region["expected_departments"]:
            if dept not in region["departments"]:
                region["departments"][dept] = [{
                    "task_id": f"{dept.lower()}-auto-task",
                    "progress": "Task not started. Please confirm.",
                    "comments": {
                        "supervisor_note": "",
                        "llm_reasoning": f"Expected department task for {dept}. No update yet."
                    },
                    "requires_update": True,
                    "update_responded": False,
                    "last_update_time": datetime.utcnow().isoformat()
                }]

    def calculate_region_progress(self, region: Dict):
        updates = [update for dept in region["departments"].values() for update in dept]
        completed = sum(1 for u in updates if not u["requires_update"])
        total = len(updates)
        region["region_progress"] = {
            "status": "Completed" if completed == total else "In Progress",
            "last_checked": datetime.utcnow().isoformat(),
            "confidence": round(completed / total, 2) if total else 0.0,
            "assumed_by": "system"
        }

    def get_guidelines_from_context_brain(self, region_type: str, project_structure: Dict, site_metadata: Dict) -> List[Dict]:
        mock_guidelines = {
            "Bathroom": [
                {"department": "Waterproofing", "task": "Apply waterproofing membrane."},
                {"department": "Tiling", "task": "Install floor and wall tiles."}
            ],
            "Bedroom": [
                {"department": "Electrical", "task": "Install conduits before plastering."},
                {"department": "Carpentry", "task": "Install wardrobes after painting."}
            ]
        }
        return mock_guidelines.get(region_type, [])
