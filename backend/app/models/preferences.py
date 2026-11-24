# preferences.py: the request/response schema for a user's saved preferences. Only the four fields below are
# accepted, and only the listed values. Anything else (including a user id) is rejected with a 422.

from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

TravelMode = Literal["train", "bus"]
Budget = Literal["budget", "comfort", "premium"]
Interest = Literal["beach", "mountains", "heritage", "spiritual", "adventure", "nature", "food", "culture", "relaxing", "wildlife"]
ReplyLanguage = Literal["en", "hi", "kn"]


class Preferences(BaseModel):
    """A user's preferences. Empty values mean "not set". Unknown fields are an error."""

    model_config = ConfigDict(extra="forbid")  # a body with "sub", "user_id" or anything else is a 422

    travel_mode: list[TravelMode] = []
    budget: Budget | None = None
    interests: list[Interest] = []
    reply_language: ReplyLanguage | None = None

    @field_validator("travel_mode", "interests")
    @classmethod
    def _dedupe(cls, values: list) -> list:
        """Drop repeated list items, keeping the first occurrence's order."""
        return list(dict.fromkeys(values))
