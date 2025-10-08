# utils/sku_normalizer.py
import re
from fractions import Fraction
from math import isnan
from typing import Optional

INCH_TO_MM = 25.4

INCH_NOMINAL_MM = {
    "1/4": 8,
    "3/8": 10,
    "1/2": 15,
    "5/8": 16,
    "3/4": 20,
    "1": 25,
    "1 1/4": 32,
    "1-1/4": 32,
    "1 1/2": 40,
    "1-1/2": 40,
    "2": 50,
    "2 1/2": 65,
    "2-1/2": 65,
    "3": 80,
    "4": 100,
    "5": 125,
    "6": 150,
    "8": 200,
    "10": 250,
    "12": 300,
    "6/3": 50,
}

def _parse_fraction_token(token: str) -> Fraction:
    token = token.strip()
    if not token:
        raise ValueError("empty fraction token")
    cleaned = token.replace("-", " ")
    cleaned = re.sub(r"\s+", " ", cleaned)
    total = Fraction(0, 1)
    for part in cleaned.split(" "):
        if not part:
            continue
        if "/" in part:
            total += Fraction(part)
        else:
            total += Fraction(part)
    return total

INCH_FRACTION_TO_MM = {
    _parse_fraction_token(key): mm for key, mm in INCH_NOMINAL_MM.items()
}


def _fraction_to_mixed_string(frac: Fraction) -> str:
    sign = "-" if frac < 0 else ""
    frac = abs(frac)
    whole = frac.numerator // frac.denominator
    remainder = Fraction(frac.numerator % frac.denominator, frac.denominator)
    if remainder == 0:
        return f"{sign}{whole}"
    if whole:
        return f"{sign}{whole}-{remainder.numerator}/{remainder.denominator}"
    return f"{sign}{remainder.numerator}/{remainder.denominator}"


def _format_mm(value: float) -> str:
    if value is None:
        return ""
    if abs(value - round(value)) < 1e-6:
        return str(int(round(value)))
    formatted = f"{value:.2f}".rstrip("0").rstrip(".")
    return formatted


def _snap_mm(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    for fraction, nominal_mm in INCH_FRACTION_TO_MM.items():
        actual_mm = float(fraction * INCH_TO_MM)
        if abs(value - actual_mm) <= 0.6:
            return float(nominal_mm)
    return float(round(value)) if abs(value - round(value)) <= 0.1 else value

TYPE_ALIASES = {
    "pipe": "pipe", "pipes": "pipe",
    "elbow": "elbow", "elbow 90": "elbow-90", "elbow 45": "elbow-45",
    "tee": "tee", "tees": "tee", "tee fittings": "tee",
    "reducer": "reducer", "union": "union",
    "adapter": "adapter", "adaptor": "adapter",
    "coupling": "coupling", "couplings": "coupling",
    "nipple": "nipple", "cap": "cap", "plug": "plug",
    "bushing": "bushing", "valve": "valve",
    "tap": "tap", "hose": "hose",
}

MATERIAL_KEYWORDS = {
    "upvc": "uPVC",
    "pvc-u": "uPVC",
    "cpvc": "CPVC",
    "hdpe": "HDPE",
    "gi": "GI",
    "galvanized": "GI",
    "ss": "SS",
    "stainless": "SS",
    "brass": "Brass",
    "pvc": "PVC",
}

VARIANT_KEYWORDS = {
    "swr": "SWR",
    "pressure": "Pressure",
}

def normalize_text(s: str) -> str:
    s = (s or "").strip().lower()
    s = s.replace("??3", '"').replace("???", '"')
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

def _inch_to_mm(token: str):
    try:
        fraction = _parse_fraction_token(token)
    except (ValueError, ZeroDivisionError):
        return float("nan"), False, token
    native = f"{_fraction_to_mixed_string(fraction)}\""
    mapped = INCH_FRACTION_TO_MM.get(fraction)
    if mapped is not None:
        return float(mapped), True, native
    return float(fraction * INCH_TO_MM), False, native


def _parse_one_size(
    raw_token: str,
    *,
    assume_inch: bool = False,
    allow_unitless_numeric: bool = True,
):
    raw_token = (raw_token or "").strip()
    if not raw_token:
        return None, None, True, None

    s = normalize_text(raw_token)
    s = s.replace("inches", "inch")
    s = s.replace("″", '"').replace("”", '"').replace("“", '"')
    s = s.replace("mm.", "mm")
    s = s.replace("dia", "")
    s = re.sub(r"[()]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()

    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*mm", s)
    if m:
        mm = _snap_mm(float(m.group(1)))
        return mm, "mm", False, f"{_format_mm(mm)} mm"

    m = re.fullmatch(r'((?:\d+[\-\s])?\d+(?:/\d+)?|\d+(?:\.\d+)?)\s*(?:inch|in|\")', s)
    if m:
        frac_token = m.group(1)
        mm_val, mapped, native = _inch_to_mm(frac_token)
        if not isnan(mm_val):
            mm_val = _snap_mm(mm_val)
            return mm_val, "inch", not mapped, native

    if assume_inch:
        m = re.fullmatch(r'(?:\d+[\-\s])?\d+(?:/\d+)?|\d+(?:\.\d+)?', s)
        if m:
            frac_token = m.group(0)
            mm_val, mapped, native = _inch_to_mm(frac_token)
            if not isnan(mm_val):
                mm_val = _snap_mm(mm_val)
                return mm_val, "inch", not mapped, native

    m = re.fullmatch(r"\d+(?:\.\d+)?", s)
    if m and allow_unitless_numeric:
        token_value = m.group(0)
        if "." in token_value:
            return None, None, True, raw_token
        mm = _snap_mm(float(token_value))
        return mm, "mm", False, f"{_format_mm(mm)} mm"

    return None, None, True, raw_token or None

def normalize_dimension(raw_dim: str):
    if not raw_dim:
        return dict(primary_mm=None, secondary_mm=None, primary_native=None, secondary_native=None, primary_unit=None, secondary_unit=None, display=None, ambiguous=True)

    inch_hint = bool(re.search(r'(?:"|inch)', raw_dim.lower())) or bool(re.search(r'/\d', raw_dim))
    split_pattern = re.compile(r'[xX×✕✖✗✘⋅·∙*\ufffd]')
    raw_parts = [p.strip() for p in split_pattern.split(raw_dim.replace('A-', 'x')) if p.strip()]

    vals, units, natives, ambs = [], [], [], []
    for raw_part in raw_parts[:2]:
        v, u, a, native = _parse_one_size(raw_part, assume_inch=inch_hint)
        v = _snap_mm(v) if v is not None else None
        vals.append(v)
        units.append(u)
        natives.append(native)
        ambs.append(a)

    display_parts = []
    for raw_part, native, v, unit, ambiguous in zip(raw_parts[:2], natives, vals, units, ambs):
        if native:
            display_parts.append(native)
        elif v is not None:
            label = 'mm' if unit in (None, 'mm') else unit
            display_parts.append(f"{_format_mm(v)} {label}")
        else:
            display_parts.append(f"{raw_part} (?)")

    display = ' x '.join(display_parts) if display_parts else raw_dim.strip()

    return dict(
        primary_mm=vals[0] if vals else None,
        secondary_mm=vals[1] if len(vals) > 1 else None,
        primary_native=natives[0] if natives else None,
        secondary_native=natives[1] if len(natives) > 1 else None,
        primary_unit=units[0] if units and units[0] else ('mm' if (vals and vals[0] is not None) else None),
        secondary_unit=units[1] if len(units) > 1 and units[1] else ('mm' if len(vals) > 1 and vals[1] is not None else None),
        display=display,
        ambiguous=any(ambs) or all(v is None for v in vals),
    )

def try_infer_size_from_text(text: str):
    t = normalize_text(text or "")
    m = re.search(r'((?:\d+[\-\s])?\d+(?:/\d+)?)\s*(?:inch|")', t)
    if m:
        inches = _parse_mixed_inches(m.group(1))
        if not isnan(inches):
            mm = _snap_mm(inches * INCH_TO_MM)
            native = f"{m.group(1)}\""
            return mm, None, native, None, False
    m = re.search(r"(\d+(?:\.\d+)?)\s*mm", t)
    if m:
        mm_val = _snap_mm(float(m.group(1)))
        native = f"{m.group(1)} mm"
        return mm_val, None, native, None, False
    return None, None, None, None, True

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
            v, _, a, _ = _parse_one_size(p, allow_unitless_numeric=False)
            if not a and v is not None:
                mm.append(v)
    if not mm:
        for tok in re.findall(r'((?:\d+[\-\s])?\d+(?:/\d+)?\s*(?:mm|inch|"))|\b\d+(?:\.\d+)?\b', k):
            v, _, a, _ = _parse_one_size(tok, allow_unitless_numeric=False)
            if not a and v is not None:
                mm.append(v)
            if len(mm) == 2:
                break
    q_p1 = mm[0] if mm else None
    q_p2 = mm[1] if len(mm) > 1 else None
    base = q_p1 if q_p1 else 25.0
    tol = max(1.0, 0.02 * base)

    material = None
    for token, canonical in MATERIAL_KEYWORDS.items():
        if re.search(rf"\b{re.escape(token)}\b", k):
            material = canonical
            break

    variant = None
    for token, canonical in VARIANT_KEYWORDS.items():
        if re.search(rf"\b{re.escape(token)}\b", k):
            variant = canonical
            break
    if variant is None:
        m = re.search(r"sdr\s*([0-9]+(?:\.[0-9]+)?)", k)
        if m:
            variant = f"SDR{m.group(1).replace(' ', '')}"
    if variant is None:
        m = re.search(r"pn\s*([0-9]+(?:\.[0-9]+)?)", k)
        if m:
            variant = f"PN {m.group(1)}"
    if variant is None:
        m = re.search(r"sch\s*([0-9]+)", k)
        if m:
            variant = f"SCH {m.group(1)}"

    q_norm = " ".join(keyword.split()).lower()
    raw_tokens = re.findall(r"[a-z0-9]+", keyword.lower())
    keyword_tokens = [t for t in raw_tokens if not t.isdigit()]

    return {
        "raw": keyword,
        "q_norm": q_norm,
        "q_type": q_type,
        "type_norm": q_type,
        "q_p1": q_p1,
        "q_p2": q_p2,
        "tol": tol,
        "material": material,
        "variant": variant,
        "tokens": keyword_tokens,
    }

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