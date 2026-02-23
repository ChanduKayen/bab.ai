# users/user_handler.py
from datetime import datetime
from typing import Optional, Tuple

from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import User  # adjust import to your path
from database.models import UserCategory, UserStage  # adjust import to your path
from whatsapp.builder_out import whatsapp_output
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from database.models import User

async def upsert_user_basic(
    session: AsyncSession,
    *,
    sender_id: str,
    user_full_name: Optional[str],
    user_stage: Optional[UserStage] = None,
    user_identity: Optional[str] = None,
) -> User:
    """
    Idempotent 'get or create' with gentle updates.
    - Creates user if not present.
    - If present, refreshes name/stage/identity when provided (non-empty).
    - Always bumps last_action_ts.
    Returns ORM User (loaded).
    """
    # Prepare incoming values (don’t overwrite with Nones)
    stage_val = user_stage if user_stage is not None else UserStage.NEW

    insert_values = {
        "sender_id": sender_id,
        "user_full_name": user_full_name or "User",
        "user_category": UserCategory.USER,
        "user_stage": stage_val,
        "user_identity": user_identity,
        "last_action_ts": func.now(),
    }

    # Build an UPSERT that only updates non-null incoming fields.
    set_updates = {
        "last_action_ts": func.now(),
    }
    if user_full_name:
        set_updates["user_full_name"] = user_full_name
    if user_stage is not None:
        set_updates["user_stage"] = user_stage
    if user_identity:
        set_updates["user_identity"] = user_identity

    stmt = (
        pg_insert(User)
        .values(**insert_values)
        .on_conflict_do_update(
            index_elements=[User.sender_id],
            set_=set_updates,
        )
        .returning(User.id)  # lightweight returning; we’ll reselect the row
    )

    await session.execute(stmt)
    # Load full row to return a live ORM object
    row = await session.execute(select(User).where(User.sender_id == sender_id))
    user = row.scalar_one()
    return user


async def ensure_user_and_state_fields(
    session: AsyncSession,
    *,
    sender_id: str,
    user_full_name: Optional[str],
    user_stage: Optional[UserStage],
    user_identity: Optional[str] = None,
    state: dict = None,
) : 
    """
    Convenience wrapper:
    - Upserts user
    - Returns (user, state_patch) where state_patch contains fields you want in `state`
    """
    print("User Onboarding Manager :::::: ensure_user_and_state_fields::::: Started", sender_id, user_full_name, user_stage, user_identity)
    await upsert_user_basic(
            session,
            sender_id=sender_id,
            user_full_name=user_full_name,
            user_stage=user_stage,
            user_identity=user_identity,
        )
    # state_patch = {
    #     "user_full_name": user.user_full_name,
    #     "user_stage": user.user_stage.value if hasattr(user.user_stage, "value") else str(user.user_stage),
    # } 
    
def _role_from_actions(actions) -> Optional[str]:
    if not actions: return None
    for a in reversed(actions):
        if isinstance(a, str) and a.startswith("role:"):
            r = a.split(":", 1)[1].strip().lower()
            if r in ("builder", "vendor"): return r
    return None

async def get_user_role(session: AsyncSession, *, sender_id: str) -> Optional[str]:
    # Fast path: only fetch the needed column
    role = await session.scalar(
        select(User.user_category).where(User.sender_id == sender_id)
    )
    if role is None:
        return None

    # Depending on how SA Enum is configured, `role` may be a `UserCategory` or a string.
    try:
        # If it's a Python Enum (UserCategory)
        return role.name  # e.g., UserCategory.BUILDER -> "BUILDER"
    except AttributeError:
        # If it's already a DB string value (e.g., "BUILDER" or "BUILDER" as value)
        return str(role).upper()

async def set_user_role(session: AsyncSession, *, sender_id: str, role: str) -> bool:
    print("User Onboarding Manager :::::: set_user_role::::: Started", sender_id, role)
    try:
        # Fetch user
        u = await session.scalar(select(User).where(User.sender_id == sender_id))
        if not u:
            print(f"⚠️ User not found for sender_id={sender_id}")
            return False

        # ✅ Convert role string to enum safely
        try:
            user_role_enum = UserCategory[role.upper()]  # e.g. "builder" -> UserCategory.BUILDER
        except KeyError:
            print(f"❌ Invalid role provided: {role}")
            return False

        # ✅ Update user_category
        u.user_category = user_role_enum
        u.last_action_ts = datetime.utcnow()

        # Optional: keep an audit trail
        actions = list(u.user_actions or [])
        actions.append(f"set_category:{role.upper()}")
        u.user_actions = actions

        await session.flush()
        await session.commit()

        print(f"✅ User category updated to {user_role_enum} for {sender_id}")
        return True

    except Exception as e:
        await session.rollback()
        print("❌ Failed to update user category:", repr(e))
        return False

async def record_user_action(
    session: AsyncSession,
    *,
    sender_id: str,
    action: str,
) -> None:
    """
    Append an action (once per arrival) and bump score if you like.
    Safe no-op if user not found.
    """
    row = await session.execute(select(User).where(User.sender_id == sender_id))
    user = row.scalar_one_or_none()
    if not user:
        return

    actions = list(user.user_actions or [])
    actions.append(action)
    user.user_actions = actions
    user.user_score = (user.user_score or 0) + 1
    user.last_action_ts = datetime.utcnow()
    await session.flush()
