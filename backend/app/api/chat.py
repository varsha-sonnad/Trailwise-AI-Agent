# chat.py: the POST /chat route. It stays thin: validate the input, call the agent, return the reply.

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from app.agent.agent import AgentBusyError, AgentError, AgentUnavailableError, run_agent
from app.agent.modes import MODES
from app.core.auth import Principal, get_optional_principal
from app.core.mongo import get_db
from app.models.chat import ChatRequest, ChatResponse, clean_images, clean_legs, clean_places
from app.services import trips_store

logger = logging.getLogger(__name__)
router = APIRouter()

# The explore entry is saved before the reply goes out; if the database takes longer than this, the reply is sent with saved=false.
SAVE_TIMEOUT_SECONDS = 2


async def _save_explore(request: Request, owner_id: str, place: str) -> bool:
    """Record one explore entry for a signed-in user. Never raises: True if saved, False on any failure or timeout.

    Only the error's type is logged, never the message, the reply or the place text.
    """
    try:
        db = get_db(request)
        await asyncio.wait_for(trips_store.add_explore(db, owner_id, place), timeout=SAVE_TIMEOUT_SECONDS)
        return True
    except Exception as exc:  # noqa: BLE001 - a failed save must never break the chat reply
        logger.warning("explore history save failed: %s", type(exc).__name__)
        return False


@router.post("/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    request: Request,
    # Who is calling: a Keycloak user, a guest session, or nobody yet. A bad token is rejected (401) here.
    # Saved chat history (by user) will be keyed by this later; for now it only blocks bad credentials. The per-session
    # memory in the request body is separate: the browser holds it, and the server stores none of it.
    principal: Principal | None = Depends(get_optional_principal),
) -> ChatResponse:
    """Send the user's message to the agent (OpenAI) and return its reply and the photos to show under it."""
    try:
        # The per-session memory is passed along as it came (already validated) and is not stored anywhere.
        reply = await run_agent(
            body.message,
            history=[m.model_dump() for m in body.history],
            last_mode=body.last_mode,
            last_place=body.last_place.model_dump() if body.last_place else None,
            # Already validated: only the three fixed-value fields. The agent turns them into one short fixed-template line.
            preferences=body.preferences.model_dump() if body.preferences else None,
        )
    except AgentBusyError:
        # 503 = the AI service is overloaded (temporary), even after retries. Retry-After hints when to try again.
        raise HTTPException(
            status_code=503,
            detail="The assistant is busy right now. Please try again in a minute.",
            headers={"Retry-After": "60"},
        )
    except AgentUnavailableError:
        # 503 without Retry-After = the AI service cannot be used at all right now (for example its quota is used up),
        # so retrying soon will not help. The details (the kind of error) are only in the audit log.
        raise HTTPException(
            status_code=503,
            detail="The assistant is not available right now. Please try again later.",
        )
    except AgentError:
        # 502 = the upstream AI service failed. The message is generic so no internals leak.
        raise HTTPException(status_code=502, detail="The assistant is unavailable. Please try again.")
    # The reply may carry photos, the mode used and the geocoded places (see AgentReply); a plain string carries none of them.
    # The browser keeps the mode and the first place in memory and sends them back with the next message.
    mode = getattr(reply, "mode", None)
    mode = mode if mode in MODES else None
    places = clean_places(getattr(reply, "places", []))
    # Signed-in users only (guests store nothing): after a successful explore reply that found a place, record the
    # first geocoded place (its name comes from the geocoder, not from the user's text). saved stays None otherwise.
    saved = None
    if principal is not None and principal.kind == "user" and mode == "explore" and places:
        saved = await _save_explore(request, principal.id, places[0]["name"])
    return ChatResponse(
        reply=str(reply),
        images=clean_images(getattr(reply, "images", [])),
        mode=mode,
        places=places,
        legs=clean_legs(getattr(reply, "legs", [])),
        saved=saved,
    )
