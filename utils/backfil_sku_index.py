# utils/sku_setup_and_loader_async.py
# Run:  python utils/sku_setup_and_loader_async.py
#
# What this single file does (async, hardcoded DB URL):
#   1) Ensures schema objects exist (extensions, derived columns + indexes, materialized views)
#   2) Optionally upserts SKU rows from a CSV/XLSX file (auto-detect), computing:
#        - type_norm
#        - size_mm_primary / size_mm_secondary (mm)
#        - attributes.volume_ml  (base unit = ml, supports L, ltr, etc.)
#   3) Backfills derived cols (and adds volume_ml) for any existing rows missing them
#   4) Refreshes the materialized views (CONCURRENTLY by default)
#
# Deps:
#   pip install "sqlalchemy[asyncio]" asyncpg
#   (and if using Excel) pip install pandas openpyxl

from __future__ import annotations

import asyncio
import csv
import json
import math
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

from sqlalchemy import MetaData, Table, func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine.url import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

# ======================================================================================
#                                  CONFIG (EDIT)
# ======================================================================================

# Async Postgres URL (asyncpg)
import os
from dotenv import load_dotenv
load_dotenv()
DB_URL = os.getenv("DATABASE_URL")

# Optional input file for upserting the master list (CSV or XLSX)
INPUT_FILE = r"C:\path\to\sku_master_data_with_additions.xlsx"  # or .csv
EXCEL_SHEET_NAME = None  # set to sheet name if needed, or None to let pandas pick first

SCHEMA = "public"
TABLE_NAME = "sku_master"
BATCH_SIZE = 1000

# Steps toggles
DO_ENSURE_SCHEMA = True
DO_UPSERT_FROM_FILE = True          # set False if you only want to backfill & refresh
DO_BACKFILL = True                  # backfill type_norm/size_mm_* (and add volume_ml) where missing
DO_REFRESH_MV = True
REFRESH_CONCURRENTLY = True

# Price weighting params for MVs
HALF_LIFE_DAYS = 3.0
WINDOW_DAYS = 7

# ======================================================================================
#                         NORMALIZATION HELPERS (TYPE / SIZE / VOLUME / PRICE)
# ======================================================================================

INCH_TO_MM = 25.4

TYPE_ALIASES = {
    "pipe": "pipe", "pipes": "pipe",
    "elbow": "elbow", "elbow 90": "elbow", "elbow 45": "elbow",
    "tee": "tee", "tees": "tee",
    "reducer": "reducer", "union": "union",
    "adapter": "adapter", "adaptor": "adapter",
    "coupling": "coupling", "couplings": "coupling",
    "nipple": "nipple", "cap": "cap", "plug": "plug",
    "bushing": "bushing", "valve": "valve",
}

def _norm_text(s: Optional[str]) -> str:
    s = (s or "").strip().lower()
    # unify fancy quotes → "
    s = s.replace("″", '"').replace("”", '"')
    return " ".join(s.split())

def normalize_type(raw_type: str, raw_sub: Optional[str] = None) -> str:
    raw = _norm_text((raw_type or "") + " " + (raw_sub or ""))
    if raw in TYPE_ALIASES:
        return TYPE_ALIASES[raw]
    for key in sorted(TYPE_ALIASES.keys(), key=len, reverse=True):
        if f" {key} " in f" {raw} ":
            return TYPE_ALIASES[key]
    return raw

def _frac_to_float(s: str) -> float:
    num, den = s.split("/")
    return float(num) / float(den)

def _parse_mixed_inches(text_val: str) -> float:
    import re
    t = text_val.strip()
    m = re.fullmatch(r"(?:(\d+)[\-\s])?(\d+/\d+)", t)
    if m:
        whole = float(m.group(1)) if m.group(1) else 0.0
        frac = _frac_to_float(m.group(2))
        return whole + frac
    m2 = re.fullmatch(r"\d+(?:\.\d+)?", t)
    if m2:
        return float(m2.group(0))
    return float("nan")

def _parse_one_size(token: str) -> Tuple[Optional[float], Optional[str], bool]:
    import re
    s = _norm_text(token).replace("inches", "inch")
    # mm
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*mm", s)
    if m:
        return float(m.group(1)), "mm", False
    # inches / fractions
    m = re.fullmatch(r'((?:\d+[\-\s])?\d+(?:/\d+)?)\s*(?:inch|")', s)
    if m:
        inches = _parse_mixed_inches(m.group(1))
        if not math.isnan(inches):
            return inches * INCH_TO_MM, "inch", False
    # bare number (ambiguous)
    m = re.fullmatch(r"\d+(?:\.\d+)?", s)
    if m:
        return None, None, True
    return None, None, True

def normalize_dimension(raw_dim: Optional[str]):
    """Return mm for first two dims if present: {'primary_mm','secondary_mm','ambiguous'}"""
    if not raw_dim:
        return dict(primary_mm=None, secondary_mm=None, ambiguous=True)
    s = _norm_text(raw_dim).replace("×", "x")
    parts = [p.strip() for p in s.split("x")]
    vals, ambs = [], []
    for p in parts[:2]:
        v, _, a = _parse_one_size(p)
        vals.append(v); ambs.append(a)
    return dict(
        primary_mm=vals[0] if vals else None,
        secondary_mm=vals[1] if len(vals) > 1 else None,
        ambiguous=any(ambs) or all(v is None for v in vals),
    )

# ------------------- Volume normalization (base unit = ml) -------------------

_VOLUME_TO_ML = {
    "ml": 1.0, "milliliter": 1.0, "millilitre": 1.0, "milliliters": 1.0, "millilitres": 1.0,
    "l": 1000.0, "lt": 1000.0, "ltr": 1000.0, "liter": 1000.0, "litre": 1000.0,
    "liters": 1000.0, "litres": 1000.0,
}
def _normalize_unit_token(u: Optional[str]) -> Optional[str]:
    if not u: return None
    u = u.strip().lower()
    if u.endswith("s") and u[:-1] in _VOLUME_TO_ML:  # liters -> liter
        return u[:-1]
    return u

def volume_to_ml(value: float, unit: str) -> float:
    """Convert numeric (value, unit) → ml."""
    u = _normalize_unit_token(unit)
    if u not in _VOLUME_TO_ML:
        raise ValueError(f"Unknown volume unit: {unit}")
    return float(value) * _VOLUME_TO_ML[u]

_VOL_RE = __import__("re").compile(
    r"""^\s*(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>[a-zA-Z]+)\s*$""",
    __import__("re").VERBOSE | __import__("re").IGNORECASE,
)

def parse_volume_str_to_ml(s: str) -> Optional[float]:
    """Parse '250ml', '250 ml', '0.5L', '0.5 ltr' → ml; else None."""
    if not s: return None
    m = _VOL_RE.match(s)
    if not m:
        return None
    num = float(m.group("num"))
    unit = _normalize_unit_token(m.group("unit"))
    if unit not in _VOLUME_TO_ML:
        return None
    return num * _VOLUME_TO_ML[unit]

def normalize_volume_in_attributes(attrs: Dict[str, Any]) -> Dict[str, Any]:
    """
    If attributes contain any capacity/volume, add attributes['volume_ml'] = float.
    Keys probed: volume, capacity, size, pack_size, qty, quantity, plus *_unit variants.
    """
    if not isinstance(attrs, dict):
        return attrs
    cand = ["volume", "capacity", "size", "pack_size", "qty", "quantity"]
    out = dict(attrs)
    vol_ml: Optional[float] = None

    # 1) explicit value + unit in sibling key
    for base in cand:
        unit_key = f"{base}_unit"
        if base in attrs and unit_key in attrs:
            try:
                v = float(attrs[base])
                u = str(attrs[unit_key])
                vol_ml = volume_to_ml(v, u)
                break
            except Exception:
                pass

    # 2) compact string forms
    if vol_ml is None:
        for k in cand:
            v = attrs.get(k)
            if isinstance(v, str):
                parsed = parse_volume_str_to_ml(v)
                if parsed is not None:
                    vol_ml = parsed
                    break

    # 3) numeric + generic 'unit'/'uom' in attrs
    if vol_ml is None:
        unit = attrs.get("unit") or attrs.get("uom") or attrs.get("pack_uom")
        for k in cand:
            v = attrs.get(k)
            if isinstance(v, (int, float)) and unit:
                try:
                    vol_ml = volume_to_ml(float(v), str(unit))
                    break
                except Exception:
                    pass

    if vol_ml is not None:
        out["volume_ml"] = float(vol_ml)
    return out

# ------------------- Price normalization helper (volume-aware) -------------------

def normalize_price_to_base_units(
    quoted_price: Union[int, float],
    price_unit: str,
    base_uom: str,
    pack_uom: Optional[str] = None,
    pack_qty: Optional[Union[int, float]] = None,
) -> float:
    """
    Normalize a vendor quoted price from `price_unit` to `base_uom` (string units).
    - Supports L↔ml conversions (base for solvents often 'ml')
    - Supports pack conversions (price per pack → per pack content unit)
    This is a standalone helper; in CRUD you'd pass sku.uom_code, sku.pack_uom, sku.pack_qty.
    """
    q = float(quoted_price)
    base = (base_uom or "").strip().lower()
    u = (price_unit or "").strip().lower()
    p_uom = (pack_uom or "").strip().lower() if pack_uom else ""
    p_qty = float(pack_qty or 1)

    if not base:
        return q
    if u == base:
        return q

    # volume conversion (L <-> ml)
    try:
        base_ml = _VOLUME_TO_ML[_normalize_unit_token(base)]
        unit_ml = _VOLUME_TO_ML[_normalize_unit_token(u)]
        return q / (unit_ml / base_ml)
    except Exception:
        pass  # not both volume units

    # pack conversion
    if p_uom and u == p_uom and p_qty:
        # price per content unit
        per_pack_content = q / p_qty
        # if pack content unit itself is volume and base is volume, convert the scale
        try:
            base_ml = _VOLUME_TO_ML[_normalize_unit_token(base)]
            pack_ml = _VOLUME_TO_ML[_normalize_unit_token(p_uom)]
            return per_pack_content / (pack_ml / base_ml)
        except Exception:
            return per_pack_content

    return q

# ======================================================================================
#                              SCHEMA / MV CREATION (ASYNC)
# ======================================================================================

def _short(sql: str) -> str:
    s = " ".join(sql.split())
    return (s[:100] + "…") if len(s) > 100 else s

def _ddl_statements() -> List[str]:
    return [
        # Extensions (ignore if lacking privileges — script will warn and continue)
        "CREATE EXTENSION IF NOT EXISTS pg_trgm;",
        "CREATE EXTENSION IF NOT EXISTS btree_gin;",
        # Derived columns on sku_master
        "ALTER TABLE public.sku_master ADD COLUMN IF NOT EXISTS type_norm TEXT;",
        "ALTER TABLE public.sku_master ADD COLUMN IF NOT EXISTS size_mm_primary NUMERIC;",
        "ALTER TABLE public.sku_master ADD COLUMN IF NOT EXISTS size_mm_secondary NUMERIC;",
        # New: mark ambiguous placeholders
        "ALTER TABLE public.sku_master ADD COLUMN IF NOT EXISTS ambiguous BOOLEAN NOT NULL DEFAULT false;",
        # Helpful indexes
        "CREATE INDEX IF NOT EXISTS idx_sku_size_mm ON public.sku_master (size_mm_primary, size_mm_secondary);",
        "CREATE INDEX IF NOT EXISTS idx_sku_type_trgm ON public.sku_master USING gin (type_norm gin_trgm_ops);",
        # Alias table for regional/alternate names
        """
        CREATE TABLE IF NOT EXISTS public.sku_alias (
            id            BIGSERIAL PRIMARY KEY,
            master_sku_id TEXT NOT NULL REFERENCES public.sku_master(sku_id) ON DELETE CASCADE,
            alias_text    TEXT NOT NULL,
            region        TEXT NULL,
            vendor_id     UUID NULL REFERENCES public.vendors(vendor_id) ON DELETE SET NULL,
            confidence    NUMERIC NULL,
            created_at    TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now(),
            updated_at    TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now()
        );
        """,
        "CREATE INDEX IF NOT EXISTS idx_sku_alias_master ON public.sku_alias(master_sku_id);",
        "CREATE INDEX IF NOT EXISTS idx_sku_alias_text ON public.sku_alias(alias_text);",
        # Vendor-specific 7d exp-weighted MV
        f"""
        CREATE MATERIALIZED VIEW IF NOT EXISTS public.sku_vendor_price_7d_mv AS
        WITH w AS (
          SELECT
            sku_id,
            vendor_id,
            price,
            quoted_at,
            resolved,
            exp( ln(0.5) * (EXTRACT(EPOCH FROM (now() - quoted_at)) / 86400.0) / {HALF_LIFE_DAYS} ) AS w_time
          FROM public.sku_vendor_price
          WHERE quoted_at >= now() - interval '{WINDOW_DAYS} days'
        )
        SELECT
          sku_id,
          vendor_id,
          SUM(price * w_time) / NULLIF(SUM(w_time), 0) AS self_exp_avg_7d,
          COUNT(*) FILTER (WHERE resolved)              AS n_self_resolved_7d,
          COUNT(*)                                      AS n_self_all_7d,
          MAX(quoted_at)                                AS last_quote_at_self
        FROM w
        GROUP BY sku_id, vendor_id
        WITH NO DATA;
        """,
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_price7d_vendor ON public.sku_vendor_price_7d_mv (sku_id, vendor_id);",
        # Market-wide 7d exp-weighted MV
        f"""
        CREATE MATERIALIZED VIEW IF NOT EXISTS public.sku_market_price_7d_mv AS
        WITH w AS (
          SELECT
            sku_id,
            price,
            quoted_at,
            resolved,
            exp( ln(0.5) * (EXTRACT(EPOCH FROM (now() - quoted_at)) / 86400.0) / {HALF_LIFE_DAYS} ) AS w_time
          FROM public.sku_vendor_price
          WHERE quoted_at >= now() - interval '{WINDOW_DAYS} days'
        )
        SELECT
          sku_id,
          SUM(price * w_time) / NULLIF(SUM(w_time), 0) AS market_exp_avg_7d,
          PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY price) AS market_median_7d,
          COUNT(*) FILTER (WHERE resolved)              AS n_market_resolved_7d,
          COUNT(*)                                      AS n_market_all_7d,
          MAX(quoted_at)                                AS last_quote_at_market
        FROM w
        GROUP BY sku_id
        WITH NO DATA;
        """,
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_price7d_market ON public.sku_market_price_7d_mv (sku_id);",
    ]

async def ensure_schema(engine: AsyncEngine):
    stmts = _ddl_statements()
    async with engine.begin() as conn:
        for s in stmts:
            try:
                await conn.exec_driver_sql(s)
            except Exception as e:
                print(f"[WARN] DDL: {_short(s)} -> {e}")

async def refresh_mvs(engine: AsyncEngine, concurrently: bool = True):
    async with engine.connect() as conn:
        ac = conn.execution_options(isolation_level="AUTOCOMMIT")
        if asyncio.iscoroutine(ac):  # some SA versions make this awaitable
            ac = await ac
        if concurrently:
            try:
                await ac.exec_driver_sql("REFRESH MATERIALIZED VIEW CONCURRENTLY public.sku_vendor_price_7d_mv;")
                await ac.exec_driver_sql("REFRESH MATERIALIZED VIEW CONCURRENTLY public.sku_market_price_7d_mv;")
                return
            except Exception as e:
                print(f"[WARN] Concurrent refresh failed ({e}); falling back to non-concurrent.")
        # fallback (non-concurrent)
        await conn.exec_driver_sql("REFRESH MATERIALIZED VIEW public.sku_vendor_price_7d_mv;")
        await conn.exec_driver_sql("REFRESH MATERIALIZED VIEW public.sku_market_price_7d_mv;")

# ======================================================================================
#                                   LOADER (CSV/XLSX)
# ======================================================================================

REQUIRED_COLUMNS = {"brand", "category", "uom_code"}  # 'sku_id' optional; will be derived if absent

def _parse_number(value: Any, default: Union[int, float, None] = 1) -> Union[int, float, None]:
    if value is None:
        return default
    s = str(value).strip()
    if not s:
        return default
    try:
        i = int(float(s))
        if str(i) == s or s.endswith(".0"):
            return i
    except Exception:
        pass
    try:
        return float(s)
    except Exception:
        return default

def _normalize_status(s: Optional[str]) -> str:
    if not s: return "active"
    s2 = str(s).strip().lower()
    return s2 if s2 in {"active", "retired"} else "active"

def _normalize_attributes(raw: Any) -> Dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        try:
            raw = json.loads(str(raw))
        except Exception:
            return {}
    out: Dict[str, Any] = {}
    for k, v in raw.items():
        nk = _norm_text(k)
        out[nk] = _norm_text(v) if isinstance(v, str) else v
    return out

def _canonical_fingerprint(brand: str, category: str, uom_code: str, pack_uom: str, attributes: Dict[str, Any]) -> str:
    payload = {
        "brand": _norm_text(brand),
        "category": _norm_text(category),
        "uom_code": _norm_text(uom_code),
        "pack_uom": _norm_text(pack_uom or "kg"),
        "attributes": attributes,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))

def _deterministic_sku_id(fingerprint: str) -> str:
    import uuid
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "sku:" + fingerprint))

def _canonical_key_for_humans(attributes: Dict[str, Any]) -> Optional[str]:
    preferred = [
        "material","standard","spec","grade","class","sdr",
        "nominal_size_in","size_in","size","nominal_size_mm","size_mm","dia_mm","diameter_mm",
        "thickness_mm","gauge",
    ]
    vals, seen = [], set()
    for k in preferred:
        if k in attributes and attributes[k]:
            vals.append(str(attributes[k])); seen.add(k)
    if not vals:
        for k in sorted(attributes.keys()):
            if k in seen: continue
            v = attributes[k]
            if isinstance(v, (str,int,float)) and str(v).strip():
                vals.append(str(v))
            if len(vals) >= 6:
                break
    return "|".join(vals) if vals else None

def _read_rows(path: str, sheet_name: Optional[str]) -> List[Dict[str, Any]]:
    ext = os.path.splitext(path)[1].lower()
    rows: List[Dict[str, Any]] = []
    if ext in (".xlsx", ".xls"):
        try:
            import pandas as pd
        except ImportError:
            print("ERROR: pandas & openpyxl required for Excel files. pip install pandas openpyxl")
            sys.exit(1)
        df = pd.read_excel(path, dtype=str, sheet_name=sheet_name)
        df.columns = [c.strip() for c in df.columns]
        for _, r in df.iterrows():
            as_dict = {k: (None if (isinstance(v, float) and math.isnan(v)) else v) for k, v in r.items()}
            rows.append(as_dict)
    else:
        with open(path, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                raise ValueError("CSV has no header row.")
            for r in reader:
                rows.append(r)
    return rows

async def _reflect_table(conn, schema: str, table_name: str):
    md = MetaData(schema=schema)
    # use the underlying sync connection for reflection
    return Table(table_name, md, autoload_with=conn.sync_connection(), schema=schema)

def _row_to_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    brand    = row.get("brand")
    category = row.get("category")
    uom_code = row.get("uom_code")

    pack_uom = (str(row.get("pack_uom", "kg")).strip() or "kg")
    attributes = _normalize_attributes(row.get("attributes") or row.get("attrs") or {})

    # Add normalized volume (ml) if present in attributes
    attributes = normalize_volume_in_attributes(attributes)

    # Derived type + dimension (from attributes)
    type_norm = normalize_type(attributes.get("type") or "", attributes.get("sub_type") or "")
    d = normalize_dimension(attributes.get("dimension"))
    p1 = d.get("primary_mm")
    p2 = d.get("secondary_mm")

    fingerprint = _canonical_fingerprint(brand, category, uom_code, pack_uom, attributes)
    sku_id = (str(row.get("sku_id")).strip() if row.get("sku_id") else _deterministic_sku_id(fingerprint))
    human_key = _canonical_key_for_humans(attributes)

    now = datetime.utcnow()
    return {
        "sku_id": sku_id,
        "brand": str(brand).strip(),
        "category": str(category).strip(),
        "uom_code": str(uom_code).strip(),
        "pack_qty": _parse_number(row.get("pack_qty"), default=1),
        "pack_uom": pack_uom,
        "description": (str(row["description"]).strip() if row.get("description") is not None else None),
        "attributes": attributes,
        "canonical_key": human_key,
        "status": _normalize_status(row.get("status")),
        "created_at": now,
        "updated_at": now,
        # derived
        "type_norm": type_norm or None,
        "size_mm_primary": p1,
        "size_mm_secondary": p2,
    }

UPSERT_SQL_COLUMNS = """
INSERT INTO public.sku_master
(
  sku_id, brand, category, uom_code, pack_qty, pack_uom, description, attributes,
  canonical_key, status, created_at, updated_at,
  type_norm, size_mm_primary, size_mm_secondary
)
VALUES
(
  :sku_id, :brand, :category, :uom_code, :pack_qty, :pack_uom, :description, :attributes::jsonb,
  :canonical_key, :status, :created_at, :updated_at,
  :type_norm, :size_mm_primary, :size_mm_secondary
)
ON CONFLICT (sku_id) DO UPDATE SET
  brand            = EXCLUDED.brand,
  category         = EXCLUDED.category,
  uom_code         = EXCLUDED.uom_code,
  pack_qty         = EXCLUDED.pack_qty,
  pack_uom         = EXCLUDED.pack_uom,
  description      = EXCLUDED.description,
  attributes       = EXCLUDED.attributes,
  canonical_key    = EXCLUDED.canonical_key,
  status           = EXCLUDED.status,
  updated_at       = now(),
  type_norm        = EXCLUDED.type_norm,
  size_mm_primary  = EXCLUDED.size_mm_primary,
  size_mm_secondary= EXCLUDED.size_mm_secondary
;
"""

async def upsert_from_file(engine: AsyncEngine, path: str, sheet_name: Optional[str], batch_size: int) -> Dict[str, int]:
    rows = _read_rows(path, sheet_name)
    if not rows:
        return {"processed": 0, "inserted_or_updated": 0, "skipped": 0}

    headers = {h for h in rows[0].keys()}
    missing = sorted(REQUIRED_COLUMNS - headers)
    if missing:
        raise ValueError(f"Input missing required columns: {missing}. Required: {sorted(REQUIRED_COLUMNS)}")

    processed = affected = skipped = 0

    # Prebuild payloads (also normalizes attributes incl. volume_ml)
    payloads: List[Dict[str, Any]] = []
    for row in rows:
        processed += 1
        if not (row.get("brand") and row.get("category") and row.get("uom_code")):
            skipped += 1
            continue
        payloads.append(_row_to_payload(row))

    if not payloads:
        return {"processed": processed, "inserted_or_updated": 0, "skipped": skipped}

    # Execute in batches
    async with engine.begin() as conn:
        for i in range(0, len(payloads), batch_size):
            batch = payloads[i : i + batch_size]
            await conn.execute(text(UPSERT_SQL_COLUMNS), batch)
            affected += len(batch)

    return {"processed": processed, "inserted_or_updated": affected, "skipped": skipped}

# ======================================================================================
#                                   BACKFILL (ASYNC)
# ======================================================================================

BACKFILL_SELECT = """
SELECT sku_id, attributes, description
FROM public.sku_master
WHERE type_norm IS NULL
   OR size_mm_primary IS NULL
   OR size_mm_secondary IS NULL
"""

BACKFILL_UPDATE = """
UPDATE public.sku_master
SET type_norm = :type_norm,
    size_mm_primary = :p1,
    size_mm_secondary = :p2,
    attributes = :attributes::jsonb,
    updated_at = now()
WHERE sku_id = :sku_id
"""

async def backfill_sku_master(engine: AsyncEngine) -> int:
    # Read set of rows needing backfill (or where we want to add volume_ml)
    async with engine.connect() as conn:
        res = await conn.execute(text(BACKFILL_SELECT))
        rows = res.all()

    if not rows:
        return 0

    updated = 0
    async with engine.begin() as conn:
        for sku_id, attrs, desc in rows:
            # parse/ensure dict
            if isinstance(attrs, str):
                try:
                    attrs = json.loads(attrs)
                except Exception:
                    attrs = {}
            elif not isinstance(attrs, dict):
                attrs = {}

            # Add volume_ml if detectable
            attrs = normalize_volume_in_attributes(attrs)

            a_type = normalize_type(attrs.get("type") or "", attrs.get("sub_type") or "")
            nd = normalize_dimension(attrs.get("dimension"))
            p1 = nd.get("primary_mm")
            p2 = nd.get("secondary_mm")

            await conn.execute(
                text(BACKFILL_UPDATE),
                {
                    "sku_id": sku_id,
                    "type_norm": a_type or None,
                    "p1": p1,
                    "p2": p2,
                    "attributes": json.dumps(attrs, ensure_ascii=False),
                },
            )
            updated += 1
    return updated

# ======================================================================================
#                                          MAIN
# ======================================================================================

async def main():
    # sanity on async driver
    url = make_url(DB_URL)
    if url.get_dialect().driver != "asyncpg":
        raise RuntimeError("DB_URL must use the async driver: postgresql+asyncpg://...")

    engine = create_async_engine(DB_URL, future=True)

    if DO_ENSURE_SCHEMA:
        print("Ensuring schema (extensions, derived columns, indexes, MVs)…")
        await ensure_schema(engine)
        print("Schema ensured.")

    if DO_UPSERT_FROM_FILE and INPUT_FILE and os.path.exists(INPUT_FILE):
        print(f"Upserting from file: {INPUT_FILE}")
        stats = await upsert_from_file(engine, INPUT_FILE, EXCEL_SHEET_NAME, BATCH_SIZE)
        print(f"Upsert stats: {stats}")
    elif DO_UPSERT_FROM_FILE:
        print(f"[SKIP] INPUT_FILE not found: {INPUT_FILE}")

    if DO_BACKFILL:
        print("Backfilling sku_master (type_norm, size_mm_*, volume_ml in attributes if detectable)…")
        cnt = await backfill_sku_master(engine)
        print(f"Backfilled rows: {cnt}")

    if DO_REFRESH_MV:
        print("Refreshing materialized views…")
        await refresh_mvs(engine, concurrently=REFRESH_CONCURRENTLY)
        print("MVs refreshed.")

    await engine.dispose()
    print("✔ Done.")

if __name__ == "__main__":
    asyncio.run(main())
