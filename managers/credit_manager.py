from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, Any, Optional
import asyncio
import random
from managers.trust_module import BabaiTrustModule
import managers.credit_manager 
from database.credit_crud import CreditCRUD
from database.models import CreditStatus  # Enum with APPROVED/PENDING/REJECTED etc.

FRESHNESS_DEFAULT_MIN = 60  # Default freshness window in minutes

# ──────────────────────────────────────────────────────────────────────────────
# Value object (internal), but callers always get dicts.
# ──────────────────────────────────────────────────────────────────────────────
class DecisionSnapshot:
    __slots__ = ("status", "limit", "used", "bts_score", "partner_score", "composite_score", "reasons")

    def __init__(
        self,
        status: str,
        limit: float,
        used: float,
        bts_score: float,
        partner_score: float,
        composite_score: float,
        reasons: Dict[str, Any],
    ):
        self.status = status
        self.limit = float(limit or 0.0)
        self.used = float(used or 0.0)
        self.bts_score = float(bts_score or 0.0)
        self.partner_score = float(partner_score or 0.0)
        self.composite_score = float(composite_score or 0.0)
        self.reasons = reasons or {}

class CreditManager:
    def __init__(self, session= None):
        # One CRUD bound to the provided AsyncSession
        self.crud = CreditCRUD(session)
        # Optional dependencies you can inject later:
        self.partner_client = None # e.g., NBFCClient()

    # ──────────────────────────────────────────────────────────────────────
    # Public polling API: always returns a dict snapshot
    # ──────────────────────────────────────────────────────────────────────
    async def poll_until_approved(
        self,
        sender_id: str,
        max_wait_seconds: int = 300,    # 5 minutes
        interval_seconds: int = 20,     # poll ~ every 20s
        jitter_seconds: int = 5,
    ) -> Dict[str, Any]:
        """
        Poll partner/DB until status == 'approved' or timeout.
        Always returns the latest status snapshot as a dict.
        """
        print("Credit Agent:::: polling until approved for sender_id:", sender_id)
        waited = 0
        while waited < max_wait_seconds:
            status = await self.check_status(sender_id)
            if status and status.get("status") == "approved":
                return status

            sleep_for = interval_seconds + random.randint(0, jitter_seconds)
            await asyncio.sleep(sleep_for)
            waited += sleep_for
            print(f"Credit Agent:::: still waiting for approval... {waited}/{max_wait_seconds} seconds")

        print("Credit Agent:::: polling timed out, returning last known status")
        return await self.check_status(sender_id) or {"status": "pending", "sender_id": sender_id}

    # ──────────────────────────────────────────────────────────────────────
    # Profile snapshot helpers (always return plain dicts)
    # ──────────────────────────────────────────────────────────────────────
    async def get_profile(self, sender_id: str) -> Dict[str, Any]:
        """
        Return a dict snapshot from CRUD. If not found, return 'not_found'.
        """
        data = await self.crud.get_profile_by_sender(sender_id)
        if not data:
            return {"status": "not_found", "sender_id": sender_id}
        # data is already a dict, do NOT re-map as ORM
        return data

    async def onboard_user(self, sender_id: str, aadhaar: str, pan: str, gst: str) -> Dict[str, Any]:
        """
        Ensure a profile exists/upserts KYC identifiers; returns dict snapshot.
        """
        existing = await self.get_profile(sender_id)
        if existing.get("status") == "not_found":
            # create new profile with PENDING (or APPROVED in dummy flow)
            created = await self.crud.create_profile(
                sender_id=sender_id,
                aadhaar=aadhaar,
                pan=pan,
                gst=gst,
                status=CreditStatus.PENDING,
            )
        else:
            # upsert identifiers on existing
            created = await self.crud.upsert_profile(
                sender_id,
                aadhaar=aadhaar,
                pan=pan,
                gst=gst,
            )
        # Map to dict (some CRUDs already return dicts—no harm)
        return await self.get_profile(sender_id)

    async def approve_credit(self, sender_id: str, limit: float, trust_score: float) -> Dict[str, Any]:
        await self.crud.upsert_profile(
            sender_id,
            status=CreditStatus.APPROVED,
            limit=limit,
            trust_score=trust_score,
        )
        return await self.get_profile(sender_id)

    async def record_usage(self, sender_id: str, vendor_id: str, amount: float, description: str) -> Dict[str, Any]:
        """
        Deduct usage from available credit and log a transaction.
        Returns the created transaction snapshot.
        """
        profile = await self.get_profile(sender_id)  # dict
        if (profile.get("status") or "").lower() != "approved":
            raise ValueError("Credit not approved")

        limit = float(profile.get("limit") or 0.0)
        used = float(profile.get("used") or 0.0)
        if (limit - used) < float(amount or 0.0):
            raise ValueError("Insufficient limit")

        new_used = used + float(amount)
        await self.crud.upsert_profile(sender_id, used=new_used)
        # Prefer using profile_id if your CRUD needs it; we kept both.
        profile_id = profile.get("profile_id")
        return await self.crud.log_transaction(profile_id, amount, vendor_id, description)

    async def get_transactions(self, sender_id: str):
        profile = await self.get_profile(sender_id)
        if profile.get("status") == "not_found":
            return []
        return await self.crud.get_transactions(profile["profile_id"])

    # ──────────────────────────────────────────────────────────────────────
    # Decisioning (dummy today): callers receive dicts
    # ──────────────────────────────────────────────────────────────────────
    async def check_status(self, sender_id: str) -> Dict[str, Any]:
        print("Credit Manager:::: checking status for sender_id:", sender_id)
        snap = await self.compute_decision(sender_id) 
        return {
            "status": snap.status,
            "limit": snap.limit,
            "used": snap.used,
            "trust_score": snap.bts_score,
            "partner_score": snap.partner_score,
            "composite_score": snap.composite_score,
            "reasons": snap.reasons,
            "sender_id": sender_id,
        }

    async def compute_decision(self, sender_id: str) -> DecisionSnapshot:
        """
        Dummy decision + persist to DB.
        Uses dict snapshots only (no ORM leakage).
        """
        print("Credit Manager:::: compute_decision:::: Computing decision for sender_id:", sender_id)

        # 1) Ensure trust is fresh (dict: {score, band, version})
        #trust = await self.ensure_trust_score_fresh(sender_id)

        # 2) Partner score (dummy for now)
        partner= "Syndicate Bank"
        partner_score = 80.0

        # 3) Heuristic decision (dummy numbers)
        limit = 500_000.0  # ₹5 lakh
        # Keep existing used to avoid clobbering usage
        existing = await self.crud.get_profile_by_sender(sender_id) or {}
        used = float(existing.get("used") or 0.0)
        trust_score = 75.0  # Dummy trust score
        composite = (trust_score * 0.6) + (partner_score * 0.4)
        # 4) Persist decision to CreditProfile (whitelisted by CRUD -> only real columns update)
        await self.crud.upsert_profile(
            sender_id,
            status=CreditStatus.APPROVED,    # enum is fine; CRUD whitelists to model columns
            limit=limit,
            used=used,
            trust_score=composite,
            nbfc_partner=partner,     # make sure this column exists in your model
            # optionally: decision_score=composite if you have such a column
        )

        # 5) Build the in-memory snapshot returned to callers
        
        return DecisionSnapshot(
            status="approved",
            limit=limit,
            used=used,
            bts_score=float(trust_score),
            partner_score=float(partner_score),
            composite_score=float(composite),
            reasons={"positives": ["Approval for testing"], "risks": []},
        )


    # ──────────────────────────────────────────────────────────────────────
    # Trust score freshness (safe, no ORM leakage)
    # ──────────────────────────────────────────────────────────────────────
    async def ensure_trust_score_fresh(
    self, sender_id: str, max_age_minutes: int = FRESHNESS_DEFAULT_MIN
) -> Dict[str, Any]:
        """
        Ensure the sender has a recent Trust Score snapshot on CreditProfile.
        Recompute if missing or stale. Returns a dict.
        Expects CRUD.get_profile_by_sender() to include trust fields in the dict.
        """
        print("Credit Manager:::: ensure_trust_score_fresh for sender_id:", sender_id)
        profile = await self.crud.get_profile_by_sender(sender_id)
        if not profile:
            return {"score": 0.0, "band": "LOW"}

        # Expect these optional keys to be present in the dict snapshot; if not,
        # add them in your CRUD.get_profile_by_sender (see note below).
        score = profile.get("trust_score")
        band = profile.get("trust_score_band")
        version = profile.get("trust_score_version")
        computed_at_iso = profile.get("trust_score_computed_at")

        computed_at = None
        if computed_at_iso:
            # stored as ISO string in dict; parse back to dt for staleness check
            try:
                computed_at = datetime.fromisoformat(computed_at_iso)
            except ValueError:
                computed_at = None

        stale = True
        if computed_at:
            age = datetime.utcnow() - computed_at
            stale = age > timedelta(minutes=max_age_minutes)

        if score is None or stale:
            if not self.trust:
                # No recompute available; return what we have (or sensible defaults)
                return {
                    "score": float(score or 0.0),
                    "band": band or "MEDIUM",
                    "version": version,
                }
            babai_trust = BabaiTrustModule()
            # Recompute from TrustSignals
            result = await babai_trust.compute(sender_id)  
            print("Credit Manager:::: recomputed babai trust score:", result)
            await self.crud.upsert_profile(
                sender_id,
                trust_score=result["score"],
                trust_score_version=result.get("version"),
                trust_score_computed_at=(result.get("computed_at").isoformat()
                                        if isinstance(result.get("computed_at"), datetime)
                                        else result.get("computed_at")),
                trust_score_band=result.get("band"),
            )
            return {
                "score": result["score"],
                "band": result.get("band"),
                "version": result.get("version"),
            }

        # Already fresh
        return {
            "score": float(score or 0.0),
            "band": band or "MEDIUM",
            "version": version,
        }
    # ──────────────────────────────────────────────────────────────────────
    # Partner status refresh (stub)
    # ──────────────────────────────────────────────────────────────────────
    async def refresh_partner_status(self, sender_id: str) -> Dict[str, Any]:
        """
        Call NBFC partner → persist status/limit if changed → return snapshot via check_status().
        """
        # if self.partner_client:
        #     partner_status = await self.partner_client.check(sender_id)
        #     if partner_status.changed:
        #         await self.crud.upsert_profile(sender_id, status=..., limit=..., used=...)
        return await self.check_status(sender_id)

    # ──────────────────────────────────────────────────────────────────────
    # KYC submit (dummy)
    # ──────────────────────────────────────────────────────────────────────
    async def submit_kyc(self, sender_id: str, profile: dict, full_name: Optional[str] = None):
        """
        Stubbed KYC submission for flow testing.
        Persist KYC fields and set PENDING (or APPROVED in dummy).
        """
        print(f"[DUMMY] Submitting KYC for sender_id={sender_id} with profile={profile}")
        await self.crud.upsert_profile(
            sender_id,
            aadhaar=profile.get("aadhaar"),
            pan=profile.get("pan"),
            gst=profile.get("gst"),
            status=CreditStatus.PENDING,  # dummy flow shortcut
        )
        return await self.get_profile(sender_id)
