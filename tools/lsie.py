# power_purchase/tools/lsie_tool.py

from difflib import get_close_matches
from langchain.tools import tool

SKU_CATALOG = [
    "Vizag TMT",
    "Ultratech Cement",     
    "Deccan Cement",
    "Raasi Cement",
    "OPC variant",
    "PPC variant",
    "Iron"
]


@tool
def _local_sku_intent_engine(query: str, quantity: str) -> dict:
    """
    Match a material query to known or similar SKU.
    """
    matches = get_close_matches(query, SKU_CATALOG, n=1, cutoff=0.3)
    return {
        "query": query,
        "quantity": quantity,
        "matches": matches
    }
