# test_explore_required.py: tests for explore mode's required tools (web_search, get_weather, geocode_places and
# get_place_images): the one extra forced round, the fixed reply when web_search fails, the note for the other three,
# the greeting and other-mode exceptions, and dropping duplicate calls in one round.
# The model call (_generate) and the MCP client are faked: no key, quota or network is used.

import json

import pytest

from app.agent import agent, modes
from app.agent.agent import NO_LIVE_INFO_REPLY, run_agent
from app.agent.modes import FORCED_TOOLS, REQUIRED_TOOLS, build_system_prompt, prompt_version, required_tools
from tests.test_agent_tools import FakeMcp, call_item, make_response, script_model, text_reply, tool_calls_reply, tool_reply
from tests.test_tool_choice import tool_list

FOUR = ["web_search", "get_weather", "geocode_places", "get_place_images"]
MESSAGE = "Tell me about Rishikesh"  # explore mode, names a place

# The four calls a model makes together for a named place.
RISHIKESH = [
    ("web_search", {"query": "Rishikesh events and conditions"}),
    ("get_weather", {"location": "Rishikesh"}),
    ("geocode_places", {"names": ["Rishikesh"], "context": "Uttarakhand, India"}),
    ("get_place_images", {"place": "Rishikesh", "region": "Uttarakhand, India"}),
]


class Env:
    """What the fake MCP server offers and answers; tests change it."""

    offered = FOUR
    results: dict = {}


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    """Turn the required-tools check back ON (conftest switches it off), and install a fake MCP server."""
    monkeypatch.setattr(modes, "REQUIRED_TOOLS", REQUIRED_TOOLS)
    FakeMcp.calls, FakeMcp.fail_connect, FakeMcp.fail_list = [], False, False
    Env.offered, Env.results = list(FOUR), {}
    monkeypatch.setattr(agent, "McpClient", FakeMcp)

    async def list_tools(self):
        return tool_list(*Env.offered)

    async def call_tool(self, name, arguments):
        FakeMcp.calls.append((name, arguments))
        return Env.results.get(name, {"ok": True, "data": name})

    monkeypatch.setattr(FakeMcp, "list_tools", list_tools, raising=False)
    monkeypatch.setattr(FakeMcp, "call_tool", call_tool)


def names(choice):
    """The tool names of a recorded tool_choice."""
    assert choice["type"] == "allowed_tools" and choice["mode"] == "required"
    return [t["name"] for t in choice["tools"]]


def fail(*tools):
    """Make these tools return an error object."""
    for tool in tools:
        Env.results[tool] = {"ok": False, "error": "unavailable", "message": "down"}


def logged(monkeypatch):
    """Record every audit event as (event, status, fields)."""
    events = []
    monkeypatch.setattr(agent, "audit_event", lambda event, status="ok", **fields: events.append((event, status, fields)))
    return events


# ---------------------------------------------------------------- the settings and the helper

def test_the_required_tools_are_the_four_and_all_are_forced_on_round_one():
    assert list(REQUIRED_TOOLS["explore"]) == FOUR and list(FORCED_TOOLS["explore"]) == FOUR
    assert set(REQUIRED_TOOLS) == {"explore", "itinerary", "route"}  # email has no requirement


def test_required_tools_helper():
    assert required_tools("explore", MESSAGE, set(FOUR)) == tuple(FOUR)  # the order of the setting, not of the set
    assert required_tools("explore", MESSAGE, ["get_place_images", "web_search", "send_trip_email"]) == ("web_search", "get_place_images")
    assert required_tools("explore", MESSAGE, []) == ()
    assert required_tools("email", MESSAGE, set(FOUR)) == ()  # sending an email is never forced
    for message in ("hi", "Thanks!", "ok", "yes", "bye"):
        assert required_tools("explore", message, set(FOUR)) == ()


def test_the_explore_prompt_has_the_new_rule_and_a_bumped_version():
    assert prompt_version("explore") == "trailwise.explore@6"
    text = build_system_prompt("explore").text
    for tool in FOUR:
        assert f"`{tool}`" in text
    assert "same round" in text and "once each" in text and "Uttarakhand, India" in text


# ---------------------------------------------------------------- all four together: no extra round

async def test_four_calls_in_one_round_need_no_extra_round(monkeypatch):
    seen = script_model(monkeypatch, [tool_calls_reply(*RISHIKESH), text_reply("Rishikesh is lovely.")])
    assert await run_agent(MESSAGE) == "Rishikesh is lovely."
    assert sorted(n for n, _ in FakeMcp.calls) == sorted(FOUR)  # each tool once
    # Round 1 is forced to be web_search alone (the model cannot answer from its own knowledge first); round 2 is automatic.
    assert names(seen["tool_choices"][0]) == ["web_search"] and seen["tool_choices"][1] is None
    assert len(seen["inputs"]) == 2


# ---------------------------------------------------------------- the extra forced round

async def test_a_skipped_tool_gets_one_extra_forced_round(monkeypatch):
    seen = script_model(monkeypatch, [
        tool_reply(*RISHIKESH[0]),                       # round 1: only web_search
        text_reply("Draft without the rest."),           # round 2: a final answer too early
        tool_calls_reply(*RISHIKESH[1:]),                # round 3: the extra round, the missing three
        text_reply("Final with everything."),            # round 4
    ])
    assert await run_agent(MESSAGE) == "Final with everything."
    choices = seen["tool_choices"]
    assert names(choices[0]) == ["web_search"] and choices[1] is None  # the forced search, then an automatic round
    assert names(choices[2]) == ["get_weather", "geocode_places", "get_place_images"]  # exactly the missing ones, required
    assert choices[3] is None
    assert [n for n, _ in FakeMcp.calls] == FOUR  # nothing ran twice
    assert "Draft without the rest" not in repr(seen["inputs"])  # the too-early draft was dropped, not kept


async def test_a_model_that_answers_without_any_tool_is_forced_to_try_all_four(monkeypatch):
    seen = script_model(monkeypatch, [text_reply("From memory."), tool_calls_reply(*RISHIKESH), text_reply("Final.")])
    assert await run_agent(MESSAGE) == "Final."
    assert names(seen["tool_choices"][1]) == FOUR


async def test_the_extra_round_happens_only_once(monkeypatch):
    seen = script_model(monkeypatch, [
        tool_reply(*RISHIKESH[0]),
        text_reply("d1"),
        tool_reply("get_weather", {"location": "Rishikesh"}),   # the extra round only does one of the missing three
        text_reply("Final."),
    ])
    reply = await run_agent(MESSAGE)
    assert len(seen["inputs"]) == 4  # no second extra round
    assert reply == "Final.\n\nNote: I could not check the map locations and the photos right now."


async def test_web_search_still_not_attempted_after_the_extra_round_gives_the_fixed_reply(monkeypatch):
    script_model(monkeypatch, [
        tool_reply("get_weather", {"location": "Rishikesh"}),
        text_reply("d1"),
        tool_reply("geocode_places", {"names": ["Rishikesh"], "context": "Uttarakhand, India"}),  # ignores web_search
        text_reply("Final from memory."),
    ])
    assert await run_agent(MESSAGE) == NO_LIVE_INFO_REPLY


async def test_no_extra_round_when_there_is_no_room_for_it(monkeypatch):
    monkeypatch.setattr(agent, "MAX_TOOL_ROUNDS", 2)
    seen = script_model(monkeypatch, [tool_reply(*RISHIKESH[0]), text_reply("Final.")])
    assert await run_agent(MESSAGE) == "Final.\n\nNote: I could not check the weather, the map locations and the photos right now."
    assert len(seen["inputs"]) == 2


async def test_the_extra_round_is_logged_with_tool_names_only(monkeypatch):
    events = logged(monkeypatch)
    script_model(monkeypatch, [tool_reply(*RISHIKESH[0]), text_reply("d"), tool_calls_reply(*RISHIKESH[1:]), text_reply("ok")])
    await run_agent(MESSAGE)
    forced = [f for e, s, f in events if e == "model_decision" and f.get("decision") == "force_missing_tools"]
    assert len(forced) == 1 and forced[0]["missing"] == ["get_weather", "geocode_places", "get_place_images"]


# ---------------------------------------------------------------- web_search failure: the existing fixed reply

async def test_web_search_failing_gives_the_fixed_reply_even_if_the_others_worked(monkeypatch):
    fail("web_search")
    seen = script_model(monkeypatch, [tool_calls_reply(*RISHIKESH), text_reply("Never used.")])
    assert await run_agent(MESSAGE) == NO_LIVE_INFO_REPLY == "I could not check live information right now, please try again."
    assert len(seen["inputs"]) == 1  # the model was not asked again, so nothing can come from memory


async def test_web_search_failing_in_the_extra_round_gives_the_fixed_reply(monkeypatch):
    fail("web_search")
    seen = script_model(monkeypatch, [
        tool_reply("get_weather", {"location": "Rishikesh"}), text_reply("d"),
        tool_calls_reply(RISHIKESH[0], RISHIKESH[2], RISHIKESH[3]), text_reply("Never used."),
    ])
    assert await run_agent(MESSAGE) == NO_LIVE_INFO_REPLY
    assert len(seen["inputs"]) == 3


async def test_a_web_search_call_with_unreadable_arguments_counts_as_failed(monkeypatch):
    script_model(monkeypatch, [make_response([call_item("web_search", None, raw_arguments="{broken")]), text_reply("Never used.")])
    assert await run_agent(MESSAGE) == NO_LIVE_INFO_REPLY
    assert FakeMcp.calls == []  # the tool was never called with garbage


# ---------------------------------------------------------------- the other three: answer without that part and say so

@pytest.mark.parametrize("broken, note", [
    (["get_weather"], "the weather"),
    (["geocode_places"], "the map locations"),
    (["get_place_images"], "the photos"),
    (["get_weather", "get_place_images"], "the weather and the photos"),
    (["get_weather", "geocode_places", "get_place_images"], "the weather, the map locations and the photos"),
])
async def test_a_failed_weather_geocode_or_images_is_left_out_and_noted(monkeypatch, broken, note):
    fail(*broken)
    seen = script_model(monkeypatch, [tool_calls_reply(*RISHIKESH), text_reply("Rishikesh is lovely.")])
    assert await run_agent(MESSAGE) == f"Rishikesh is lovely.\n\nNote: I could not check {note} right now."
    # The model was still asked (the search worked) and saw the error object for the failed tool.
    assert len(seen["inputs"]) == 2 and '"ok": false' in repr([i for i in seen["inputs"][1] if i.get("type") == "function_call_output"]).lower()


async def test_a_failure_in_the_extra_round_is_noted_too(monkeypatch):
    fail("get_place_images")
    script_model(monkeypatch, [tool_reply(*RISHIKESH[0]), text_reply("d"), tool_calls_reply(*RISHIKESH[1:]), text_reply("Final.")])
    assert await run_agent(MESSAGE) == "Final.\n\nNote: I could not check the photos right now."


async def test_no_photos_found_is_not_a_failure(monkeypatch):
    Env.results["get_place_images"] = {"ok": True, "found": False, "images": [], "source": "Pexels"}
    script_model(monkeypatch, [tool_calls_reply(*RISHIKESH), text_reply("Final.")])
    assert await run_agent(MESSAGE) == "Final."  # no note: the lookup worked, there were just no photos


async def test_everything_working_adds_no_note(monkeypatch):
    script_model(monkeypatch, [tool_calls_reply(*RISHIKESH), text_reply("Final.")])
    assert await run_agent(MESSAGE) == "Final."


# ---------------------------------------------------------------- when the check does not apply

@pytest.mark.parametrize("message", ["hi", "Hello!", "thanks", "Thank you.", "ok", "Yes", "no", "bye!"])
async def test_greetings_and_short_confirmations_are_never_checked(monkeypatch, message):
    seen = script_model(monkeypatch, [text_reply("Hello!")])
    assert await run_agent(message) == "Hello!"
    assert seen["tool_choices"] == [None] and len(seen["inputs"]) == 1  # not forced, no extra round, no note


async def test_email_mode_has_no_required_tools(monkeypatch):
    seen = script_model(monkeypatch, [text_reply("An answer.")])
    assert await run_agent("Email me this plan") == "An answer."
    assert len(seen["inputs"]) == 1


async def test_only_tools_the_server_offered_are_required(monkeypatch):
    Env.offered = ["web_search", "get_weather"]
    seen = script_model(monkeypatch, [tool_calls_reply(RISHIKESH[0], RISHIKESH[1]), text_reply("Final.")])
    assert await run_agent(MESSAGE) == "Final."  # no geocode or photos were offered, so none are required or noted
    assert len(seen["inputs"]) == 2


async def test_the_extra_round_forces_only_offered_missing_tools(monkeypatch):
    Env.offered = ["get_weather", "send_trip_email"]
    seen = script_model(monkeypatch, [text_reply("d"), tool_reply("get_weather", {"location": "Rishikesh"}), text_reply("Final.")])
    assert await run_agent(MESSAGE) == "Final."
    assert names(seen["tool_choices"][1]) == ["get_weather"]


async def test_no_tools_at_all_means_no_check(monkeypatch):
    Env.offered = []
    seen = script_model(monkeypatch, [text_reply("Plain answer.")])
    assert await run_agent(MESSAGE) == "Plain answer."
    assert seen["tool_choices"] == [None] and len(seen["inputs"]) == 1


async def test_mcp_server_down_means_no_check(monkeypatch):
    FakeMcp.fail_connect = True
    script_model(monkeypatch, [text_reply("Plain answer.")])
    assert await run_agent(MESSAGE) == "Plain answer."


# ---------------------------------------------------------------- duplicate calls in one round

async def test_identical_calls_in_one_round_run_once(monkeypatch):
    seen = script_model(monkeypatch, [
        tool_calls_reply(
            ("web_search", {"query": "Rishikesh", "x": 1}), ("web_search", {"x": 1, "query": "Rishikesh"}),  # same arguments, other key order
            RISHIKESH[1], RISHIKESH[2], RISHIKESH[3],
        ),
        text_reply("Final."),
    ])
    assert await run_agent(MESSAGE) == "Final."
    assert [n for n, _ in FakeMcp.calls].count("web_search") == 1 and len(FakeMcp.calls) == 4
    items = seen["inputs"][1]
    calls = [i for i in items if i.get("type") == "function_call"]
    outputs = [i for i in items if i.get("type") == "function_call_output"]
    assert len(calls) == len(outputs) == 4 and [c["call_id"] for c in calls] == [o["call_id"] for o in outputs]  # no call left unanswered


async def test_calls_with_different_arguments_are_both_kept(monkeypatch):
    script_model(monkeypatch, [
        tool_calls_reply(("web_search", {"query": "Rishikesh rafting"}), ("web_search", {"query": "Rishikesh yoga"}), *RISHIKESH[1:]),
        text_reply("Final."),
    ])
    await run_agent(MESSAGE)
    assert [n for n, _ in FakeMcp.calls].count("web_search") == 2


async def test_the_same_tool_with_different_places_is_not_a_duplicate(monkeypatch):
    script_model(monkeypatch, [
        tool_calls_reply(*RISHIKESH, ("get_weather", {"location": "Haridwar"})), text_reply("Final."),
    ])
    await run_agent(MESSAGE)
    assert [a for n, a in FakeMcp.calls if n == "get_weather"] == [{"location": "Rishikesh"}, {"location": "Haridwar"}]


async def test_identical_unreadable_calls_are_one_refused_call(monkeypatch):
    events = logged(monkeypatch)
    script_model(monkeypatch, [
        make_response([call_item("get_weather", None, raw_arguments="{bad"), call_item("get_weather", None, raw_arguments="{bad")]),
        text_reply("Sorry."),
    ])
    await run_agent("hi")  # a greeting: nothing is required, so the loop just carries on
    statuses = [(s, f["error_code"]) for e, s, f in events if e == "tool_call"]
    assert sorted(statuses) == [("refused", "bad_arguments"), ("skipped", "duplicate")]


async def test_a_dropped_duplicate_is_logged_with_argument_names_only(monkeypatch):
    events = logged(monkeypatch)
    secret = {"query": "Secretville hidden festival", "region": "Secret Region"}
    script_model(monkeypatch, [tool_calls_reply(("web_search", secret), ("web_search", secret), *RISHIKESH[1:]), text_reply("Final.")])
    await run_agent(MESSAGE)
    skipped = [f for e, s, f in events if e == "tool_call" and s == "skipped"]
    assert len(skipped) == 1 and skipped[0]["tool"] == "web_search" and skipped[0]["error_code"] == "duplicate"
    assert skipped[0]["argument_names"] == ["query", "region"]
    assert "Secretville" not in repr(events) and "Secret Region" not in repr(events)
    decision = next(f for e, s, f in events if e == "model_decision" and f.get("decision") == "tool_calls")
    assert decision["tool_names"] == FOUR  # the dropped duplicate is not listed as a second web_search


async def test_the_audit_file_has_argument_names_not_values(monkeypatch, tmp_path):
    """Through the real audit writer: the skipped duplicate line holds names only."""
    from app.core import audit

    settings = audit.get_settings()
    path = tmp_path / "audit.log"
    monkeypatch.setattr(settings, "audit_log_enabled", True)
    monkeypatch.setattr(settings, "audit_log_path", str(path))
    audit.reset_audit_logger()
    try:
        secret = {"query": "Secretville hidden festival"}
        script_model(monkeypatch, [tool_calls_reply(("web_search", secret), ("web_search", secret), *RISHIKESH[1:]), text_reply("Final.")])
        await run_agent(MESSAGE)
        raw = path.read_text(encoding="utf-8")
    finally:
        audit.reset_audit_logger()
    lines = [json.loads(line) for line in raw.splitlines()]
    skipped = [x for x in lines if x["event"] == "tool_call" and x["status"] == "skipped"]
    assert len(skipped) == 1 and skipped[0]["argument_names"] == ["query"]
    assert "Secretville" not in raw


# ---------------------------------------------------------------- a tool that is not configured: say so, do not say "try again"

def not_configured(*tools):
    """Make these tools answer the way an unconfigured tool does."""
    for tool in tools:
        Env.results[tool] = {"ok": False, "error": "not_configured", "message": "Not configured."}


async def test_web_search_not_configured_gives_the_setup_message_not_try_again(monkeypatch, caplog):
    events = logged(monkeypatch)
    not_configured("web_search")
    seen = script_model(monkeypatch, [tool_calls_reply(*RISHIKESH), text_reply("Never used.")])
    with caplog.at_level("WARNING"):
        reply = await run_agent(MESSAGE)
    assert reply == agent.SEARCH_NOT_SET_UP_REPLY and "not set up" in reply and "try again" not in reply
    assert reply != NO_LIVE_INFO_REPLY and len(seen["inputs"]) == 1  # no further model call
    decision = [f for e, s, f in events if e == "model_decision" and f.get("decision") == "web_search_not_configured"]
    assert len(decision) == 1
    assert "OPENAI_SEARCH_MODEL" in caplog.text  # the warning names the setting to fill in, never a value


@pytest.mark.parametrize("code", ["unavailable", "quota_exceeded", "no_results", "invalid_arguments"])
async def test_other_web_search_errors_keep_the_try_again_reply(monkeypatch, code):
    Env.results["web_search"] = {"ok": False, "error": code, "message": "x"}
    script_model(monkeypatch, [tool_calls_reply(*RISHIKESH), text_reply("Never used.")])
    assert await run_agent(MESSAGE) == NO_LIVE_INFO_REPLY


async def test_web_search_not_configured_in_the_extra_round_gives_the_setup_message(monkeypatch):
    not_configured("web_search")
    script_model(monkeypatch, [
        tool_reply("get_weather", {"location": "Rishikesh"}), text_reply("d"),
        tool_calls_reply(RISHIKESH[0], RISHIKESH[2], RISHIKESH[3]), text_reply("Never used."),
    ])
    assert await run_agent(MESSAGE) == agent.SEARCH_NOT_SET_UP_REPLY


async def test_photos_not_configured_are_left_out_with_a_setup_note(monkeypatch, caplog):
    not_configured("get_place_images")
    script_model(monkeypatch, [tool_calls_reply(*RISHIKESH), text_reply("Rishikesh is lovely.")])
    with caplog.at_level("WARNING"):
        reply = await run_agent(MESSAGE)
    assert reply == "Rishikesh is lovely.\n\nNote: I could not check the photos: that is not set up on this server."
    assert "get_place_images is not configured" in caplog.text


async def test_a_temporary_failure_and_a_missing_setup_get_their_own_sentences(monkeypatch):
    fail("get_weather")
    not_configured("get_place_images")
    script_model(monkeypatch, [tool_calls_reply(*RISHIKESH), text_reply("Final.")])
    assert await run_agent(MESSAGE) == (
        "Final.\n\nNote: I could not check the weather right now. "
        "Note: I could not check the photos: that is not set up on this server."
    )


async def test_several_tools_not_configured_are_named_together(monkeypatch):
    not_configured("get_weather", "geocode_places", "get_place_images")
    script_model(monkeypatch, [tool_calls_reply(*RISHIKESH), text_reply("Final.")])
    assert await run_agent(MESSAGE) == (
        "Final.\n\nNote: I could not check the weather, the map locations and the photos: that is not set up on this server."
    )


async def test_a_tool_that_was_never_tried_is_not_called_a_setup_problem(monkeypatch):
    """Only an actual not_configured answer gets the setup wording; a skipped tool keeps the 'right now' note."""
    script_model(monkeypatch, [tool_reply(*RISHIKESH[0]), text_reply("d"), tool_reply("get_weather", {"location": "Rishikesh"}), text_reply("Final.")])
    assert await run_agent(MESSAGE) == "Final.\n\nNote: I could not check the map locations and the photos right now."
