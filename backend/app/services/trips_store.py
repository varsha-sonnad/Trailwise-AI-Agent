# trips_store.py: the MongoDB reads and writes for saved trips and explore history (signed-in users only).
# Every function takes the owner_id (the Keycloak token's "sub", given by the route, never by the client) and
# puts it in EVERY query, so one user can never read, list or delete another user's records.
# All calls are async (PyMongo's AsyncMongoClient). Nothing here logs message text or the connection string.

import logging
import re
from datetime import datetime, timezone
from uuid import uuid4

logger = logging.getLogger(__name__)

# Collection names.
TRIPS = "trips"
EXPLORE = "explore_history"
# Per-user limits.
MAX_TRIPS_PER_USER = 100
MAX_EXPLORE_PER_USER = 50
# Ids are random 32-character hex strings (uuid4), so a malformed id can be rejected before touching the database.
_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


class TripLimitError(Exception):
    """The user already has the maximum number of saved trips."""


def valid_id(value: str) -> bool:
    """True if the text looks like an id this module created."""
    return bool(_ID_PATTERN.match(value))


def _now() -> datetime:
    """The current time in UTC."""
    return datetime.now(timezone.utc)


async def ensure_indexes(db) -> None:
    """Create the (owner_id, created_at) index on both collections. A failure logs the index NAME only and carries on."""
    for collection, name in ((TRIPS, "trips_owner_created"), (EXPLORE, "explore_owner_created")):
        try:
            await db[collection].create_index([("owner_id", 1), ("created_at", -1)], name=name)
        except Exception:  # noqa: BLE001 - never stop startup; the error text is deliberately not logged
            logger.warning("could not create index %s", name)


def _trip_doc_to_dict(doc: dict) -> dict:
    """A stored trip as a plain dict for the response: _id becomes id and owner_id is dropped."""
    return {
        "id": doc["_id"],
        "title": doc["title"],
        "places": doc.get("places", []),
        "summary": doc.get("summary", ""),
        "reply": doc.get("reply", ""),
        "created_at": doc["created_at"],
    }


async def create_trip(db, owner_id: str, data: dict) -> dict:
    """Save a trip for this owner. Raises TripLimitError at 100 trips (counting stops at the limit, so it stays cheap)."""
    existing = await db[TRIPS].count_documents({"owner_id": owner_id}, limit=MAX_TRIPS_PER_USER)
    if existing >= MAX_TRIPS_PER_USER:
        raise TripLimitError
    doc = {"_id": uuid4().hex, "owner_id": owner_id, "created_at": _now(), **data}
    await db[TRIPS].insert_one(doc)
    return _trip_doc_to_dict(doc)


async def list_trips(db, owner_id: str, page: int, page_size: int) -> list[dict]:
    """One page of this owner's trips, newest first (page starts at 1)."""
    cursor = (
        db[TRIPS]
        .find({"owner_id": owner_id})
        .sort([("created_at", -1), ("_id", -1)])
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    return [_trip_doc_to_dict(doc) async for doc in cursor]


async def get_trip(db, owner_id: str, trip_id: str) -> dict | None:
    """One trip, only if it belongs to this owner (otherwise None, exactly as if it did not exist)."""
    if not valid_id(trip_id):
        return None
    doc = await db[TRIPS].find_one({"_id": trip_id, "owner_id": owner_id})
    return _trip_doc_to_dict(doc) if doc else None


async def delete_trip(db, owner_id: str, trip_id: str) -> bool:
    """Delete one trip, only if it belongs to this owner. True if something was deleted."""
    if not valid_id(trip_id):
        return False
    result = await db[TRIPS].delete_one({"_id": trip_id, "owner_id": owner_id})
    return result.deleted_count == 1


async def add_explore(db, owner_id: str, place: str) -> None:
    """Record one explored place, then drop this owner's entries beyond the latest 50."""
    await db[EXPLORE].insert_one({"_id": uuid4().hex, "owner_id": owner_id, "place": place, "created_at": _now()})
    # Find the entries past the newest 50 (only their ids) and delete them, still limited to this owner.
    old = (
        db[EXPLORE]
        .find({"owner_id": owner_id}, {"_id": 1})
        .sort([("created_at", -1), ("_id", -1)])
        .skip(MAX_EXPLORE_PER_USER)
    )
    old_ids = [doc["_id"] async for doc in old]
    if old_ids:
        await db[EXPLORE].delete_many({"owner_id": owner_id, "_id": {"$in": old_ids}})


async def list_explore(db, owner_id: str) -> list[dict]:
    """This owner's explore history, newest first (at most 50)."""
    cursor = (
        db[EXPLORE]
        .find({"owner_id": owner_id})
        .sort([("created_at", -1), ("_id", -1)])
        .limit(MAX_EXPLORE_PER_USER)
    )
    return [{"id": d["_id"], "place": d["place"], "created_at": d["created_at"]} async for d in cursor]


async def delete_explore(db, owner_id: str) -> int:
    """Delete all of this owner's explore history. Returns how many entries were removed."""
    result = await db[EXPLORE].delete_many({"owner_id": owner_id})
    return result.deleted_count


async def delete_all_for_owner(db, owner_id: str) -> dict:
    """Delete this owner's trips and explore history (used by DELETE /me/data). Returns the two counts."""
    trips = await db[TRIPS].delete_many({"owner_id": owner_id})
    explore = await delete_explore(db, owner_id)
    return {"trips_deleted": trips.deleted_count, "explore_deleted": explore}
