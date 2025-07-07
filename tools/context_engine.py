import json
from typing import List
from sentence_transformers import SentenceTransformer, util
import torch


model = SentenceTransformer("all-MiniLM-L6-v2")

UNIFIED_BRAIN = [
    {
        "content": "IS 456: Slabs must be cured for at least 7 days after casting.",
        "tags": ["slab", "curing", "structure"],
        "source": "IS"
    },
    {
        "content": "Many leakage complaints happen when balcony tiles are laid before waterproofing.",
        "tags": ["balcony", "waterproofing", "tile"],
        "source": "Complaint"
    },
    {
        "content": "During monsoons, delay brick stacking to avoid moisture bulging.",
        "tags": ["brickwork", "moisture", "monsoon"],
        "source": "Field"
    },
    {
        "content": "AC pipes on east-facing walls often cause seepage if not insulated properly.",
        "tags": ["ac", "wall", "east", "seepage"],
        "source": "Field"
    },
    {
        "content": "IS 2212: Mortar for brick masonry should have a cement:sand ratio not leaner than 1:6.",
        "tags": ["brickwork", "mortar", "IS", "wall"],
        "source": "IS"
    },
    {
        "content": "Walls should be checked for vertical alignment after every 5 layers of brickwork.",
        "tags": ["wall", "alignment", "brickwork", "qa"],
        "source": "Field"
    },
    {
        "content": "Common cracks in walls occur when plastering is done before proper curing of brickwork.",
        "tags": ["plastering", "cracks", "curing", "wall"],
        "source": "Complaint"
    },
    {
        "content": "Avoid applying plaster on wet or dusty surfaces for better adhesion.",
        "tags": ["plastering", "adhesion", "surface prep"],
        "source": "Field"
    },
    {
        "content": "IS 1661: The recommended thickness of internal cement plaster is 12 mm.",
        "tags": ["plastering", "thickness", "IS", "wall"],
        "source": "IS"
    },
    {
        "content": "Plaster should be finished within 60 minutes of mixing to avoid setting issues.",
        "tags": ["plastering", "timing", "qa"],
        "source": "Field"
    }
]



brain_embeddings = model.encode([item["content"] for item in UNIFIED_BRAIN], convert_to_tensor=True)

def filter_tags(component: str, stage: str, zone: str) -> List[str]:
    relevant = []
    for entry in UNIFIED_BRAIN:
        if component in entry["tags"] or stage in entry["tags"] or zone in entry["tags"]:
            relevant.append(entry["content"])
    return relevant

def vector_search(query: str, top_k: int = 5) -> List[str]:
    query_embedding = model.encode(query, convert_to_tensor=True)
    cos_scores = util.pytorch_cos_sim(query_embedding, brain_embeddings)[0]
    top_results = torch.topk(cos_scores, k=top_k)
    
    results = []
    for score, idx in zip(top_results[0], top_results[1]):
        results.append({
            "content": UNIFIED_BRAIN[idx]["content"],
            "score": score.item(),
            "source": UNIFIED_BRAIN[idx]["source"]
        })
    
    return results

