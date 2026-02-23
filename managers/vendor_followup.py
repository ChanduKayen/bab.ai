from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional, Sequence

NUDGE_SCHEDULE: Sequence[timedelta] = (
    timedelta(hours=1),
    timedelta(hours=3),
    timedelta(hours=10),
)


def compute_next_due(invited_at: datetime, stage: int) -> Optional[datetime]:
    """
    Return the absolute due time for the next nudge based on the
    invitation timestamp and how many nudges have already been sent.
    `stage` is the number of nudges delivered so far.
    """
    if stage < len(NUDGE_SCHEDULE):
        return invited_at + NUDGE_SCHEDULE[stage]
    return None
