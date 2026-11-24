# preferences.py (core): MongoDB access for saved preferences. One document per Keycloak "sub" in the
# "Preferences" collection. The caller passes the sub taken from the verified token; every query is filtered on it,
# so one user can never read or write another's document.

import logging
from datetime import datetime, timezone

from app.models.preferences import Preferences

logger = logging.getLogger(__name__)

COLLECTION = "Preferences"


async def ensure_indexes(db) -> None:
    """Create the unique index on sub (safe to repeat). A failure is only logged, never raised."""
    try:
        await db[COLLECTION].create_index("sub", unique=True)
    except Exception as exc:  # noqa: BLE001 - startup must not fail; log only the error type (no URI or details)
        logger.warning("Could not create the Preferences index (%s)", type(exc).__name__)


async def load(db, sub: str) -> Preferences:
    """This user's saved preferences, or empty defaults if none are saved."""
    doc = await db[COLLECTION].find_one({"sub": sub}, {"_id": 0, "sub": 0, "updated_at": 0})
    return Preferences(**doc) if doc else Preferences()


async def save(db, sub: str, prefs: Preferences) -> None:
    """Replace this user's preferences (create the document if it is missing)."""
    doc = {**prefs.model_dump(), "updated_at": datetime.now(timezone.utc)}
    await db[COLLECTION].update_one({"sub": sub}, {"$set": doc}, upsert=True)
