# test_modes.py: tests for mode selection and prompt building (app/agent/modes.py), and for how
# agent.py uses them. The model call is faked (no key, quota or network is used).

import hashlib
import re

import pytest

from app.agent import agent
from app.agent.agent import run_agent
from app.agent import modes
from app.agent.modes import (
    MODES, PROMPT_NAMES, PROMPTS_DIR, PromptHeadingError, build_system_prompt, has_two_places, prompt_version, select_mode,
    select_mode_with_reason, validate_prompts,
)
from tests.test_agent_tools import FakeMcp, script_model, text_reply


@pytest.mark.parametrize("message, expected", [
    ("Email me this plan", "email"),
    ("can you e-mail the trip to me", "email"),
    ("mail me the details", "email"),
    ("send the plan to my inbox", "email"),
    ("Plan a trip to Goa", "itinerary"),
    ("Give me an itinerary for Kerala", "itinerary"),
    ("3 days in Jaipur", "itinerary"),
    ("a 5-day trip to Manali", "itinerary"),
    ("Best train to Delhi?", "route"),
    ("any bus options", "route"),
    ("flights to Goa", "route"),
    ("how to get to Hampi", "route"),
    ("from Bangalore to Mysore", "route"),
    ("What is the route?", "route"),
    ("Where should I go in winter?", "explore"),
    ("hello", "explore"),
])
def test_each_rule_picks_its_mode(message, expected):
    assert select_mode(message) == expected


def test_rule_order_email_then_itinerary_then_route():
    assert select_mode("email me a plan for 3 days") == "email"  # email beats itinerary
    assert select_mode("plan a train trip from Pune to Goa") == "itinerary"  # itinerary beats route


def test_words_inside_other_words_do_not_match():
    assert select_mode("I love my plants") == "explore"  # not "plan"
    assert select_mode("a business trip") == "explore"  # not "bus"
    assert select_mode("trainer tips") == "explore"  # not "train"


def test_previous_mode_is_used_only_when_nothing_matches():
    assert select_mode("tell me more", previous_mode="route") == "route"
    assert select_mode("tell me more", previous_mode="bogus") == "explore"  # invalid is ignored
    assert select_mode("plan it", previous_mode="route") == "itinerary"  # a match beats the previous mode
    assert select_mode("tell me more") == "explore"


@pytest.mark.parametrize("mode", MODES)
def test_prompt_is_system_then_mode_file(mode):
    built = build_system_prompt(mode)
    system = (PROMPTS_DIR / "system.md").read_text(encoding="utf-8").rstrip()
    mode_text = (PROMPTS_DIR / f"{mode}.md").read_text(encoding="utf-8").strip()
    assert built.text.startswith(system)  # stable text first, for implicit caching
    assert built.text.rstrip().endswith(mode_text)
    assert re.fullmatch(r"[0-9a-f]{12}", built.hash)
    assert built.hash == hashlib.sha256(built.text.encode("utf-8")).hexdigest()[:12]


def test_hash_is_stable_and_differs_between_modes():
    assert build_system_prompt("route").hash == build_system_prompt("route").hash
    assert len({build_system_prompt(m).hash for m in MODES}) == len(MODES)


def test_unknown_mode_raises():
    with pytest.raises(ValueError):
        build_system_prompt("shopping")


async def test_agent_sends_the_mode_prompt_and_logs_mode_hash_and_cached_tokens(monkeypatch):
    """run_agent picks the mode from the message, sends that prompt and records it in model_decision."""
    monkeypatch.setattr(agent, "McpClient", FakeMcp)
    FakeMcp.fail_connect = False
    sent, logged = [], []
    reply = text_reply("Take the train.", cached=1234)  # usage.input_tokens_details.cached_tokens = 1234

    async def fake_generate(input_items, tools, system_prompt, tool_choice=None):
        sent.append(system_prompt)
        return reply

    monkeypatch.setattr(agent, "_generate", fake_generate)
    monkeypatch.setattr(agent, "audit_event", lambda event, status="ok", **fields: logged.append((event, fields)))

    await run_agent("Best train from Pune to Goa?")
    expected = build_system_prompt("route")
    assert sent == [expected.text]
    received = next(f for e, f in logged if e == "request_received")
    decision = next(f for e, f in logged if e == "model_decision")
    assert received["mode"] == "route" and received["prompt_hash"] == expected.hash
    assert decision["mode"] == "route" and decision["prompt_hash"] == expected.hash
    assert decision["cached_tokens"] == 1234
    # The version label of each part is logged next to the hash.
    assert received["prompt_versions"] == list(expected.versions) == decision["prompt_versions"]
    assert expected.versions[0].startswith("trailwise.system@") and expected.versions[1].startswith("trailwise.route@")


async def test_cached_tokens_is_zero_when_the_response_has_no_usage(monkeypatch):
    monkeypatch.setattr(agent, "McpClient", FakeMcp)
    script_model(monkeypatch, [text_reply("Hi.")])
    logged = []
    monkeypatch.setattr(agent, "audit_event", lambda event, status="ok", **fields: logged.append((event, fields)))
    await run_agent("hello")
    assert next(f for e, f in logged if e == "model_decision")["cached_tokens"] == 0


# ---- prompt headings and version labels ----

@pytest.mark.parametrize("name", PROMPT_NAMES)
def test_every_prompt_file_has_a_valid_heading(name):
    first = (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8").split("\n", 1)[0]
    assert re.fullmatch(rf"# Trailwise .+ \(prompt ID: trailwise\.{name}, version [1-9]\d*\)", first)
    assert re.fullmatch(rf"trailwise\.{name}@[1-9]\d*", prompt_version(name))


def test_validate_prompts_covers_all_five_files():
    assert set(validate_prompts()) == {"system", "explore", "route", "itinerary", "email"}


def test_built_prompt_carries_both_version_labels():
    built = build_system_prompt("itinerary")
    assert built.versions == (prompt_version("system"), prompt_version("itinerary"))


@pytest.mark.parametrize("bad_first_line", [
    "# Trailwise Route mode",  # no ID or version
    "# Trailwise Route mode (prompt ID: trailwise.route, version 0)",  # versions start at 1
    "# Trailwise Route mode (prompt ID: trailwise.route, version 1.5)",
    "# Trailwise Route mode (prompt ID: trailwise.route, version 1) extra",
    "# Trailwise Route mode (prompt ID: trailwise.explore, version 1)",  # ID does not match the file name
    "Trailwise Route mode (prompt ID: trailwise.route, version 1)",  # no leading '# '
])
def test_bad_heading_is_rejected(monkeypatch, bad_first_line):
    monkeypatch.setattr(modes, "_read", lambda name: bad_first_line + "\n\nbody\n")
    with pytest.raises(PromptHeadingError):
        prompt_version("route")
    with pytest.raises(PromptHeadingError):
        validate_prompts()  # this is what stops the backend from starting


def test_backend_refuses_to_start_with_a_bad_heading(monkeypatch):
    import app.main as main

    monkeypatch.setattr(modes, "_read", lambda name: "no heading here\n")
    monkeypatch.setattr(main, "validate_prompts", modes.validate_prompts)
    with pytest.raises(PromptHeadingError):
        main.create_app()


# ---------------------------------------------------------------- the widened route rule, and mode_reason

@pytest.mark.parametrize("message, reason", [
    ("travel options from Gulbarga to Bangalore", "route_words"),
    ("Gulbarga to Bangalore options", "route_words"),
    ("how can I go to Bangalore from Gulbarga", "route_how_to_go"),
    ("how do I reach Hampi", "route_how_to_go"),
    ("how to travel to Goa", "route_words"),  # the word "travel" is matched first
    ("ways to get to Coorg", "route_ways_to"),
    ("to Mysore from Bangalore", "route_to_from"),
    ("from Pune to Goa", "route_from_to"),
    ("Gulbarga to Bangalore", "route_bare_x_to_y"),
    ("Gulbarga to Bangalore?", "route_bare_x_to_y"),
    ("New Delhi to Navi Mumbai", "route_bare_x_to_y"),
    ("transport in Goa", "route_words"),
    ("travel", "route_words"),
])
@pytest.mark.parametrize("previous", [None, "explore", "itinerary"])
def test_route_rules_and_their_names_beat_the_previous_mode(message, reason, previous):
    assert select_mode_with_reason(message, previous) == ("route", reason)
    assert select_mode(message, previous) == "route"  # transport wins over last_mode, explore included


@pytest.mark.parametrize("message", [
    "how to bake bread",                # "how to" is not a bare "X to Y"
    "how to go",                        # not after how
    "I want to go",                     # starts like a sentence
    "what to see",
    "Gulbarga is far from Bangalore",   # no "to"
    "one two three to four five six",   # more than two words each side
    "hello",
])
def test_these_are_not_the_bare_x_to_y_rule(message):
    mode, reason = select_mode_with_reason(message)
    assert reason != "route_bare_x_to_y"


def test_varanasi_alone_keeps_the_previous_mode_or_explore():
    assert select_mode_with_reason("varanasi") == ("explore", "default")
    assert select_mode_with_reason("Varanasi", "explore") == ("explore", "previous_mode")
    assert select_mode_with_reason("varanasi", "route") == ("route", "previous_mode")  # a bare place name decides nothing
    assert select_mode_with_reason("Varanasi to Lucknow", "explore") == ("route", "route_bare_x_to_y")


def test_tell_me_about_bangalore_after_route_goes_back_to_explore():
    assert select_mode_with_reason("tell me about Bangalore", "route") == ("explore", "explore_words")
    assert select_mode_with_reason("tell me about Bangalore") == ("explore", "explore_words")
    assert select_mode_with_reason("tell me about trains in Bangalore", "explore") == ("route", "route_words")  # transport wins


def test_the_other_rules_have_names_too():
    assert select_mode_with_reason("Email me this plan") == ("email", "email_words")
    assert select_mode_with_reason("Plan a trip to Goa") == ("itinerary", "itinerary_words")
    assert select_mode_with_reason("tell me more", "route") == ("route", "previous_mode")
    assert select_mode_with_reason("tell me more", "bogus") == ("explore", "default")
    # The earlier order still holds: email, then itinerary, then route.
    assert select_mode_with_reason("plan a train trip from Pune to Goa")[0] == "itinerary"


@pytest.mark.parametrize("message, expected", [
    ("from Pune to Goa", True), ("to Mysore from Bangalore", True), ("Gulbarga to Bangalore", True),
    ("how can I go to Bangalore from Gulbarga", True),
    ("buses to Goa", False),          # a transport word on the left: no source city
    ("any bus options", False), ("how to get to Hampi", False), ("Best train", False), ("hello", False),
])
def test_has_two_places(message, expected):
    assert has_two_places(message) is expected


async def test_request_received_logs_the_rule_name_and_never_the_message(monkeypatch):
    monkeypatch.setattr(agent, "McpClient", FakeMcp)
    FakeMcp.fail_connect = False
    script_model(monkeypatch, [text_reply("Which city are you leaving from?")])
    logged = []
    monkeypatch.setattr(agent, "audit_event", lambda event, status="ok", **fields: logged.append((event, fields)))
    secret_message = "how can I go to Zzyzx from Qwertyville"
    await run_agent(secret_message, last_mode="explore")
    received = next(f for e, f in logged if e == "request_received")
    assert received["mode"] == "route" and received["mode_reason"] == "route_how_to_go" and received["last_mode"] == "explore"
    assert "Zzyzx" not in repr(logged) and "Qwertyville" not in repr(logged)


async def test_request_received_reason_is_kept_in_the_real_audit_file(monkeypatch, tmp_path):
    from app.core import audit

    settings = audit.get_settings()
    path = tmp_path / "audit.log"
    monkeypatch.setattr(settings, "audit_log_enabled", True)
    monkeypatch.setattr(settings, "audit_log_path", str(path))
    monkeypatch.setattr(agent, "McpClient", FakeMcp)
    FakeMcp.fail_connect = False
    script_model(monkeypatch, [text_reply("Hi.")])
    await run_agent("hello", last_mode="route")
    import json as _json

    line = next(_json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if '"request_received"' in l)
    assert line["mode"] == "route" and line["mode_reason"] == "previous_mode"
