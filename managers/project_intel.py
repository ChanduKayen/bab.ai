import uuid
import datetime
import json
from sqlalchemy import select
from database.models import Task, Region
from typing import List, Optional
from uuid import UUID
import os
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv
from database.uoc_crud import DatabaseCRUD
from langchain_core.messages import SystemMessage, HumanMessage
load_dotenv()
llm = ChatOpenAI(
    model="gpt-4o",
    temperature=0,
    openai_api_key=os.getenv("OPENAI_API_KEY")
)

def parse_json(response):
    try:
        raw_content = getattr(response, "content", response)
        if not isinstance(raw_content, str):
            print("parse_json error: Res ponse content is not a string.")
            return {}
        raw_content = raw_content.strip()
        if raw_content.startswith("```"):
            raw_content = raw_content.strip("`").replace("json\n", "").replace("json", "").strip()
        if not raw_content:
            print("parse_json error: Content is empty after stripping.")
            return {}
        print("parse_json attempting to parse:", raw_content[:200])
        return json.loads(raw_content)
    except Exception as e:
        print(f"parse_json error: {e}")
        return {}



async def validate_scope_via_llm(message: str, region: str,existing_scopes: list[str]) -> dict:
    print(f"project_intel:::validate_scope_via_llm::: --Entering validate_scope_via_llm with message --: {message}")
    formatted_scopes = ", ".join(f'"{s}"' for s in existing_scopes) if existing_scopes else "None"
    
    prompt = f"""
You are an expert assistant for construction project management. Your job is to classify a user's job update message into the most appropriate task scope for a given region.

Context:
- **Job update message:** "{message}"
- **Region:** {region}
- **Existing scopes in this region:** [{formatted_scopes}]
────────────────────────────────────────
YOUR TASK IS TO:
────────────────────────────────────────
Your task is to:
1. Check if the update clearly fits one of these scopes — return that exact scope string as "scope_fit".
2. If none are suitable, return "new" for "scope_fit" and suggest a clear, specific scope title;  try inform what activity is desrcibed in the message/ update(e.g., "kitchen granite platform installation") in "new_scope_title".


Use only standard, professional terms. **Do not invent vague phrases.** or ** DO NO SAY UNSPECIFIED NEW TASK or UNCATEGORIZED TASK or ANYTHING LIKE THAT.**

Output: 
Respond ONLY with a valid JSON object in this format:
{{
  "scope_fit": "<one of the existing scopes, or 'new'>",
  "new_scope_title": "<filled only if scope_fit is 'new'>"
}}

Rules:
- Do not include any markdown, explanations, or extra text.
- Only output the JSON object.
"""

    try:
        response = await llm.ainvoke(prompt)
        result = parse_json(response)

        scope_fit = result.get("scope_fit")


        new_scope_title = result.get("new_scope_title", "")
        # Ensure output structure
        if scope_fit in existing_scopes:
            print(f"project_intel:::validate_scope_via_llm::: --Scope fit found in existing scopes --: {scope_fit}")
            return {"scope_fit": scope_fit, "new_scope_title": ""}
        elif scope_fit == "new" and new_scope_title.strip():
            print(f"project_intel:::validate_scope_via_llm::: --New scope title suggested --: {new_scope_title.strip()}")
            return {"scope_fit": "new", "new_scope_title": new_scope_title.strip()}
        else:
            # fallback if malformed
            print("project_intel:::validate_scope_via_llm::: --Malformed response, returning new scope --")
            return {"scope_fit": "new", "new_scope_title": "unspecified new task"}
    except Exception as e:
        print(f"Scope validation failed: {e}")
        return {"scope_fit": "new", "new_scope_title": "uncategorized task"}


class TaskHandler:
    def __init__(self, crud: DatabaseCRUD):
        self.crud = crud
    
    # async def split_region_ids(self, region_ids: list[str]) -> dict:

    #     flat_region_candidates = []
    #     common_region_candidates = []
        
    #     for rid in region_ids:
    #         parts = rid.split("::")
    #         if any("flat" in part.lower() for part in parts):
    #             # Flat-specific region
    #             flat_region = "::".join(parts[-2:])
    #             flat_region_candidates.append(flat_region)
    #         else:  
    #             # Common area region
    #             common_region = "::".join(parts[-2:])
    #             common_region_candidates.append(common_region)
    #     #print(f"project_intel:::split_region_ids::: --Flat region candidates --: {flat_region_candidates}")
    #     #print(f"project_intel:::split_region_ids::: --Common region candidates --: {common_region_candidates}")
    #     return {
    #         "flat_region_candidates": flat_region_candidates,
    #         "common_region_candidates": common_region_candidates
    #     }


    async def get_region_via_llm(self, state: dict):
        
        print("project_intel:::get_region_via_llm::: --Entering get_region_via_llm with state --:")
        chat_history = state.get("messages", [])
        region_id = "uncertain"
        message = (
        state.get("messages", [])[-1].get("content", "").strip().lower()
        if state.get("messages") else "")
        region_candidates = state.get("region_candidates", [])

        prompt = """
    You are a construction site assistant helping identify the correct region ID for a job update based on a user's message.



    Each job update must be associated with one of the following types of region IDs:
    You are provided with a list of region IDs that are valid for this project.
    These region IDs are structured as follows:
    1. **Flat-Specific Region IDs** — for work happening inside a flat (e.g., kitchen, bedroom, balcony). Format: `<UUID>::<Block>::<Floor>::<Flat>::<Region>`
    2. **Common Area Region IDs** — for work happening in shared areas like staircases, terraces, lifts. Format: `<UUID>::<Block>::<Floor>::<Region>` or `<UUID>::<Block>::<Region>`

    ---

    ### Your task: 
    Be intelligent, if the user speicifes a flat no, which is almost like a unique identifier, then you can assume that the region is flat-specific. If the user specifies a common area like lift, staircase, etc., then you can assume that the region is common area. 
    If you get a flat Id assume its previous structure and only ask for the region name. 
    - **Step 1: Infer region type** — flat-specific or common area — based on terms like “flat 301”, “kitchen”, or “lift”.
    - **Step 2: Select the best-matching region ID** from the provided list (`region_json`).
    - **Step 3: Extract any useful details from the message (like flat number, room name, block, intent) and include them in a field called `extracted_context`.
    - **Step 4: If unsure, set `"uoc_confidence": "low"` and ask a follow-up question — but do not repeat questions already answered.
    - **Step 5: If confident, set `"uoc_confidence": "high"` and return the exact region ID as `"region"`.
    - **Step 6: If no region matches, set `"uoc_confidence": "low"` with a follow-up question.
    - **Step 7: Extract any useful details from the message (like flat number, room name, block, intent) and include them in a field called `extracted_context`.

    ---

    ### At the end of your reasoning, ALWAYS respond in this exact JSON format:

    {
    "region_format": "flat_specific" | "common_area" | "uncertain",
    "region": "<exact string from the candidate list>" | "",
    "uoc_confidence": "high" | "low", 
    "followup": "<clarification question if confidence is low, else empty string>",
    "extracted_context": "<any useful details extracted from the message>"
    }
    RULES:
                - Never wrap the JSON in markdown.
                - Return ONLY the JSON. No markdown, no extra text.

    """

        try:
            extracted_context = state.get("project_structure", {})
            prompts = [SystemMessage(content=prompt)]
            prompts += [HumanMessage(content=m["content"]) for m in chat_history]
            prompts += [HumanMessage(content=f"Here are the region ID candidates:::- Flat region candidates: {region_candidates}")]
            if extracted_context:
                prompts.append(HumanMessage(content="Current known extracted context is:\n" + json.dumps(extracted_context)))
                print(f"project_intel:::get_region_via_llm::: --Updated extracted context with extracted context --: {state['project_structure']}")
                state["project_structure"] = extracted_context
        except Exception as e:
            print(f"project_intel:::get_region_via_llm::: Exception while building prompts: {e}" )
            prompts = [
                SystemMessage(content=prompt),
                HumanMessage(content=f"Here are the region ID candidates:::- Flat region candidates: {region_candidates}")
            ] 

        print(f"project_intel:::get_region_via_llm::: --Prompt for LLM --: {prompts}")
        response = await llm.ainvoke(prompts)
        

        result = parse_json(response)
        
        region = result.get("region", "uncertain")
        if not isinstance(region, str) or not region:
         region = "uncertain"
        extracted_context = result.get("extracted_context")
        followup = result.get("followup", "")
        confidence = result.get("uoc_confidence", "low" if region == "uncertain" else "high")
        state["uoc_confidence"] = confidence
        print(f"project_intel:::get_region_via_llm::: --LLM response --: {result}")
        state["latest_respons"] = followup if region == "uncertain" else f"Identified region: {region}"
        
        if confidence == "high":
            print("project_intel:::get_region_via_llm::: --High confidence in region identification, updating state --")
            await self.handle_job_update(state)

        return state  # if webhook calls this the state is retined back to webhook and if the task handler calls this, the state is returned to the task handler. So 
    #...now if the user have to respond to the followup question, the state will be updated snet back to webhook thats why task handler is not called. 


    async def extract_project_id(self, state: dict) -> str | None:
        valid_ids = {item["id"] for item in state.get("uoc_next_message_extra_data", [])}
        
        for msg in reversed(state.get("messages", [])):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if content in valid_ids:
                    return content
        return None
    async def handle_job_update(self, state: dict):
        print("project_intel:::handle_job_update::: --Entering handle_job_update with state --:", state)
        #message="I'm building a wall in ASM Elite 101 flat"
        message = (
    state.get("messages", [])[-1].get("content", "").strip().lower()
    if state.get("messages") else "")
        # project_id = (
        #     state.get("messages", [])[-1].get("content", "").strip().lower()
        #     if state.get("messages") else "")
        try:
            project_id = await self.extract_project_id(state)
        except Exception as e:
            print(f"Error extracting project_id: {e}")
            return {"error": "Failed to extract project_id"}
        print(f"project_intel:::handle_job_update::: --Entering handle_job_update with project_id --:", project_id)
        print(f"project_intel:::handle_job_update::: --Received message --:", message)
        try:
            regions = await self.crud.get_region_full_ids_by_project(UUID(project_id))
            state["region_candidates"] = regions
            print(f"project_intel:::handle_job_update::: --Fetched region candidates --: {regions}")
        except Exception as e:
            print(f"Error fetching region candidates for project {project_id}: {e}")
        
        #flat_region_candidates, common_region_candidates = await self.split_region_ids(regions)
        #  split_result = await self.split_region_ids(regions)
        # flat_region_candidates = split_result["flat_region_candidates"]
        # common_region_candidates = split_result["common_region_candidates"]
        # Only run get_region_via_llm if uoc_confidence is low
        state_after_region_selection = state
        if state.get("uoc_confidence", "low") == "low":
            state_after_region_selection = await self.get_region_via_llm(state)
        latest_response = state_after_region_selection.get("latest_respons", "")
        if latest_response == "":
            print("project_intel:::handle_job_update::: --No region identified, returning empty state --")
            return {"error": "No region identified"}
        if latest_response.startswith("Identified region"):
            selected_region = state_after_region_selection.get("latest_respons", "")
            selected_region = selected_region.split("Identified region:")[-1].strip()
          
        if state_after_region_selection.get("uoc_confidence", "low") == "low":
            print(f"project_intel:::handle_job_update::: --Low confidence in region identification, asking for clarification --")  
            state["latest_respons"] = state_after_region_selection.get("latest_respons", "")
            state.update({
                "needs_clarification": True,
                "uoc_question_type": "task_region_identification",
                "uoc_next_message_type": "plain",
            }) 
            print("project_intel:::handle_job_update::: --Returning state with clarification needed --:", state["latest_respons"])
            return state
        
        print(f"project_intel:::handle_job_update::: --Identified region --:", selected_region)
        existing_scopes = await self.crud.get_scopes_in_region(selected_region)
        print(f"project_intel:::handle_job_update::: --Existing scopes in region {selected_region} --:", existing_scopes)
        scope_result = await validate_scope_via_llm(message, selected_region, existing_scopes)
        print(f"project_intel:::handle_job_update::: --Scope validation result --:", scope_result)
        if scope_result["scope_fit"] == "new":
            print(f"project_intel:::handle_job_update::: --Creating new task for region {selected_region} with scope {scope_result['new_scope_title']} --")
            scope = scope_result["new_scope_title"]
            # Add last two things from selected_region to the scope
            region_parts = selected_region.split("::")
            if len(region_parts) >3:
                scope = f"{scope} in {region_parts[-2]} "
            elif len(region_parts) <= 3:
                scope = f"{scope} in {region_parts[-1]}"
            try:
                task = await self.crud.create_task(UUID(project_id), selected_region, scope)
            except Exception as e:
                print(f"Error creating new task for region {selected_region} with scope {scope}: {e}")
                return {"error": "Failed to create new task"}
        else: 
            print(f"project_intel:::handle_job_update::: --Using existing task for region {selected_region} with scope {scope_result['scope_fit']} --")
            scope = scope_result["scope_fit"]
            #task = await self.crud.create_task(UUID(project_id), selected_region, scope)
        print(f"project_intel:::handle_job_update::: --Task retrieved or created --: {task.id if task else 'None'}")
        print(f"project_intel:::handle_job_update::: --Validating Job--")
        state["task_id"] = str(task.id) if task else None
        from managers.job_handler import handle_job 
        # Validate and handle the job update
        job_update = await handle_job(state) 
       