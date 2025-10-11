from __future__ import annotations

import asyncio
import json
import math
import os
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import MetaData, Table
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine.url import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

EXCEL_PATH = r"D:\babai\prime\outputs\cleaned_sku_master_sorted_cleaned_descfix.xlsx"
EXCEL_SHEET = "Sheet1"
SCHEMA = "public"
TABLE_NAME = "sku_master"
BATCH_SIZE = 1000

load_dotenv()
DB_URL = os.getenv("DATABASE_URL")

REQUIRED_COLS = ("brand", "category", "uom_code", "attributes")


def _require_asyncpg(url: str):
    if not url:
        raise RuntimeError("DATABASE_URL not set in environment or .env")
    u = make_url(url)
    if u.get_dialect().driver != "asyncpg":
        raise RuntimeError("DATABASE_URL must use async driver 'asyncpg' (postgresql+asyncpg://...)")


def _safe_json_obj(v: Any) -> Optional[Dict[str, Any]]:
    if v is None:
        return None
    if isinstance(v, dict):
        return v
    s = str(v).strip()
    if not s:
        return None
    try:
        j = json.loads(s)
        return j if isinstance(j, dict) else None
    except Exception:
        return None


def _read_excel_rows(path: str, sheet_name: Optional[str]) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    df = pd.read_excel(path, dtype=str, sheet_name=sheet_name, engine="openpyxl")
    df.columns = [c.strip() for c in df.columns]
    rows: List[Dict[str, Any]] = []
    for _, r in df.iterrows():
        d: Dict[str, Any] = {}
        for k, v in r.items():
            if isinstance(v, float) and math.isnan(v):
                d[k] = None
            else:
                d[k] = v
        rows.append(d)
    return rows


async def _reflect_table(engine: AsyncEngine, schema: str, table_name: str) -> Table:
    def _do_reflect(sync_conn):
        md = MetaData(schema=schema)
        return Table(table_name, md, autoload_with=sync_conn, schema=schema)

    async with engine.connect() as conn:
        return await conn.run_sync(_do_reflect)


def _safe_number(value: Optional[str]) -> Optional[float]:
    if value in (None, "", "NaN"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clean_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() == "nan":
        return None
    return s


def _format_mm_text(value: Optional[float]) -> Optional[str]:
    if value is None:
        return None
    if abs(value - round(value)) < 1e-6:
        return f"{int(round(value))} mm"
    return f"{value:g} mm"


def _build_record(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for key in REQUIRED_COLS:
        if key not in row:
            return None

    brand = _clean_text(row.get("brand")) or ""
    category = _clean_text(row.get("category")) or ""
    uom_code = _clean_text(row.get("uom_code")) or ""
    attrs = _safe_json_obj(row.get("attributes"))

    if not brand or not category or not uom_code or attrs is None:
        return None

    sku_id = (_clean_text(row.get("sku_id")) or str(uuid.uuid4()))

    pack_uom = _clean_text(row.get("pack_uom")) or "kg"

    try:
        pq_raw = row.get("pack_qty")
        pack_qty = int(float(pq_raw)) if pq_raw not in (None, "", "NaN") else 1
    except Exception:
        pack_qty = 1

    description = _clean_text(row.get("description"))
    canonical_key = _clean_text(row.get("canonical_key"))
    status = (_clean_text(row.get("status")) or "active").lower()
    if status not in ("active", "retired"):
        status = "active"

    ambiguous_raw = row.get("ambiguous")
    ambiguous_val: Optional[bool] = None
    if isinstance(ambiguous_raw, str):
        cleaned = ambiguous_raw.strip().lower()
        if cleaned in ("true", "1", "yes"):
            ambiguous_val = True
        elif cleaned in ("false", "0", "no"):
            ambiguous_val = False
    elif isinstance(ambiguous_raw, (bool, int)):
        ambiguous_val = bool(ambiguous_raw)

    type_norm = _clean_text(row.get("type_norm"))

    size_mm_primary = _safe_number(row.get("size_mm_primary"))
    size_mm_secondary = _safe_number(row.get("size_mm_secondary"))

    primary_size_native = _clean_text(row.get("primary_size_native"))
    primary_size_unit = _clean_text(row.get("primary_size_unit"))
    if primary_size_native is None and size_mm_primary is not None:
        primary_size_native = _format_mm_text(size_mm_primary)
        primary_size_unit = primary_size_unit or "mm"

    secondary_size_native = _clean_text(row.get("secondary_size_native"))
    secondary_size_unit = _clean_text(row.get("secondary_size_unit"))
    if secondary_size_native is None and size_mm_secondary is not None:
        secondary_size_native = _format_mm_text(size_mm_secondary)
        secondary_size_unit = secondary_size_unit or "mm"

    fragments = [brand, category, type_norm or "", (attrs.get("type") if attrs else ""), (attrs.get("variant") if attrs else ""), (attrs.get("raw_dimension") if attrs else ""), description or ""]
    if size_mm_primary is not None:
        fragments.append(f"{size_mm_primary:g} mm")
    if size_mm_secondary is not None:
        fragments.append(f"{size_mm_secondary:g} mm")
    search_text = " ".join(str(f).strip() for f in fragments if f and str(f).strip())
    if not search_text:
        search_text = None

    now = datetime.utcnow()
    record: Dict[str, Any] = {
        "sku_id": sku_id,
        "brand": brand,
        "category": category,
        "uom_code": uom_code,
        "pack_qty": pack_qty,
        "pack_uom": pack_uom,
        "description": description,
        "attributes": attrs,
        "canonical_key": canonical_key,
        "status": status,
        "ambiguous": ambiguous_val,
        "type_norm": type_norm,
        "size_mm_primary": size_mm_primary,
        "size_mm_secondary": size_mm_secondary,
        "primary_size_native": primary_size_native,
        "primary_size_unit": primary_size_unit,
        "secondary_size_native": secondary_size_native,
        "secondary_size_unit": secondary_size_unit,
        "search_text": search_text,
        "created_at": now,
        "updated_at": now,
    }
    return record


async def insert_excel_into_sku_master(
    excel_path: str,
    sheet_name: Optional[str] = None,
    schema: str = SCHEMA,
    table_name: str = TABLE_NAME,
    batch_size: int = BATCH_SIZE,
) -> Dict[str, int]:
    _require_asyncpg(DB_URL)
    engine = create_async_engine(DB_URL, future=True)

    rows = _read_excel_rows(excel_path, sheet_name)

    processed = 0
    skipped = 0
    payloads: List[Dict[str, Any]] = []

    for row in rows:
        processed += 1
        rec = _build_record(row)
        if rec is None:
            skipped += 1
            continue
        payloads.append(rec)

    if not payloads:
        await engine.dispose()
        return {"processed": processed, "inserted": 0, "skipped": skipped}

    table = await _reflect_table(engine, schema, table_name)

    inserted = 0
    async with engine.begin() as conn:
        for i in range(0, len(payloads), batch_size):
            batch = payloads[i : i + batch_size]
            stmt = pg_insert(table).values(batch).on_conflict_do_nothing(index_elements=[table.c.sku_id])
            await conn.execute(stmt)
            inserted += len(batch)

    await engine.dispose()
    return {"processed": processed, "inserted": inserted, "skipped": skipped}


async def main():
    print(f"Excel file: {EXCEL_PATH}")
    print("Loading DATABASE_URL from .env ...")
    stats = await insert_excel_into_sku_master(EXCEL_PATH, EXCEL_SHEET)
    print(stats)


if __name__ == "__main__":
    asyncio.run(main())
