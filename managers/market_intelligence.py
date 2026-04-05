from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import SkuVendorPrice, SkuMaster


class MarketIntelligenceService:
    """
    Produces one optional market hint for a given order.
    Never raises — always fails silently.
    Only fires when signal is strong enough to be actionable.
    """

    MIN_QUOTES = 5          # minimum quotes needed to compute drift
    MIN_VENDORS = 2         # minimum distinct vendors needed
    DRIFT_THRESHOLD = 0.15  # 15% above baseline → flag
    POSITIVE_THRESHOLD = -0.10  # 10% below baseline → positive signal
    STRONG_DRIFT = 0.20     # 20% above → stronger wording

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_market_hint(
        self,
        new_items: List[Dict[str, Any]],
        region_pincode: Optional[str] = None,
    ) -> Optional[str]:
        """
        Main entry point. Returns one hint string or None.
        Runs Signal A (price drift) + Signal B (web sentiment).
        Only fires when 2+ signals align.
        """
        if not new_items:
            return None

        try:
            # Extract material names from order
            material_names = [
                it.get("material", "").lstrip("*").strip().lower()
                for it in new_items
                if it.get("material")
            ]

            # Signal A — price drift from transaction history
            signal_a = await self._check_price_drift(
                material_names, region_pincode
            )

            # Signal B — web search sentiment
            signal_b = await self._check_web_sentiment(material_names)

            # Signal C — vendor spread on recent quotes
            signal_c = await self._check_vendor_spread(material_names)

            # Only fire when 2+ signals align
            return self._synthesize(signal_a, signal_b, signal_c, material_names)

        except Exception as e:
            print(f"market_intelligence ::::: get_market_hint ::::: exception: {e}")
            return None

    async def _check_price_drift(
        self,
        material_names: List[str],
        pincode: Optional[str],
    ) -> Optional[Tuple[str, float, float]]:
        """
        Returns (material_name, drift_score, confidence) or None.
        drift_score > 0 means prices trending up.
        confidence 0-1 based on data volume.
        """
        try:
            now = datetime.now(timezone.utc)
            window_days = 60

            for material in material_names[:3]:  # check top 3 items only
                # Find matching SKUs
                sku_result = await self.session.execute(
                    select(SkuMaster.sku_id)
                    .where(SkuMaster.search_text.ilike(f"%{material}%"))
                    .where(SkuMaster.status == "active")
                    .limit(5)
                )
                sku_ids = [r[0] for r in sku_result.all()]
                if not sku_ids:
                    continue

                # Get quotes in window
                window_start = now - timedelta(days=window_days)
                stmt = (
                    select(
                        SkuVendorPrice.vendor_id,
                        SkuVendorPrice.price,
                        SkuVendorPrice.quoted_at,
                    )
                    .where(
                        and_(
                            SkuVendorPrice.sku_id.in_(sku_ids),
                            SkuVendorPrice.resolved == True,
                            SkuVendorPrice.quoted_at >= window_start,
                        )
                    )
                    .order_by(SkuVendorPrice.quoted_at.desc())
                    .limit(50)
                )
                if pincode:
                    stmt = stmt.where(SkuVendorPrice.pincode == pincode)

                result = await self.session.execute(stmt)
                quotes = result.all()

                # Confidence gate
                vendor_ids = {q.vendor_id for q in quotes}
                if len(quotes) < self.MIN_QUOTES or len(vendor_ids) < self.MIN_VENDORS:
                    continue  # not enough data

                # Compute vendor-normalized baseline (median per vendor, then average)
                vendor_prices: Dict[str, List[float]] = {}
                for q in quotes:
                    vid = str(q.vendor_id)
                    vendor_prices.setdefault(vid, []).append(float(q.price))

                vendor_medians = [
                    sorted(prices)[len(prices) // 2]
                    for prices in vendor_prices.values()
                ]
                baseline = sum(vendor_medians) / len(vendor_medians)

                # Latest price = most recent 3 quotes average
                recent_prices = [float(q.price) for q in quotes[:3]]
                current = sum(recent_prices) / len(recent_prices)

                # Drift score
                drift = (current - baseline) / baseline if baseline > 0 else 0.0
                confidence = min(len(quotes) / 10.0, 1.0)
                weighted = drift * confidence

                if abs(weighted) > 0.08:  # meaningful signal
                    return (material, weighted, confidence)

        except Exception as e:
            print(f"market_intelligence ::::: _check_price_drift ::::: {e}")

        return None

    async def _check_web_sentiment(
        self,
        material_names: List[str],
    ) -> Optional[str]:
        """
        Returns 'up', 'down', 'tight_supply', or None.
        Uses web search + LLM to detect market direction.
        """
        try:
            from langchain_openai import ChatOpenAI
            from langchain_core.messages import SystemMessage, HumanMessage
            import httpx
            from datetime import date

            # Focus on price-sensitive materials only
            PRICE_SENSITIVE = {
                "cement", "tmt", "steel", "sand", "aggregate",
                "rmc", "ready mix", "brick", "aggregate"
            }
            relevant = [
                m for m in material_names
                if any(ps in m for ps in PRICE_SENSITIVE)
            ]
            if not relevant:
                return None

            material = relevant[0]
            month_year = date.today().strftime("%B %Y")

            # Web search
            search_query = f"{material} price India {month_year} construction"
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(
                    "https://api.search.brave.com/res/v1/web/search",
                    params={"q": search_query, "count": 5},
                    headers={"Accept": "application/json",
                             "Accept-Encoding": "gzip",
                             "X-Subscription-Token": os.getenv("BRAVE_SEARCH_API_KEY", "")},
                )
                if resp.status_code != 200:
                    return None
                results = resp.json().get("web", {}).get("results", [])

            if not results:
                return None

            # Summarise search snippets
            snippets = " | ".join([
                r.get("description", "")
                for r in results[:4]
                if r.get("description")
            ])

            if not snippets.strip():
                return None

            llm = ChatOpenAI(
                model="gpt-4o-mini",
                temperature=0,
                openai_api_key=os.getenv("OPENAI_API_KEY"),
            )

            resp = await llm.ainvoke([
                SystemMessage(content=(
                    "You are analyzing construction material market news for Indian builders. "
                    "Given search snippets, classify the current market signal as exactly one of: "
                    "UP (prices rising), DOWN (prices falling), TIGHT (supply shortage), STABLE, SKIP. "
                    "Reply with ONLY that single word. Nothing else."
                )),
                HumanMessage(content=f"Material: {material}\nSnippets: {snippets}"),
            ])

            signal = (resp.content or "").strip().upper()
            if signal in ("UP", "DOWN", "TIGHT"):
                return signal.lower()

        except Exception as e:
            print(f"market_intelligence ::::: _check_web_sentiment ::::: {e}")

        return None

    async def _check_vendor_spread(
        self,
        material_names: List[str],
    ) -> Optional[float]:
        """
        Returns spread ratio (max/min - 1) if unusually wide, else None.
        Checks recent quotes for these materials across vendors.
        """
        try:
            now = datetime.now(timezone.utc)
            window_start = now - timedelta(days=14)  # last 2 weeks

            for material in material_names[:3]:
                sku_result = await self.session.execute(
                    select(SkuMaster.sku_id)
                    .where(SkuMaster.search_text.ilike(f"%{material}%"))
                    .where(SkuMaster.status == "active")
                    .limit(5)
                )
                sku_ids = [r[0] for r in sku_result.all()]
                if not sku_ids:
                    continue

                result = await self.session.execute(
                    select(SkuVendorPrice.price, SkuVendorPrice.vendor_id)
                    .where(
                        and_(
                            SkuVendorPrice.sku_id.in_(sku_ids),
                            SkuVendorPrice.resolved == True,
                            SkuVendorPrice.quoted_at >= window_start,
                        )
                    )
                )
                rows = result.all()
                vendor_ids = {str(r.vendor_id) for r in rows}

                if len(rows) < 3 or len(vendor_ids) < 2:
                    continue

                prices = [float(r.price) for r in rows]
                spread = (max(prices) - min(prices)) / min(prices) if min(prices) > 0 else 0

                if spread > 0.15:  # >15% spread between cheapest and most expensive
                    return spread

        except Exception as e:
            print(f"market_intelligence ::::: _check_vendor_spread ::::: {e}")

        return None

    def _synthesize(
        self,
        signal_a: Optional[Tuple],
        signal_b: Optional[str],
        signal_c: Optional[float],
        material_names: List[str],
    ) -> Optional[str]:
        """
        Combine signals. Only fire when 2+ align.
        Returns one short hint or None.
        """
        material_label = material_names[0].title() if material_names else "materials"

        signals_up = 0
        signals_down = 0
        signals_tight = 0
        signals_spread = 0

        if signal_a:
            _, weighted, _ = signal_a
            material_label = signal_a[0].title()
            if weighted > self.DRIFT_THRESHOLD:
                signals_up += 1
            elif weighted < self.POSITIVE_THRESHOLD:
                signals_down += 1

        if signal_b == "up":
            signals_up += 1
        elif signal_b == "down":
            signals_down += 1
        elif signal_b == "tight":
            signals_tight += 1

        if signal_c and signal_c > 0.15:
            signals_spread += 1

        # Strong drift — signal A alone is enough if weighted is very high
        if signal_a:
            _, weighted, confidence = signal_a
            if weighted > self.STRONG_DRIFT and confidence > 0.7:
                return f"{material_label} prices are trending higher than usual — worth comparing vendors carefully."

        # Two signals pointing up
        if signals_up >= 2:
            return f"{material_label} prices are moving up right now — locking in quotes soon makes sense."

        # Supply tight + any upward signal
        if signals_tight >= 1 and signals_up >= 1:
            return f"{material_label} supply looks tight in the market — good to have backup vendors."

        # Wide vendor spread
        if signals_spread >= 1 and signals_up >= 1:
            return f"Vendors are quoting very different prices on {material_label} right now — compare carefully."

        # Prices looking good
        if signals_down >= 2:
            return f"{material_label} prices look favorable right now."

        return None
