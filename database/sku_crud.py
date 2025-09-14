# crud/sku.py
import datetime
import json
from uuid import uuid4
from decimal import Decimal
from typing import List, Dict, Any
import re
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy import select, or_, and_, case, cast, String, desc, func
from sqlalchemy.ext.asyncio import AsyncSession
from utils.sku_normalizer import (
    normalize_type, normalize_dimension, try_infer_size_from_text,
    parse_query, type_similarity
)

from database.models import SkuMaster, SkuVendorPrice, MaterialRequestItem
from sqlalchemy import update as sa_update


def _tokenize(q: str) -> List[str]:
    """
    Simple, robust tokenizer for SKU search:
    - lowercase
    - keep numbers, letters, '/' and '"' (so 1/2" survives), collapse spaces
    - split on anything else
    """
    q = q.strip().lower()
    # normalize common inch symbols and spaces: 1/2", 1/2 in, 1/2 inch -> 1/2"
    q = q.replace(" inch", '"').replace(" in", '"').replace("″", '"').replace("”", '"')
    # collapse multiple spaces
    q = re.sub(r"\s+", " ", q)
    # split on non [a-z0-9/"-]
    tokens = re.split(r"[^a-z0-9/\".-]+", q)
    return [t for t in tokens if t]

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

        print(f"sku_crud ::::: process_vendor_quote_item ::::: built query text: {query_text}")
        candidates = await self.search_skus_score(query_text, limit=5)
        top = candidates[0] if candidates else None
        score = float(top["score"]) if top else 0.0
        score_norm = min(1.0, score / 240.0)
        print(
            f"sku_crud ::::: process_vendor_quote_item ::::: query='{query_text}', candidates={len(candidates)}, top={(top or {}).get('sku_id') if top else None}, score={score}, score_norm={score_norm}"
        )

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

    async def search_skus(self, keyword: str, limit: int = 25) -> List[Dict[str, Any]]:
        """
        Score-based search over sku_master prioritizing:
        1) exact sku_id match
        2) all tokens present in canonical_key
        3) any token in canonical_key
        4) brand/category/description/attributes hits

        Returns top-N with a score field for debugging.
        """
        q = (keyword or "").strip()
        if not q:
            return []

        tokens = _tokenize(q)
        if not tokens:
            return []

        SK = SkuMaster
        attrs_text = cast(SK.attributes, String)

        # Build token-wise LIKEs
        patt = [f"%{t}%" for t in tokens]

        cankey_all = and_(*[SK.canonical_key.ilike(p) for p in patt])
        cankey_any = or_(*[SK.canonical_key.ilike(p) for p in patt])

        brand_any   = or_(*[SK.brand.ilike(p) for p in patt])
        cat_any     = or_(*[SK.category.ilike(p) for p in patt])
        desc_any    = or_(*[SK.description.ilike(p) for p in patt])
        attrs_any   = or_(*[attrs_text.ilike(p) for p in patt])

        # Base filter: at least one hit somewhere
        any_hit = or_(cankey_any, brand_any, cat_any, desc_any, attrs_any, SK.sku_id == q)

        # Score: higher is better. Tune weights as needed.
        score = (
            case((SK.sku_id == q, 200), else_=0) +
            case((cankey_all, 100), else_=0) +
            case((cankey_any, 40), else_=0) +
            case((brand_any, 25), else_=0) +
            case((cat_any, 20), else_=0) +
            case((desc_any, 15), else_=0) +
            case((attrs_any, 10), else_=0)
        ).label("score")

        stmt = (
            select(
                SK.sku_id,
                SK.brand,
                SK.category,
                SK.uom_code,
                SK.pack_qty,
                SK.pack_uom,
                SK.description,
                SK.attributes,
                SK.canonical_key,
                SK.status,
                score,
            )
            .where(SK.status == "active")
            .where(any_hit)
            .order_by(desc(score), desc(SK.updated_at), desc(SK.created_at))
            .limit(limit)
        )

        res = await self.session.execute(stmt)
        rows = res.all()

        # Return plain dicts for the API
        items: List[Dict[str, Any]] = []
        for (
            sku_id, brand, category, uom_code, pack_qty, pack_uom,
            description, attributes, canonical_key, status, score_val
        ) in rows:
            items.append({
                "sku_id": sku_id,
                "brand": brand,
                "category": category,
                "uom_code": uom_code,
                "pack_qty": float(pack_qty) if pack_qty is not None else None,
                "pack_uom": pack_uom,
                "description": description,
                "attributes": attributes,
                "canonical_key": canonical_key,
                "status": status,
                "score": float(score_val or 0),
            })

        return items

    async def search_skus_score(self, keyword: str, limit: int = 25) -> List[Dict[str, Any]]:
        """
        Fuzzy search that ENFORCES type & dimension match (with tolerance) and ranks primarily by them.
        No reliance on brand/category/description/canonical_key scoring.
        """
        q = (keyword or "").strip()
        if not q:
            return []

        qinfo = parse_query(q)
        q_type, q_p1, q_p2, tol = qinfo["q_type"], qinfo["q_p1"], qinfo["q_p2"], qinfo["tol"]

        # Pull a broad active set (we re-rank in Python). Adjust LIMIT_UPPER if needed.
        LIMIT_UPPER = 8000  # safe for your dataset size; can be tuned
        stmt = (
            select(
                SkuMaster.sku_id, SkuMaster.brand, SkuMaster.category,
                SkuMaster.uom_code, SkuMaster.pack_qty, SkuMaster.pack_uom,
                SkuMaster.description, SkuMaster.attributes, SkuMaster.canonical_key,
                SkuMaster.status, SkuMaster.updated_at, SkuMaster.created_at
            )
            .where(SkuMaster.status == "active")
            .order_by(desc(SkuMaster.updated_at), desc(SkuMaster.created_at))
            .limit(LIMIT_UPPER)
        )
        res = await self.session.execute(stmt)
        rows = res.all()

        def dim_bucket(p1_mm, p2_mm) -> int:
            if q_p1 is None and q_p2 is None:
                return 0
            if q_p2 is None and p2_mm is None:
                if p1_mm is None: return 0
                diff = abs(p1_mm - q_p1)
                if diff <= max(0.5, 0.01*q_p1): return 120
                if diff <= 1.0:  return 110
                if diff <= 2.0:  return 100
                if diff <= 5.0:  return 80
                if diff <= 10.0: return 60
                return 0
            if q_p1 is not None and q_p2 is not None and p1_mm is not None and p2_mm is not None:
                d1 = abs(p1_mm - q_p1) + abs(p2_mm - q_p2)
                d2 = abs(p1_mm - q_p2) + abs(p2_mm - q_p1)
                diff = min(d1, d2)
                if diff <= 1.0:  return 120
                if diff <= 2.0:  return 110
                if diff <= 5.0:  return 90
                if diff <= 10.0: return 70
                return 0
            return 0

        items: List[Dict[str, Any]] = []
        for (
            sku_id, brand, category, uom_code, pack_qty, pack_uom,
            description, attributes, canonical_key, status, updated_at, created_at
        ) in rows:

            # attributes → dict
            attr = attributes if isinstance(attributes, dict) else (
                json.loads(attributes) if isinstance(attributes, str) else {})

            # type normalization (attributes first)
            a_type = normalize_type(f"{attr.get('type','')} {attr.get('sub_type','')}".strip())

            # dimension normalization (attributes first)
            nd = normalize_dimension(attr.get("dimension"))
            p1_mm = nd["primary_mm"]; p2_mm = nd["secondary_mm"]
            disp  = nd["display"];    amb   = nd["ambiguous"]

            # fallback: try to infer from description if we didn't get size
            if p1_mm is None and (description or ""):
                _p1, _p2, _disp, _amb = try_infer_size_from_text(description)
                if _p1 is not None:
                    p1_mm = _p1; p2_mm = _p2; disp = _disp or disp; amb = amb and _amb

            # ----- HARD GATES -----
            # If both q_type and q_size provided → both must match
            # If only one provided → only that one must match
            type_ok = True
            if q_type:
                type_ok = (type_similarity(a_type, q_type) >= 0.35)

            dim_ok = True
            if q_p1 is not None:
                if p1_mm is None:
                    dim_ok = False
                else:
                    if q_p2 is None:
                        dim_ok = abs(p1_mm - q_p1) <= max(tol, 2.0)
                    else:
                        dim_ok = (p2_mm is not None) and (
                            abs(p1_mm - q_p1) + abs(p2_mm - q_p2) <= 2*max(tol, 2.0) or
                            abs(p1_mm - q_p2) + abs(p2_mm - q_p1) <= 2*max(tol, 2.0)
                        )

            if q_type and q_p1 is not None:
                if not (type_ok and dim_ok): 
                    continue
            elif q_type and not type_ok:
                continue
            elif (q_p1 is not None) and not dim_ok:
                continue

            # ----- SCORE (type + dimension only) -----
            s_type = type_similarity(a_type, q_type) if q_type else 0.0
            s_dim  = dim_bucket(p1_mm, p2_mm)

            score = 120 * s_type + s_dim  # dominant signals only

            items.append({
                "sku_id": sku_id,
                "brand": brand,
                "category": category,
                "uom_code": uom_code,
                "pack_qty": float(pack_qty) if pack_qty is not None else None,
                "pack_uom": pack_uom,
                "description": description,
                "attributes": attributes,
                "canonical_key": canonical_key,
                "status": status,
                "score": float(score),
                "normalized": {
                    "type_norm": a_type,
                    "size_mm_primary": p1_mm,
                    "size_mm_secondary": p2_mm,
                    "display": disp,
                    "ambiguous": amb,
                },
            })

        items.sort(key=lambda x: (-x["score"], x["sku_id"]))
        return items[:limit]
    
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
