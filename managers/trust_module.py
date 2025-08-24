from datetime import datetime
import hashlib

class BabaiTrustModule:
    """
    Deterministic dummy trust score:
    - 0–100 based on a stable hash of sender_id
    - Bands: LOW <50, MEDIUM 50–74, HIGH 75+
    """
    VERSION = "dummy.v1"

    async def compute(self, sender_id: str):
        print("BabaiTrustModule:::: computing trust score for:", sender_id)
        h = hashlib.sha256((sender_id or "").encode("utf-8")).hexdigest()
        # Take first 8 hex chars -> int -> normalize to 0..100
        raw = int(h[:8], 16) % 101
        band = "LOW" if raw < 50 else ("MEDIUM" if raw < 75 else "HIGH")
        return {
            "score": float(raw),
            "band": band,
            "version": self.VERSION,
            "computed_at": datetime.utcnow(),  # real datetime
        }