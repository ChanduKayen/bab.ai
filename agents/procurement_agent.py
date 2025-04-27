# agents/procurement_agent.py

from tools.lsie import _local_sku_intent_engine
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
import os
from dotenv import load_dotenv
import json  # Import the json module
load_dotenv()  # lodad environment variables from .env file
#llm = ChatOpenAI(model="gpt-4", temperature=0)
llm = ChatOpenAI(
    model="gpt-4o",
    temperature=0,
    openai_api_key=os.getenv("OPENAI_API_KEY")  # safely pulls from env
)   
async def run_procurement_agent(state: dict) -> dict:
    last_msg = state["messages"][-1]["content"]
    print("Procurement agent received:", last_msg)

    # Step 1: Ask LLM to extract structured fields

    system_prompt = """
A customer sent the following material request:


Given the user's message, extract:
- material → combine brand name and material type (e.g., "Deccan TMT"). ALso note that dimenions, sizer variotions can be present the saem SKU. So present the SKU with different dimensions as a single SKU. ) Ex: Deccan TMT 20mm, Vizag TMT 8mm etc
- quantity → size, count, or weight  be careful size doesent always mean quantity, it cana difretn version of the same SKU. Also note that the quantity can be a range (e.g., "100-200 bags")
- location → (if present). Note: the word "Vizag" can also be a brand (e.g., "Brand TMT") — do not always assume it's a location.

Respond ONLY in this JSON format:
{ "material": "...", "quantity": "...", "location": "..." }
"""

  
    print("System prompt:", system_prompt)
    print("Last message:", last_msg)
    chat_response = await llm.ainvoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=last_msg)
    ])
    print("#############Chat response:", chat_response.content)
    try:
        extracted = eval(chat_response.content)  # TODO: Use safe pydantic parsing
    except Exception as e:
        return {
            "messages": state["messages"] + [{
                "role": "assistant",
                "content": f" Couldn't understand your request: {e}"
            }]
        }

    # Step 2: Match SKU using LSIE
   
    match = _local_sku_intent_engine.invoke({
    "query": extracted["material"],
    "quantity": extracted["quantity"]})
    matched_sku = match["matches"][0] if match["matches"] else "No match"

    # Step 3: Return formatted quote
    quote_msg = (
        f"🧾 Quote:\n"
        f"- SKU: {matched_sku}\n"
        f"- Quantity: {extracted['quantity']}\n"
        f"- Location: {extracted['location']}\n"
        f"- Vendor: Srinivas Traders\n"
        f"- Price: ₹395/unit (mock)\n"
        f"- ETA: 6 hrs"
    )
    print("###########This is the quote message",quote_msg)
    state["messages"].append({"role": "assistant", "content": quote_msg})
    return state
