# crud/sku.py

import datetime
import json
from uuid import uuid4
from decimal import Decimal
from typing import List, Dict, Any
import re
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy import select, or_, and_, case, cast, String, desc, func, text
from sqlalchemy.ext.asyncio import AsyncSession
from utils.sku_normalizer import (
    normalize_type, normalize_dimension, try_infer_size_from_text,
    parse_query, type_similarity
)
from database.models import SkuMaster, SkuVendorPrice, MaterialRequestItem
from sqlalchemy import update as sa_update

DEFAULT_SIM_THRESHOLD = 0.12

SEARCH_SQL = text("""
    WITH params AS (
        SELECT
            CASE WHEN :q_norm = '' THEN NULL::tsquery
                 ELSE websearch_to_tsquery('simple', unaccent(:q_norm))
            END AS tsq,
            CAST(:dim1 AS numeric) AS dim1,
            CAST(:dim2 AS numeric) AS dim2,
            CAST(:material AS text) AS material,
            CAST(:variant AS text) AS variant,
            CAST(:type_norm AS text) AS type_norm
    )
    SELECT
        sku_id,
        brand,
        category,
        uom_code,
        pack_qty,
        pack_uom,
        description,
        attributes,
        canonical_key,
        status,
        ambiguous,
        type_norm,
        size_mm_primary,
        size_mm_secondary,
        primary_size_native,
        primary_size_unit,
        secondary_size_native,
        secondary_size_unit,
        search_text,
        ts_rank,
        trigram,
        dim1_score,
        dim2_score,
        material_hit,
        variant_hit,
        type_hit,
        final_score
    FROM (
        SELECT
            sm.sku_id,
            sm.brand,
            sm.category,
            sm.uom_code,
            sm.pack_qty,
            sm.pack_uom,
            sm.description,
            sm.attributes,
            sm.canonical_key,
            sm.status,
            sm.ambiguous,
            sm.type_norm,
            sm.size_mm_primary,
            sm.size_mm_secondary,
            sm.primary_size_native,
            sm.primary_size_unit,
            sm.secondary_size_native,
            sm.secondary_size_unit,
            sm.search_text,
            COALESCE(ts_rank_cd(sm.tsv, params.tsq), 0) AS ts_rank,
            similarity(sm.search_text, :q_norm) AS trigram,
            CASE
                WHEN params.dim1 IS NULL OR sm.size_mm_primary IS NULL THEN 0
                ELSE 1 / (1 + ABS(LN((sm.size_mm_primary + 0.1) / (CAST(:dim1 AS numeric) + 0.1))))
            END AS dim1_score,
            CASE
                WHEN params.dim2 IS NULL OR sm.size_mm_secondary IS NULL THEN 0
                ELSE 1 / (1 + ABS(LN((sm.size_mm_secondary + 0.1) / (CAST(:dim2 AS numeric) + 0.1))))
            END AS dim2_score,
            CASE
                WHEN params.material IS NULL OR params.material = '' THEN 0
                WHEN lower(COALESCE(sm.attributes->>'material','')) = lower(params.material) THEN 1
                ELSE 0
            END AS material_hit,
            CASE
                WHEN params.variant IS NULL OR params.variant = '' THEN 0
                WHEN lower(COALESCE(sm.attributes->>'variant','')) = lower(params.variant) THEN 1
                ELSE 0
            END AS variant_hit,
            CASE
                WHEN params.type_norm IS NULL OR params.type_norm = '' THEN 0
                WHEN sm.type_norm = params.type_norm THEN 1
                WHEN sm.type_norm LIKE params.type_norm || '%' THEN 0.6
                ELSE 0
            END AS type_hit,
            (0.45 * GREATEST(COALESCE(ts_rank_cd(sm.tsv, params.tsq), 0), similarity(sm.search_text, :q_norm))
             + 0.25 * (0.75 * (CASE
                    WHEN params.dim1 IS NULL OR sm.size_mm_primary IS NULL THEN 0
                    ELSE 1 / (1 + ABS(LN((sm.size_mm_primary + 0.1) / (CAST(:dim1 AS numeric) + 0.1))))
                END)
                + 0.25 * (CASE
                    WHEN params.dim2 IS NULL OR sm.size_mm_secondary IS NULL THEN 0
                    ELSE 1 / (1 + ABS(LN((sm.size_mm_secondary + 0.1) / (CAST(:dim2 AS numeric) + 0.1))))
                END))
             + 0.07 * (CASE
                    WHEN params.material IS NULL OR params.material = '' THEN 0
                    WHEN lower(COALESCE(sm.attributes->>'material','')) = lower(params.material) THEN 1
                    ELSE 0
                END)
             + 0.10 * (CASE
                    WHEN params.variant IS NULL OR params.variant = '' THEN 0
                    WHEN lower(COALESCE(sm.attributes->>'variant','')) = lower(params.variant) THEN 1
                    ELSE 0
                END)
             + 0.05 * (CASE
                    WHEN params.type_norm IS NULL OR params.type_norm = '' THEN 0
                    WHEN sm.type_norm = params.type_norm THEN 1
                    WHEN sm.type_norm LIKE params.type_norm || '%' THEN 0.6
                    ELSE 0
                END)) AS final_score
        FROM public.sku_master sm, params
        WHERE sm.status = 'active'
          AND (
                similarity(sm.search_text, :q_norm) > :sim_threshold
                OR (params.tsq IS NOT NULL AND sm.tsv @@ params.tsq)
              )
    ) scored
    ORDER BY final_score DESC, ts_rank DESC, trigram DESC
    LIMIT :limit OFFSET :offset
    """)

def _normalize_price_to_base(quoted_price: float, price_unit: str, sku: SkuMaster) -> Decimal:
    """
    Normalize vendor quoted price (in price_unit) to sku.uom_code.
    Rules:
      - if price_unit == sku.uom_code -> no change
      - if price_unit == sku.pack_uom and sku.pack_uom != sku.uom_code and pack_qty present
            assume pack contains `pack_qty` of base uom, so price/base = price/pack_qty
      - otherwise: leave as-is (best effort; you can add more conversions later)
    """
    u = (price_unit or "").strip().lower()
    base = (sku.uom_code or "").strip().lower()
    pack_uom = (sku.pack_uom or "").strip().lower()
    pack_qty = sku.pack_qty or 1
    q = Decimal(str(quoted_price))
    if not base:
        return q
    if u == base:
        return q
    if u == pack_uom and pack_uom and pack_uom != base and pack_qty:
        return q / Decimal(str(pack_qty))
    # fallback (no conversion rule known)
    return q

class SkuCRUD:
    def __init__(self, session: AsyncSession):
        self.session = session
    # -------------------------------------------------------------
    # Helpers for matching & price upsert
    # -------------------------------------------------------------
    async def _build_query_from_request_item(self, req_item_id) -> str:
        row = await self.session.scalar(
            select(MaterialRequestItem).where(MaterialRequestItem.id == req_item_id)
        )
        if not row:
            return ""
        parts = [row.material_name or "", row.sub_type or "", row.dimensions or "", row.dimension_units or ""]
        q = " ".join([p for p in parts if p]).strip()
        return q
    
    async def _upsert_price(self, request_id, vendor_id, request_item_id, sku_id: str, quoted_price: float, price_unit: str, tag: str, resolved: bool):
        sku = await self.session.scalar(select(SkuMaster).where(SkuMaster.sku_id == sku_id))
        if not sku:
            raise ValueError(f"sku_id not found: {sku_id}")
        normalized_price = _normalize_price_to_base(quoted_price, price_unit, sku)
        now = datetime.datetime.utcnow()
        quote_ref = f"{request_id}:{vendor_id}:{request_item_id}"
        
        print(
            f"sku_crud ::::: upsert_price ::::: sku_id={sku_id}, vendor_id={vendor_id}, request_item_id={request_item_id}, "
            f"quoted_price={quoted_price}, price_unit={price_unit}, normalized_price={normalized_price}, resolved={resolved}, quote_ref={quote_ref}"
        )
        stmt = (
            pg_insert(SkuVendorPrice)
            .values(
                sku_id=sku_id,
                vendor_id=vendor_id,
                quoted_at=now,
                price=normalized_price,
                currency="INR",
                resolved=resolved,
                quote_ref=quote_ref,
                tag=tag,
                created_at=now,
            )
            .on_conflict_do_update(
                constraint="uq_vendor_quote_sku",
                set_={
                    "price": normalized_price,
                    "quoted_at": now,
                    "tag": tag,
                    "resolved": resolved,
                },
            )
        )
        print(f"sku_crud ::::: upsert_price ::::: executing svp_stmt : {stmt}")
        await self.session.execute(stmt)
        print(f"sku_crud ::::: upsert_price ::::: upsert OK for sku_id={sku_id}")

    async def _create_ambiguous_sku(self, req_query: str, fallback_parse: dict | None = None) -> str:
        # Minimal viable SKU row with required fields
        sid = str(uuid4())
        # Try parse to derive a category/type
        qinfo = parse_query(req_query or "")
        q_type = (qinfo or {}).get("q_type") if qinfo else None
        attributes = {
            "source": "auto-created",
            "ambiguous": True,
        }
        if q_type:
            attributes["type"] = q_type
        if fallback_parse:
            attributes.update({k: v for k, v in fallback_parse.items() if v is not None})
        sku = SkuMaster(
            sku_id=sid,
            brand="generic",
            category=(q_type or "misc"),
            uom_code="unit",
            pack_qty=1,
            pack_uom="unit",
            description=req_query[:500] if req_query else None,
            attributes=attributes,
            canonical_key=None,
            ambiguous=True,
            status="active",
        )
        self.session.add(sku)
        print(f"sku_crud ::::: _create_ambiguous_sku ::::: created sid={sid}, category={sku.category}, description={(sku.description or '')[:60]}")
        return sid
    
    async def process_vendor_quote_item(self, request_id, vendor_id, item) -> None:
        """
        Thresholded match using search_skus_score(). Behavior:
          - score_norm >= 0.80: insert one resolved price for top candidate
          - 0.50 <= score_norm < 0.80: insert top 3 candidates as unresolved with same quote_ref
          - < 0.50: create ambiguous SKU and insert one unresolved price
        Expects item to have: requested_item_id, quoted_price, price_units (or price_unit), comments
        """
        is_dict = isinstance(item, dict)
        req_item_id = getattr(item, "requested_item_id", None) or (item.get("requested_item_id") if is_dict else None)
        quoted_price = getattr(item, "quoted_price", None) or (item.get("quoted_price") if is_dict else None)
        price_unit = (
            getattr(item, "price_units", None)
            or getattr(item, "price_unit", None)
            or (item.get("price_units") if is_dict else None)
            or (item.get("price_unit") if is_dict else None)
            or "unit"
        )
        comments = getattr(item, "comments", None) or (item.get("comments") if is_dict else None)
        print(
            f"sku_crud ::::: process_vendor_quote_item ::::: req_item_id={req_item_id}, quoted_price={quoted_price}, price_unit={price_unit}, comments={comments}"
        )
        if not req_item_id or quoted_price is None:
            return
        # Compose the query from the request item
        query_text = await self._build_query_from_request_item(req_item_id)
        if not query_text:
            # fallback to comments if present
            query_text = comments or ""
        # print(f"sku_crud ::::: process_vendor_quote_item ::::: built query text: {query_text}")
        candidates = await self.search_skus_score_sql(query_text, limit=5)
        top = candidates[0] if candidates else None
        score = float(top["score"]) if top else 0.0
        score_norm = min(1.0, score / 240.0)
        # print(
        #     f"sku_crud ::::: process_vendor_quote_item ::::: query='{query_text}', candidates={len(candidates)}, top={(top or {}).get('sku_id') if top else None}, score={score}, score_norm={score_norm}"
        # )
        if top and score_norm >= 0.80:
            print(f"sku_crud ::::: process_vendor_quote_item ::::: high confidence match: {top['sku_id']} with score {score} (norm {score_norm})")
            await self._upsert_price(request_id, vendor_id, req_item_id, top["sku_id"], quoted_price, price_unit, comments or query_text, True)
            return
        if top and score_norm >= 0.50:
            k = min(3, len(candidates))
            print(f"sku_crud ::::: process_vendor_quote_item ::::: moderate confidence match: top {k} candidates with top score {score} (norm {score_norm})")
            for c in candidates[:k]:
                await self._upsert_price(request_id, vendor_id, req_item_id, c["sku_id"], quoted_price, price_unit, comments or query_text, False)
            return
        # Create a placeholder ambiguous SKU and attach price as unresolved
        print(f"sku_crud ::::: process_vendor_quote_item ::::: low confidence match: creating ambiguous SKU for query '{query_text}' with score {score} (norm {score_norm})")
        sid = await self._create_ambiguous_sku(query_text)
        await self._upsert_price(request_id, vendor_id, req_item_id, sid, quoted_price, price_unit, (comments or query_text or "ambiguous"), False)

    # -------------------------------------------------------------
    # Alias/reconciliation helpers (manual/admin-triggered)
    # -------------------------------------------------------------
    async def reassign_prices_to_master(self, alias_sku_id: str, master_sku_id: str) -> int:
        """Repoint all sku_vendor_price rows from alias_sku_id to master_sku_id, then retire alias SKU.
        Returns number of price rows updated.
        """
        res = await self.session.execute(
            sa_update(SkuVendorPrice)
            .where(SkuVendorPrice.sku_id == alias_sku_id)
            .values(sku_id=master_sku_id, resolved=True)
            .execution_options(synchronize_session=False)
        )
        count = res.rowcount or 0
        # Retire alias SKU
        await self.session.execute(
            sa_update(SkuMaster).where(SkuMaster.sku_id == alias_sku_id).values(status="retired", ambiguous=False)
        )
        return count
    
    async def search_skus_score(self, keyword: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Legacy wrapper retained for callers expecting the old name."""
        return await self.search_skus_score_sql(keyword, limit=limit)

    async def search_skus_score_sql(self, keyword: str, limit: int = 5, offset: int = 0) -> List[Dict[str, Any]]:
        """Execute weighted text + dimension search directly in Postgres.
        Returns rows with blended score so downstream callers can keep existing thresholds.
        """
        q = (keyword or '').strip()
        if not q:
            return []
        qinfo = parse_query(q)
        params = {
            'q_norm': qinfo.get('q_norm') or q.lower(),
            'dim1': qinfo.get('q_p1'),
            'dim2': qinfo.get('q_p2'),
            'material': qinfo.get('material'),
            'variant': qinfo.get('variant'),
            'type_norm': qinfo.get('type_norm'),
            'sim_threshold': DEFAULT_SIM_THRESHOLD if (qinfo.get('q_norm') or '').strip() else -1.0,
            'limit': int(max(1, min(limit, 100))),
            'offset': int(max(0, offset)),
        }
        result = await self.session.execute(SEARCH_SQL, params)
        rows = result.mappings().all()
        items: List[Dict[str, Any]] = []
        for row in rows:
            attrs = row.get('attributes')
            if isinstance(attrs, str):
                try:
                    attrs = json.loads(attrs)
                except json.JSONDecodeError:
                    attrs = {}
            elif attrs is None:
                attrs = {}
            ts_rank = float(row.get('ts_rank') or 0)
            trigram = float(row.get('trigram') or 0)
            dim1_score = float(row.get('dim1_score') or 0)
            dim2_score = float(row.get('dim2_score') or 0)
            material_hit = float(row.get('material_hit') or 0)
            variant_hit = float(row.get('variant_hit') or 0)
            type_hit = float(row.get('type_hit') or 0)
            final_raw = float(row.get('final_score') or 0)
            final_clamped = max(0.0, min(final_raw, 1.0))
            score = round(final_clamped * 240.0, 3)
            items.append({
                'sku_id': row['sku_id'],
                'brand': row['brand'],
                'category': row['category'],
                'uom_code': row['uom_code'],
                'pack_qty': float(row['pack_qty']) if row.get('pack_qty') is not None else None,
                'pack_uom': row['pack_uom'],
                'description': row['description'],
                'attributes': attrs,
                'canonical_key': row['canonical_key'],
                'status': row['status'],
                'ambiguous': bool(row.get('ambiguous')),
                'score': score,
                'normalized': {
                    'type_norm': row.get('type_norm'),
                    'size_mm_primary': float(row['size_mm_primary']) if row.get('size_mm_primary') is not None else None,
                    'size_mm_secondary': float(row['size_mm_secondary']) if row.get('size_mm_secondary') is not None else None,
                    'primary_size_native': row.get('primary_size_native'),
                    'primary_size_unit': row.get('primary_size_unit'),
                    'secondary_size_native': row.get('secondary_size_native'),
                    'secondary_size_unit': row.get('secondary_size_unit'),
                },
                'search_text': row.get('search_text'),
                'debug': {
                    'final_score': final_raw,
                    'ts_rank': ts_rank,
                    'trigram': trigram,
                    'dim1_score': dim1_score,
                    'dim2_score': dim2_score,
                    'material_hit': material_hit,
                    'variant_hit': variant_hit,
                    'type_hit': type_hit,
                },
            })
        if not items:
            return items

        keyword_lower = q.lower()

        def _sizes_match(norm: Dict[str, Any]) -> bool:
            base_tol = qinfo.get('tol') or 0.0
            primary_query = qinfo.get('q_p1')
            secondary_query = qinfo.get('q_p2')

            if primary_query is not None:
                primary_value = norm.get('size_mm_primary')
                if primary_value is None:
                    return False
                tol = max(1.0, base_tol)
                if abs(primary_value - primary_query) > tol:
                    return False

            if secondary_query is not None:
                secondary_value = norm.get('size_mm_secondary')
                if secondary_value is None:
                    return False
                tol = max(1.0, base_tol)
                if abs(secondary_value - secondary_query) > tol:
                    return False

            return True

        def _has_exact_match(candidate: Dict[str, Any]) -> bool:
            attrs = candidate.get('attributes') or {}
            norm = candidate.get('normalized') or {}

            brand = (candidate.get('brand') or '').lower()
            brand_match = bool(brand and brand in keyword_lower)

            material_query = qinfo.get('material')
            material_candidate = (attrs.get('material') or '').lower() if isinstance(attrs, dict) else ''
            material_match = bool(material_query and material_candidate == material_query.lower())

            type_query = qinfo.get('type_norm')
            type_candidate = (norm.get('type_norm') or '').lower()
            type_match = bool(type_query and type_candidate == type_query.lower())

            dimension_match = _sizes_match(norm)

            return brand_match and material_match and type_match and dimension_match

        if _has_exact_match(items[0]):
            return [items[0]]

        return items
    
    async def insert_sku_vendor_price(self, request_id, vendor_id, item):
        # validate SKU exists & active
        sku = await self.session.scalar(
        select(SkuMaster).where(
            SkuMaster.sku_id == item.sku_id,
            SkuMaster.status == "active",)
        )
        if not sku:
            raise ValueError(f"Invalid or inactive sku_id: {item.sku_id}")
        
        print(f"sku_crud ::::: insert_sku_vendor_quotes ::::: found SKU : {sku}")
        # write resolved price into sku_vendor_price (1 line per (vendor, request_line, sku))
        try:
            normalized_price = _normalize_price_to_base(item.quoted_price, item.price_unit, sku)
            quote_ref = f"{request_id}:{vendor_id}:{item.requested_item_id}"  # stable idempotency key
            now = datetime.utcnow()
            svp_stmt = (
                pg_insert(SkuVendorPrice)
                .values(
                    sku_id=item.sku_id,
                    vendor_id=vendor_id,
                    quoted_at=now,
                    price=normalized_price,
                    currency="INR",
                    resolved=True,
                    quote_ref=quote_ref,             # matches your UniqueConstraint with vendor_id + sku_id
                    tag=(item.comments or sku.description),
                    # pincode=vendor_pincode,
                    created_at=now,
                )
                .on_conflict_do_update(
                    constraint="uq_vendor_quote_sku",
                    set_={
                        "price": normalized_price,
                        "quoted_at": now,
                        # "pincode": vendor_pincode,
                        "tag": (item.comments or sku.description),
                    },
                )
            )
            print(f"sku_crud ::::: insert_sku_vendor_quotes ::::: executing svp_stmt : {svp_stmt}")
            await self.session.execute(svp_stmt)
            print(f"sku_crud ::::: insert_sku_vendor_quotes ::::: upsert OK for sku_id={item.sku_id}")
        except Exception as e:
            self.session.rollback()
            print(f"sku_crud ::::: insert_sku_vendor_quotes ::::: ERROR for sku_id={item.sku_id} : {e}")
            raise
        print(f"sku_crud ::::: insert_sku_vendor_quotes ::::: OK for item_id={item.requested_item_id}")
