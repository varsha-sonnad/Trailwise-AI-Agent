# trips.py: routes for saved trips and explore history (signed-in users only; guests get 401).
#   POST /trips, GET /trips, GET /trips/{id}, DELETE /trips/{id}, GET /explore-history, DELETE /explore-history
# The owner is ALWAYS the Keycloak token's "sub" (from require_user). It is never read from the request body or the URL,
# and every query in trips_store filters on it. Someone else's trip answers 404, the same as a trip that does not exist.

import logging
from contextlib import contextmanager

from fastapi import APIRouter, Depends, HTTPException, Query
from pymongo.errors import PyMongoError

from app.core import rate_limit
from app.core.auth import Principal, require_user
from app.core.mongo import get_db
from app.models.trips import (
    MAX_PAGE_SIZE,
    ExploreEntry,
    ExploreHistory,
    TripCreate,
    TripOut,
    TripPage,
    TripSummary,
)
from app.services import trip_email, trips_store

logger = logging.getLogger(__name__)
router = APIRouter()


@contextmanager
def db_errors():
    """Turn a database failure into a generic 503. Only the error's type is logged, never its text (it can hold a URI)."""
    try:
        yield
    except PyMongoError as exc:
        logger.warning("database error: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Saving is not available right now. Please try again.")


def _not_found() -> HTTPException:
    """The one 404 used for a missing trip and for another user's trip."""
    return HTTPException(status_code=404, detail="Trip not found.")


@router.post("/trips", response_model=TripOut, status_code=201)
async def create_trip(body: TripCreate, user: Principal = Depends(require_user), db=Depends(get_db)) -> dict:
    """Save a trip for the signed-in user. At most 100 trips each (409 after that)."""
    with db_errors():
        try:
            return await trips_store.create_trip(db, user.id, body.model_dump())
        except trips_store.TripLimitError:
            raise HTTPException(
                status_code=409,
                detail=f"You have reached the limit of {trips_store.MAX_TRIPS_PER_USER} saved trips. Delete one to save another.",
            )


@router.get("/trips", response_model=TripPage)
async def list_trips(
    page: int = Query(default=1, ge=1, le=1000),
    page_size: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
    user: Principal = Depends(require_user),
    db=Depends(get_db),
) -> TripPage:
    """The signed-in user's trips, newest first, one page at a time (page size 50 at most)."""
    with db_errors():
        trips = await trips_store.list_trips(db, user.id, page, page_size)
    return TripPage(items=[TripSummary(**t) for t in trips], page=page, page_size=page_size)


@router.get("/trips/{trip_id}", response_model=TripOut)
async def get_trip(trip_id: str, user: Principal = Depends(require_user), db=Depends(get_db)) -> dict:
    """One whole trip of the signed-in user (404 if it is not theirs or does not exist)."""
    with db_errors():
        trip = await trips_store.get_trip(db, user.id, trip_id)
    if trip is None:
        raise _not_found()
    return trip


@router.delete("/trips/{trip_id}", status_code=204)
async def delete_trip(trip_id: str, user: Principal = Depends(require_user), db=Depends(get_db)) -> None:
    """Delete one trip of the signed-in user (404 if it is not theirs or does not exist)."""
    with db_errors():
        deleted = await trips_store.delete_trip(db, user.id, trip_id)
    if not deleted:
        raise _not_found()


# Emailing a trip: at most this many per user per window (seconds).
EMAIL_LIMIT = 5
EMAIL_WINDOW_SECONDS = 3600


def _email_error(status: int, code: str, message: str, headers: dict | None = None) -> HTTPException:
    """A truthful failure: the body says sent is false and names the specific error."""
    return HTTPException(status_code=status, detail={"sent": False, "error": code, "message": message}, headers=headers)


@router.post("/trips/{trip_id}/email")
async def email_trip(trip_id: str, user: Principal = Depends(require_user), db=Depends(get_db)) -> dict:
    """Email one SAVED trip of the signed-in user to the email address in their token, only when it is verified.

    The recipient is never taken from the request. The body is built here (no model call). 5 per user per hour.
    Returns {"sent": true} only after the SMTP server accepted the message; otherwise a specific error.
    """
    with db_errors():
        trip = await trips_store.get_trip(db, user.id, trip_id)  # also the ownership check: someone else's trip is a 404
    if trip is None:
        raise _not_found()
    recipient = trip_email.valid_address(user.email) if user.email_verified else None
    if recipient is None:
        raise _email_error(403, "email_not_verified", "Your email address is not verified, so the plan cannot be emailed.")
    try:
        allowed, retry_after = await rate_limit.hit(f"ratelimit:trip-email:{user.id}", EMAIL_LIMIT, EMAIL_WINDOW_SECONDS)
    except rate_limit.RateLimitUnavailable:
        raise _email_error(503, "email_unavailable", "Emailing is not available right now. Please try again later.")
    if not allowed:
        raise _email_error(
            429, "rate_limited", f"You can email at most {EMAIL_LIMIT} plans per hour. Please try again later.",
            headers={"Retry-After": str(retry_after)},
        )
    subject, body = trip_email.build_trip_email(trip)
    try:
        await trip_email.send_trip_email(recipient, subject, body)
    except trip_email.EmailNotConfigured:
        raise _email_error(503, "email_not_configured", "Email is not set up on this server.")
    except trip_email.EmailSendFailed:
        raise _email_error(502, "email_send_failed", "The email could not be sent right now. Please try again.")
    return {"sent": True, "to": trip_email.mask(recipient)}


@router.get("/explore-history", response_model=ExploreHistory)
async def get_explore_history(user: Principal = Depends(require_user), db=Depends(get_db)) -> ExploreHistory:
    """The signed-in user's latest explored places, newest first (at most 50)."""
    with db_errors():
        items = await trips_store.list_explore(db, user.id)
    return ExploreHistory(items=[ExploreEntry(**i) for i in items])


@router.delete("/explore-history")
async def delete_explore_history(user: Principal = Depends(require_user), db=Depends(get_db)) -> dict:
    """Delete all of the signed-in user's explore history."""
    with db_errors():
        count = await trips_store.delete_explore(db, user.id)
    return {"deleted": count}
