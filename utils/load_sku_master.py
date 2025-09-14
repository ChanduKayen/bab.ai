from __future__ import annotations

import asyncio
import json
import math
import os
import uuid
import openpyxl
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import MetaData, Table
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine.url import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

# ──────────────────────────────────────────────────────────────────────────────
# HARD-CODED EXCEL FILE PATH (set this to your local file)
EXCEL_PATH = r"C:\Users\vlaks\Downloads\sku_master_data_with_additions.xlsx"  # <-- EDIT ME
EXCEL_SHEET = "Sheet1"  # None = first sheet
SCHEMA = "public"
TABLE_NAME = "sku_master"
BATCH_SIZE = 1000
# ──────────────────────────────────────────────────────────────────────────────

# Load DB URL from .env
load_dotenv()
DB_URL = os.getenv("DATABASE_URL")  # must be postgresql+asyncpg://...

REQUIRED_COLS = ("brand", "category", "uom_code", "attributes")


# ───────────────────────────── helpers ─────────────────────────────
def _require_asyncpg(url: str):
    if not url:
        raise RuntimeError("DATABASE_URL not set in environment or .env")
    u = make_url(url)
    if u.get_dialect().driver != "asyncpg":
        raise RuntimeError("DATABASE_URL must use async driver 'asyncpg' (postgresql+asyncpg://...)")

def _safe_json_obj(v: Any) -> Optional[Dict[str, Any]]:
    """
    Accept dict or a JSON string that decodes to an object.
    Returns dict or None (invalid/blank). No normalization or fixes.
    """
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

def _build_record(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Build an insert dict from a raw row (no data modifications).
    Required: brand, category, uom_code, attributes(JSON object).
    """
    # Ensure required columns exist on the row
    for key in REQUIRED_COLS:
        if key not in row:
            return None

    brand = (row.get("brand") or "").strip()
    category = (row.get("category") or "").strip()
    uom_code = (row.get("uom_code") or "").strip()
    attrs = _safe_json_obj(row.get("attributes"))

    if not brand or not category or not uom_code or attrs is None:
        return None

    # sku_id: keep if present, else UUIDv4
    sku_id = (str(row.get("sku_id")).strip() if row.get("sku_id") else str(uuid.uuid4()))

    # pack_uom: keep as-is (default 'kg' if blank)
    pack_uom = (row.get("pack_uom") or "kg").strip() or "kg"

    # pack_qty: strictly integer-like; default to 1 if blank/unparseable
    try:
        pq_raw = row.get("pack_qty")
        pack_qty = int(float(pq_raw)) if pq_raw not in (None, "", "NaN") else 1
    except Exception:
        pack_qty = 1

    description = (None if not row.get("description") else str(row.get("description")))
    canonical_key = (None if not row.get("canonical_key") else str(row.get("canonical_key")))
    status = (row.get("status") or "active").strip().lower()
    if status not in ("active", "retired"):
        status = "active"

    now = datetime.utcnow()
    return {
        "sku_id": sku_id,
        "brand": brand,
        "category": category,
        "uom_code": uom_code,
        "pack_qty": pack_qty,
        "pack_uom": pack_uom,
        "description": description,
        "attributes": attrs,           # dict → JSONB
        "canonical_key": canonical_key,
        "status": status,
        "created_at": now,
        "updated_at": now,
    }


# ───────────────────────────── inserter ─────────────────────────────
async def insert_excel_into_sku_master(
    excel_path: str,
    sheet_name: Optional[str] = None,
    schema: str = SCHEMA,
    table_name: str = TABLE_NAME,
    batch_size: int = BATCH_SIZE,
) -> Dict[str, int]:
    _require_asyncpg(DB_URL)
    engine = create_async_engine(DB_URL, future=True)

    # Read Excel
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

    # Reflect table and insert with ON CONFLICT DO NOTHING
    table = await _reflect_table(engine, schema, table_name)

    inserted = 0
    async with engine.begin() as conn:
        for i in range(0, len(payloads), batch_size):
            batch = payloads[i : i + batch_size]
            stmt = pg_insert(table).values(batch).on_conflict_do_nothing(index_elements=[table.c.sku_id])
            await conn.execute(stmt)
            inserted += len(batch)  # asyncpg rowcount is unreliable for DO NOTHING; assume all attempted

    await engine.dispose()
    return {"processed": processed, "inserted": inserted, "skipped": skipped}


# ───────────────────────────── entrypoint ─────────────────────────────
async def main():
    print(f"Excel file: {EXCEL_PATH}")
    print("Loading DATABASE_URL from .env …")
    stats = await insert_excel_into_sku_master(EXCEL_PATH, EXCEL_SHEET)
    print(stats)

if __name__ == "__main__":
    asyncio.run(main())
