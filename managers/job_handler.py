import uuid
import datetime
from typing import Optional
from database.models import Job
from database.uoc_crud import DatabaseCRUD
from langchain_core.messages import HumanMessage

from managers.job_material_handler import handle_material_details
from managers.job_worker_handler import handle_worker_details

async def handle_job(state: dict, crud: Optional[DatabaseCRUD] = None):
    print("job_handler:::handle_job::: Starting with state")

    task_id = state.get("task_id")
    if not task_id:
        return {"error": "Task ID missing"}

    user_message = (
        state.get("messages", [])[-1].get("content", "").strip()
        if state.get("messages") else ""
    )
    if not user_message:
        return {"error": "User message missing"}

    print(f"job_handler:::handle_job::: task_id={task_id}, message={user_message}")

    # Validate and extract material data
    material_result = await handle_material_details(user_message)
    if material_result.get("needs_clarification"):
        print("job_handler:::handle_job::: Material clarification needed")
        state.update({
            "latest_respons": material_result["followup"],
            "needs_clarification": True,
            "uoc_question_type": "task_material_details",
            "uoc_next_message_type": "plain"
        })
        return state

    # Validate and extract worker data
    worker_result = await handle_worker_details(user_message)
    if worker_result.get("needs_clarification"):
        print("job_handler:::handle_job::: Worker clarification needed")
        state.update({
            "latest_respons": worker_result["followup"],
            "needs_clarification": True,
            "uoc_question_type": "task_worker_details",
            "uoc_next_message_type": "plain"
        })
        return state

    #log job
    job_data = {
        "id": uuid.uuid4(),
        "task_id": task_id,
        "description": worker_result.get("description", user_message),
        "material": material_result.get("material"),
        "worker": worker_result.get("worker"),
        "quality": worker_result.get("quality", "Not Mentioned"),
        "time": datetime.datetime.now(),
        "confidence_flags": {
            "material_confidence": material_result.get("confidence", "low"),
            "worker_confidence": worker_result.get("confidence", "low")
        },
        "raw_text": user_message
    }

    try:
        session = crud.get_session() if crud else None
        job = Job(**job_data)
        session.add(job)
        session.commit()
        print(f"job_handler:::handle_job::: Job logged with ID: {job.id}")
    except Exception as e:
        print(f"Error while logging job: {e}")
        return {"error": "Failed to save job"}

    state["latest_respons"] = f"Job update saved under task ID {task_id}"
    state["job_saved"] = True
    return state
