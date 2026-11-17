# chat.py: Pydantic models for the /chat endpoint (what the client sends and what it gets back).

from pydantic import BaseModel, ConfigDict, Field

from app.config import get_settings


class ChatRequest(BaseModel):
    """The body of POST /chat: one user message."""

    # Remove leading and trailing spaces before the length check, so "   " counts as empty.
    model_config = ConfigDict(str_strip_whitespace=True)

    # Must not be empty and must not exceed the configured maximum length.
    message: str = Field(min_length=1, max_length=get_settings().max_message_chars)


class ChatResponse(BaseModel):
    """The body returned by POST /chat: the agent's reply."""

    reply: str
