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
# Import DatabaseCRUD for type hinting
from database.uoc_crud import DatabaseCRUD
load_dotenv()
llm = ChatOpenAI(
    model="gpt-4o",
    temperature=0,
    openai_api_key=os.getenv("OPENAI_API_KEY")
)

def parse_json(response):
    try:
        return json.loads(response)
    except:
        return {}


class Job:
    def __init__(self, message):
        self.id = str(uuid.uuid4())[:8]
        self.message = message
        self.timestamp = datetime.datetime.now().isoformat()


async def get_region_via_llm(state: dict):
    
    print("project_intel:::get_region_via_llm::: --Entering get_region_via_llm with state --:")

    region_candidates =[]#Call some function to get the filered region IDs
    region_id = "uncertain"
    message = (
            state.get("messages", [])[-1].get("content", "").strip().lower()
            if state.get("messages") else "")

    prompt = f"""
You are helping categorize a construction job update at a building site.

The user said:
```{message}```

You're given a list of possible region IDs in these formats:
1. `<UUID>::<Block Name>::<Floor Number>::<Flat Number>::<Region>` → a **specific area inside a flat**  
2. `<UUID>::<Block Name>::<Floor Number>::<Region>` → a **common area on that floor**  
3. `<UUID>::<Block Name>::<Region>` → a **common area in that block**

Each format has different required information:

- Format 1 needs: Block, Floor, Flat Number, and Sub-region  
- Format 2 needs: Block, Floor, and Sub-region  
- Format 3 needs: Block and Sub-region

Your task is:
- Step 1: Decide which format fits this message best.
- Step 2: Identify which of these fields are mentioned or implied: Block, Floor, Flat, Region (sub-area).
- Step 3: If all the required fields for that format are present or confidently inferred, mark `"uoc_confidence": "high"`. Otherwise, mark `"uoc_confidence": "low"` and ask a follow-up question to get the missing field(s)

{{
  "region_format": "flat_specific" | "floor_common" | "block_common" | "uncertain",
  "identified_fields": {{
    "block": "...",     // leave "" if not applicable
    "floor": "...",     // leave "" if not applicable
    "flat": "...",      // leave "" if not applicable
    "region": "..."     // required in all formats
  }},
  "uoc_confidence": "high" | "low",
  "followup": "question to clarify the region if confidence is low"
}}

Here are the known region IDs for reference:
{json.dumps(region_candidates, indent=2)}
"""
    
    # If low confidence, 
    response = await llm.ainvoke(prompt)
    print(f"project_intel:::get_region_via_llm::: --LLM response --: {response}")
    result = parse_json(response)
    region = result.get("region", "uncertain")
    followup = result.get("followup", "")
    confidence = result.get("uoc_confidence", "low" if region_id == "uncertain" else "high")
    state["uoc_confidence"] = confidence
    print(f"project_intel:::get_region_via_llm::: --LLM response --: {result}")
    if state["uoc_confidence"] == "high" and region != "uncertain":
     
        return region
    
    state.update({
        "latest_respons": followup,
        "uoc_next_message_type": "plain",
        "uoc_next_message_extra_data": None,
        "needs_clarification": True,
        "uoc_confidence": confidence,
        "uoc_question_type": "task_region_identification"
    })
    return state

async def validate_scope_via_llm(message: str, existing_scopes: list[str]) -> dict:
    print(f"project_intel:::validate_scope_via_llm::: --Entering validate_scope_via_llm with message --: {message}")
    formatted_scopes = ", ".join(f'"{s}"' for s in existing_scopes) if existing_scopes else "None"

    prompt = f"""
You are an assistant classifying construction task updates into scopes.

Given the job update:
\"{message}\"

And the list of existing task scopes already active in this region:
[{formatted_scopes}]

Your task is to:
1. Check if the update clearly fits one of these scopes — return that exact scope string as `"scope_fit"`.
2. If none are suitable, return `"new"` for `"scope_fit"` and suggest a clear, specific scope title (e.g., `"kitchen granite platform installation"`) in `"new_scope_title"`.

Respond strictly in this JSON format:
{{
  "scope_fit": "<one of the existing scopes, or 'new'>",
  "new_scope_title": "<filled only if scope_fit is 'new'>"
}}
"""

    try:
        response = await call_llm(prompt)
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

# ------------------------
# Task Handler
# ------------------------
class TaskHandler:
    def __init__(self, crud: DatabaseCRUD):
        self.crud = crud


    async def handle_job_update(self, state: dict):
        print("project_intel:::handle_job_update::: --Entering handle_job_update with state --:", state)
        message="I'm building a wall in ASM Elite 101 flat"
        project_id = (
            state.get("messages", [])[-1].get("content", "").strip().lower()
            if state.get("messages") else "")
        print(f"project_intel:::handle_job_update::: --Entering handle_job_update with project_id --: {project_id}")
        print(f"project_intel:::handle_job_update::: --Received message --: {message}")
        try:
            region_candidates = await self.crud.get_region_full_ids_by_project(UUID(project_id))
            print(f"project_intel:::handle_job_update::: --Fetched region candidates --: {region_candidates}")
        except Exception as e:
            print(f"Error fetching region candidates for project {project_id}: {e}")
        
           
           
        region = await get_region_via_llm(state)

        print(f"project_intel:::handle_job_update::: --Identified region --: {region}")
        existing_scopes = await self.crud.get_scopes_in_region(region)
        scope_result = await validate_scope_via_llm(message, existing_scopes)
        print(f"project_intel:::handle_job_update::: --Scope validation result --: {scope_result}")
        if scope_result["scope_fit"] == "new":
            print(f"project_intel:::handle_job_update::: --Creating new task for region {region} with scope {scope_result['new_scope_title']} --")
            scope = scope_result["new_scope_title"]
            task = await self.crud.create_task(UUID(project_id), UUID(region), scope)

        else:
            print(f"project_intel:::handle_job_update::: --Using existing task for region {region} with scope {scope_result['scope_fit']} --")
            scope = scope_result["scope_fit"]
            task = await self.crud.create_task(UUID(project_id), UUID(region), scope)


        print(f"project_intel:::handle_job_update::: --Task retrieved or created --: {task.id if task else 'None'}")
        print(f"project_intel:::handle_job_update::: --Validating Job--")
###########
        job = Job(message)
        task.add_job(job)

        return {
            "task_id": task.id,
            "job_id": job.id,
            "status": task.status,
            "updated_at": task.updated_at,
            "jobs_count": len(task.jobs)
        }
