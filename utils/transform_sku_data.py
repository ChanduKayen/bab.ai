from __future__ import annotations

import json
import math
import re
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

SOURCE_PATH = Path(r"C:\Users\vlaks\Downloads\data_download")
OUTPUT_PATH = Path("outputs/cleaned_sku_master.xlsx")
SORTED_OUTPUT_PATH = Path("outputs/cleaned_sku_master_sorted.xlsx")
ALLOWED_MATERIALS = {"uPVC", "CPVC", "HDPE", "GI", "SS", "Brass", "PVC"}
ALLOWED_PRODUCT_TYPES = {"Pipe", "Fitting", "Valve", "Tap", "Hose"}
UNIT_MAPPINGS = {
    "m": "Meter",
    "meter": "Meter",
    "nos": "Piece",
    "no": "Piece",
    "piece": "Piece",
    "unit": "Piece",
}
DROP_KEYWORDS = {
    "cement",
    "tile",
    "tiles",
    "putty",
    "paint",
    "primer",
    "sand",
    "aggregate",
    "brick",
    "switch",
    "cable",
    "wire",
    "mcb",
    "led",
    "adhesive",
}
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
}
STANDARD_PATTERNS = (
    re.compile(r"\bIS\s*\d+\b", re.I),
    re.compile(r"\bSDR\s*\d+(?:\.\d+)?\b", re.I),
    re.compile(r"\bSCH\s*\d+\b", re.I),
    re.compile(r"\bPN\s*-?\s*\d+(?:\.\d+)?\b", re.I),
    re.compile(r"\bClass\s*[A-Z0-9]+\b", re.I),
    re.compile(r"\bType\s+[AB]\b", re.I),
)
TYPE_TO_PRODUCT = {
    "pipe": "Pipe",
    "double tee": "Fitting",
    "tee": "Fitting",
    "coupler": "Fitting",
    "elbow": "Fitting",
    "elbow 90": "Fitting",
    "elbow 45": "Fitting",
    "bend 87 5": "Fitting",
    "bend 45": "Fitting",
    "reducer": "Fitting",
    "reducer bush": "Fitting",
    "union": "Fitting",
    "end cap": "Fitting",
    "mt adapter": "Fitting",
    "ft adapter": "Fitting",
    "ball valve": "Valve",
    "stop valve": "Valve",
    "angle valve": "Valve",
    "flush valve": "Valve",
    "bib tap": "Tap",
    "flex hose": "Hose",
    "hose": "Hose",
    "floor drain": "Fitting",
    "nahani trap": "Fitting",
    "p trap": "Fitting",
    "bottle trap": "Fitting",
}
TYPE_NORMALIZATION = {
    "double tee": "Double Tee",
    "tee": "Tee",
    "coupler": "Coupler",
    "elbow": "Elbow",
    "elbow 90": "Elbow 90 Deg",
    "elbow 45": "Elbow 45 Deg",
    "bend 87 5": "Bend 87.5 Deg",
    "bend 45": "Bend 45 Deg",
    "reducer": "Reducer",
    "reducer bush": "Reducer Bush",
    "union": "Union",
    "end cap": "End Cap",
    "mt adapter": "MT Adapter",
    "ft adapter": "FT Adapter",
    "ball valve": "Ball Valve",
    "stop valve": "Stop Valve",
    "angle valve": "Angle Valve",
    "flush valve": "Flush Valve",
    "bib tap": "Bib Tap",
    "flex hose": "Hose",
    "hose": "Hose",
    "floor drain": "Floor Drain",
    "nahani trap": "Nahani Trap",
    "p trap": "P Trap",
    "bottle trap": "Bottle Trap",
}


@dataclass
class DimensionResult:
    info: Dict[str, str]
    display_size: Optional[str]
    numeric_sizes: List[int]
    ambiguous: bool


def load_source_dataframe(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Source file not found at {path}")
    return pd.read_csv(path)


def clean_brand(raw: Optional[str]) -> str:
    if raw is None:
        return "Generic"
    value = str(raw).strip()
    if not value or value.lower() in {"-", "na", "n/a", "none", "null"}:
        return "Generic"
    return value


def normalize_uom(raw: Optional[str]) -> str:
    if raw is None:
        return ""
    value = str(raw).strip()
    if not value:
        return ""
    mapped = UNIT_MAPPINGS.get(value.lower())
    return mapped or ""


def safe_int(value: Optional[object]) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def parse_attributes(raw: object) -> Dict[str, object]:
    if not raw or not isinstance(raw, str):
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _material_from_token(token: str) -> Optional[str]:
    lower = token.lower().strip()
    if not lower:
        return None
    if "cpvc" in lower:
        return "CPVC"
    if "hdpe" in lower:
        return "HDPE"
    if "pvc-u" in lower or "upvc" in lower:
        return "uPVC"
    if lower.startswith("gi") or " gi" in lower:
        return "GI"
    if "brass" in lower:
        return "Brass"
    if "stainless" in lower or "ss" in lower:
        return "SS"
    if "pvc" in lower:
        return "PVC"
    return None


def normalize_material(raw_material: Optional[str], category: str, description: str) -> Tuple[Optional[str], bool]:
    ambiguous = False
    tokens: List[str] = []
    if raw_material:
        tokens.extend(re.split(r"[\/,]", str(raw_material)))
    if category:
        tokens.append(category)
    if description:
        tokens.append(description)
    material = None
    for token in tokens:
        guess = _material_from_token(token)
        if guess:
            material = guess
            break
    if material not in ALLOWED_MATERIALS:
        ambiguous = True
        material = None
    return material, ambiguous


def normalize_type(raw_type: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if not raw_type:
        return None, None
    base = raw_type.strip().lower()
    if not base or base in {"pipe", "type a", "type b"}:
        return None, None
    title = TYPE_NORMALIZATION.get(base, base.title())
    slug = re.sub(r"[^a-z0-9]+", "-", base)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return title, slug or None


def detect_product_type(row_category: str, raw_type: Optional[str]) -> Tuple[Optional[str], bool]:
    ambiguous = False
    product = None
    type_key = (raw_type or "").strip().lower()
    if type_key in TYPE_TO_PRODUCT:
        product = TYPE_TO_PRODUCT[type_key]
    elif row_category:
        lowered = row_category.lower()
        if "pipe" in lowered:
            product = "Pipe"
        elif "fitting" in lowered:
            product = "Fitting"
        elif "valve" in lowered:
            product = "Valve"
        elif "tap" in lowered:
            product = "Tap"
        elif "hose" in lowered:
            product = "Hose"
    if product not in ALLOWED_PRODUCT_TYPES:
        ambiguous = product is not None
        product = None
    return product, ambiguous


def extract_standards(*texts: str) -> List[str]:
    standards: List[str] = []
    for text in texts:
        if not text:
            continue
        for pattern in STANDARD_PATTERNS:
            for match in pattern.findall(text):
                cleaned = re.sub(r"\s+", " ", match.strip()).upper()
                if cleaned not in standards:
                    standards.append(cleaned)
    return standards


def normalize_fraction(text: str) -> str:
    text = text.replace(chr(0x2013), '-')
    text = text.replace(chr(0x2014), '-')
    text = re.sub(r'(\d)\s+(\d/\d)', r'\1-\2', text)
    text = text.replace(chr(0x2044), '/')
    return text



def normalize_dimension_text(text: str) -> str:
    text = normalize_fraction(text)
    replacements = {
        "\u00D7": "x",
        "\u2715": "x",
        "\u2716": "x",
        "\uFFFD": "x",
        "*": "x",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    text = re.sub(r"(?i)(\d)(mm)", r"\1 mm", text)
    text = re.sub(r"(?i)(\d)(cm)", r"\1 cm", text)
    text = re.sub(r"(?i)(\d)(m)\b", r"\1 m", text)
    text = re.sub(r"\s*x\s*", " x ", text)
    text = re.sub(r"\s+", " ", text)
    text = text.strip()
    text = re.sub(r"^(\d+)\s+(\d+)\s*(mm)$", r"\1 x \2 \3", text)
    return text


def parse_numeric(value: str) -> float:
    if "-" in value and "/" in value:
        whole, frac = value.split("-", 1)
        num, den = frac.split("/")
        return float(whole) + float(num) / float(den)
    if "/" in value:
        num, den = value.split("/")
        return float(num) / float(den)
    return float(value)


def inch_to_mm(value: str) -> Optional[int]:
    value = value.strip()
    if not value:
        return None
    nominal = INCH_NOMINAL_MM.get(value)
    if nominal is not None:
        return nominal
    try:
        computed = parse_numeric(value) * 25.4
    except ValueError:
        return None
    nearest = min(INCH_NOMINAL_MM.values(), key=lambda mm: abs(mm - computed))
    if abs(nearest - computed) <= 1:
        return nearest
    return int(round(computed))


def invert_inch_lookup(mm_value: int) -> Optional[str]:
    for inch, mm in INCH_NOMINAL_MM.items():
        if abs(mm - mm_value) <= 1:
            return inch + '"'
    return None


def _token_from_part(part: str) -> Tuple[str, str]:
    match = re.match(r'(?i)(\d+(?:-\d+/\d+)?(?:/\d+)?)\s*(mm|cm|m|")?', part)
    if not match:
        return part.strip(), ""
    value = match.group(1).replace('"', '')
    unit = (match.group(2) or "").lower()
    if unit == '"':
        unit = 'inch'
    return value.strip(), unit


def parse_dimension(material: Optional[str], product_type: Optional[str], type_title: Optional[str], raw_dimension: Optional[str]) -> DimensionResult:
    info: Dict[str, str] = {}
    numeric_sizes: List[int] = []
    display_size: Optional[str] = None
    ambiguous = False
    if not raw_dimension:
        return DimensionResult(info, display_size, numeric_sizes, True)
    raw_dimension = raw_dimension.strip()
    if not raw_dimension:
        return DimensionResult(info, display_size, numeric_sizes, True)
    info["raw_dimension"] = raw_dimension
    normalized = normalize_dimension_text(raw_dimension)
    if not normalized:
        return DimensionResult(info, display_size, numeric_sizes, True)
    preference = None
    if material in {"uPVC", "HDPE", "PVC"}:
        preference = "mm"
    elif material in {"CPVC", "GI"}:
        preference = "inch"
    is_hose = product_type == "Hose" or (type_title and type_title.lower() == "hose")
    if is_hose:
        size_match = re.search(r"(\d+(?:-\d+/\d+)?(?:/\d+)?)\s*\"", normalized)
        length_match = re.search(r"(\d+(?:\.\d+)?)\s*mm", normalized)
        if size_match:
            size_value = size_match.group(1)
            mm_value = inch_to_mm(size_value)
            if mm_value:
                numeric_sizes.append(mm_value)
            info["dimension"] = size_value + '"'
            if length_match:
                length_mm = int(round(float(length_match.group(1))))
                info["length"] = f"{length_mm} mm"
                info["length_mm"] = str(length_mm)
                display_size = f"{size_value}\" {length_mm} mm"
            else:
                ambiguous = True
                display_size = size_value + '"'
        else:
            ambiguous = True
        return DimensionResult(info, display_size, numeric_sizes, ambiguous)
    parts = [p.strip() for p in normalized.split(" x ") if p.strip()]
    tokens: List[Dict[str, Optional[str]]] = []
    for part in parts:
        value, unit = _token_from_part(part)
        token: Dict[str, Optional[str]] = {
            "value": value,
            "unit": unit,
            "mm": None,
            "inch": None,
            "native": None,
            "other": None,
        }
        if unit == "mm":
            try:
                token["mm"] = int(round(parse_numeric(value)))
            except ValueError:
                ambiguous = True
        elif unit == "cm":
            try:
                token["mm"] = int(round(parse_numeric(value) * 10))
            except ValueError:
                ambiguous = True
        elif unit == "m":
            try:
                token["mm"] = int(round(parse_numeric(value) * 1000))
            except ValueError:
                ambiguous = True
        elif unit == "inch":
            mm_val = inch_to_mm(value)
            if mm_val:
                token["mm"] = mm_val
            token["inch"] = value + '"'
        else:
            ambiguous = True
        if token["mm"] is not None and token["inch"] is None:
            token["inch"] = invert_inch_lookup(token["mm"])
        tokens.append(token)
    if not tokens:
        return DimensionResult(info, display_size, numeric_sizes, True)
    valid_tokens = [t for t in tokens if t.get("mm") is not None or t.get("inch") is not None]
    if valid_tokens:
        if len(valid_tokens) != len(tokens):
            ambiguous = True
        tokens = valid_tokens
    else:
        return DimensionResult(info, display_size, numeric_sizes, True)
    if len(tokens) > 2:
        ambiguous = True
    for token in tokens:
        pref = preference
        mm_value = token["mm"]
        inch_value = token["inch"]
        if pref == "mm":
            if mm_value is not None:
                token["native"] = f"{mm_value} mm"
                token["other"] = inch_value
            elif inch_value:
                token["native"] = inch_value
                ambiguous = True
            else:
                token["native"] = token["value"]
                ambiguous = True
        elif pref == "inch":
            if inch_value:
                token["native"] = inch_value
                token["other"] = f"{mm_value} mm" if mm_value is not None else None
            elif mm_value is not None:
                inch_lookup = invert_inch_lookup(mm_value)
                if inch_lookup:
                    token["native"] = inch_lookup
                    token["other"] = f"{mm_value} mm"
                else:
                    token["native"] = f"{mm_value} mm"
                    ambiguous = True
            else:
                token["native"] = token["value"]
                ambiguous = True
        else:
            if inch_value:
                token["native"] = inch_value
                token["other"] = f"{mm_value} mm" if mm_value is not None else None
            elif mm_value is not None:
                token["native"] = f"{mm_value} mm"
            else:
                token["native"] = token["value"]
                ambiguous = True
    ordered_tokens = tokens
    if len(tokens) >= 2 and all(t.get("mm") is not None for t in tokens[:2]):
        ordered_tokens = sorted(tokens, key=lambda t: t.get("mm") or 0, reverse=True)
    if ordered_tokens:
        primary = ordered_tokens[0]
        if primary.get("mm"):
            numeric_sizes.append(primary["mm"])
        if len(ordered_tokens) >= 2:
            secondary = ordered_tokens[1]
            if secondary.get("mm"):
                numeric_sizes.append(secondary["mm"])
    if len(ordered_tokens) == 1:
        info["dimension"] = primary["native"] or ""
        display_size = primary["native"] or ""
        if primary.get("other"):
            display_size = f"{display_size} ({primary['other']})"
    else:
        primary = ordered_tokens[0]
        secondary = ordered_tokens[1]
        primary_text = primary["native"] or ""
        secondary_text = secondary["native"] or ""
        info["dimension"] = f"{primary_text} A- {secondary_text}".strip()
        info["dimension_secondary"] = secondary_text
        display_size = info["dimension"]
        primary_other = primary.get("other")
        secondary_other = secondary.get("other")
        if primary_other and secondary_other:
            display_size = f"{display_size} ({primary_other} A- {secondary_other})"
    info = {k: v for k, v in info.items() if v}
    if not display_size:
        display_size = info.get("dimension")
    numeric_sizes = [s for s in numeric_sizes if s]
    if not numeric_sizes:
        ambiguous = True
    return DimensionResult(info, display_size, numeric_sizes, ambiguous)


def build_description(material: Optional[str], product_type: Optional[str], type_title: Optional[str], size_text: Optional[str], variant: Optional[str], standards: List[str]) -> str:
    parts: List[str] = []
    if material:
        parts.append(material)
    if product_type:
        parts.append(product_type)
    if type_title and type_title != product_type:
        parts.append(type_title)
    if size_text:
        parts.append(size_text)
    description = " ".join(parts)
    if variant:
        description = f"{description} {chr(0x2013)} {variant}"
    if standards:
        description = f"{description} [{', '.join(standards)}]"
    return " ".join(description.split())


def determine_variant(category: str, description: str, attr_variant: Optional[str]) -> Tuple[Optional[str], List[str]]:
    variant = None
    standards = extract_standards(description, attr_variant or "")
    combined = " ".join(filter(None, [category, description, attr_variant]))
    lowered = combined.lower()
    if "swr" in lowered:
        variant = "SWR"
    elif "pressure" in lowered:
        variant = "Pressure"
    if attr_variant:
        clean = attr_variant.strip().upper()
        if clean in {"SWR", "PRESSURE"}:
            variant = clean.title()
    return variant, standards


def should_drop(material: Optional[str], product_type: Optional[str], description: str) -> bool:
    if material in ALLOWED_MATERIALS or product_type in ALLOWED_PRODUCT_TYPES:
        return False
    lowered = description.lower()
    return any(token in lowered for token in DROP_KEYWORDS)


def compute_size_mm_fields(numeric_sizes: List[int]) -> Tuple[Optional[int], Optional[int]]:
    if not numeric_sizes:
        return None, None
    primary = numeric_sizes[0]
    secondary = numeric_sizes[1] if len(numeric_sizes) > 1 else None
    return primary, secondary


def build_attributes(material: Optional[str], type_title: Optional[str], variant: Optional[str], dimension_info: Dict[str, str]) -> str:
    ordered = OrderedDict()
    if material:
        ordered["material"] = material
    if type_title:
        ordered["type"] = type_title
    if variant:
        ordered["variant"] = variant
    for key in ("dimension", "dimension_secondary", "raw_dimension", "length", "length_mm"):
        value = dimension_info.get(key)
        if value:
            ordered[key] = value
    return json.dumps(ordered, ensure_ascii=False)


def build_canonical_key(type_slug: Optional[str], material: Optional[str], product_type: Optional[str], size_mm_primary: Optional[int], size_mm_secondary: Optional[int], variant: Optional[str]) -> str:
    parts: List[str] = []
    if type_slug:
        parts.append(type_slug)
    if material:
        parts.append(material.lower())
    if product_type:
        parts.append(product_type.lower())
    if size_mm_primary:
        size_block = str(size_mm_primary)
        if size_mm_secondary:
            size_block = f"{size_mm_primary}x{size_mm_secondary}"
        parts.append(size_block)
    if variant:
        parts.append(variant.lower())
    return "|".join(parts)


@dataclass
class ProcessResult:
    row: Dict[str, object]
    dropped: bool
    ambiguous: bool


def process_row(row: pd.Series) -> ProcessResult:
    description_raw = str(row.get("description") or "").strip()
    attributes = parse_attributes(row.get("attributes"))
    raw_type = attributes.get("type") if isinstance(attributes, dict) else None
    brand = clean_brand(row.get("brand"))
    material, material_ambiguous = normalize_material(
        attributes.get("material") if isinstance(attributes, dict) else None,
        str(row.get("category") or ""),
        description_raw,
    )
    type_title, type_slug = normalize_type(raw_type)
    product_type, product_ambiguous = detect_product_type(str(row.get("category") or ""), raw_type)
    variant, standards = determine_variant(str(row.get("category") or ""), description_raw, attributes.get("variant") if isinstance(attributes, dict) else None)
    dimension_result = parse_dimension(material, product_type, type_title, attributes.get("dimension") if isinstance(attributes, dict) else None)
    size_mm_primary, size_mm_secondary = compute_size_mm_fields(dimension_result.numeric_sizes)
    size_text = dimension_result.display_size
    description = build_description(material, product_type, type_title, size_text, variant, standards)
    should_drop_row = should_drop(material, product_type, description_raw)
    attributes_json = build_attributes(material, type_title, variant, dimension_result.info)
    uom_code = normalize_uom(row.get("uom_code"))
    pack_uom = normalize_uom(row.get("pack_uom"))
    if uom_code and not pack_uom:
        pack_uom = uom_code
    if pack_uom and not uom_code:
        uom_code = pack_uom
    if uom_code != pack_uom:
        pack_uom = uom_code
    pack_qty = safe_int(row.get("pack_qty"))
    ambiguous_flags = [material_ambiguous, product_ambiguous, dimension_result.ambiguous]
    if not material or not product_type or not size_mm_primary:
        ambiguous_flags.append(True)
    ambiguous = any(ambiguous_flags)
    canonical = ""
    if not ambiguous:
        canonical = build_canonical_key(type_slug, material, product_type, size_mm_primary, size_mm_secondary, variant)
    output_row = {
        "sku_id": "",
        "brand": brand,
        "category": f"{material} {product_type}".strip() if material and product_type else str(row.get("category") or ""),
        "uom_code": uom_code,
        "pack_qty": pack_qty if pack_qty is not None else "",
        "pack_uom": pack_uom,
        "description": description,
        "attributes": attributes_json,
        "canonical_key": canonical,
        "status": "active",
        "created_at": "",
        "updated_at": "",
        "type_norm": type_slug or "",
        "size_mm_primary": str(size_mm_primary) if size_mm_primary else "",
        "size_mm_secondary": str(size_mm_secondary) if size_mm_secondary else "",
        "ambiguous": bool(ambiguous),
    }
    return ProcessResult(output_row, should_drop_row, ambiguous)


def transform() -> None:
    df = load_source_dataframe(SOURCE_PATH)
    processed: List[Dict[str, object]] = []
    dropped = 0
    ambiguous = 0
    for _, row in df.iterrows():
        result = process_row(row)
        if result.dropped:
            dropped += 1
            continue
        if result.ambiguous:
            ambiguous += 1
        processed.append(result.row)
    if not processed:
        raise RuntimeError("No rows processed; check input data")
    output_df = pd.DataFrame(processed)
    output_df = output_df.drop_duplicates()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        output_df.to_excel(OUTPUT_PATH, index=False)
        wrote_unsorted = True
    except PermissionError:
        print(f"Warning: unable to write {OUTPUT_PATH} (permission denied).")
        wrote_unsorted = False
    category_counts = output_df["category"].value_counts(dropna=False)
    sorted_df = output_df.copy()
    sorted_df["__category_freq"] = sorted_df["category"].map(category_counts).fillna(0)
    sorted_df["__size_mm_primary_sort"] = pd.to_numeric(sorted_df["size_mm_primary"], errors="coerce")
    sorted_df["__size_mm_secondary_sort"] = pd.to_numeric(sorted_df["size_mm_secondary"], errors="coerce")
    sorted_df = sorted_df.sort_values(
        by=["__category_freq", "category", "__size_mm_primary_sort", "__size_mm_secondary_sort", "description"],
        ascending=[False, True, True, True, True],
        na_position="last",
    ).drop(columns=["__category_freq", "__size_mm_primary_sort", "__size_mm_secondary_sort"])
    sorted_df.to_excel(SORTED_OUTPUT_PATH, index=False)

    print(f"Rows processed: {len(processed)}")
    print(f"Rows dropped: {dropped}")
    print(f"Ambiguous rows: {ambiguous}")
    if wrote_unsorted:
        print(f"Output written to {OUTPUT_PATH}")
    else:
        print(f"Skipped updating {OUTPUT_PATH} (file locked).")
    print(f"Sorted output written to {SORTED_OUTPUT_PATH}")


if __name__ == "__main__":
    transform()
