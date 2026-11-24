# me.py: the /me routes. GET /me returns who the signed-in user is, read from the verified Keycloak token.
# GET and PUT /me/preferences read and save that user's preferences in MongoDB. The user always comes from the
# verified token, never from the request body. Guests and anonymous callers get a 401.

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.core import preferences as prefs_store
from app.core.auth import Principal, require_user
from app.core.mongo import get_db
from app.models.preferences import Preferences

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/me")
async def me(user: Principal = Depends(require_user)) -> dict:
    """Return the signed-in user's Keycloak id, email and name."""
    return {"id": user.id, "email": user.email, "name": user.name}


@router.get("/me/preferences", response_model=Preferences)
async def get_preferences(user: Principal = Depends(require_user), db=Depends(get_db)) -> Preferences:
    """The signed-in user's saved preferences (empty defaults when nothing is saved)."""
    try:
        return await prefs_store.load(db, user.id)
    except Exception as exc:  # noqa: BLE001 - a generic 503, no database details to the caller
        logger.error("could not load preferences (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Preferences are not available right now.") from exc


@router.put("/me/preferences", response_model=Preferences)
async def put_preferences(body: Preferences, user: Principal = Depends(require_user), db=Depends(get_db)) -> Preferences:
    """Replace the signed-in user's preferences with the body (fields left out become empty)."""
    try:
        await prefs_store.save(db, user.id, body)
    except Exception as exc:  # noqa: BLE001 - a generic 503, no database details to the caller
        logger.error("could not save preferences (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Preferences are not available right now.") from exc
    return body
