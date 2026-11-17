# train_tool.py: the train tool. Takes a train number, returns its details and scheduled stops from RailRadar.
# Flow: validate the argument -> call the API -> return a shaped result (no cache yet).
# It never raises into the agent loop: every failure becomes {"ok": False, "error": ...}.
# Transport is indicative only: this tool returns SCHEDULED data and never seat counts or prices
# (RailRadar has no seat availability API). Every result carries a "verify before travel" notice.

import logging

import httpx
from pydantic import BaseModel, ValidationError, field_validator

from config import get_settings

logger = logging.getLogger(__name__)

# RailRadar API host (the website railradar.in is not the API).
API_BASE_URL = "https://api.railradar.in"
# Give up on the external API after this many seconds.
HTTP_TIMEOUT_SECONDS = 10.0
# Most stops returned to the agent, so one long route cannot flood the model's context.
MAX_STOPS = 60
# Notice attached to every result (CLAUDE.md rule 4).
DISCLAIMER = (
    "Scheduled information only, not live status. Seat availability and fares are not provided. "
    "Verify before travel on the official IRCTC or Indian Railways channels."
)


class TrainArgs(BaseModel):
    """Arguments the model passes to the tool. Never trusted: validated here."""

    train_number: str

    @field_validator("train_number")
    @classmethod
    def clean_number(cls, value: str) -> str:
        """Trim the text and allow only a plausible train number (1 to 10 letters, digits or hyphens)."""
        value = value.strip()
        if not 1 <= len(value) <= 10:
            raise ValueError("train_number must be 1 to 10 characters")
        # Letters, digits and hyphen only: nothing that could alter the URL path.
        if not all(ch.isascii() and (ch.isalnum() or ch == "-") for ch in value):
            raise ValueError("train_number contains invalid characters")
        return value


def _error(code: str, message: str) -> dict:
    """Build the error object returned to the agent."""
    return {"ok": False, "error": code, "message": message}


def _text(value, limit: int = 100) -> str:
    """Turn any API value into a short plain string (third-party text is data, so keep it small)."""
    return "" if value is None else str(value)[:limit]


def _station(value) -> dict:
    """Keep only the code and name of a station object."""
    value = value if isinstance(value, dict) else {}
    return {"code": _text(value.get("code"), 10), "name": _text(value.get("name"))}


def _shape(body: dict) -> dict:
    """Keep only the fields we need from the API response (structured data, not raw text)."""
    data = body.get("data") or {}
    train = data.get("train") or {}
    route = data.get("route") or []

    # The stops, in order, capped. Times are scheduled times as the API returned them.
    stops = []
    for stop in route[:MAX_STOPS]:
        if not isinstance(stop, dict):
            continue
        stops.append(
            {
                "station": _station(stop.get("station")),
                "arrival": _text(stop.get("arrival"), 20),
                "departure": _text(stop.get("departure"), 20),
                "day": stop.get("arrivalDay") if stop.get("arrivalDay") is not None else stop.get("departureDay"),
                "platform": _text(stop.get("platform"), 10),
                "distance_km": stop.get("distance"),
            }
        )

    return {
        "ok": True,
        "train": {
            "number": _text(train.get("number"), 10),
            "name": _text(train.get("name")),
            "type": _text(train.get("type")),
            "from": _station(train.get("source")),
            "to": _station(train.get("destination")),
            "run_days": [_text(d, 10) for d in (train.get("runDays") or [])[:7]],
            "distance_km": train.get("distance"),
            "duration": _text(train.get("duration"), 20),
            "total_halts": train.get("totalHalts"),
            "classes": [_text(c, 10) for c in (train.get("classes") or [])[:10]],
            # Whether the train can be booked through the passenger reservation system: not a seat count.
            "bookable_via_prs": train.get("isPrsBookable"),
        },
        "stops": stops,
        "stops_truncated": len(route) > MAX_STOPS,
        "source": "railradar",
        "disclaimer": DISCLAIMER,
    }


async def get_train_details(train_number: str) -> dict:
    """Get a train's details and scheduled halts by its number, e.g. "12919".

    Returns the name, type, route ends, run days, classes and scheduled stops with times,
    or {"ok": False, "error": ..., "message": ...} when it cannot. No seat availability or fares.
    """
    # 1. Validate the model-supplied argument.
    try:
        args = TrainArgs(train_number=train_number)
    except ValidationError:
        return _error("invalid_arguments", "train_number must be a train number such as 12919.")

    # 2. Needs an API key from the project-root .env.
    api_key = get_settings().railradar_api_key
    if not api_key:
        return _error("not_configured", "The train service is not configured.")

    # 3. Call the API with a timeout. The key goes in a header, never in the URL, so it cannot be logged.
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            response = await client.get(
                f"{API_BASE_URL}/v1/trains/{args.train_number}",
                params={"haltsOnly": "true"},
                headers={"Authorization": f"Bearer {api_key}"},
            )
    except httpx.HTTPError:
        return _error("unavailable", "Train information is unavailable right now.")

    if response.status_code == 404:
        return _error("train_not_found", f"No train found with number '{args.train_number}'.")
    if response.status_code != 200:
        # Covers 401 (bad key), 429 (quota) and 5xx. Details stay in the log, not in the reply.
        logger.warning("train API returned status %s", response.status_code)
        return _error("unavailable", "Train information is unavailable right now.")

    # 4. Keep only the fields we need and return them.
    try:
        result = _shape(response.json())
    except (ValueError, AttributeError, TypeError):
        return _error("unavailable", "Train information is unavailable right now.")
    # An unexpected body with no train data is treated as a failure, not an empty success.
    if not result["train"]["number"]:
        return _error("unavailable", "Train information is unavailable right now.")
    return result
