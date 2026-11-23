# me.py: the GET /me route. It returns who the signed-in user is, read from the verified Keycloak token.
# Guests and anonymous callers get a 401. DELETE /me/data removes the user's saved trips and explore history.

from fastapi import APIRouter, Depends

from app.api.trips import db_errors
from app.core.auth import Principal, require_user
from app.core.mongo import get_db
from app.services import trips_store

router = APIRouter()


@router.get("/me")
async def me(user: Principal = Depends(require_user)) -> dict:
    """Return the signed-in user's Keycloak id, email and name."""
    return {"id": user.id, "email": user.email, "name": user.name}


@router.delete("/me/data")
async def delete_my_data(user: Principal = Depends(require_user), db=Depends(get_db)) -> dict:
    """Delete everything stored for the signed-in user (trips and explore history). Only their own records, by token sub."""
    with db_errors():
        return await trips_store.delete_all_for_owner(db, user.id)
