import os
import json
from typing import Dict, Optional, List
import random
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


    user_name = None
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
        user_message = (
            state.get("messages", [])[-1].get("content", "").strip().lower()
            if state.get("messages") else "")

    # 1. If user is responding to a fuzzy match confirmation
        if state.get("uoc_pending_question") and "fuzzy_project_suggestion" in state:
            print("UOC Manager:::::: select_or_create_project:::::  -- Confirming yes or no   --", possible_project_name)
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
            print("UOC Manager:::::: select_or_create_project:::::  -- Is project ifrom the list    --", possible_project_name)
            selected_id = user_message
            if selected_id in [proj["id"] for proj in user_projects]:
                state["active_project_id"] = selected_id
                state["uoc_pending_question"] = False
                return state

            if selected_id == "add_new":
                state["uoc_pending_question"] = False
                return await self.collect_project_structure_with_priority_sources(state)

    # 3. First attempt: try fuzzy match from possible name
        if possible_project_name:
            print("UOC Manager:::::: select_or_create_project:::::  --  fuzzy match from possible name    --", possible_project_name)
            match = fuzzy_match_project_name(possible_project_name, user_projects)
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
        return await self.collect_project_structure_with_priority_sources(state)


    async def resolve_uoc(self,  state: Dict, uoc_last_called_by: str ) -> Dict:
        print("UOC Manager:::::: resolve_uoc:::::  -- Called UOC Manager")
        state["uoc_last_called_by"] = uoc_last_called_by
        message = state.get("messages", [])[-1]["content"].strip() if state.get("messages") else ""
        user_input = state.get("caption") or message or ""
        possible_project_name = await self.extract_possible_project_name(user_input)
        state= await self.select_or_create_project(state, possible_project_name)
        print("UOC Manager:::::: resolve_uoc::::: -- State after the project selection/ creation : --", state)


        if state.get("uoc_pending_question"):
            state["uoc_confidence"] = "low"
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
            return await self.collect_project_structure_with_priority_sources(state)
            
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
    async def collect_project_structure_with_priority_sources(self, state: Dict) -> Dict:
        """
        Modular project structure collector with prioritized input types:
        - If integrated tool data available (workflow handler): use it.
        - Else, ask for plan image or PDF.
        - Else, check for WhatsApp guided flow (future).
        - Else, fall back to iterative collection.
        """
        print("UOC Manager:::::: collect_project_structure_with_priority_sources:::::: -- Modular project structure collection initiated --")

        # 1. Check if external workflow handler has project structure
        if state.get("workflow_project_structure"):
            print("UOC Manager:::::: collect_project_structure_with_priority_sources:::::: -- Found project structure from workflow handler --")
            state["project_structure"] = state["workflow_project_structure"]
            state["uoc_pending_question"] = False
            state["uoc_confidence"] = "high"
            return state

        # 2. Check if user already responded to plan image or document question
        last_message = state.get("messages", [])[-1].get("content", "").strip().lower() if state.get("messages") else ""
        question_type = state.get("uoc_question_type")

        if question_type == "has_plan_or_doc":
            if last_message in ["yes", "i have a plan", "yes i have a plan"]:
                print("UOC Manager:::::: collect_project_structure_with_priority_sources:::::: -- User has a plan, requesting upload --")
                state["latest_respons"] = "Please upload the project plan image or PDF."
                # Future: handle plan image/pdf processing here
                state["uoc_pending_question"] = True
                return state
            elif last_message in ["no", "continue without it"]:
                print("UOC Manager:::::: collect_project_structure_with_priority_sources:::::: -- User said no to plan, continuing with fallback flow --")
                # Future: WhatsApp template flow logic could go here
                return await self.collect_project_structure_interactively(state)
        possible_messages = [
"Ah, we’re starting from scratch — no problem at all. I’ll guide you through it. Just to help me do that better: do you have any kind of plan or layout?",
    "Looks like we haven’t set up a project yet — and that’s totally fine. I’ll help you from here. If you have a drawing or layout, even a rough one, it can make this part a lot easier.",
    "Hmm, seems like this is a fresh start. Nothing to worry about. I’ll help you get it going — just before that, do you have a plan or image I could refer to?",
    "Alright — no project linked yet. Happens all the time. I’ll take care of the setup with you. If you happen to have a layout or basic sketch, it’ll help avoid confusion later.",
    "Okay, I didn’t find a project here yet. All good. Let’s set one up quickly. Do you have anything that shows the layout? Even a photo works — it helps me guide things right.",
    "Just getting started here? That’s absolutely fine. I’ll help set up the project. To save you time later — do you have a plan, sketch, or drawing handy?",
    "No project yet — no stress. I’ll walk with you through it. If there’s anything visual — even a rough layout or photo — it’ll help me support you more clearly.",
    "Alright, I see we haven’t created a project yet. That’s totally okay. I’ll guide the setup — just checking, do you have any drawing or site sketch to begin with?",
    "Looks like we’re right at the start. That’s great. I’ll help you get things going. Do you happen to have a plan or PDF? It’ll help me set things up more smoothly for you.",
    "Ah — nothing set up yet, which is perfectly normal. I’ll take care of it. To keep things simple and accurate, do you have any kind of layout I could look at?",
]
        # 3. If we haven't asked yet → ask if they have a plan or docume
        if question_type not in {"has_plan_or_doc", "project_formation"}:
            print("UOC Manager:::::: collect_project_structure_with_priority_sources:::::: -- Asking user if they have a project plan or document --")
            state.update({
            "uoc_pending_question": True,   
            "uoc_question_type": "has_plan_or_doc",
            "latest_respons": random.choice(possible_messages),
            "uoc_next_message_type": "button",
            "uoc_next_message_extra_data": [
                {"id": "has_plan", "title": "I have a plan"},
                {"id": "no_plan", "title": "I don't"}
            ]
            })
            return state

        # 4. Fallback
        print("UOC Manager:::::: collect_project_structure_with_priority_sources:::::: -- Fallback to iterative collection --")
        return await self.collect_project_structure_interactively(state)


    async def collect_project_structure_interactively(self, state: Dict) -> Dict:
        print("UOC Manager:::::: collect_project_structure_interactively:::::: -- Started collecting details--")
    
        """LLM driven progressive project setup conversation."""
        chat_history = state.get("messages", [])
        project_structure = state.get("project_structure", {})

        system_prompt = (
  "You are a structured assistant helping a builder fill out their project setup on WhatsApp.\n\n"

    "Your task is to fill in the following fields one-by-one, in strict order:\n"
    "- project_name (text)\n"
    "- project_type (choose from: Individual House, Apartment, Gated Community, High Rise)\n"
    "- number_of_blocks (integer)\n"
    "- number_of_floors (integer)\n"
    "- flats_per_floor (integer)\n"
    "- region_types (list, like: 1BHK, 2BHK, 3BHK, 4BHK, etc)\n\n"

    "Behavior rules:\n"
    "- From the conversation history, extract any already-known fields.\n"
    "- Only ask for the next missing field, one at a time.\n"
    "- Be warm, polite, and brief.\n"
    "- If the latest message is funny, off-topic, emoji-only, or unclear:\n"
    "     → Gently acknowledge it, then respond with empathy(e.g., with empathy, a smile, or understanding tone),\n"
    "     → Then re-ask the current pending field in a supportive and friendly way.\n"
    "- If user repeatedly sends irrelevant messages, remain patient and gently repeat the current field request.\n"
    "- Be emotionally aware, but stay focused.\n"
    "- Do not proceed to the next field until the current one is answered.\n"
    "- Use interaction types wisely:\n"
    "    'plain' → open-text\n"
    "    'button' → up to 3 choices\n"
    "    'list' → more than 3 choices\n\n"

    "Output STRICTLY in this JSON structure, and nothing else:\n"
    "{\n"
    '  "uoc": { ...fields },\n'
    '  "latest_respons": "friendly and clear next message",\n'
    '  "next_message_type": "plain" | "button" | "list",\n'
    '  "next_message_extra_data": [options if applicable, else null],\n'
    '  "uoc_pending_question": true | false,\n'
    '  "uoc_confidence": "low" | "high"\n'
    "}\n"

    "You must not include markdown, explanations, comments, or formatting beyond the JSON above."
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
                print("UOC Manager:::::: collect_project_structure_interactively:::::: <pending_question No> -- Saving the project to DB --")
                self.project_db.save_project_for_user(state["sender_id"], state["active_project_id"], project_structure)
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
