# modes.py: picks the agent's mode for a message and builds the system prompt for that mode.
# select_mode() uses simple regex rules (no model call): email, then itinerary, then route (transport words and
# "X to Y" shapes, which beat the previous mode, explore included), then a few explore phrases, else the
# previous mode, else explore. select_mode_with_reason() also returns the NAME of the rule that decided (never message text). build_system_prompt() returns system.md followed by the mode's file, plus a
# short hash of that exact text so every answer can be traced to the prompt that produced it.
# The stable text (system.md) always comes first: the model's prompt caching reuses a shared prefix.
# Each prompt file starts with a heading holding its prompt ID and version; the backend checks all five at startup.

import hashlib
import re
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

# The four modes, and the prompt files sit next to this file, in agent/prompts/.
MODES = ("explore", "route", "itinerary", "email")
DEFAULT_MODE = "explore"
PROMPTS_DIR = Path(__file__).parent / "prompts"

# Rule 1: the user wants an email ("email", "e-mail", "mail me", "send the plan").
_EMAIL = re.compile(r"\be-?mails?\b|\bmail\s+(?:me|it|this)\b|\bsend\s+(?:me\s+)?(?:the\s+|this\s+|my\s+)?(?:trip\s+)?plan\b")
# Rule 2: a plan or itinerary, or a trip length such as "3 days" or "3-day".
_ITINERARY = re.compile(r"\bitinerar(?:y|ies)\b|\bplan(?:ning)?\b|\b\d+\s*-?\s*days?\b")
# Rule 3: getting somewhere. Several rules, each with a name that is logged as mode_reason (never message text).
# Transport words, "how to go", "ways to" and the "X to Y" shapes all beat the previous mode (explore included).
_PLACE = r"[^\s,.?!]+"  # one word of a place name (no spaces or sentence punctuation)
_ROUTE_RULES = (
    ("route_words", re.compile(r"\broutes?\b|\btrains?\b|\bbus(?:es)?\b|\bflights?\b|\btravel(?:l?ing|s)?\b|\boptions?\b|\btransport(?:ation)?\b")),
    ("route_how_to_go", re.compile(r"\bhow\s+(?:to|can\s+i|do\s+i)\s+(?:go|travel|get|reach)\b")),
    ("route_ways_to", re.compile(r"\bways?\s+to\b")),
    ("route_from_to", re.compile(rf"\bfrom\s+{_PLACE}(?:\s+{_PLACE}){{0,3}}?\s+to\s+{_PLACE}")),
    ("route_to_from", re.compile(rf"\bto\s+{_PLACE}(?:\s+{_PLACE}){{0,2}}?\s+from\s+{_PLACE}")),
)
# A bare "X to Y": the whole message (punctuation dropped) is one or two words, "to", then one or two words. It does not
# count after "how" ("how to go"), and not when it starts like a sentence ("I want to go", "what to see").
_BARE_X_TO_Y = re.compile(
    r"(?!(?:how|i|we|you|what|where|when|why|who|want|need|like|going|trying)\b)\w+(?:\s\w+)?\s+to\s+\w+(?:\s\w+)?"
)
# Rule 4: asking about a place ("tell me about Bangalore", "things to do"): explore, even after a route question.
_EXPLORE = re.compile(r"\btell\s+me\s+about\b|\bthings\s+to\s+do\b|\bplaces\s+to\s+visit\b|\bwhat\s+to\s+(?:see|do)\b")


# The first line every prompt file must have, e.g. "# Trailwise Route mode (prompt ID: trailwise.route, version 1)".
# The ID must be trailwise.<file name without .md>; the version is a whole number from 1 (see docs/prompts.md).
_HEADING = re.compile(r"# Trailwise [^()\n]+ \(prompt ID: (?P<id>trailwise\.[a-z]+), version (?P<n>[1-9]\d*)\)")
# All five prompt files: system.md plus one per mode.
PROMPT_NAMES = ("system", *MODES)


# Tools the model may be FORCED to call on round 1, per mode (only those the MCP server really offered are used).
# Email is never forced: sending an email must always wait for the user's own confirmation.
FORCED_TOOLS = {
    "explore": ("web_search", "get_weather", "geocode_places", "get_place_images"),
    "route": ("geocode_places", "search_trains", "search_buses"),
    "itinerary": ("get_weather", "web_search", "geocode_places", "get_route"),
    "email": (),
}
# Tools a mode must have ATTEMPTED (successfully or not) before its final answer is accepted. Explore needs all four for a
# named place; itinerary needs web_search, get_weather and geocode_places (get_route stays optional: it needs two named
# places); route needs geocode_places, search_trains and search_buses (no web_search in route mode) and, after
# geocode_places found at least two places, get_route (see MAP_TOOL). If the model answers without having tried one of them,
# the agent runs one extra round that forces the missing ones (see agent.py). Only tools the MCP server really offered are required.
REQUIRED_TOOLS = {
    "explore": ("web_search", "get_weather", "geocode_places", "get_place_images"),
    "itinerary": ("web_search", "get_weather", "geocode_places"),
    "route": ("geocode_places", "search_trains", "search_buses", "get_route"),
}
# The one required tool that depends on another's result: get_route draws the road between two places, so it is only
# required once geocode_places returned at least MAP_MIN_PLACES found places in this request (agent.py checks that).
MAP_TOOL = "get_route"
MAP_MIN_PLACES = 2
# Tools the model is not even offered in a mode: route mode answers from the route tools only (no web search, no weather).
EXCLUDED_TOOLS = {"route": ("web_search", "get_weather")}
# A model reply that is only a short clarifying question (no tools asked for yet) is not checked: the user has to answer first.
MAX_CLARIFYING_CHARS = 300
# A whole message that is only one of these (after lower-casing and dropping punctuation) is not forced to use a tool.
_SMALL_TALK = frozenset({"hi", "hello", "hey", "thanks", "thank you", "ok", "yes", "no", "bye"})


def is_small_talk(message: str) -> bool:
    """True when the whole normalized message is a greeting or confirmation (hi, thanks, ok, yes, no, bye...)."""
    normalized = " ".join(re.sub(r"[^\w\s]", " ", message.lower()).split())
    return normalized in _SMALL_TALK


def required_tools(mode: str, message: str, available: list[str] | set[str]) -> tuple[str, ...]:
    """The tools this turn must attempt before the final answer: the mode's REQUIRED_TOOLS that the server offered.

    Empty for modes with no requirement (email) and for a greeting or short confirmation
    (is_small_talk). The order of REQUIRED_TOOLS is kept.
    """
    if is_small_talk(message):
        return ()
    offered = set(available)
    return tuple(name for name in REQUIRED_TOOLS.get(mode, ()) if name in offered)


def is_clarifying_question(text: str) -> bool:
    """True when a reply is a short clarifying question: at most MAX_CLARIFYING_CHARS characters, ending with a question mark.

    Used by the guard in agent.py: such a reply (given before any tool was requested) skips the required-tools check.
    """
    cleaned = text.strip().rstrip("\"'\u201d\u2019) ").rstrip()  # ignore closing quotes and brackets after the question mark
    return 0 < len(text.strip()) <= MAX_CLARIFYING_CHARS and cleaned.endswith("?")


# A message that itself asks to send, or confirms sending: "yes", "ok", "confirm", "go ahead", "send it", "send the email"...
_SEND_CONFIRMATION = re.compile(
    r"^\W*(?:yes|yep|yeah|sure|ok|okay|confirm|confirmed|go ahead|do it|please send|send)\b"
    r"|\bsend\b[^.?!]{0,40}\b(?:it|this|that|plan|email|e-mail|mail)\b",
    re.IGNORECASE,
)
# A confirmation is a short message; a long text that merely contains "yes" or "send" is not one.
MAX_CONFIRMATION_CHARS = 300


def is_send_confirmation(message: str) -> bool:
    """True when THIS message (the one the user just sent) asks to send the email or confirms the draft.

    Earlier messages, which the browser sends back as history, can never count: they are untrusted. The agent refuses
    to send an email (confirmed=true) unless the current message passes this check.
    """
    text = message.strip()
    return 0 < len(text) <= MAX_CONFIRMATION_CHARS and bool(_SEND_CONFIRMATION.search(text))


class PromptHeadingError(ValueError):
    """A prompt file's first line is missing or not in the required format."""


class BuiltPrompt(NamedTuple):
    """A built system prompt: the full text, its 12-character hash and the version label of each part."""

    text: str
    hash: str
    versions: tuple[str, ...] = ()


def _bare_x_to_y(text: str) -> bool:
    """True when the whole message is a bare "X to Y" (one or two words each side), for example "gulbarga to bangalore"."""
    return _BARE_X_TO_Y.fullmatch(" ".join(re.sub(r"[^\w\s]", " ", text).split())) is not None


def has_two_places(message: str) -> bool:
    """True when the message itself names a route in a shape with both ends: "from X to Y", "to X from Y" or a bare "X to Y".

    Route mode forces its tools on round 1 only then. With only transport words ("any bus options") a city may be missing, so
    the model is left free to ask for it.
    """
    text = message.lower()
    if any(rule.search(text) for name, rule in _ROUTE_RULES if name in ("route_from_to", "route_to_from")):
        return True
    # A bare "X to Y" counts only when X is not itself a transport word ("buses to Goa" has no source city).
    return _bare_x_to_y(text) and not _ROUTE_RULES[0][1].search(text)


def select_mode_with_reason(message: str, previous_mode: str | None = None) -> tuple[str, str]:
    """Return (mode, rule name) for this message. The rule name is a fixed label, never any part of the message.

    The first rule that matches wins: email words, then plan/itinerary/N days, then the route rules (transport words,
    "how to go", "ways to", "from X to Y", "to X from Y", a bare "X to Y"), then explore phrases. With no match the previous
    mode is kept (rule "previous_mode", if it is a valid mode), else explore (rule "default").
    """
    text = message.lower()
    if _EMAIL.search(text):
        return "email", "email_words"
    if _ITINERARY.search(text):
        return "itinerary", "itinerary_words"
    for name, rule in _ROUTE_RULES:
        if rule.search(text):
            return "route", name
    if _bare_x_to_y(text):
        return "route", "route_bare_x_to_y"
    if _EXPLORE.search(text):
        return "explore", "explore_words"
    if previous_mode in MODES:
        return previous_mode, "previous_mode"
    return DEFAULT_MODE, "default"


def select_mode(message: str, previous_mode: str | None = None) -> str:
    """Return explore, route, itinerary or email for this message (see select_mode_with_reason for the rules)."""
    return select_mode_with_reason(message, previous_mode)[0]


@lru_cache
def _read(name: str) -> str:
    """Read one prompt file from disk once and reuse it on every later call."""
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


def prompt_version(name: str) -> str:
    """Return the version label of one prompt file, e.g. "trailwise.route@1", from its first-line heading.

    Raises PromptHeadingError if the heading is missing, malformed, or its ID does not match the file name.
    """
    first_line = _read(name).split("\n", 1)[0].rstrip()
    match = _HEADING.fullmatch(first_line)
    if match is None:
        raise PromptHeadingError(f"{name}.md: first line must be '# Trailwise <name> (prompt ID: trailwise.{name}, version N)'")
    if match["id"] != f"trailwise.{name}":
        raise PromptHeadingError(f"{name}.md: prompt ID must be trailwise.{name}, found {match['id']}")
    return f"{match['id']}@{match['n']}"


def validate_prompts() -> dict[str, str]:
    """Check the heading of all five prompt files; return {file name: version label}.

    Called when the backend starts, so a bad heading stops it from starting.
    """
    return {name: prompt_version(name) for name in PROMPT_NAMES}


@lru_cache
def build_system_prompt(mode: str) -> BuiltPrompt:
    """Build system.md + the mode's file, a 12-character SHA-256 hash of that exact text, and the version labels."""
    if mode not in MODES:
        raise ValueError(f"unknown mode: {mode!r}")
    # system.md first (the same on every request), then the part that changes with the mode.
    text = _read("system").rstrip() + "\n\n" + _read(mode).strip() + "\n"
    return BuiltPrompt(
        text=text,
        hash=hashlib.sha256(text.encode("utf-8")).hexdigest()[:12],
        versions=(prompt_version("system"), prompt_version(mode)),
    )
