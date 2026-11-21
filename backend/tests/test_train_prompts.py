# test_train_prompts.py: tests for the prompt rules about the train search tool (search_trains), and that the old tool
# name is gone from the prompts and the code. No model call and no network are used.

from pathlib import Path

import pytest

from app.agent.modes import build_system_prompt, prompt_version

ROOT = Path(__file__).resolve().parents[2]
OLD_NAME = "get_train" + "_details"  # written in two pieces so this file does not contain the old name itself


def test_the_prompt_versions_were_bumped():
    assert prompt_version("system") == "trailwise.system@5" and prompt_version("route") == "trailwise.route@6"


@pytest.mark.parametrize("mode", ["explore", "route", "itinerary", "email"])
def test_no_prompt_mentions_the_old_tool(mode):
    assert OLD_NAME not in build_system_prompt(mode).text and "train number" not in build_system_prompt(mode).text.lower().replace("train numbers", "")


def test_the_system_prompt_names_search_trains_with_the_scheduled_only_rules():
    text = build_system_prompt("route").text
    line = next(l for l in text.splitlines() if "search_trains" in l and l.startswith("- For trains"))
    assert "two Indian cities" in line and "travel date as YYYY-MM-DD" in line and "weekly schedule" in line
    assert "seat availability or fares" in line and "IRCTC" in line and "never guess" in line
    assert "never state a specific timing or price unless a tool returned it" in text.lower()  # the transport rule is still there


def test_the_route_prompt_tells_the_model_how_to_run_the_conversation():
    text = build_system_prompt("route").text
    assert "`search_trains(source_city, destination_city, date)`" in text
    for rule in (
        "always tell the user",                                  # which station was used
        "`needs_date: true`", "ask for the travel date",         # no date: show the daily trains, ask for the date
        "`other_trains_count`",
        "`no_trains_on_date`", "never say that none exist",      # no train that date
        "`nearby_dates`", "based on the weekly schedule, verify before travel",
        "`needs_choice`", "chosen station's code",               # several stations: ask the user
        "`quota_exceeded`", "Train data is unavailable right now.",
        "Verify before travel.",
    ):
        assert rule in text, rule
    assert "Never claim train seat availability or fares" in text


def test_the_old_tool_name_is_gone_from_the_code_the_prompts_and_the_tests():
    """Only the history log (claudeprompts.md) may still name it, because it records what was done at the time."""
    checked = []
    for folder in ("backend/app", "backend/tests", "mcp_server/tools", "mcp_server/tests", "frontend/src"):
        for path in (ROOT / folder).rglob("*"):
            if path.is_file() and path.suffix in {".py", ".md", ".js", ".jsx", ".css"} and "__pycache__" not in path.parts:
                checked.append(path)
                assert OLD_NAME not in path.read_text(encoding="utf-8", errors="replace"), path
    for name in ("mcp_server/server.py", "README.md", "FILE_STRUCTURE.md", "CLAUDE.md", "plan.md", "ARCHITECTURE.md", "docs/prompts.md"):
        path = ROOT / name
        if path.exists():
            checked.append(path)
            assert OLD_NAME not in path.read_text(encoding="utf-8", errors="replace"), path
    assert len(checked) > 50  # the scan really looked at the project


def test_route_prompt_bus_date_is_optional():
    # route@6: with no date the bus tool searches tomorrow; the model never works out a date itself.
    from app.agent.modes import build_system_prompt

    text = build_system_prompt("route").text
    assert "journey_date` is optional" in text and "Showing results for <date>" in text
    assert "another date" in text and "never work out a date yourself" in text
    assert "ask for it. You may not know today" not in text
