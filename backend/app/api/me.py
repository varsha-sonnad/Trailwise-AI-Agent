# me.py: the GET /me route. It returns who the signed-in user is, read from the verified Keycloak token.
# Guests and anonymous callers get a 401. No database is used yet (a users collection comes with saved trips).

from fastapi import APIRouter, Depends

from app.core.auth import Principal, require_user

router = APIRouter()


@router.get("/me")
async def me(user: Principal = Depends(require_user)) -> dict:
    """Return the signed-in user's Keycloak id, email and name."""
    return {"id": user.id, "email": user.email, "name": user.name}
