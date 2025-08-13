import os
import json
import base64
from typing import Dict, Optional, List
import random
from datetime import datetime, timezone
from dotenv import load_dotenv
from rapidfuzz import fuzz
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from whatsapp.builder_out import whatsapp_output
from database._init_ import AsyncSessionLocal
from database.models import Project, Flat, Region
from sqlalchemy import select
from sqlalchemy.orm import joinedload
import json, uuid
from database.uoc_crud import DatabaseCRUD


load_dotenv()
LOW_RISE_ZONES = [
    "STAIR_CORE",
    "COMMON_CORRIDOR",
    "OH_WATER_TANK",
    "SITE_BOUNDARY",
    "DRIVEWAY",
]

MID_RISE_ZONES = [
    "PASSENGER_LIFT",
    "FIRE_ESCAPE_STAIR",
    "PUMP_ROOM",
    "DG_ROOM",
    "TRANSFORMER_YARD",
    "FIRE_RISER_SHAFT",
]

HIGH_RISE_ZONES = [
    "SERVICE_LIFT",
    "REFUGE_FLOOR",
    "PRESSURIZED_LOBBY",
    "HVAC_PLANT_ROOM",
    "BMS_SERVER_ROOM",
    "STP_PLANT",
    "FACADE_ZONE",
]
llm = ChatOpenAI(
    model="gpt-4o",
    temperature=0,
    openai_api_key=os.getenv("OPENAI_API_KEY")
)

visual_llm = ChatOpenAI(
    model="gpt-o4-mini-high",
    temperature=0,
    openai_api_key=os.getenv("OPENAI_API_KEY")
)

def encode_image_base64(path: str) -> str:
    if not os.path.exists(path):
        raise FileNotFoundError(f"File not found at: {path}")
    
    try:
        with open(path, "rb") as f:
            encoded = base64.b64encode(f.read()).decode("utf-8")
            if not encoded:
                raise ValueError("Base64 string is empty. File may be corrupted or unreadable.")
            return encoded
    except Exception as e:
        raise RuntimeError(f"Failed to encode image at {path}: {e}")

def clean_llm_response(raw_text: str) -> str:
    return raw_text.strip().replace("```json", "").replace  ("```", "")

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
    def __init__(self, crud: DatabaseCRUD, openai_api_key: Optional[str] = None):
        self.crud = crud
        self.llm = ChatOpenAI(
            model="gpt-4o",
            temperature=0,
            openai_api_key=openai_api_key or os.getenv("OPENAI_API_KEY")
        )
        #self.project_db = ProjectDatabase()

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
        prompt = "Extract the possible project name from this text. Return only the project name if found, empty string if nothing found. No comments, no explanations, nothing"
        messages = [SystemMessage(content=prompt), HumanMessage(content=caption_text)]
        response = await self.llm.ainvoke(messages)
        print("UOC Manager:::::: extract_possible_project_name::::: --The possible project name's LLM resoponse is --", response.content.strip())
        return response.content.strip()

    async def select_or_create_project(self, state: Dict, possible_project_name: Optional[str]) -> Dict:
        print("UOC Manager:::::: select_or_create_project:::::  -- Called this function with a potential project name  --", possible_project_name)
        sender_id = state["sender_id"]
        #user_projects = self.project_db.get_projects_for_user(sender_id)
        user_projects = await self.crud.get_projects_by_sender(sender_id)
        user_message = (
            state.get("messages", [])[-1].get("content", "").strip().lower()
            if state.get("messages") else "")
        
    # 1. If user is responding to a fuzzy match confirmation
        if state.get("needs_clarification") and "fuzzy_project_suggestion" in state:
            print("UOC Manager:::::: select_or_create_project:::::  -- Confirming yes or no   --", possible_project_name)
            if user_message == "yes":
                project = state["fuzzy_project_suggestion"]
                state["active_project_id"] = project["id"]
                del state["fuzzy_project_suggestion"]
                state["needs_clarification"] = False
                return state # Dont return state, respond back the resolve UOC

            elif user_message == "no":
                state["needs_clarification"] = True
                state["uoc_question_type"] = "project_selection"
                state["messages"].append({
                    "role": "assistant",
                    "content": "Okay, please select your project:"
                })
                state["uoc_next_message_type"] = "button"
                state["uoc_next_message_extra_data"] = user_projects + [{"id": "add_new", "title": "➕ Add New Project"}]
                return state

    # 2. If user selected from project list
        if state.get("needs_clarification"):
            print("UOC Manager:::::: select_or_create_project:::::  -- Is project ifrom the list    --", possible_project_name)
            selected_id = user_message
            if selected_id in [proj["id"] for proj in user_projects]:
                state["active_project_id"] = selected_id
                state["needs_clarification"] = False
                return state

            if selected_id == "add_new":
                state["needs_clarification"] = False
                return await self.collect_project_structure_with_priority_sources(state)

    # 3. First attempt: try fuzzy match from possible name
        if possible_project_name:
            print("UOC Manager:::::: select_or_create_project:::::  --  fuzzy match from possible name    --", possible_project_name)
            match = fuzzy_match_project_name(possible_project_name, user_projects)
            if match:
                state["fuzzy_project_suggestion"] = match
                state["needs_clarification"] = True
                state["uoc_question_type"] = "project_selection"
                state["messages"].append({
                    "role": "assistant",
                    "content": f"Did you mean '{match['title']}'?"})
                state["latest_respons"] = f"Did you mean '{match['title']}'? Please confirm."
                state["uoc_next_message_type"] = "button"
                state["uoc_next_message_extra_data"] = [{"id": "yes", "title": "Yes"}, {"id": "no", "title": "No"}]
                return state
                

    # 4. No fuzzy match or possible name → show project list (if any)
        if user_projects:
            project_titles = [proj.get("title") for proj in user_projects]
            state["needs_clarification"] = True
            state["uoc_question_type"] = "project_selection"
            state["messages"].append({
                "role": "assistant",
                "content": "Please select your project:",
            })
            state["latest_respons"] = "Please select your project from the list below or add a new one."
            state["uoc_next_message_type"] = "button"
            project_titles = [
    {
        "id": str(project["id"]),        # UUID to string
        "title": f"🏗️ {project['title']}"  # optional emoji or formatting
    }
    for project in user_projects
]
            state["uoc_next_message_extra_data"] = project_titles + [{"id": "add_new", "title": "➕ Add New Project"}]
            print("UOC Manager:::::: select_or_create_project:::::  -- No fuzzy match or possible name, showing project list --", state["uoc_next_message_extra_data"])
            return state

    # 5. No projects at all → begin onboarding immediately
        return await self.collect_project_structure_with_priority_sources(state)

    async def resolve_uoc(self,  state: Dict, uoc_last_called_by: str ) -> Dict:
        print("UOC Manager:::::: resolve_uoc:::::  -- Called UOC Manager")
        state["uoc_last_called_by"] = uoc_last_called_by
        message = state.get("messages", [])[-1]["content"].strip() if state.get("messages") else ""
        user_input = state.get("caption") or message or ""
        print("UOC Manager:::::: resolve_uoc::::: -- User Input --", user_input)
        possible_project_name = await self.extract_possible_project_name(user_input)
        state= await self.select_or_create_project(state, possible_project_name)
        print("UOC Manager:::::: resolve_uoc::::: -- State after the project selection/ creation : --", state)


        if state.get("needs_clarification"):
            state["uoc_confidence"] = "low"
            print("UOC Manager:::::: resolve_uoc:::::  <needs_clarification Yes> --UOCManager has a question, sending the resonse back to siteops Agent--")
            return state
        active_project_id = state.get("active_project_id")
        print("UOC Manager:::::: resolve_uoc:::::   --Active project Set :-- ", active_project_id)
        
        # if active_project_id not in state:
        #     print("UOC Manager:::::: resolve_uoc::::: <active_project_id No>  Waiting for confirmation on project selection.")
        #     return state
        
        structure = await self.crud.get_project(active_project_id)

        if not structure:
            return await self.collect_project_structure_with_priority_sources(state)
            
        state["project_structure"] = structure

        extracted = await self.extract_candidate_fields(message, state["project_structure"])
        required = ("project_name", "block", "floor", "flat_number", "region_name", "region_type")
        if not extracted or not all(k in extracted for k in required):
            state["needs_clarification"] = True
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
                        "last_updated": datetime.now(timezone.utc).isoformat(),
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
            state["needs_clarification"] = False
            state["uoc_confidence"] = "high"
            return state

        # 2. Check if user already responded to plan image or document question
        last_message = state.get("messages", [])[-1].get("content", "").strip().lower() if state.get("messages") else ""
        question_type = state.get("uoc_question_type")

        if question_type == "has_plan_or_doc":
            if last_message in ["yes", "i have a plan", "yes i have a plan", "has_plan"]:
                print("UOC Manager:::::: collect_project_structure_with_priority_sources:::::: -- User has a plan, checking for file...")

                file_url = state.get("file_url")
                file_type = state.get("file_type")  

                if file_url:
                    print(f"UOC Manager:::::: collect_project_structure_with_priority_sources:::::: -- File received ({file_type}), processing...")
                    return await self.process_plan_file(state, file_url, file_type)

                state["latest_respons"] = "Please upload the project plan image or PDF to continue."
                state["needs_clarification"] = True
                return state
            elif last_message in ["no","I don't", "continue without it","no_plan"]:
                print("UOC Manager:::::: collect_project_structure_with_priority_sources:::::: -- User said no to plan, continuing with fallback flow --")
                # Future: WhatsApp template flow logic could go here
                return await self.collect_project_structure_interactively(state)
        possible_messages = [
"""I couldn’t find any ongoing project to link this update to — no worries! Let’s set one up real quick 🚀
This takes less than a minute and makes everything smoother from here — I’ll remember your blocks, floors, plans, and even auto-analyze photos or text going forward. Smarter replies. No repetition. More power to you. 💪

Let’s begin with your site plan 👇"""
]
        # 3. If we haven't asked yet → ask if they have a plan or document
        if question_type not in {"has_plan_or_doc", "project_formation"}:
            print("UOC Manager:::::: collect_project_structure_with_priority_sources:::::: -- Asking user if they have a project plan or document --")
            state.update({
            "needs_clarification": True,   
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

    async def process_plan_file(self, state: Dict, file_url: str, file_type: str) -> Dict:
        print("UOC Manager:::::: process_plan_file:::::: -- Processing plan file --", file_type, file_url)
        """
        Uses GPT-4o Vision to extract structured project layout from plan image or PDF text.
        """
        print(f"UOC Manager:::::: process_plan_file:::::: -- GPT-4o Vision processing for {file_type}: {file_url}")
        sender_id = state["sender_id"]
        if file_type.lower() == "image":
            # Download and encode image to base64
            image_b64 = encode_image_base64(file_url)
            print("UOC Manager:::::: process_plan_file:::::: -- Image encoded to base64 --")
        elif file_type.lower() == "pdf":
            # Assume PDF text was extracted and stored in state
            pdf_text = state.get("pdf_text")
            if not pdf_text:
                state["latest_respons"] = "⚠️ Sorry, I couldn’t read your PDF. Please try with a clearer version."
                state["needs_clarification"] = True
                return state
            print("UOC Manager:::::: process_plan_file:::::: -- PDF text extracted --")
        else:
            raise ValueError("Unsupported file type")
        try:
            vision_prompt = ("""You are a highly intelligent assistant helping a construction company extract and structure data from architectural floor plans and blueprints.

Your task is to return the project layout in a clean JSON format to initialize a construction database.

Use your best judgment and architectural reasoning to extract the following:

========================
STRUCTURE RULES
========================

1. **Blocks**  
   - If only one structure is visible in the drawing, assume a single block.  
   - Name it `"Block 1"` if no block label is present.  
   - If multiple buildings are clearly labeled or separated, include them individually.

2. **Flat Detection**
   - Count a flat only if it contains **one kitchen**.
   - Group rooms around that kitchen and hall.
   - Each such grouping is considered one flat.

3. **BHK Calculation**
   - Count how many rooms are labeled `"BED"`, `"M.BED"`, or similar.
   - 1 BED = 1BHK, 2 BED = 2BHK, 3 BED = 3BHK, and so on.
   - Do not assume based on visual symmetry or mirroring.

4. **Facing**
   - Use compass labels, road directions, or flat entry orientation to infer `"East"`, `"West"`, etc.
   - Use `"unknown"` if it’s unclear.

5. **Carpet Area Calculation (in sqft)**
   - For each flat, calculate total carpet area by summing the size of individual rooms.
   - Room size format is `'LENGTH' x 'WIDTH'` — e.g., `15'0" x 12'8"`  
   - Convert inches to feet: 1 inch = 1/12 feet.
   - For example:  
     - `12'8"` = 12 + (8 ÷ 12) = 12.67 ft  
     - `15'0"` = 15.0 ft  
     - Area = 15.0 × 12.67 = 190.05 sqft
   - Add up all such areas:
     - Bedrooms  
     - Toilets/Baths  
     - Hall/Living/Dining  
     - Kitchen  
     - Utility  
     - Balconies / Sit-outs  
   - Do **not** include stairs, shafts, ducts, or open terraces unless labeled.

6. **Floors**
   - If only one floor is visible, return that as `"floor": 1`.
   - Use `"no_of_floors": "unknown"` if floor count is not labeled.

7. **Flats per floor**
   - Count the number of self-contained kitchen-based flats in that floor.
   - Store this in `"flats_per_floor"`.

========================
OUTPUT FORMAT
========================

{
  "project_name": "Unnamed Project",
  "blocks": [
    {
      "block_name": "Block 1",
      "no_of_floors": "unknown",
      "flats_per_floor": 2,
      "floors": [
        {
          "floor": 1,
          "flats": [
            {
              "type": "3BHK",
              "facing": "East",
              "carpet_area": "1175"
            },
            {
              "type": "3BHK",
              "facing": "West",
              "carpet_area": "1175"
            }
          ]
        }
      ]
    }
  ]
}

========================
OUTPUT RULES
========================

- Return only valid JSON.
- Do not include markdown (like ```json).
- Do not explain anything.
- Your response must start with “{” and end with “}”.
"""
)

            if file_type.lower() == "image":
                print("UOC Manager:::::: process_plan_file:::::: -- Preparing message for image processing --",{len(image_b64)} )
                message = [
    {"role": "system", "content": vision_prompt},
    {
        "role": "user",
        "content": [
            {"type": "text", "text": "Extract the building project structure from this floor plan."},
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{image_b64}"
                }
            }
        ] 
    }
]
            elif file_type.lower() == "pdf":
                # extract text (use pdfplumber/pdfminer upstream, or assume text was extracted)
                pdf_text = state.get("pdf_text") or "Text content from PDF not found."
                message = [
                    SystemMessage(content=vision_prompt),
                    HumanMessage(content=pdf_text),
                ] 
            else:
                raise ValueError("Unsupported file type")

            llm_raw = await llm.ainvoke(message)
            print("UOC Manager:::::: process_plan_file:::::: -- LLM response structure --", llm_raw.content)
            try:
                #structure = json.loads(clean_llm_response(llm_raw.content))
                structure = (clean_llm_response(llm_raw.content))
            except Exception as e:
                print("UOC Manager:::::: process_plan_file:::::: -- Error parsing LLM response:", str(e))
                return state
            
            # Save the extracted structure and continue with interactive collection if needed
            state["project_structure"] = structure
            state["needs_clarification"] = True
            state["uoc_confidence"] = "low"
            #state["latest_respons"] = "This is the information we have so far: " + str(structure)
            return await self.collect_project_structure_interactively(state)
        except Exception as e:
            print("UOC Manager:::::: process_plan_file:::::: -- Error with GPT-4o vision processing:", str(e))
            state["latest_respons"] = "Sorry, I couldn’t read your plan correctly. Please try with a clearer version."
            state["needs_clarification"] = True
            return state
        
    async def collect_project_structure_interactively(self, state: Dict) -> Dict:
        """
        One-turn loop to collect building structure over WhatsApp:
          • Sends chat history + current structure to the LLM
          • Receives structure update and control JSON
          • Merges result, updates state, and returns
        """
        chat_history = state.get("messages", [])
        #project_structure = state.setdefault("project_structure", {})
        project_structure = state.get("project_structure", {
            "project_name": "",
            "blocks": [],
            "no_of_floors": 0,
            "flats": []
        })

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
            2. Use and update a variable called `expected_flat_index` to track which flat you're asking for.
            2. Only update the ['flats'][expected_flat_index] when you're specifically asking about a flat's configuration (like type + facing). Then increment `expected_flat_index += 1`.
            3. If user response is vague or unclear:
               - Gently ask again, with rephrasing let them know why you are asking again. 
            4. Do NOT assume layout consistency — always confirm.
            5. If the porject setup is complete, respond with a congratulatory message and guide the next steps.
            6. Always Provide an example with each question on how the user should respnd. 
            7. If the user says something random, respond accordingly and nudge the question, and explain why this setup is imporatant. 
            8. If the user asks a question, or trying to understand the process, explain him in detail. 

            ================ STEPS PER BLOCK ================
            - Ask project name and 
            - Ask if there are any blocks, if yes how many and the block name (default to Block A, B… if needed) - If the user says there are no blocks, skip the blocks step and treatit like a single block by default.
            - Ask number of floors.  Store it in this variable - 'no_of_floors' inside project structure. 
            - Ask number of flats per floor
            - Ask flat configurations (type + facing + carpet-area (Total Sq.ft)) for Floor Example :- 3BHK, East Facing, 1550; 2BHK West Facing-1300 
            - Ask if this layout is same for all remaining floors after. Yes/ No button
            - If not, ask:
              • From which floor layout changes
              • What changes (flat type / count / facing)
              • Configurations for those floors
            - Ask if they want to continue to next block - only if the user said there are multiple blocks. Dont ask anything if the user says there are no blocks.
        
  
            At the end of your reasoning, ALWAYS respond in this exact JSON format:
            {
              "latest_respons": "<your next WhatsApp message here>",
              "next_message_type": "button",  // 'plain' for text-only, 'button' for buttons
              "next_message_extra_data": [{ "id": "<kebab-case>", "title": "<≤20 chars>" }, "{ "id": "<kebab-case>", "title": "<≤20 chars>" }", "{ "id": "main_menu", "title": "📋 Main Menu" }"],
              "project_structure": { <updated structure so far> },
              "needs_clarification": true,  // false if user exited
              "uoc_confidence": "low",      // 'high' only when structure is complete
              "uoc_question_type": "project_formation"
            }
    
            RULES:
            - Never wrap the JSON in markdown. 
            - Return ONLY the JSON. No markdown, no extra text.
            """
        )

        # ------------------------------------------------------- -----------
        # BUILD LLM MESSAGE HISTORY
        # ------------------------------------------------------------------

        messages = [SystemMessage(content=system_prompt)]
        messages += [HumanMessage(content=m["content"]) for m in chat_history]
        
        if project_structure:
            messages.append(HumanMessage(content="Current known project structure:\n" + json.dumps(project_structure)))

        # ------------------------------------------------------------------
        # CALL LLM
        # ------------------------------------------------------------------
        print("UOC Manager:::::: collect_project_structure_interactively:::::: -- Calling LLM with messages --", messages)
        try:
            llm_raw = await self.llm.ainvoke(messages)
            llm_clean = clean_llm_response(llm_raw.content)
            parsed = json.loads(llm_clean)
        except Exception:
            state.update({
                "needs_clarification": True,
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
            "needs_clarification": parsed.get("needs_clarification", True),
            "uoc_confidence": parsed.get("uoc_confidence", "low"),
            "uoc_question_type": "project_formation"
        })

      
        
        
        # ------------------------------------------------------------------
        # FINALIZE IF SETUP COMPLETE OR USER EXITED
        # ------------------------------------------------------------------
        user_message = (
            state.get("messages", [])[-1].get("content", "").strip().lower()
            if state.get("messages") else "")
        if user_message == "main_menu" or not state["needs_clarification"]:
            sender_id = state["sender_id"]
            quick_msg = parsed.get("latest_respons", "Project setup completed. You can now continue with your project.")
            print("UOC Manager:::::: collect_project_structure_interactively:::::: -- User exited or completed setup with a message--", quick_msg)
            if not quick_msg:
                quick_msg = "<Placeholder for project setup completion message>"
            whatsapp_output(sender_id, quick_msg, message_type="plain")
            print("UOC Manager:::::: collect_project_structure_interactively:::::: -- User exited or completed setup --")
            state["needs_clarification"] = False
            state["uoc_confidence"] = "high" if updated_structure else "low"
            state["uoc_question_type"] = "project_formation"
            

            state["project_structure"]["title"] = state["project_structure"].get("project_name", "Unnamed Project")
            project_title = state["project_structure"]["title"]
            # Finalize project structure
            if not state.get("active_project_id"):
                #state["active_project_id"] = f"{project_title}-{random.randint(1000, 9999)}"
                state["active_project_id"] = uuid.uuid4()
            state["project_structure"]["id"] = state["active_project_id"]
            
            # self.project_db.save_project_for_user(
            #     state["sender_id"],
            #     state["active_project_id"],
            #     state["project_structure"]
            # )
            state["project_structure"]["sender_id"] = state["sender_id"]
            await self.sync_structure_to_db(state["project_structure"])
            print("UOC Manager:::::: collect_project_structure_interactively:::::: -- Project structure finalized and saved --",state["project_structure"] )
            if state["uoc_confidence"]=="high":
               try:
                   ids_list=  await self.map_region_ids(state.get("project_structure"))
                   print("UOC Manager:::::: collect_project_structure_interactively:::::: -- list of IDs --", ids_list)
               except Exception as e:
                   print("UOC Manager:::::: collect_project_structure_interactively:::::: -- Error mapping region IDs --", str(e))
                   state["latest_respons"] = "An error occurred while mapping region IDs. Please try again."
                   state["needs_clarification"] = True
                   return state
        return state
    
    
    
    @staticmethod
    async def map_region_ids(project: Dict) -> List[Dict]:

        """Generate region-ID map for flats **and** building/common zones."""
        project_id = project["id"]
        output: List[Dict] = []

        def build_region_ids_for_flat(
        project_id: str,
        block: str,
        floor: int,
        flat_number: int,
        bhk_type: str,
    ) -> List[str]:
            base = f"{project_id}::{block}::F{floor}::Flat{flat_number}"
            ids  = [f"{base}::LIV", f"{base}::KIT"] 

            bhk  = int(''.join(filter(str.isdigit,bhk_type)))
            for label in ["MBR", "GBR", "CBR", "BR4", "BR5", "BR6"][:bhk]:
               ids.append(f"{base}::{label}")

            for i in range(1, bhk + 1):                           # toilets
               ids.append(f"{base}::TOILET{i}")

            ids.append(f"{base}::BAL1")                           # balcony
            return ids
    
        def common_zone_ids(project_id: str, block: str, floor: int | None, zones: List[str]) -> List[str]:
            if floor is None:
                base = f"{project_id}::{block}"
            else:
                base = f"{project_id}::{block}::F{floor}"
            return [f"{base}::{z}" for z in zones]
        
        for block in project.get("blocks", []):
            block_name      = block["block_name"]
            template_flats  = []
            floors_to_make  = 0

            if block.get("floors"):
                template_flats = block["floors"][0].get("flats", [])
                floors_to_make = len(block["floors"])
            else:
                template_flats = block.get("flats", [])
                floors_to_make = block.get("no_of_floors", 0)
            if not template_flats:
                raise ValueError(f"{block_name} has no flats template")
            
            if floors_to_make <= 3:
                global_zones = LOW_RISE_ZONES
            elif floors_to_make <= 5:
                global_zones = LOW_RISE_ZONES + MID_RISE_ZONES
            else:
                global_zones = LOW_RISE_ZONES + MID_RISE_ZONES + HIGH_RISE_ZONES
  
            for zid in common_zone_ids(project_id, block_name, None, global_zones):
                output.append(
                {
                    "block":       block_name,
                    "floor":       None,
                    "flat_number": None,
                    "type":        "COMMON",
                    "facing":      None,
                    "area":        None,
                    "region_ids":  [zid],   # single-item list for consistency
                }
            )

             
            for floor_no in range(1, floors_to_make + 1):
                floor_zone_ids = common_zone_ids(
                project_id,
                block_name,
                floor_no,
                ["STAIR_CORE", "COMMON_CORRIDOR", "PASSENGER_LIFT"]
                if floors_to_make >= 4
                else ["STAIR_CORE", "COMMON_CORRIDOR"],
            )
                output.append(
                {
                    "block":       block_name,
                    "floor":       floor_no,
                    "flat_number": None,
                    "type":        "FLOOR_COMMON",
                    "facing":      None,
                    "area":        None,
                    "region_ids":  floor_zone_ids,
                }
            )
                for idx, flat in enumerate(template_flats, start=1):
                    bhk_type = flat["type"]
                    flat_number = floor_no * 100 + idx
                    region_ids = build_region_ids_for_flat(
                    project_id, block_name, floor_no, flat_number, bhk_type
                )
                    output.append(
                    {
                        "block":       block_name,
                        "floor":       floor_no,
                        "flat_number": flat_number,
                        "type":        bhk_type,
                        "facing":      flat.get("facing"),
                        "area":        flat.get("carpet_area"),
                        "region_ids":  region_ids,
                    }
                )
        return output

    
    async def sync_structure_to_db(self, ps: dict):
        """Upsert project, blocks, floors, flats, and regions."""
        blocks = ps.get("blocks", [])
        project_data = {
        "name": ps["project_name"],
        "location": ps.get("location"),
        "sender_id": ps.get("sender_id"),
        "no_of_blocks": len(blocks),
        "floors_per_block": max((b.get("no_of_floors", 0) for b in blocks), default=0),
        "flats_per_floor": max((b.get("flats_per_floor", 0) for b in blocks), default=0)
    }
        print(
            "UOC Manager:::::: sync_structure_to_db:::::: -- Project Details --", project_data )
        proj_exists = await self.crud.session.execute(
        select(Project.id).where(Project.id == ps.get("id"))
         )
        proj_exists = proj_exists.scalar()

        if not proj_exists:
              proj = await self.crud.create_project(project_data)
              ps["id"] =  proj.id
        else:
            await self.crud.update_project(ps["id"], project_data)

        for block in ps["blocks"]:
             block_name = block["block_name"]

            #  for floor in range(1, block["no_of_floors"] + 1):
            #       for idx, flat in enumerate(block.get("flats", []), start=1):
            #            flat_no = floor * 100 + idx
            #            flat_data = {
            #     "project_id": ps["id"],
            #     "block_name": block_name,
            #     "floor_no": floor,
            #     "flat_no": flat_no,
            #     "bhk_type": flat["type"],
            #     "facing": flat.get("facing"),
            #     "carpet_sft": flat.get("carpet_area")
            # }
            #       await self.crud.upsert_flat(flat_data)
             region_list = await self.map_region_ids(ps)
             for region in region_list:
                   for region_id in region["region_ids"]:  
                       region_data = {
    "full_id": region_id,
    "code": region["type"],
    "project_id": ps["id"],
    "flat_id": None,
    "block_name": region.get("block_name"),
    "floor_no": region.get("floor_no"),
    "meta": {}
}
                       await self.crud.upsert_region(region_data)
    




    def build_region_id(self, block: str, floor: int, flat_number: int, region_type: str) -> str:
        return f"{block}-{floor}-{flat_number}-{region_type}"
    
