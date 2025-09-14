# utils/sku_normalizer.py
import re
from math import isnan

INCH_TO_MM = 25.4

TYPE_ALIASES = {
    "pipe": "pipe", "pipes": "pipe",
    "elbow": "elbow", "elbow 90": "elbow", "elbow 45": "elbow",
    "tee": "tee", "tees": "tee", "tee fittings": "tee",
    "reducer": "reducer", "union": "union",
    "adapter": "adapter", "adaptor": "adapter",
    "coupling": "coupling", "couplings": "coupling",
    "nipple": "nipple", "cap": "cap", "plug": "plug",
    "bushing": "bushing", "valve": "valve",
}

def normalize_text(s: str) -> str:
    s = (s or "").strip().lower()
    s = s.replace("″", '"').replace("”", '"')
    s = re.sub(r"\s+", " ", s)
    return s

def normalize_type(raw: str) -> str:
    raw = normalize_text(raw)
    if not raw:
        return ""
    if raw in TYPE_ALIASES:
        return TYPE_ALIASES[raw]
    for key in sorted(TYPE_ALIASES.keys(), key=len, reverse=True):
        if re.search(rf"\b{re.escape(key)}\b", raw):
            return TYPE_ALIASES[key]
    return raw

def _frac_to_float(s: str) -> float:
    num, den = s.split("/")
    return float(num) / float(den)

def _parse_mixed_inches(text: str) -> float:
    text = text.strip()
    m = re.fullmatch(r"(?:(\d+)[\-\s])?(\d+/\d+)", text)
    if m:
        whole = float(m.group(1)) if m.group(1) else 0.0
        frac = _frac_to_float(m.group(2))
        return whole + frac
    m2 = re.fullmatch(r"\d+(?:\.\d+)?", text)
    if m2:
        return float(m2.group(0))
    return float("nan")

def _parse_one_size(token: str):
    s = normalize_text(token).replace("inches", "inch")
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*mm", s)
    if m:
        mm = float(m.group(1))
        return mm, "mm", False
    m = re.fullmatch(r'((?:\d+[\-\s])?\d+(?:/\d+)?)\s*(?:inch|")', s)
    if m:
        inches = _parse_mixed_inches(m.group(1))
        if not isnan(inches):
            return inches * INCH_TO_MM, "inch", False
    m = re.fullmatch(r"\d+(?:\.\d+)?", s)
    if m:
        return None, None, True
    return None, None, True

def normalize_dimension(raw_dim: str):
    if not raw_dim:
        return dict(primary_mm=None, secondary_mm=None, display=None, ambiguous=True)
    s = normalize_text(raw_dim).replace("×", "x")
    parts = [p.strip() for p in re.split(r"\bx\b", s)]
    vals, units, ambs = [], [], []
    for p in parts[:2]:
        v, u, a = _parse_one_size(p)
        vals.append(v); units.append(u); ambs.append(a)
    disp_parts = []
    for v, u, a, src in zip(vals, units, ambs, parts[:2]):
        if v is None and a:
            disp_parts.append(f"{src} (?)")
        elif u == "mm":
            disp_parts.append(f"{v:g} mm")
        elif u == "inch":
            disp_parts.append(f"{v:g} mm")
        else:
            disp_parts.append(src)
    display = " x ".join(disp_parts) if disp_parts else s
    return dict(
        primary_mm=vals[0] if vals else None,
        secondary_mm=vals[1] if len(vals) > 1 else None,
        display=display,
        ambiguous=any(ambs) or all(v is None for v in vals),
    )

def try_infer_size_from_text(text: str):
    t = normalize_text(text or "")
    m = re.search(r'((?:\d+[\-\s])?\d+(?:/\d+)?)\s*(?:inch|")', t)
    if m:
        inches = _parse_mixed_inches(m.group(1))
        if not isnan(inches):
            mm = inches * INCH_TO_MM
            return mm, None, f'{m.group(1)}" ({mm:g} mm)', False
    m = re.search(r"(\d+(?:\.\d+)?)\s*mm", t)
    if m:
        mm = float(m.group(1))
        return mm, None, f"{mm:g} mm", False
    return None, None, None, True

def parse_query(keyword: str):
    k = normalize_text(keyword)
    q_type = None
    for key in sorted(TYPE_ALIASES.keys(), key=len, reverse=True):
        if re.search(rf"\b{re.escape(key)}\b", k):
            q_type = TYPE_ALIASES[key]
            break
    mm = []
    if "x" in k:
        parts = [p.strip() for p in re.split(r"\bx\b", k)]
        for p in parts[:2]:
            v, _, a = _parse_one_size(p)
            if not a and v is not None:
                mm.append(v)
    if not mm:
        for tok in re.findall(r'((?:\d+[\-\s])?\d+(?:/\d+)?\s*(?:mm|inch|"))|\b\d+(?:\.\d+)?\b', k):
            v, _, a = _parse_one_size(tok)
            if not a and v is not None:
                mm.append(v)
            if len(mm) == 2:
                break
    q_p1 = mm[0] if mm else None
    q_p2 = mm[1] if len(mm) > 1 else None
    base = q_p1 if q_p1 else 25.0
    tol = max(1.0, 0.02 * base)  # 2% or 1mm
    return {"q_type": q_type, "q_p1": q_p1, "q_p2": q_p2, "tol": tol, "raw": keyword}

def type_similarity(a: str, b: str) -> float:
    a = normalize_text(a); b = normalize_text(b)
    if not a or not b: return 0.0
    if a == b: return 1.0
    if a in b or b in a: return 0.85
    aset = set(re.findall(r"[a-z0-9]+", a))
    bset = set(re.findall(r"[a-z0-9]+", b))
    if not aset or not bset: return 0.0
    inter = len(aset & bset); denom = (len(aset) + len(bset)) / 2.0
    return inter / denom
