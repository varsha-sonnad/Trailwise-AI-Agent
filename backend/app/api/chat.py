# chat.py: the POST /chat route. It stays thin: validate the input, call the agent, return the reply.

from fastapi import APIRouter, Depends, HTTPException

from app.agent.agent import AgentBusyError, AgentError, run_agent
from app.core.auth import Principal, get_optional_principal
from app.models.chat import ChatRequest, ChatResponse

router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    # Who is calling: a Keycloak user, a guest session, or nobody yet. A bad token is rejected (401) here.
    # Chat history will be keyed by this later; for now it only blocks bad credentials.
    principal: Principal | None = Depends(get_optional_principal),
) -> ChatResponse:
    """Send the user's message to the Gemini agent and return its reply."""
    try:
        reply = await run_agent(body.message)
    except AgentBusyError:
        # 503 = the AI service is overloaded (temporary), even after retries. Retry-After hints when to try again.
        raise HTTPException(
            status_code=503,
            detail="The assistant is busy right now. Please try again in a minute.",
            headers={"Retry-After": "60"},
        )
    except AgentError:
        # 502 = the upstream AI service failed. The message is generic so no internals leak.
        raise HTTPException(status_code=502, detail="The assistant is unavailable. Please try again.")
    return ChatResponse(reply=reply)
