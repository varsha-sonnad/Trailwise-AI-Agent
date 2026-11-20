# chat.py: the POST /chat route. It stays thin: validate the input, call the agent, return the reply.

from fastapi import APIRouter, Depends, HTTPException

from app.agent.agent import AgentBusyError, AgentError, AgentUnavailableError, run_agent
from app.agent.modes import MODES
from app.core.auth import Principal, get_optional_principal
from app.models.chat import ChatRequest, ChatResponse, clean_images, clean_places

router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
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
    return ChatResponse(
        reply=str(reply),
        images=clean_images(getattr(reply, "images", [])),
        mode=mode if mode in MODES else None,
        places=clean_places(getattr(reply, "places", [])),
    )
