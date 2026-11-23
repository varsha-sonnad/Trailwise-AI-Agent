# trips.py: Pydantic models for saved trips and explore history (signed-in users only).
# Input models are strict: unknown fields are refused (so a client can never send an owner_id), text is plain,
# and every size has a cap. Output models have no owner_id, so it can never leak back out.

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.chat import MAX_CHAT_PLACES, LastPlace, _plain_text

# Size caps (characters, or number of places).
MAX_TRIP_TITLE_CHARS = 100
MAX_TRIP_SUMMARY_CHARS = 500
MAX_TRIP_REPLY_CHARS = 8000
MAX_TRIP_PLACES = MAX_CHAT_PLACES  # 12
# Largest page GET /trips returns.
MAX_PAGE_SIZE = 50


class TripCreate(BaseModel):
    """The body of POST /trips. Nothing else is accepted (no owner_id, no created_at, no id)."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    title: str = Field(min_length=1, max_length=MAX_TRIP_TITLE_CHARS)
    places: list[LastPlace] = Field(default_factory=list, max_length=MAX_TRIP_PLACES)
    summary: str = Field(default="", max_length=MAX_TRIP_SUMMARY_CHARS)
    reply: str = Field(min_length=1, max_length=MAX_TRIP_REPLY_CHARS)

    @field_validator("title", "summary", "reply", mode="before")
    @classmethod
    def _plain(cls, value: object) -> str:
        """Plain text only (a newline and a tab are fine, other control characters are refused)."""
        return _plain_text(value)


class TripSummary(BaseModel):
    """A trip as shown in the list: everything except the long reply text."""

    id: str
    title: str
    places: list[LastPlace]
    summary: str
    created_at: datetime


class TripOut(TripSummary):
    """One whole trip, including the saved reply text."""

    reply: str


class TripPage(BaseModel):
    """One page of GET /trips, newest first."""

    items: list[TripSummary]
    page: int
    page_size: int


class ExploreEntry(BaseModel):
    """One explore-history entry: the place that was explored and when."""

    id: str
    place: str
    created_at: datetime


class ExploreHistory(BaseModel):
    """The body of GET /explore-history, newest first (at most 50 entries)."""

    items: list[ExploreEntry]
