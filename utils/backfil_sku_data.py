"""Utility script to backfill search helper columns on sku_master.

Run locally with DATABASE_URL pointing to the Postgres instance.  The script populates
`type_norm`, size fields (mm + native/unit pairs), and bumps `updated_at` so the trigger
refreshes `search_text` and `tsv`.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


import json
import math
import os
from datetime import datetime
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from database.models import SkuMaster
from utils.sku_normalizer import (
    normalize_type,
    normalize_dimension,
    try_infer_size_from_text,
)

BATCH_SIZE = 500


def _load_attrs(raw: Any) -> Dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            return {}
    return {}


def _as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _numeric_equal(a: Optional[float], b: Optional[float], tol: float = 1e-6) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return math.isclose(a, b, rel_tol=0.0, abs_tol=tol)


def _derive_type_norm(attrs: Dict[str, Any]) -> Optional[str]:
    parts = [attrs.get("type"), attrs.get("sub_type")]
    type_norm = normalize_type(" ".join(p for p in parts if p))
    return type_norm or None


def _derive_sizes(attrs: Dict[str, Any], description: Optional[str]) -> Dict[str, Optional[float]]:
    dim_source = attrs.get("dimension") or attrs.get("raw_dimension") or attrs.get("length")
    dims = normalize_dimension(dim_source)
    p1 = dims.get("primary_mm")
    p2 = dims.get("secondary_mm")

    if p1 is None and attrs.get("length_mm") is not None:
        candidate = _as_float(attrs.get("length_mm"))
        if candidate is not None:
            p1 = candidate

    if p1 is None and description:
        q_p1, q_p2, _display, _amb = try_infer_size_from_text(description)
        if q_p1 is not None:
            p1 = q_p1
            if p2 is None:
                p2 = q_p2

    return {"primary_mm": p1, "secondary_mm": p2}


def _desired_units(primary_mm: Optional[float], secondary_mm: Optional[float]) -> Dict[str, Optional[Any]]:
    unit = "mm" if primary_mm is not None else None
    sec_unit = "mm" if secondary_mm is not None else None
    return {
        "primary_size_native": primary_mm,
        "primary_size_unit": unit,
        "secondary_size_native": secondary_mm,
        "secondary_size_unit": sec_unit,
    }


def _compute_updates(row: Dict[str, Any]) -> Dict[str, Any]:
    attrs = _load_attrs(row.get("attributes"))
    type_norm = _derive_type_norm(attrs)
    sizes = _derive_sizes(attrs, row.get("description"))
    units = _desired_units(sizes["primary_mm"], sizes["secondary_mm"])

    current_type = row.get("type_norm") or None
    current_p1 = _as_float(row.get("size_mm_primary"))
    current_p2 = _as_float(row.get("size_mm_secondary"))
    current_native_p1 = _as_float(row.get("primary_size_native"))
    current_native_p2 = _as_float(row.get("secondary_size_native"))
    current_unit_p1 = row.get("primary_size_unit") or None
    current_unit_p2 = row.get("secondary_size_unit") or None

    updates: Dict[str, Any] = {}

    if type_norm != current_type:
        updates["type_norm"] = type_norm

    if not _numeric_equal(current_p1, sizes["primary_mm"]):
        updates["size_mm_primary"] = sizes["primary_mm"]
    if not _numeric_equal(current_p2, sizes["secondary_mm"]):
        updates["size_mm_secondary"] = sizes["secondary_mm"]

    if not _numeric_equal(current_native_p1, units["primary_size_native"]):
        updates["primary_size_native"] = units["primary_size_native"]
    if not _numeric_equal(current_native_p2, units["secondary_size_native"]):
        updates["secondary_size_native"] = units["secondary_size_native"]

    if (units["primary_size_unit"] or current_unit_p1) and units["primary_size_unit"] != current_unit_p1:
        updates["primary_size_unit"] = units["primary_size_unit"]
    if (units["secondary_size_unit"] or current_unit_p2) and units["secondary_size_unit"] != current_unit_p2:
        updates["secondary_size_unit"] = units["secondary_size_unit"]

    return updates


def _require_db_url() -> str:
    load_dotenv()
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL not set in environment")
    return url


def _select_stmt(offset: int, limit: int):
    return (
        select(
            SkuMaster.sku_id,
            SkuMaster.description,
            SkuMaster.attributes,
            SkuMaster.type_norm,
            SkuMaster.size_mm_primary,
            SkuMaster.size_mm_secondary,
            SkuMaster.primary_size_native,
            SkuMaster.primary_size_unit,
            SkuMaster.secondary_size_native,
            SkuMaster.secondary_size_unit,
        )
        .where(SkuMaster.status == "active")
        .order_by(SkuMaster.sku_id)
        .offset(offset)
        .limit(limit)
    )


async def _fetch_batch(engine: AsyncEngine, offset: int, limit: int):
    async with engine.connect() as conn:
        result = await conn.execute(_select_stmt(offset, limit))
        return result.mappings().all()


async def backfill(engine: AsyncEngine) -> None:
    offset = 0
    total = 0
    touched = 0

    while True:
        rows = await _fetch_batch(engine, offset, BATCH_SIZE)
        if not rows:
            break

        total += len(rows)

        async with engine.begin() as conn:
            for row in rows:
                updates = _compute_updates(row)
                if not updates:
                    continue
                updates["updated_at"] = datetime.utcnow()
                await conn.execute(
                    update(SkuMaster)
                    .where(SkuMaster.sku_id == row["sku_id"])
                    .values(**updates)
                )
                touched += 1

        offset += BATCH_SIZE

    print(f"Processed {total} rows; updated {touched} rows.")


async def main() -> None:
    url = _require_db_url()
    engine = create_async_engine(url, future=True)
    try:
        await backfill(engine)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
