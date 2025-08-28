# database/credit_crud.py  (patched parts only)
from datetime import datetime
from typing import Optional, Dict, Any, List
from sqlalchemy import select, update, inspect
from sqlalchemy.ext.asyncio import AsyncSession 
from sqlalchemy.orm import selectinload, joinedload
 
from database.models import User, CreditProfile, CreditTransaction, PartnerStatusHistory, CreditStatus

class CreditCRUD:
    def __init__(self, session: AsyncSession):
        self.session = session

    # --------- helpers ----------
    async def _get_or_create_user_by_sender(self, sender_id: str, *, full_name: str | None = None) -> User:
        res = await self.session.execute(select(User).where(User.sender_id == sender_id))
        user = res.scalar_one_or_none()
        if user:
            return user
        user = User(sender_id=sender_id, user_full_name=(full_name or "Bab.ai User").strip())
        self.session.add(user)
        await self.session.commit()
        await self.session.refresh(user)
        return user

    async def _get_user_by_sender(self, sender_id: str) -> Optional[User]:
        res = await self.session.execute(select(User).where(User.sender_id == sender_id))
        return res.scalar_one_or_none()

    # --------- reads (return dicts) ----------
    async def get_profile_by_sender(self, sender_id: str) -> Optional[Dict[str, Any]]:
        """
        Eager-load User and map to a plain dict INSIDE the session.
        """
        stmt = (
            select(CreditProfile)
            .join(User, CreditProfile.user_id == User.id)
            .options(joinedload(CreditProfile.user))  # ensures profile.user is populated
            .where(User.sender_id == sender_id)
            .limit(1)
        )
        res = await self.session.execute(stmt)
        profile: Optional[CreditProfile] = res.scalars().first()
        if not profile:
            return None

        user = profile.user  # safe: eagerly loaded above
        return {
    "profile_id": str(profile.id),
    "user_id": str(profile.user_id),
    "sender_id": user.sender_id if user else sender_id,
    "status": (profile.status.value if isinstance(profile.status, CreditStatus) else (profile.status or "")).lower(),
    "limit": float(profile.limit or 0.0),
    "used": float(profile.used or 0.0),
    "trust_score": float(profile.trust_score or 0.0),
    "trust_score_band": getattr(profile, "trust_score_band", None),
    "trust_score_version": getattr(profile, "trust_score_version", None),
    "trust_score_computed_at": (
        profile.trust_score_computed_at.isoformat()
        if getattr(profile, "trust_score_computed_at", None) else None
    ),
    "nbfc_partner": getattr(profile, "nbfc_partner", None),
    "created_at": profile.created_at.isoformat() if getattr(profile, "created_at", None) else None,
    "updated_at": profile.updated_at.isoformat() if getattr(profile, "updated_at", None) else None,
}

    # Legacy alias (keep signature honest; it's sender_id, not user_id)
    async def _get_profile_row(self, sender_id: str) -> Optional[Dict[str, Any]]:
        return await self.get_profile_by_sender(sender_id)

    # --------- writes ----------
    async def create_profile(self, sender_id: str, full_name: str | None = None, **kwargs) -> Dict[str, Any]:
        user = await self._get_or_create_user_by_sender(sender_id, full_name=full_name)
        obj = CreditProfile(user_id=user.id, **kwargs)
        self.session.add(obj)
        await self.session.commit()
        # normalize return via the dict reader:
        return await self.get_profile_by_sender(sender_id)

    async def update_profile(self, sender_id: str, **kwargs) -> int:
        valid_cols = {c.name for c in inspect(CreditProfile).columns}
        values = {k: v for k, v in kwargs.items() if k in valid_cols}
        if not values:
            return 0
        user = await self._get_user_by_sender(sender_id)
        if not user:
            return 0
        result = await self.session.execute(
            update(CreditProfile)
            .where(CreditProfile.user_id == user.id)
            .values(**values)
            .execution_options(synchronize_session="fetch")
        )
        await self.session.commit()
        return int(result.rowcount or 0)

    async def upsert_profile(self, sender_id: str, full_name: str | None = None, **kwargs) -> Dict[str, Any]:
        """
        Update if exists; otherwise insert. Returns dict snapshot.
        """
        valid_cols = {c.name for c in inspect(CreditProfile).columns}
        values = {k: v for k, v in kwargs.items() if k in valid_cols}

        user = await self._get_or_create_user_by_sender(sender_id, full_name=full_name)

        upd = await self.session.execute(
            update(CreditProfile)
            .where(CreditProfile.user_id == user.id)
            .values(**values)
            .returning(CreditProfile.id)
            .execution_options(synchronize_session="fetch")
        )
        cid = upd.scalar_one_or_none()
        if not cid:
            obj = CreditProfile(user_id=user.id, **values)
            self.session.add(obj)
            await self.session.commit()
        else:
            await self.session.commit()

        # Always return normalized dict
        return await self.get_profile_by_sender(sender_id)

    # --------- transactions (return dicts) ----------
    async def log_transaction(
        self,
        credit_profile_id: str,
        amount: float,
        vendor_id: Optional[str] = None,
        description: Optional[str] = None,
        status: str = "pending",
    ) -> Dict[str, Any]:
        txn = CreditTransaction(
            credit_profile_id=credit_profile_id,
            vendor_id=vendor_id,
            amount=amount,
            description=description,
            status=status,
        )
        self.session.add(txn)
        await self.session.commit()
        await self.session.refresh(txn)
        return {
            "id": str(txn.id),
            "credit_profile_id": str(txn.credit_profile_id),
            "vendor_id": txn.vendor_id,
            "amount": float(txn.amount or 0.0),
            "description": txn.description,
            "status": txn.status,
            "created_at": txn.created_at.isoformat() if getattr(txn, "created_at", None) else None,
        }

    async def get_transactions(self, credit_profile_id: str) -> List[Dict[str, Any]]:
        res = await self.session.execute(
            select(CreditTransaction).where(CreditTransaction.credit_profile_id == credit_profile_id)
        )
        txns = res.scalars().all()
        out: List[Dict[str, Any]] = []
        for t in txns:
            out.append({
                "id": str(t.id),
                "credit_profile_id": str(t.credit_profile_id),
                "vendor_id": t.vendor_id,
                "amount": float(t.amount or 0.0),
                "description": t.description,
                "status": t.status,
                "created_at": t.created_at.isoformat() if getattr(t, "created_at", None) else None,
            })
        return out

    # --------- partner snapshots ----------
    async def upsert_partner_snapshot(
        self,
        sender_id: str,
        *,
        status: str,
        score: Optional[float] = None,
        limit: Optional[float] = None,
        ref_id: Optional[str] = None,
        partner: str = "NBFC_X",
    ) -> None:
        await self.update_profile(
            sender_id,
            partner_status=status,
            partner_score=score,
            partner_limit_suggested=limit,
            partner_ref_id=ref_id,
            partner_updated_at=datetime.utcnow(),
        )
        self.session.add(
            PartnerStatusHistory(
                sender_id=sender_id,
                partner=partner,
                status=status,
                score=score,
                limit=limit,
                ref_id=ref_id,
                recorded_at=datetime.utcnow(),
            )
        )
        await self.session.commit()

    async def set_final_limit(self, sender_id: str, limit: float, used: Optional[float] = None) -> int:
        values: Dict[str, Any] = {"limit": limit}
        if used is not None:
            values["used"] = used
        return await self.update_profile(sender_id, **values)
