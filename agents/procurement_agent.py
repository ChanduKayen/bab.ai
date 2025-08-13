# agents/procurement_agent.py

import base64, requests
from typing import List
from tools.lsie import _local_sku_intent_engine
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from managers.uoc_manager import UOCManager
from whatsapp.builder_out import whatsapp_output
import os
from managers.procurement_manager import ProcurementManager
from models.chatstate import AgentState
from database.procurement_crud import ProcurementCRUD
from database.uoc_crud import DatabaseCRUD
from dotenv import load_dotenv
import json  # Import the json module
import re
from database._init_ import AsyncSessionLocal
from whatsapp import apis
from whatsapp.builder_out import whatsapp_output
from agents.credit_agent import handle_credit_entry
load_dotenv()  # lodad environment variables from .env file
#llm = ChatOpenAI(model="gpt-4", temperature=0)

llm = ChatOpenAI(
    model="gpt-4o",
    temperature=0,
    openai_api_key=os.getenv("OPENAI_API_KEY")  # safely pulls from env
)  


async def handle_siteops(state: AgentState, crud: ProcurementCRUD,latest_response: str, uoc_next_message_extra_data=None ) -> AgentState:
    #handle a message here 
    state.update(
        intent="siteops",
        latest_respons=latest_response, 
        uoc_next_message_type="button",
        uoc_question_type="siteops_welcome",
        needs_clarification=True,  
        uoc_next_message_extra_data=[uoc_next_message_extra_data],
        agent_first_run=True
    )
    print("Siteops Agent::::: handle_siteops:::::  --Handling siteops intent --", state)
    return state    

def handle_main_menu(state: AgentState, crud: ProcurementCRUD, latest_response: str, uoc_next_message_extra_data=None) -> AgentState:
    state.update(
        intent="random",
        latest_respons=latest_response,
        uoc_next_message_type="button",
        uoc_question_type="siteops_welcome",
        needs_clarification=True,   
        uoc_next_message_extra_data=uoc_next_message_extra_data,
    )
    print("Random Agent::::: handle_main_menu:::::  --Handling main menu intent --", state)
    return state

def handle_procurement(state: AgentState, crud: ProcurementCRUD, latest_response: str, uoc_next_message_extra_data=None) -> AgentState:
    """
    Handle the procurement intent by updating the state and returning it.
    """
    state.update(
        intent="procurement",
        latest_respons=latest_response,
        uoc_next_message_type="button",
        uoc_question_type="procurement",
        needs_clarification=True,
        uoc_next_message_extra_data=[uoc_next_message_extra_data],
        agent_first_run=False
    )
    print("Procurement Agent::::: handle_procurement:::::  --Handling procurement intent --", state)
    return state
def handle_rfq(state: AgentState, crud: ProcurementCRUD, latest_response: str, uoc_next_message_extra_data=None) -> AgentState:
    """
    Handle the RFQ intent by updating the state and returning it.
    """
    material_request_id = state["active_material_request_id"] if "active_material_request_id" in state else None
    review_order_url = apis.get_review_order_url("https://bab-ai.com/review-order", {}, {"uuid": state["active_material_request_id"]})
    review_order_url_response = f"Please review your order carefully"
    state.update(
        intent="rfq",
        latest_respons=review_order_url_response,
        uoc_next_message_type="link_cta",
        uoc_question_type="procurement_new_user_flow",
        needs_clarification=True,
        uoc_next_message_extra_data= {"display_text": "Review Order", "url": review_order_url},
        agent_first_run=False  
    )
    print("Procurement Agent::::: handle_rfq:::::  --Handling rfq intent --", state)
    return state
def handle_credit(state: AgentState, crud: ProcurementCRUD, latest_response: str, uoc_next_message_extra_data=None) -> AgentState:
    """
    Handle the credit intent by updating the state and returning it.
    """
     
    
    
    state.update({
                "latest_respons": latest_response,
                "uoc_next_message_type": "link_cta",
                "uoc_question_type": "procurement_new_user_flow",
                
                "needs_clarification": True,

                "agent_first_run": False,
            })
    
    handle_credit_entry(state, crud, latest_response, uoc_next_message_extra_data)
    print("Procurement Agent::::: handle_credit:::::  --Handling credit intent --", state)
    return state
    
_HANDLER_MAP = {
    "siteops": handle_siteops,
    "procurement": handle_procurement,
    "main_menu": handle_main_menu,
    "rfq": handle_rfq,
    "credit_use": handle_credit,
}

_JSON_PATTERN = re.compile(r"\{.*\}", re.S) 

async def new_user_flow(state: AgentState, crud: ProcurementCRUD  ) -> AgentState:
    latest_msg_intent =state.get("intent")
    last_msg = state["messages"][-1]["content"] if state.get("messages") else ""
    user_name = state.get("user_full_name", "There")
    sender_id = state["sender_id"]
    uoc_next_message_extra_data = state.get("uoc_next_message_extra_data", [])
    latest_response = state.get("latest_respons", None)
    print("Procurement Agent:::: new_user_flow : last_msg is: -", last_msg)
    # print("Procurement Agent:::: new_user_flow : procurment conversation log  is: -", state.get("siteops_conversation_log", []))
    print("Procurement Agent:::: new_user_flow : the state received here is : -", state)
    response = dict()
    material_request_id = ""
    
    img_b64 = None
    img_path = state.get("image_path")
    if img_path:
        try:
            img_b64 = encode_image_base64(img_path)
        except FileNotFoundError:
          print("⚠  Image file not found:", img_path)
          print("Procurement Agent:::: new_user_flow : called")
        #   state["siteops_conversation_log"].append({
        #         "role": "user", "content": img_b64 if img_b64 else last_msg + "\n" + state.get("caption", "")
        #     })
    if(state.get("agent_first_run", True)):
        print("Procurement Agent:::: new_user_flow : agent first run is true")
        if(last_msg == ""):
            print("Procurement Agent:::: new_user_flow : last_msg is empty and no image, setting up welcome message")
            greeting_message = (
                f"👋 Hi {user_name}! I'm your procurement assistant.\n"
                "I can help you get quotes and manage your construction material orders.\n\n"
                "What would you like to do?"
            )
            state["latest_respons"] = greeting_message
            state["uoc_next_message_type"] = "button"
            state["uoc_question_type"] = "procurement_new_user_flow"
            state["uoc_confidence"]="low"
            state["needs_clarification"] = True
            state["agent_first_run"] = False
            state["user_verified"] = True
            state["uoc_next_message_extra_data"] = [
                {"id": "procurement_start", "title": "🧱 Request Material"},
                {"id": "main_menu", "title": "🏠 Main Menu"},
            ]
            return state
             
        else:
            print("Procurement Agent:::: new_user_flow : Last message/ Image is found")
            caption = state.get("caption", "")
            if img_b64:
                whatsapp_output(
                    sender_id,
                    f"Hey 👋\n\nGot your photo. Give me a sec — scanning this carefully. 🔍",
                    message_type="plain",
                )
                combined = caption if caption else ""
            else:
                combined = last_msg
            combined = combined.strip()
        print("Procurement Agent:::: new_user_flow : combined text:", combined)
        state.setdefault("procurement_details", {})["materials"] = await extract_materials(combined, img_b64)
        print("Procurement Agent:::: new_user_flow : extracted materials:", state["procurement_details"]["materials"])
        
        try:
            async with AsyncSessionLocal() as session:
                procurement_mgr = ProcurementManager(session)
            print("Procurement Agent:::: new_user_flow :::: calling persist_procurement for material : ", state["procurement_details"]["materials"])
            material_request_id = await procurement_mgr.persist_procurement(state)
            state["active_material_request_id"] = material_request_id
            print("Procurement Agent:::: new_user_flow : persist_procurement completed: ", material_request_id)
        except Exception as e:
            print("Procurement Agent:::: new_user_flow : Error in persist_procurement:", e)
            state["latest_respons"] = "Sorry, there was an error saving your procurement request. Please try again later."
            return state
        try: 
            
            review_order_url_response = f"Got it ✅ Would you like to buy using credit or get quotations first?"
            state.update({  
                "latest_respons": review_order_url_response,
                "uoc_next_message_type": "button",
                "uoc_question_type": "procurement_new_user_flow",
                #"uoc_next_message_extra_data": {"display_text": "Review Order", "url": review_order_url},
                "uoc_next_message_extra_data": [
                    {"id": "rfq", "title": "Get Quotations"},
                    {"id": "credit_use", "title": "Buy with Credit"},
                ],
                "needs_clarification": True,
                "active_material_request_id": material_request_id,
                "agent_first_run": False,
            })
        except Exception as e:
            print("Procurement Agent:::: new_user_flow : Error in fetching review order:", e)
        
        return state
    else:
        print("Procurement Agent:::: new_user_flow : agent first run is false, not setting it to false")
        if last_msg in _HANDLER_MAP:
            #Main menu for new user
            if last_msg =="main_menu":
                print("Procurement Agent:::: new_user_flow : last_msg is main_menu, setting up main menu")
                latest_response = "Welcome back! How can I assist you today?"
                uoc_next_message_extra_data =[{"id": "siteops", "title": "🏗 Manage My Site"},
                                          {"id": "procurement", "title": "⚡ Get Quick Quotes"},
                                          {"id": "credit",      "title": "💳 Get Credit Now"}] 
                return await _HANDLER_MAP[last_msg](state, crud, latest_response, uoc_next_message_extra_data)
            else:
                print("Procurement Agent:::: new_user_flow : last_msg is not main_menu, handling it as a specific intent")
                if latest_msg_intent == "random":
                    from agents.random_agent import classify_and_respond
                    return await classify_and_respond(state, config={"configurable": {"crud": crud}})
                elif latest_msg_intent == "siteops":
                    latest_response = "📷 Ready to check your site? Let's continue!"
                    uoc_next_message_extra_data = {"id": "siteops", "title": "📁 Site Setup"}
                    return await handle_siteops(state, crud, latest_response, uoc_next_message_extra_data)
                elif latest_msg_intent == "procurement":
                    latest_response = "🧱 Tell me what materials you're looking for, and I'll fetch quotes!"
                    uoc_next_message_extra_data = {"id": "procurement", "title": "📦 Continue Procurement"}
                    return await handle_procurement(state, crud, latest_response, uoc_next_message_extra_data)
                else:
                    state["latest_respons"] = (
                        "🤔 I'm not sure what you're looking for. "
                        "Please choose an option below."
                    )
                    state["uoc_next_message_type"] = "button"
                    state["uoc_question_type"] = "main_menu"
                    state["needs_clarification"] = True
                    state["uoc_next_message_extra_data"] = [
                        {"id": "siteops", "title": "🏗 Manage My Site"},
                        {"id": "procurement", "title": "⚡ Get Quick Quotes"},
                        {"id": "credit", "title": "💳 Get Credit Now"}
                    ]
                    return state

            
async def run_procurement_agent(state: dict,  config: dict) -> dict:
    print("Procurement Agent:::: run_procurement_agent : called")
    print("Procurement Agent:::: run_procurement_agent : config received =>", config)
    try:
        crud = config["configurable"]["crud"]
        procurement_mgr = ProcurementManager(crud)
    except Exception as e:
        print("Procurement Agent:::: run_procurement_agent : failed to initialize crud or UOCManager:", e)
        state["latest_respons"] = "Sorry, there was a system error. Please try again later."
        return state
    
    last_msg = state["messages"][-1]["content"] if state.get("messages") else ""
    print("Procurement Agent:::: run_procurement_agent : last_msg:", last_msg)     
    user_stage = state.get("user_stage", {})
    print("Procurement Agent:::: run_procurement_agent : user_stage:", user_stage)
    if not state.get("first_run", True):
         # Check if the user is in the procurement stage
        state_for_intent_match = state.copy()
        state_for_intent_match["image_path"]="" if last_msg else state.get("image_path","")
        from orchastrator.core import infer_intent_node
        latest_msg_intent = (await infer_intent_node(state_for_intent_match)).get("intent")
        state["intent"] = latest_msg_intent
        print("Procurement Agent:::: run_procurement_agent - Intent of latest message is - ", latest_msg_intent)
    
        # ---------- 0 · Button click (id) ---------------------------
    if last_msg.lower() in _HANDLER_MAP:
        return await _HANDLER_MAP[last_msg.lower()](state,  state.get("latest_respons", None), config, state.get("uoc_next_message_extra_data", []))
    
    try:
        async with AsyncSessionLocal() as session:
            procurement_mgr = ProcurementManager(session)
    except Exception as e:
        print("Procurement Agent:::: run_procurement_agent : failed to initialize session:", e)
        state["latest_respons"] = "Sorry, there was a system error. Please try again later."
        return state
    if user_stage == "new":
        print("Procurement agent :::: run_procurement_agent :::: User is new, setting up procurement stage")
        await new_user_flow(state, crud)
        if state.get("uoc_confidence") == "high":
            print("Procurement Agent:::: run_procurement_agent : Procurement confirmed — updating DB")
            try:
                request_id = state.get("active_material_request_id")
                if request_id:
                    await procurement_mgr.update_procurement_request(request_id, state)
            except Exception as e:
                print("Procurement Agent:::: run_procurement_agent : Failed to update procurement after confirmation:", e)
    

        # Add additional stages or fallback logic here if needed
    return state
    # # return await collect_procurement_details_interactively(state)
    # last_msg = state["messages"][-1]["content"]
    # print("Procurement agent received:", last_msg)
    # img_path = state.get("image_path", "")
    # print("Image path from WhatsApp:", img_path)

    # # Step 1: Ask LLM to extract structured fields

    # system_prompt = """
    #     A customer sent the following material request:

    #     Given the user's message, extract:
    #     - material → combine brand name and material type (e.g., "Deccan TMT"). ALso note that dimenions, sizer variotions can be present the saem SKU. So present the SKU with different dimensions as a single SKU. ) Ex: Deccan TMT 20mm, Vizag TMT 8mm etc
    #     - quantity → size, count, or weight  be careful size doesent always mean quantity, it cana difretn version of the same SKU. Also note that the quantity can be a range (e.g., "100-200 bags")
    #     - location → (if present). Note: the word "Vizag" can also be a brand (e.g., "Brand TMT") — do not always assume it's a location.

    #     Respond ONLY in this JSON format:
    #     { "material": "...", "quantity": "..." }
        
    #      Example Scenario (for your understanding - your response should still be only JSON):
    #         If the image shows:
    #         "Shri Ram Hardware
    #         Bill No. 1234
    #         Date: 17/07/2025
    #         Customer: ABC Co.
    #         Deccan TMT 20mm - 150 units
    #         Vizag TMT 10mm - 200 units
    #         ACC Cement 50kg bags - 50 bags
    #         Total: 25000 INR"

    #         Your expected internal thought process would be:

    #         - Identify "Deccan TMT 20mm" as a material. Its quantity is "150 units".
    #         - Identify "Vizag TMT 10mm" as a material. Its quantity is "200 units".
    #         - Identify "ACC Cement 50kg bags" as a material. Its quantity is "50 bags".
    #         - Discard "Shri Ram Hardware", "Bill No. 1234", "Date", "Customer", "Total".

    #         Your JSON response should be:

    #         [
    #         { "material": "Deccan TMT 20mm", "quantity": "150 units" },
    #         { "material": "Vizag TMT 10mm", "quantity": "200 units" },
    #         { "material": "ACC Cement 50kg bags", "quantity": "50 bags" }
    #         ]
    #     """

    # print("System prompt:", system_prompt)
    # print("Last message:", last_msg)
    # # return await collect_procurement_details_interactively(state)
    # if last_msg and img_path == "":
    #     chat_response = await llm.ainvoke([
    #         SystemMessage(content=system_prompt),
    #         HumanMessage(content=last_msg)
    #     ])
    #     print("#############Chat response:", chat_response.content)
    #     try:
    #         extracted = eval(chat_response.content)  # TODO: Use safe pydantic parsing
    #     except Exception as e:
    #         return {
    #             "messages": state["messages"] + [{
    #                 "role": "assistant",
    #                 "content": f" Couldn't understand your request: {e}"
    #             }]
    #         }
    
    # print("procurement_agent :::: run_procurment_agent :::: Image path from whatsapp:", state.get("image_path"))
    
    # if img_path:
    #     try:
    #         print("procurement_agent :::: run_procurment_agent :::: Encoding image to base64")
    #         img_b64 = encode_image_base64(img_path)
    #         print("procurement_agent :::: run_procurment_agent :::: Encoded image successfully", img_b64)
    #     except FileNotFoundError:
    #         print(f"procurement_agent :::: run_procurment_agent :::: Image file not found: {img_path}")
    
    #     # print("procurement_agent :::: run_procurment_agent :::: Downloading image from WhatsApp")
    #     # image_path = download_whatsapp_image(img_path)
    #     # print("procurement_agent :::: run_procurment_agent :::: Downloaded image successfully:", image_path)
    #     extracted_material_from_image = await extract_materials_from_image(img_b64, state.get("caption", ""))
    #     print("procurement_agent :::: run_procurment_agent :::: Extracted material from image:", extracted_material_from_image)
    

    # # # Step 2: Match SKU using LSIE
   
    # # match = _local_sku_intent_engine.invoke({
    # # "query": extracted["material"],
    # # "quantity": extracted["quantity"]})
    # # matched_sku = match["matches"][0] if match["matches"] else "No match"
 
    # # # Step 3: Return formatted quote
    # # quote_msg = (
    # #     f"🧾 Quote:\n"
    # #     f"- SKU: {matched_sku}\n"
    # #     f"- Quantity: {extracted['quantity']}\n"
    # #     f"- Location: {extracted['location']}\n"
    # #     f"- Vendor: Srinivas Traders\n"
    # #     f"- Price: ₹395/unit (mock)\n"
    # #     f"- ETA: 6 hrs" 
    # # )
    # # print("###########This is the quote message",quote_msg)
    # # state["messages"].append({"role": "assistant", "content": quote_msg})
    # state["latest_respons"] = str(extracted_material_from_image) if img_path else str(extracted)
    # state["needs_clarification"] = True
    # state["uoc_question_type"] = "procurement"
    # return state


async def extract_materials(text: str = "", img_b64: str = None) -> list:
    """
    Extracts materials (with quantity) from either a user message, an image (BOQ/invoice), or both.
    Returns a list of dicts: [{'material': ..., 'quantity': ...}, ...]
    """

    sys_prompt = """
You are an expert data extraction AI specializing in construction procurement.

Your job is to extract line items for **materials** and their **quantities** from user input.
The input may be a text message, a photo (BOQ/invoice/handwritten list), or both together.

Extraction Instructions:
------------------------
For each line item, extract the following fields (not all are mandatory, only include if present):

- 'material': The main product or brand name (e.g., "Deccan Cement", "Vizag TMT", "ACC Cement").
    * This is the core name of the product or brand, without dimensions or size.
- 'sub_type': The specific type, grade, or variant (e.g., "OPC 53 Grade", "Premium", "Ultra", "Fly Ash").
    * This is an optional field for further classification if present.
- 'dimensions': Any size, thickness, or measurement (e.g., "20", "50", "8", "4x8").
    * This is optional and should be included if available.
- 'dimension_units': The unit for the dimension (e.g., "mm", "kg", "ft", "bags").
    * Optional, but include if present.
- 'quantity': The numeric value representing how many/much is needed (e.g., "150", "50", "20.5", "10-20").
    * This is the count, weight, or volume. Only include if you are confident.
- 'quantity_units': The unit for the quantity (e.g., "units", "bags", "tons", "kg", "meters").
    * Optional, but include if present.

Guidelines:
-----------
1. Each material-variation (different dimensions/spec/type) should be a separate entry.
2. Consider quantity as number instead of string, e.g., 150 instead of "150".
2. Ignore all irrelevant data (shop names, phone numbers, addresses, prices, customer names, dates, totals, payment terms, etc).
   Focus **only on material SKUs and their quantities**.
3. If a field is unclear or missing, exclude it rather than guessing.
4. If both image and text are provided, **combine all available information** for best extraction.
5. Return output as a JSON array of objects:
[
  { "material": "...", "sub_type": "...", "dimensions": "...", "dimension_units": "...", "quantity": ..., "quantity_units": "..." },
  ...
]
Only include fields that are present for each item. If nothing is found, return [].
6. Do not include any extra explanation, markdown, or commentary—**just the JSON array**.
7. User might provide a text message, an image, or both. If both are provided, extract from both.
8. User might communicate via text or image in English or Telugu. Don't translate, just extract as-is.
9. You should be able to understand Telugu text and English text from images or text and extract materials from it as well.
10. If any material is unclear to you, may be you can try to find out the category of the material based on name or dimensions, and try to extract the material name and quantity from it.
11. Most general types of construction materials are:
    - Cement (OPC, PPC, etc.)
    - TMT Bars (Deccan TMT, Vizag TMT, etc.)
    - Aggregates (Coarse, Fine, etc.)
    - Bricks (Red, Fly Ash, etc.)
    - Sand (River, Manufactured, etc.)
    - Plumbing Materials (Pipes, Fittings, etc.)
    - Electrical Materials (Wires, Switches, etc.)
    - Paints (Interior, Exterior, etc.)
    - Roofing Materials (Tiles, Sheets, etc.)
    - Flooring Materials (Tiles, Marble, etc.)
    - Hardware (Doors, Windows, etc.)
    - Miscellaneous (Tools, Safety Gear, etc.)
    - Carpentry Materials (Wood, Plywood, etc.)
    - Glass (Float, Toughened, etc.)
    - Insulation Materials (Thermal, Acoustic, etc.)
    - Waterproofing Materials (Membranes, Coatings, etc.)
    - Scaffolding Materials (Planks, Props, etc.)

12. Based on the above types, you can try to extract the material name and quantity from the text or image.

Example Input (text or photo contains):
---------------------------------------
Shri Ram Hardware
Bill No. 1234
Date: 17/07/2025
Customer: ABC Co.
Deccan TMT 20mm - 150 units
Vizag TMT 10mm - 200 units
ACC OPC 53 Grade Cement 50kg bags - 50 bags
CenturyPly 8 ft × 3½ ft × 2 in Plywood - 20 sheets
Total: 25000 INR

Expected Output:
[
  { "material": "Deccan TMT", "dimensions": "20", "dimension_units": "mm", "quantity": 150, "quantity_units": "units" },
  { "material": "Vizag TMT", "dimensions": "10", "dimension_units": "mm", "quantity": 200, "quantity_units": "units" },
  { "material": "ACC Cement", "sub_type": "OPC 53 Grade", "dimensions": 50, "dimension_units": "kg", "quantity": "50", "quantity_units": "bags" }
  { "material": "CenturyPly Plywood", "dimensions": "8 ft × 3½ ft × 2 in", "quantity": 20, "quantity_units": "sheets" }
]

If no relevant data is present, output: []
    """

    user_payload = []
    if text:
        user_payload.append({"type": "text", "text": text})
    elif img_b64:    
        user_payload.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}})
    else:
        user_payload = "Extract any construction material details from this input."

    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_payload}
    ]
    try:
        response = await llm.ainvoke(messages)
        return safe_json(response.content, default=[])
    except Exception as e:
        print("Material extraction error:", e)
        return []



# async def extract_materials_from_message(msg: str) -> list:
#     """
#     Use LLM (text-only) to extract material info from a message.
#     Should return a list of dicts with 'material' and 'quantity'.
#     """
     
#     sys_prompt = """
#         A customer sent the following material request:

#         Given the user's message, extract:
#         - material → combine brand name and material type (e.g., "Deccan TMT"). ALso note that dimenions, sizer variotions can be present the saem SKU. So present the SKU with different dimensions as a single SKU. ) Ex: Deccan TMT 20mm, Vizag TMT 8mm etc
#         - quantity → size, count, or weight  be careful size doesent always mean quantity, it cana difretn version of the same SKU. Also note that the quantity can be a range (e.g., "100-200 bags")
#         - location → (if present). Note: the word "Vizag" can also be a brand (e.g., "Brand TMT") — do not always assume it's a location.

#         Respond ONLY in this JSON format:
#         { "material": "...", "quantity": "..." }
        
#          Example Scenario (for your understanding - your response should still be only JSON):
#             If the image shows:
#             "Shri Ram Hardware
#             Bill No. 1234
#             Date: 17/07/2025
#             Customer: ABC Co.
#             Deccan TMT 20mm - 150 units
#             Vizag TMT 10mm - 200 units
#             ACC Cement 50kg bags - 50 bags
#             Total: 25000 INR"

#             Your expected internal thought process would be:

#             - Identify "Deccan TMT 20mm" as a material. Its quantity is "150 units".
#             - Identify "Vizag TMT 10mm" as a material. Its quantity is "200 units".
#             - Identify "ACC Cement 50kg bags" as a material. Its quantity is "50 bags".
#             - Discard "Shri Ram Hardware", "Bill No. 1234", "Date", "Customer", "Total".

#             Your JSON response should be:

#             [
#             { "material": "Deccan TMT 20mm", "quantity": "150 units" },
#             { "material": "Vizag TMT 10mm", "quantity": "200 units" },
#             { "material": "ACC Cement 50kg bags", "quantity": "50 bags" }
#             ]
#         """
        
#     messages = [
#         {"role": "system", "content": sys_prompt},
#         {"role": "user", "content": msg}
#     ]
#     try:
#         response = await llm.ainvoke(messages)
#         return safe_json(response.content, default=[])
#     except Exception:
#         return []


# async def extract_materials_from_image(image_path: str, caption: str = "") -> List[dict]:
#         """
#         Use LLM (with vision) to extract materials from an image/photo. 
#         """
#         sys_prompt = """
#             You are an expert data extraction AI specialized in identifying construction material information from scanned or photographed sheets (e.g., invoices, packing lists).

#             Your primary goal is to accurately extract 'material' and 'quantity' data from the provided image and format it into a JSON object.

#             Here are the strict guidelines:

#             1.  Input: You will be provided with an image of a written or printed sheet containing material data.

#             2.  Extraction - 'material' Field:

#                 * Identify the brand name and the material type. Combine them to form the 'material' string (e.g., "Deccan TMT", "Vizag TMT").
#                 * Crucially, if a material SKU has variations in dimensions (e.g., "Deccan TMT 20mm", "Deccan TMT 8mm") or other size specifications, consider these different product variations and list them as separate material entries. Do NOT consolidate different dimensional SKUs into a single entry. Each unique material-dimension combination is a distinct SKU.
#                 * Examples: "Deccan TMT 20mm", "Vizag TMT 8mm", "UltraTech Cement OPC 53 Grade".

#             3.  Extraction - 'quantity' Field:

#                 * Identify the corresponding quantity for each material. This can be a count, weight, volume, or a range.
#                 * Be precise: Extract the exact numerical value and units (e.g., "100 bags", "500 kg", "20.5 cubic meters", "10-20 units").
#                 * Important: Understand that a 'size' or 'dimension' associated with the material (e.g., "20mm" in "Deccan TMT 20mm") is part of the 'material' description itself, not the 'quantity'. The 'quantity' refers to how many of that specific material SKU are present.

#             4.  Output Format:

#                 * Your response MUST ONLY be a JSON array of objects, where each object represents a single material entry and its corresponding quantity.
#                 * Do not include any other text, explanations, or extraneous information outside of the JSON structure.
#                 * The structure for each object in the array should be:
#                     json
#                     {
#                     "material": "...",
#                     "quantity": "..."
#                     }
#                     
#                 * If multiple materials are found, the output should look like:
#                     json
#                     [
#                     { "material": "Deccan TMT 20mm", "quantity": "100 units" },
#                     { "material": "UltraTech Cement OPC 53 Grade", "quantity": "50 bags" } 
#                     ]
#                     
#                 * If no relevant material data is found, return an empty JSON array: `[]`

#             5.  Exclusions:

#                 * Ignore and exclude any irrelevant data such as shop names, addresses, phone numbers, contact details, dates, payment information, or any other content not directly related to the material SKU and its quantity. Focus exclusively on extracting the material line items.

#             Confidence Score: Implicitly, prioritize high accuracy in extraction. If unsure about an entry, it's better to exclude it than to provide incorrect data.

#             Example Scenario (for your understanding - your response should still be only JSON):
#             If the image shows:
#             "Shri Ram Hardware
#             Bill No. 1234
#             Date: 17/07/2025
#             Customer: ABC Co.
#             Deccan TMT 20mm - 150 units
#             Vizag TMT 10mm - 200 units
#             ACC Cement 50kg bags - 50 bags
#             Total: 25000 INR"

#             Your expected internal thought process would be:

#             - Identify "Deccan TMT 20mm" as a material. Its quantity is "150 units".
#             - Identify "Vizag TMT 10mm" as a material. Its quantity is "200 units".
#             - Identify "ACC Cement 50kg bags" as a material. Its quantity is "50 bags".
#             - Discard "Shri Ram Hardware", "Bill No. 1234", "Date", "Customer", "Total".

#             Your JSON response (this is what the agent will output for such a case) should be:

#             [
#             { "material": "Deccan TMT 20mm", "quantity": "150 units" },
#             { "material": "Vizag TMT 10mm", "quantity": "200 units" },
#             { "material": "ACC Cement 50kg bags", "quantity": "50 bags" }
#             ]
#         """
#         messages = [
#             {"role": "system", "content": sys_prompt},
#             {
#                 "role": "user",
#                 "content": (
#                     caption if not image_path
#                     else [
#                         {"type": "text", "text": caption},
#                         {"type": "image_url",
#                         "image_url": {"url": f"data:image/jpeg;base64,{image_path}"}}
#                     ]
#                 ),
#             },
#         ]
#         print("procurement_agent :::: extract_materials_from_image :::: Messages to LLM:", messages)
        
#         try:
#             response = await llm.ainvoke(messages)
#             print("procurement_agent :::: extract_materials_from_image :::: LLM response:", response.content)
#         except Exception as e:
#             print(f"procurement_agent :::: extract_materials_from_image :::: Error invoking LLM: {e}")
#             return []
#         return safe_json(response.content, default=[])


def encode_image_base64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")
    
def safe_json(text: str, default=None):
    """
    Try hard to get JSON out of an LLM block.
    - Strips json fences
    - Tries a raw json.loads
    - Fallback: regex find first {...}
    - On failure returns default (dict() if not supplied)
    """
    txt = text.strip()
    if txt.startswith(""):
        txt = txt.strip("`").lstrip("json").strip()

    try:
        return json.loads(txt)
    except Exception:
        match = _JSON_PATTERN.search(txt)
        if match:
            try:
                return json.loads(match.group(0))
            except Exception:
                pass

    return default if default is not None else {}

async def collect_procurement_details_interactively(state: dict) -> dict:
    """
    Interactive loop to collect procurement details over WhatsApp:
      • Sends chat history + current procurement details to the LLM
      • Receives procurement update and control JSON
      • Merges result, updates state, and returns
    """
    chat_history = state.get("messages", [])
    procurement_details = state.get("procurement_details", {
        "materials": [],
        "vendor": "",
        "price": "",
        "delivery_date": "",
        "location": "",
        "notes": ""
    })

    # SYSTEM PROMPT — clear strategy, clarify vague input, ask for missing info
    system_prompt = (
                """
        You are a **smart, friendly procurement assistant** who speaks in a soft, warm tone. You're here to **gently guide users** through placing construction material requests — whether they start with a casual message, upload a photo, or provide structured input.

        ---------------------------
        Known Procurement Details:
        ---------------------------
        <insert JSON-dump of state["procurement_details"]>

        =================== GOAL ===================
        Help the user complete a material procurement request with these fields:
        - Material name (brand/type like "ACC Cement", "Vizag TMT")
        - Sub-type or grade (e.g., "OPC 53", "Fly Ash", "53 Grade")
        - Dimensions (e.g., "20", "4x8", "10", "50")
        - Dimension unit (e.g., mm, kg, inch, ft)
        - Quantity (numeric or range like 100, 50, 10–20)
        - Quantity unit (e.g., units, bags, tons, meters)
        - Delivery urgency/date
        - Preferred vendor (or "Any")
        - Optional notes

        You may get:
        - Vague text: “Need cement and TMT”
        - Structured lists: “Vizag TMT 8mm – 200 kg, Deccan OPC – 50 bags”
        - Mixed messages over multiple replies
        - Photos (BOQ, handwritten notes, invoices)

        ================ EXAMPLE SCENARIO ================

        🧾 **1. Text-Only Message (Partial Info):**
        User: “Need Vizag TMT and ACC cement”
        
        You reply warmly:
        
        Got it! Just checking:
        - Vizag TMT: what size (e.g., 8mm, 10mm)? And how many kg?
        - ACC Cement: is it OPC 53 Grade or something else? How many bags?

        Example:
        - Vizag TMT 10mm – 300 kg
        - ACC OPC 53 – 50 bags
        

        🖼 **2. Photo of Material List:**
        You detect image + caption, extract known materials:
        
        Looks like you need:
        1. Deccan TMT 20mm – 150 units
        2. ACC Cement OPC 53 Grade – 50 bags

        Shall I proceed with these? Or would you like to adjust quantities or specs?
        

        📋 **3. Structured Entry Already Present:**
        If all fields are present and clear:
        
        Here’s what I have so far:
        - Deccan Cement OPC 53 – 50 kg – 40 bags
        - Vizag TMT 8mm – 200 kg
        - CenturyPly Plywood 8 ft × 3½ ft × 2 in – 20 sheets

        ✅ Confirm to proceed or let me know if you'd like to edit anything.
        

        🕒 **4. Missing Delivery Info:**
        
        When would you like these materials delivered?

        For example:
        - “ASAP”
        - “Within 2 days”
        - “Before Friday”
        

        🛍 **5. Vendor Selection:**
        
        Do you have a preferred vendor?

        You can say:
        - “Srinivas Traders”
        - “Any” — and I’ll fetch quotes from available suppliers.
        

        🧠 **6. Confusing Response:**
        If the message is unclear:
        
        Hmm… I didn’t quite get that. Could you help me with a few more details?

        For example:
        - "Vizag TMT 10mm – 200 kg"
        - "ACC OPC 53 Cement – 50 bags"
        

        ================ STRATEGY ================
        1. Speak warmly and professionally. Be empathetic and clear.
        2. Ask ONE thing at a time unless summarizing.
        3. If any material is unclear to you, may be you can try to find out the category of the material based on name or dimensions, and try to extract the material name and quantity from it.
        4. Most general types of construction materials are:
            - Cement (OPC, PPC, etc.)
            - TMT Bars (Deccan TMT, Vizag TMT, etc.)
            - Aggregates (Coarse, Fine, etc.)
            - Bricks (Red, Fly Ash, etc.)
            - Sand (River, Manufactured, etc.)
            - Plumbing Materials (Pipes, Fittings, etc.)
            - Electrical Materials (Wires, Switches, etc.)
            - Paints (Interior, Exterior, etc.)
            - Roofing Materials (Tiles, Sheets, etc.)
            - Flooring Materials (Tiles, Marble, etc.)
            - Hardware (Doors, Windows, etc.)
            - Miscellaneous (Tools, Safety Gear, etc.)
            - Carpentry Materials (Wood, Plywood, etc.)
            - Glass (Float, Toughened, etc.)
            - Insulation Materials (Thermal, Acoustic, etc.)
            - Waterproofing Materials (Membranes, Coatings, etc.)
            - Scaffolding Materials (Planks, Props, etc.)
        5. Based on the above types, you can try to extract the material name and quantity from the text or image.
        6. Use buttons where helpful (like "ASAP", "Any vendor", "Confirm Order").
        7. Be patient. Never rush the user.
        8. Give concrete examples always.
        9. Assume the user has minimal context — make it simple.
        10. Use might provide data in text or image in English or Telugu, Don't translate, extract as-is.
        11. You should be able to understand written Telugu or English, but do not translate it. Just extract the material details as-is. 
         
        ============= OUTPUT FORMAT ============
        At the end of every interaction, respond ONLY in this strict JSON format:

        {
          "latest_respons": "<your next WhatsApp message here>",
          "next_message_type": "button",      // 'plain' for text-only, 'button' for interactive options
          "next_message_extra_data": [        // optional — only if next message has buttons
            { "id": "<kebab-case-id>", "title": "<Short Button Title ≤20 chars>" }
          ],
          "procurement_details": {
            "materials": [
              {
                "material": "ACC Cement",
                "sub_type": "OPC 53 Grade",
                "dimensions": "50",
                "dimension_units": "kg",
                "quantity": 40,
                "quantity_units": "bags"
              },
              {
                "material": "Vizag TMT",
                "dimensions": "8",
                "dimension_units": "mm",
                "quantity": 200,
                "quantity_units": "kg"
              }
            ],
            "delivery_date": "2025-07-29",
            "vendor": "Any"
          },
          "uoc_confidence": "low",     // set to "high" only when all needed fields are present
          "uoc_question_type": "procurement"
        }
        
        At the end of your reasoning, ALWAYS respond in this exact JSON format:
            {
              "latest_respons": "<your next WhatsApp message here>",
              "next_message_type": "button",  // 'plain' for text-only, 'button' for buttons
              "next_message_extra_data": [{ "id": "<kebab-case>", "title": "<≤20 chars>" }, "{ "id": "<kebab-case>", "title": "<≤20 chars>" }", "{ "id": "main_menu", "title": "📋 Main Menu" }"],
              "procurement_details": { <updated procurement_details so far> },
              "needs_clarification": true,  // false if user exited
              "uoc_confidence": "low",      // 'high' only when structure is complete
              "uoc_question_type": "procurement"
            }

        =============== RULES =================
        - DO NOT include markdown or formatting syntax.
        - DO NOT wrap the JSON in  or markdown fences.
        - Output ONLY the raw JSON above, nothing else.
        """

    )

    # BUILD LLM MESSAGE HISTORY
    messages = [SystemMessage(content=system_prompt)]
    messages += [HumanMessage(content=m["content"]) for m in chat_history]

    if procurement_details:
        messages.append(HumanMessage(content="Current known procurement details:\n" + json.dumps(procurement_details)))

    # CALL LLM
    try:
        llm_raw = await llm.ainvoke(messages)
        llm_clean = llm_raw.content.strip().replace("json", "").replace("", "")
        parsed = json.loads(llm_clean)
    except Exception:
        state.update({
            "needs_clarification": True,
            "proc_confidence": "low",
            "latest_respons": "Sorry, I couldn’t read that. Could you please re-phrase?"
        })
        return state

    # UPDATE PROCUREMENT DETAILS
    updated_details = parsed.get("procurement_details")
    if updated_details:
        state["procurement_details"] = updated_details

    # COPY CONTROL FIELDS
    state.update({
        "latest_respons": parsed["latest_respons"],
        "proc_next_message_type": parsed.get("next_message_type", "plain"),
        "proc_next_message_extra_data": parsed.get("next_message_extra_data"),
        "needs_clarification": parsed.get("needs_clarification", True),
        "uoc_confidence": parsed.get("uoc_confidence", "low"),
        "uoc_question_type":  "procurement",
    })
    
    # print("Procurement Agent:::: new_user_flow : starting uoc resolver", )
    # try:
    #         async with AsyncSessionLocal() as session:
    #             uoc_mgr = UOCManager(DatabaseCRUD(session))
    #             state = await uoc_mgr.resolve_uoc(state, uoc_last_called_by="procurement")
    #             print("Procurement Agent:::: collect_procurement_details_interactively :::: state from uoc:", state)
    #             state["latest_respons"] = "Here are the list of projects I found for you. Please select one to link this procurement request to a project."
    #             state["active_project_id"] = state.get("uoc_next_message_extra_data", {})[0].get("id", "")
    #             state["uoc_question_Type"] = "procurement"
    #             print("Procurement Agent:::: collect_procurement_details_interactively :::: after uoc, active project id:", state["active_project_id"])
    #             return state
    # except Exception as e:
    #         print("Error resolving project for procurement:", e)
    #         state["latest_respons"] = "Sorry, I couldn't link this to a project. Please try again."
    #         return state
    # print("Procurement Agent:::: new_user_flow : uoc_manager ran :", state)

    # FINALIZE IF SETUP COMPLETE OR USER EXITED
    print("procurement_agent :::: collect_procurement_details_interactively :::: Parsed state:", parsed)
    
    user_message = (
        state.get("messages", [])[-1].get("content", "").strip().lower()
        if state.get("messages") else "")
    if user_message == "main_menu" or not state["needs_clarification"]:
        print("procurement_agent :::: collect_procurement_details_interactively :::: User exited or confirmed procurement details.")
        sender_id = state.get("sender_id")
        quick_msg = parsed.get("latest_respons", "Procurement details completed. You can now proceed with your order.")
        whatsapp_output(sender_id, quick_msg, message_type="plain")
        state["needs_clarification"] = False
        state["uoc_confidence"] = "high" if updated_details else "low"
        state["uoc_question_type"] = "procurement"
        # Save to DB or trigger next workflow here if needed
        if state.get("uoc_confidence") == "high":
            print("procurement_agent :::: collect_procurement_details_interactively :::: Procurement details are complete.")
            try:
                async with AsyncSessionLocal() as session:
                    procurement_mgr = ProcurementManager(session)
                    request_id = state.get("active_material_request_id")
                    if request_id:
                        print("procurement_agent :::: collect_procurement_details_interactivley :::: high uoc confidence :::: Updating procurement request with interactive details.")
                        await procurement_mgr.update_procurement_request(request_id, state)
                        print("procurement_agent :::: collect_procurement_details_interactively :::: Procurement request updated successfully.")
            except Exception as e:
                print("❌ Error while updating procurement after interactive confirmation:", e)
            print("procurement_agent :::: collect_procurement_details_interactively :::: Sending WhatsApp output, Saved state:", state)
            
            print("procurement_agent :::: collect_procurement_details_interactively :::: Sending quote request to vendor.")
            # try:
            #     await send_quote_request_to_vendor(state)
            # except Exception as e:
            #     print("❌ Error sending quote request to vendor:", e)
            #     state["latest_respons"] = "Sorry, I couldn't send the quote request. Please try again later."
            # print("procurement_agent :::: collect_procurement_details_interactively :::: Quote request sent to vendor.")
            
            # return state
    
    return state

async def send_quote_request_to_vendor(state: dict):
    vendor_phone_number = state["sender_id"]  # Vendor WhatsApp number (without +)
    
    # Mock: Materials this vendor can supply
    vendor_supported_materials = ["KCP 53 grade cement", "Deccan TMT 20mm", "ACC Cement 50kg bags"]

    # Get full material list from procurement
    materials = state.get("procurement_details", {}).get("materials", [])

    # Filter materials vendor can supply
    relevant_items = [
        item for item in materials
        if any(mat.lower() in item["material"].lower() for mat in vendor_supported_materials)
    ]

    if not relevant_items:
        print(f"No matching materials for vendor {vendor_phone_number}")
        return

    # Format WhatsApp message
    message_lines = ["📦 New Quote Request\n\nHere are the materials we need:"]
    for idx, item in enumerate(relevant_items, 1):
        message_lines.append(f"{idx}. {item['material']} – {item['quantity']}")

    message_lines.append("\nPlease reply with your quote and delivery estimate. ✅")
    message = "\n".join(message_lines)

    # Send WhatsApp message
    whatsapp_output(vendor_phone_number, message, message_type="plain")
    print(f"✅ Quote request sent to vendor {vendor_phone_number}")
