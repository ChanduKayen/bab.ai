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


import copy
import json
import math
import os
from datetime import datetime
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from sqlalchemy import select, text, update
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


def _clean_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return str(value).strip() or None


def _format_native_mm(value: Optional[float]) -> Optional[str]:
    if value is None:
        return None
    if abs(value - round(value)) < 1e-6:
        return f"{int(round(value))} mm"
    return f"{value:g} mm"


def _numeric_equal(a: Optional[float], b: Optional[float], tol: float = 1e-6) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return math.isclose(a, b, rel_tol=0.0, abs_tol=tol)


def _derive_type_norm(attrs: Dict[str, Any], category: Optional[str]) -> Optional[str]:
    parts = [attrs.get("type"), attrs.get("sub_type")]
    type_norm = normalize_type(" ".join(p for p in parts if p))

    if not type_norm and category:
        cat_lower = category.lower()
        if "pipe" in cat_lower:
            type_norm = "pipe"
        elif "valve" in cat_lower:
            type_norm = "valve"

    if type_norm == "pipe" and not attrs.get("type"):
        attrs["type"] = "Pipe"
    elif type_norm and not attrs.get("type"):
        attrs["type"] = type_norm.replace("-", " ").title()

    return type_norm or None


def _derive_sizes(attrs: Dict[str, Any], description: Optional[str]) -> Dict[str, Optional[Any]]:
    dim_source = attrs.get("dimension") or attrs.get("raw_dimension") or attrs.get("length")
    dims = normalize_dimension(dim_source)
    p1 = dims.get("primary_mm")
    p2 = dims.get("secondary_mm")
    primary_native = _clean_str(dims.get("primary_native"))
    secondary_native = _clean_str(dims.get("secondary_native"))
    primary_unit = dims.get("primary_unit")
    secondary_unit = dims.get("secondary_unit")
    dimension_display = dims.get("display")

    if p1 is None and attrs.get("length_mm") is not None:
        candidate = _as_float(attrs.get("length_mm"))
        if candidate is not None:
            p1 = candidate
            if not primary_native:
                primary_native = _format_native_mm(candidate)
            primary_unit = primary_unit or "mm"

    if p1 is None and description:
        q_p1, q_p2, native1, native2, _amb = try_infer_size_from_text(description)
        if q_p1 is not None:
            p1 = q_p1
            if not primary_native:
                primary_native = _clean_str(native1) or _format_native_mm(q_p1)
            if primary_unit is None:
                primary_unit = "inch" if (primary_native and '"' in primary_native) else "mm"
            if p2 is None and q_p2 is not None:
                p2 = q_p2
                if not secondary_native:
                    secondary_native = _clean_str(native2) or _format_native_mm(q_p2)
                if secondary_unit is None:
                    secondary_unit = "inch" if (secondary_native and '"' in secondary_native) else "mm"

    if p1 is not None and not primary_native:
        primary_native = _format_native_mm(p1)
    if p2 is not None and not secondary_native:
        secondary_native = _format_native_mm(p2)
    if primary_unit is None and p1 is not None:
        primary_unit = "mm"
    if secondary_unit is None and p2 is not None:
        secondary_unit = "mm"

    return {
        "primary_mm": p1,
        "secondary_mm": p2,
        "primary_native": primary_native,
        "secondary_native": secondary_native,
        "primary_unit": primary_unit,
        "secondary_unit": secondary_unit,
        "dimension_display": dimension_display,
    }




def _compute_updates(row: Dict[str, Any]) -> Dict[str, Any]:
    attrs = _load_attrs(row.get("attributes"))
    original_attrs = copy.deepcopy(attrs)
    type_norm = _derive_type_norm(attrs, row.get("category"))
    sizes = _derive_sizes(attrs, row.get("description"))

    current_type = row.get("type_norm") or None
    current_p1 = _as_float(row.get("size_mm_primary"))
    current_p2 = _as_float(row.get("size_mm_secondary"))
    current_native_p1 = _clean_str(row.get("primary_size_native"))
    current_native_p2 = _clean_str(row.get("secondary_size_native"))
    current_unit_p1 = _clean_str(row.get("primary_size_unit"))
    current_unit_p2 = _clean_str(row.get("secondary_size_unit"))

    new_p1 = sizes.get("primary_mm")
    new_p2 = sizes.get("secondary_mm")
    new_native_p1 = _clean_str(sizes.get("primary_native"))
    new_native_p2 = _clean_str(sizes.get("secondary_native"))
    new_unit_p1 = _clean_str(sizes.get("primary_unit"))
    new_unit_p2 = _clean_str(sizes.get("secondary_unit"))
    dimension_display = sizes.get("dimension_display")

    updates: Dict[str, Any] = {}

    if type_norm != current_type:
        updates["type_norm"] = type_norm

    if not _numeric_equal(current_p1, new_p1):
        updates["size_mm_primary"] = new_p1
    if not _numeric_equal(current_p2, new_p2):
        updates["size_mm_secondary"] = new_p2

    if new_native_p1 != current_native_p1:
        updates["primary_size_native"] = new_native_p1
    if new_native_p2 != current_native_p2:
        updates["secondary_size_native"] = new_native_p2

    if (new_unit_p1 or current_unit_p1) and new_unit_p1 != current_unit_p1:
        updates["primary_size_unit"] = new_unit_p1
    if (new_unit_p2 or current_unit_p2) and new_unit_p2 != current_unit_p2:
        updates["secondary_size_unit"] = new_unit_p2

    attrs_changed = False
    if dimension_display and attrs.get("dimension") != dimension_display:
        attrs["dimension"] = dimension_display
        attrs_changed = True

    if attrs != original_attrs:
        attrs_changed = True

    if attrs_changed:
        updates["attributes"] = attrs

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
            SkuMaster.category,
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



async def _ensure_native_columns_text(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        result = await conn.execute(text("""
            SELECT data_type
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'sku_master' AND column_name = 'primary_size_native'
        """))
        primary_type = result.scalar()
        result = await conn.execute(text("""
            SELECT data_type
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'sku_master' AND column_name = 'secondary_size_native'
        """))
        secondary_type = result.scalar()

    statements = []
    if primary_type not in ('text', 'character varying'):
        statements.append("ALTER TABLE public.sku_master ALTER COLUMN primary_size_native TYPE TEXT USING NULLIF(TRIM(BOTH FROM primary_size_native::TEXT), '')")
    if secondary_type not in ('text', 'character varying'):
        statements.append("ALTER TABLE public.sku_master ALTER COLUMN secondary_size_native TYPE TEXT USING NULLIF(TRIM(BOTH FROM secondary_size_native::TEXT), '')")

    if not statements:
        return

    async with engine.begin() as conn:
        for stmt in statements:
            await conn.execute(text(stmt))


async def _prune_bad_reducers(engine: AsyncEngine) -> int:
    """Delete reducer SKUs that do not expose multidimensional data."""

    prune_sql = text(
        r"""
        WITH candidates AS (
            SELECT sku_id
            FROM public.sku_master
            WHERE status = 'active'
              AND (
                    COALESCE(lower(type_norm), '') LIKE 'reducer%'
                 OR lower(COALESCE(attributes->>'type', '')) LIKE 'reducer%'
              )
              AND NOT (
                    COALESCE(attributes->>'raw_dimension', '') ~* '\\d\\s*[x\u00d7\ufffd]\\s*\\d'
                 OR COALESCE(attributes->>'dimension', '') ~* '\\d\\s*[x\u00d7\ufffd]\\s*\\d'
              )
        )
        DELETE FROM public.sku_master sm
        USING candidates c
        WHERE sm.sku_id = c.sku_id
        RETURNING sm.sku_id
        """
    )

    async with engine.begin() as conn:
        result = await conn.execute(prune_sql)
        deleted = result.fetchall()
    return len(deleted)


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
        await _ensure_native_columns_text(engine)
        await backfill(engine)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
