# chat.py: Pydantic models for the /chat endpoint (what the client sends and what it gets back).
# The request can carry per-session memory: the earlier messages (history), the mode of the last reply and the last place.
# It is all untrusted client data, so it is validated strictly (roles, sizes, plain text, fixed mode list, real coordinates)
# and nothing is stored on the server: the browser keeps it in memory and sends it again with every request.

import math
import unicodedata
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.agent.modes import MODES
from app.config import get_settings
from app.models.preferences import Budget, ReplyLanguage, TravelMode

# Most photos one reply may carry (one place has up to 3).
MAX_CHAT_IMAGES = 6
# Memory limits: how many earlier messages, how long each may be and how long all of them together may be (characters).
MAX_HISTORY_MESSAGES = 10
MAX_HISTORY_MESSAGE_CHARS = 2000
MAX_HISTORY_TOTAL_CHARS = 12000
# Most places one reply may report back (the geocoded places of the request).
MAX_CHAT_PLACES = 12
# Map lines of one reply: at most this many legs, each with 2 to MAX_LEG_POINTS points.
MAX_CHAT_LEGS = 8
MAX_LEG_POINTS = 300


def is_pexels_url(value: object) -> bool:
    """True only for a short https address on pexels.com or one of its subdomains (for example images.pexels.com)."""
    if not isinstance(value, str) or not value or len(value) > 500:
        return False
    try:
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
    except ValueError:
        return False
    return parsed.scheme == "https" and (host == "pexels.com" or host.endswith(".pexels.com"))


def _plain_text(value: object) -> str:
    """The value as text with no control characters (a newline, carriage return and tab are fine): anything else is refused."""
    if not isinstance(value, str):
        raise ValueError("must be text")
    for ch in value:
        if ch not in "\n\r\t" and unicodedata.category(ch) in ("Cc", "Cs", "Co"):
            raise ValueError("must be plain text")
    return value


def _real_number(value: object) -> float:
    """A real number (int or float): not a boolean, not text, not NaN or infinity."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("must be a real number")
    return float(value)


class HistoryMessage(BaseModel):
    """One earlier message of this conversation. Only the user's text and the assistant's reply text, nothing else."""

    model_config = ConfigDict(extra="forbid")  # no other fields (no tool calls, no names, no ids)

    role: Literal["user", "assistant"]  # never "system", "developer" or "tool"
    content: str = Field(min_length=1, max_length=MAX_HISTORY_MESSAGE_CHARS)

    @field_validator("content", mode="before")
    @classmethod
    def _content_is_plain_text(cls, value: object) -> str:
        """Plain text, and not just spaces."""
        text = _plain_text(value)
        if not text.strip():
            raise ValueError("must not be blank")
        return text


class LastPlace(BaseModel):
    """A place with real coordinates: the last place of the conversation (request) or a geocoded place (response)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    lat: float
    lng: float

    @field_validator("name", mode="before")
    @classmethod
    def _name_is_a_plain_place_name(cls, value: object) -> str:
        """Letters, marks and digits in any script, and spaces and , . ' - ( ) / & only (the same rule as the tools use)."""
        text = _plain_text(value).strip()
        if not all(unicodedata.category(ch)[0] in "LMN" or ch in " ,.'-()/&" for ch in text):
            raise ValueError("not a plain place name")
        return text

    @field_validator("lat", "lng", mode="before")
    @classmethod
    def _coordinates_are_real_numbers(cls, value: object) -> float:
        return _real_number(value)

    @model_validator(mode="after")
    def _coordinates_are_in_range(self):
        """lat from -90 to 90 and lng from -180 to 180."""
        if not (-90 <= self.lat <= 90 and -180 <= self.lng <= 180):
            raise ValueError("coordinates out of range")
        return self


class ChatPreferences(BaseModel):
    """The saved preferences that may travel with a chat request: only these three fields, only the fixed values.

    The browser sends them (a guest's live only there). They turn into one short fixed-template line for the model
    (agent/modes.py preferences_line), so free text can never get in: anything else is refused with 422.
    """

    model_config = ConfigDict(extra="forbid")

    reply_language: ReplyLanguage | None = None
    budget: Budget | None = None
    travel_mode: list[TravelMode] = Field(default_factory=list)

    @field_validator("travel_mode")
    @classmethod
    def _no_repeats(cls, values: list) -> list:
        """Drop repeated travel modes, keeping the first one's order."""
        return list(dict.fromkeys(values))


class ChatRequest(BaseModel):
    """The body of POST /chat: one user message, plus the per-session memory the browser sends along.

    history: up to 10 earlier messages (2000 characters each, 12000 together), oldest first. last_mode: the mode of the
    previous reply, from the fixed list. last_place: the last geocoded place with its coordinates. preferences: the saved
    reply language, budget style and travel mode (fixed values only). Anything else in the
    body, or any value outside these rules, is refused with 422.
    """

    # Remove leading and trailing spaces before the length check, so "   " counts as empty. Unknown fields are refused.
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    # Must not be empty and must not exceed the configured maximum length.
    message: str = Field(min_length=1, max_length=get_settings().max_message_chars)
    history: list[HistoryMessage] = Field(default_factory=list, max_length=MAX_HISTORY_MESSAGES)
    last_mode: str | None = None
    last_place: LastPlace | None = None
    preferences: ChatPreferences | None = None

    @field_validator("last_mode", mode="before")
    @classmethod
    def _last_mode_is_one_of_the_modes(cls, value: object) -> str | None:
        """Exactly one of the fixed modes (explore, route, itinerary, email), spelled exactly, or nothing."""
        if value is not None and (not isinstance(value, str) or value not in MODES):
            raise ValueError("unknown mode")
        return value

    @model_validator(mode="after")
    def _history_is_not_too_long_in_total(self):
        """All history messages together may hold at most 12000 characters."""
        if sum(len(m.content) for m in self.history) > MAX_HISTORY_TOTAL_CHARS:
            raise ValueError("history is too long")
        return self


class ChatImage(BaseModel):
    """One photo the app shows under a reply, with the credit Pexels requires. Every address must be on Pexels' own hosts."""

    url: str  # the large picture
    thumb: str = ""  # a smaller version (may be empty)
    alt: str = Field(default="", max_length=200)  # third-party text: only ever used as an image's alt attribute
    photographer: str = Field(min_length=1, max_length=100)  # the credit: a photo without a photographer is not accepted
    photographer_url: str = ""  # the photographer's Pexels page (may be empty)
    pexels_url: str  # the photo's Pexels page

    @field_validator("url", "pexels_url")
    @classmethod
    def _must_be_pexels(cls, value: str) -> str:
        """These two are required and must be https addresses on Pexels."""
        if not is_pexels_url(value):
            raise ValueError("not a Pexels address")
        return value

    @field_validator("thumb", "photographer_url")
    @classmethod
    def _empty_or_pexels(cls, value: str) -> str:
        """These two may be empty, but if given they must be on Pexels."""
        if value and not is_pexels_url(value):
            raise ValueError("not a Pexels address")
        return value


def clean_images(raw: object) -> list[dict]:
    """The valid photos of a list as plain dicts: invalid ones dropped, duplicates (same picture) dropped, at most MAX_CHAT_IMAGES."""
    if not isinstance(raw, (list, tuple)):
        return []
    images, seen = [], set()
    for item in raw:
        try:
            image = ChatImage.model_validate(item)
        except ValidationError:
            continue
        if image.url not in seen:
            seen.add(image.url)
            images.append(image.model_dump())
        if len(images) >= MAX_CHAT_IMAGES:
            break
    return images


def clean_places(raw: object) -> list[dict]:
    """The valid places of a list as plain dicts {name, lat, lng}: invalid ones dropped, repeats dropped, at most MAX_CHAT_PLACES."""
    if not isinstance(raw, (list, tuple)):
        return []
    places, seen = [], set()
    for item in raw:
        try:
            place = LastPlace.model_validate(item)
        except ValidationError:
            continue
        key = (place.name.lower(), place.lat, place.lng)
        if key not in seen:
            seen.add(key)
            places.append(place.model_dump())
        if len(places) >= MAX_CHAT_PLACES:
            break
    return places


class Leg(BaseModel):
    """One line on the map: a driven road ("road", from get_route) or a suggested connection ("other", drawn dashed)."""

    kind: Literal["road", "other"]
    points: list[tuple[float, float]] = Field(min_length=2, max_length=MAX_LEG_POINTS)

    @field_validator("points", mode="before")
    @classmethod
    def _points_are_real(cls, value: object) -> object:
        """Every point is a [lat, lng] pair of real numbers in range (not text, booleans, NaN or infinity)."""
        if not isinstance(value, (list, tuple)):
            raise ValueError("points must be a list")
        for point in value:
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                raise ValueError("a point is [lat, lng]")
            lat, lng = _real_number(point[0]), _real_number(point[1])
            if not (-90 <= lat <= 90 and -180 <= lng <= 180):
                raise ValueError("coordinates out of range")
        return value


def clean_legs(raw: object) -> list[dict]:
    """The valid legs of a list as plain dicts {kind, points}: invalid ones dropped, at most MAX_CHAT_LEGS."""
    if not isinstance(raw, (list, tuple)):
        return []
    legs = []
    for item in raw:
        try:
            leg = Leg.model_validate(item)
        except ValidationError:
            continue
        legs.append({"kind": leg.kind, "points": [list(p) for p in leg.points]})
        if len(legs) >= MAX_CHAT_LEGS:
            break
    return legs


class ChatResponse(BaseModel):
    """The body returned by POST /chat: the agent's reply, the photos the app shows under it (often none), and what the
    browser needs to remember for the next request: the mode used and the geocoded places of this reply."""

    reply: str
    images: list[ChatImage] = Field(default_factory=list)
    mode: str | None = None
    places: list[LastPlace] = Field(default_factory=list)
    # The map lines of this reply (road legs solid, other suggested connections dashed); empty when there are none.
    legs: list[Leg] = Field(default_factory=list)
    # Only for signed-in users when an explore entry was attempted: true = saved, false = the save failed. None otherwise.
    saved: bool | None = None
