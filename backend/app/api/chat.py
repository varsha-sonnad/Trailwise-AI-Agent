# chat.py: the POST /chat route. It stays thin: validate the input, call the agent, return the reply.

from fastapi import APIRouter, HTTPException

from app.agent.agent import AgentError, run_agent
from app.models.chat import ChatRequest, ChatResponse

router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest) -> ChatResponse:
    """Send the user's message to the Gemini agent and return its reply."""
    try:
        reply = await run_agent(body.message)
    except AgentError:
        # 502 = the upstream AI service failed. The message is generic so no internals leak.
        raise HTTPException(status_code=502, detail="The assistant is unavailable. Please try again.")
    return ChatResponse(reply=reply)
