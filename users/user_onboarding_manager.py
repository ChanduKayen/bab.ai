# user_onboarding_manager.py
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import json
#import redis
import sqlite3
ONBOARDING_STAGES = ["new", "curious", "identified", "engaged", "trusted"]
#r = redis.Redis(host="localhost", port=6379, db=0, decode_responses=True)

USER_ACTION_SCORES = {
    "selected_identity": 2,
    "shared_project_info": 3,
    "shared_site_photo": 2,
    "asked_for_material_quote": 2,
    "used_credit_feature": 2,
    "shared_supervisor_contact": 3,
    "clicked_cta_button": 1,
    "replied_multiple_times": 1,
}
DB_PATH = "babai_users.db"

# Ensure DB and table exists
def init_user_db():
    print("user_orboarding_manager:::::: init_user_db - creating database if not exists") 
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            sender_id TEXT PRIMARY KEY,
            user_full_name TEXT,
            user_identity TEXT DEFAULT NULL,
            credit_offer_pending BOOLEAN DEFAULT 1,
            user_actions TEXT DEFAULT '[]',
            last_action_ts TEXT,
            user_score INTEGER DEFAULT 0,
            user_stage TEXT DEFAULT 'new'
        )
    ''')
    conn.commit()
    conn.close()

init_user_db()

def user_status(sender_id: str, user_full_name: Optional[str] = None) -> Dict:
    print(f"user_orboarding_manager:::::: user_status - sender_id: {sender_id}, user_full_name: {user_full_name}")
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("SELECT * FROM users WHERE sender_id = ?", (sender_id,))
    row = cur.fetchone()

    if row:
        print(f"user_orboarding_manager:::::: user_status - found existing user record for sender_id: {sender_id}")
        keys = [d[0] for d in cur.description]
        user = dict(zip(keys, row))
        user["user_actions"] = json.loads(user.get("user_actions", "[]"))
    else:
        print(f"user_orboarding_manager:::::: user_status - creating new user record for sender_id: {sender_id}")
        user = {
            "sender_id": sender_id,
            "user_full_name": user_full_name,
            "user_identity": None,
            "credit_offer_pending": True,
            "user_actions": [],
            "last_action_ts": datetime.utcnow().isoformat(),
        }

        # Compute score and stage for new user
        score = compute_user_score(user["user_actions"])
        stage = determine_user_stage(score, False)

        user.update({
            "user_score": score,
            "user_stage": stage
        })

        # Insert into DB
        cur.execute('''
            INSERT INTO users (sender_id, user_full_name, user_identity, credit_offer_pending,
                               user_actions, last_action_ts, user_score, user_stage)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            user["sender_id"],
            user["user_full_name"],
            user["user_identity"],
            int(user["credit_offer_pending"]),
            json.dumps(user["user_actions"]),
            user["last_action_ts"],
            user["user_score"],
            user["user_stage"]
        ))
        conn.commit()

    if user_full_name:
        user["user_full_name"] = user_full_name
    print(f"user_orboarding_manager:::::: user_status - returning user record: {user}")
    conn.close()
    return user

def update_user_record(user: Dict) -> None:
    print(f"user_orboarding_manager:::::: update_user_record - updating user record for sender_id: {user.get('sender_id')}")
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''
        INSERT INTO users (sender_id, user_full_name, user_identity, credit_offer_pending,
                           user_actions, last_action_ts, user_score, user_stage)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(sender_id) DO UPDATE SET
            user_full_name=excluded.user_full_name,
            user_identity=excluded.user_identity,
            credit_offer_pending=excluded.credit_offer_pending,
            user_actions=excluded.user_actions,
            last_action_ts=excluded.last_action_ts,
            user_score=excluded.user_score,
            user_stage=excluded.user_stage
    ''', (
        user.get("sender_id"),
        user.get("user_full_name"),
        user.get("user_identity"),
        int(user.get("credit_offer_pending", True)),
        json.dumps(user.get("user_actions", [])),
        user.get("last_action_ts"),
        user.get("user_score"),
        user.get("user_stage"),
    ))
    conn.commit()
    conn.close()

def record_user_action(sender_id: str, action: str):
    print(f"user_orboarding_manager:::::: record_user_action - sender_id: {sender_id}, action: {action}")
    user = user_status(sender_id)
    user["user_actions"].append(action)
    user["last_action_ts"] = datetime.utcnow().isoformat()
    user["user_score"] = compute_user_score(user["user_actions"])
    user["user_stage"] = determine_user_stage(user["user_score"], bool(user.get("user_identity")))
    update_user_record(user)

def compute_user_score(user_actions: List[str]) -> int:
    return sum(USER_ACTION_SCORES.get(action, 0) for action in user_actions)

def determine_user_stage(score: int, identity_selected: bool) -> str:
    if score == 0:
        return "new"
    elif not identity_selected:
        return "curious"
    elif score < 4:
        return "identified"
    elif score < 7:
        return "engaged"
    else:
        return "trusted"

def onboarding_reminder(user_stage: str, last_action_ts: str) -> Optional[str]:
    if user_stage != "curious":
        return None
    try:
        last_time = datetime.fromisoformat(last_action_ts)
        if datetime.utcnow() - last_time > timedelta(days=3):
            return ("👋 Still exploring? Let me help better — are you a builder, site manager, or supplier?")
    except Exception:
        pass
    return None

