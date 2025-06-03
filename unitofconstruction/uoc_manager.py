import os
import json
from typing import Dict, Optional, List
import random
from datetime import datetime
from dotenv import load_dotenv
from rapidfuzz import fuzz
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from whatsapp.builder_out import whatsapp_output
load_dotenv()

llm = ChatOpenAI(
    model="gpt-4o",
    temperature=0,
    openai_api_key=os.getenv("OPENAI_API_KEY")
)


def clean_llm_response(raw_text: str) -> str:
    return raw_text.strip().replace("```json", "").replace  ("```", "")


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
    def apply_patch(self, tree: Dict, patch: Dict):
        """
        Walk / create nodes along patch["path"], then write patch["field"]=value.
        Supports block:  floor:  flat:  (extend with task: etc.)
        """
        print("UOC Manager:::::: apply_patch::::: -- Applying patch to project structure --", patch, "to tree:", tree)
        node = tree
        for seg in patch.get("path", []):
            typ, name = seg.split(":", 1)

            if typ == "block":
                blocks = {b["block_name"]: b for b in node.setdefault("blocks", [])}
                node = blocks.setdefault(name, {"block_name": name, "floors": []})
                if node not in tree["blocks"]:
                    tree["blocks"].append(node)

            elif typ == "floor":
                floors = {f["floor_number"]: f for f in node.setdefault("floors", [])}
                num = int(name)
                node = floors.setdefault(num, {"floor_number": num, "flats": []})

            elif typ == "flat":
                flats = {f["flat_label"]: f for f in node.setdefault("flats", [])}
                node = flats.setdefault(name, {"flat_label": name, "tasks": []})

        # coerce simple digit strings → int
        val = patch["value"]
        if isinstance(val, str) and val.isdigit():
            val = int(val)

        node[patch["field"]] = val
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
"""I couldn’t find any ongoing project to link this update to — no worries! Let’s set one up real quick 🚀

This takes less than a minute and makes everything smoother from here — I’ll remember your blocks, floors, plans, and even auto-analyze photos or text going forward. Smarter replies. No repetition. More power to you. 💪

Let’s begin with your site plan 👇"""
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
        """
        One-turn loop to collect building structure over WhatsApp:
          • Sends chat history + current structure to the LLM
          • Receives structure update and control JSON
          • Merges result, updates state, and returns
        """
        chat_history = state.get("messages", [])
        project_structure = state.setdefault("project_structure", {})

        # ------------------------------------------------------------------
        # SYSTEM PROMPT — clear strategy, no patching, clarify vague input
        # ------------------------------------------------------------------
        system_prompt = (
            """
            You are a smart, friendly assistant helping a builder describe the floor layout of one block in their project, via WhatsApp.

            ================= GOAL =================
            Collect floor-wise layout of flats in one block — including flat types and facing — without making assumptions.

            ================ STRATEGY ================
            1. Ask one short, clear question at a time.
            2. Every question includes a 📋 Main Menu button.
               - If tapped, stop setup with: “No problem. We’ll continue project setup later whenever you're ready.”
            3. If user response is vague or unclear:
               - Gently ask again with rephrasing.
            4. Do NOT assume layout consistency — always confirm.
            5. If the porject setup is complete, respond with a congratulatory message and guide teh next steps.

            ================ STEPS PER BLOCK ================
            - Ask project and block name (default to Block A, B… if needed)
            - Ask number of floors
            - Ask number of flats per floor
            - Ask flat configurations (type + facing) for Floor 1
            - Ask if this layout is same for all floors
            - If not, ask:
              • From which floor layout changes
              • What changes (flat type / count / facing)
              • Configurations for those floors
            - Ask if they want to continue to next block

            At the end of your reasoning, ALWAYS respond in this exact JSON format:
            {
              "latest_respons": "<your next WhatsApp message here>",
              "next_message_type": "button",  // 'plain' for text-only, 'button' for buttons
              "next_message_extra_data": [{ "id": "<kebab-case>", "title": "<≤20 chars>" }, "{ "id": "<kebab-case>", "title": "<≤20 chars>" }", "{ "id": "main_menu", "title": "📋 Main Menu" }"],
              "project_structure": { <updated structure so far> },
              "uoc_pending_question": true,  // false if user exited
              "uoc_confidence": "low",      // 'high' only when structure is complete
              "uoc_question_type": "project_formation"
            }

            RULES:
            - Never wrap the JSON in markdown.
            - Return ONLY the JSON. No markdown, no extra text.
            """
        )

        # ------------------------------------------------------------------
        # BUILD LLM MESSAGE HISTORY
        # ------------------------------------------------------------------
        messages = [SystemMessage(content=system_prompt)]
        messages += [HumanMessage(content=m["content"]) for m in chat_history]

        if project_structure:
            messages.append(HumanMessage(content="Current known project structure:\n" + json.dumps(project_structure)))

        # ------------------------------------------------------------------
        # CALL LLM
        # ------------------------------------------------------------------
        
        try:
            llm_raw = await self.llm.ainvoke(messages)
            llm_clean = clean_llm_response(llm_raw.content)
            parsed = json.loads(llm_clean)
        except Exception:
            state.update({
                "uoc_pending_question": True,
                "uoc_confidence": "low",
                "latest_respons": "Sorry, I couldn’t read that. Could you please re-phrase?"
            })
            return state

        # ------------------------------------------------------------------
        # UPDATE PROJECT STRUCTURE
        # ------------------------------------------------------------------
        updated_structure = parsed.get("project_structure")
        if updated_structure:
            state["project_structure"] = updated_structure
            print("UOC Manager:::::: collect_project_structure_interactively::::::<updated_structure> -- Updated project structure --", updated_structure)
        # ------------------------------------------------------------------
        # COPY CONTROL FIELDS
        # ------------------------------------------------------------------
        state.update({
            "latest_respons": parsed["latest_respons"],
            "uoc_next_message_type": parsed.get("next_message_type", "plain"),
            "uoc_next_message_extra_data": parsed.get("next_message_extra_data"),
            "uoc_pending_question": parsed.get("uoc_pending_question", True),
            "uoc_confidence": parsed.get("uoc_confidence", "low"),
            "uoc_question_type": "project_formation"
        })

        # ------------------------------------------------------------------
        # FINALIZE IF SETUP COMPLETE OR USER EXITED
        # ------------------------------------------------------------------
        user_message = (
            state.get("messages", [])[-1].get("content", "").strip().lower()
            if state.get("messages") else "")
        if user_message == "main_menu" or not state["uoc_pending_question"]:
            sender_id = state["sender_id"]
            quick_msg = parsed.get("latest_respons", "Project setup completed. You can now continue with your project.")
            print("UOC Manager:::::: collect_project_structure_interactively:::::: -- User exited or completed setup with a message--", quick_msg)
            if not quick_msg:
                quick_msg = "<Placeholder for project setup completion message>"
            whatsapp_output(sender_id, quick_msg, message_type="plain")
            print("UOC Manager:::::: collect_project_structure_interactively:::::: -- User exited or completed setup --")
            state["uoc_pending_question"] = False
            state["uoc_confidence"] = "high" if updated_structure else "low"
            state["uoc_question_type"] = "project_formation"

            # Finalize project structure
            if not state.get("active_project_id"):
                state["active_project_id"] = f"project-{random.randint(1000, 9999)}"
            state["project_structure"]["id"] = state["active_project_id"]
            state["project_structure"]["title"] = state["project_structure"].get("project_name", "Unnamed Project")
            self.project_db.save_project_for_user(
                state["sender_id"],
                state["active_project_id"],
                state["project_structure"]
            )
            print("UOC Manager:::::: collect_project_structure_interactively:::::: -- Project structure finalized and saved --", self.project_db.get_project_structure(
                state["sender_id"], state["active_project_id"]))
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
