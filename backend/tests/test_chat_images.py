# test_chat_images.py: tests for the photos that travel with a reply: the ChatImage model (Pexels hosts only, credit
# required), how the agent collects the photos from get_place_images and attaches them to the reply (AgentReply), and
# the "images" field of the POST /chat response. The model call and the MCP client are faked.

import json

import pytest
from fastapi.testclient import TestClient

from app.agent import agent, modes
from app.agent.agent import NO_LIVE_INFO_REPLY, AgentReply, run_agent
from app.core import audit
from app.main import app
from app.models.chat import MAX_CHAT_IMAGES, ChatImage, ChatResponse, clean_images, is_pexels_url
from tests.test_agent_tools import FakeMcp, script_model, text_reply, tool_calls_reply, tool_reply
from tests.test_tool_choice import tool_list


def img(n=1, **over):
    """One photo in the shape get_place_images returns."""
    item = {
        "url": f"https://images.pexels.com/photos/{n}/large.jpeg",
        "thumb": f"https://images.pexels.com/photos/{n}/medium.jpeg",
        "alt": f"Stone temple {n}",
        "photographer": f"Photographer {n}",
        "photographer_url": f"https://www.pexels.com/@photographer-{n}",
        "pexels_url": f"https://www.pexels.com/photo/hampi-{n}/",
    }
    item.update(over)
    return item


# ---------------------------------------------------------------- the model

@pytest.mark.parametrize("url, expected", [
    ("https://images.pexels.com/photos/1/large.jpeg", True),
    ("https://www.pexels.com/photo/x/", True),
    ("https://pexels.com/photo/x/", True),
    ("http://images.pexels.com/a.jpg", False),            # not https
    ("https://pexels.com.evil.example/a.jpg", False),      # look-alike host
    ("https://evilpexels.com/a.jpg", False),
    ("https://images.pexels.com.evil.example/a.jpg", False),
    ("javascript:alert(1)", False), ("data:image/png;base64,AAAA", False), ("//images.pexels.com/a.jpg", False),
    ("", False), (None, False), (5, False), ("https://images.pexels.com/" + "a" * 600, False),
])
def test_is_pexels_url(url, expected):
    assert is_pexels_url(url) is expected


def test_a_good_photo_is_valid():
    assert ChatImage.model_validate(img(1)).photographer == "Photographer 1"


@pytest.mark.parametrize("bad", [
    img(1, url="https://evil.example.com/a.jpg"), img(1, pexels_url="https://evil.example.com/p"), img(1, thumb="https://evil.example.com/t"),
    img(1, photographer_url="https://evil.example.com/me"), img(1, photographer=""), img(1, photographer="x" * 101),
    img(1, alt="x" * 201), {"url": img(1)["url"]},
])
def test_a_photo_that_breaks_a_rule_is_not_valid(bad):
    with pytest.raises(Exception):
        ChatImage.model_validate(bad)


def test_thumb_and_photographer_url_may_be_empty():
    image = ChatImage.model_validate(img(1, thumb="", photographer_url=""))
    assert image.thumb == "" and image.photographer_url == ""


def test_clean_images_drops_invalid_and_duplicates_and_caps():
    raw = [img(1), img(1), img(2, url="https://evil.example.com/x.jpg"), "not a dict", None, img(3)] + [img(n) for n in range(10, 30)]
    cleaned = clean_images(raw)
    assert [i["photographer"] for i in cleaned[:2]] == ["Photographer 1", "Photographer 3"]
    assert len(cleaned) == MAX_CHAT_IMAGES == 6 and all(isinstance(i, dict) for i in cleaned)


@pytest.mark.parametrize("raw", [None, "text", 5, {}, [], ()])
def test_clean_images_of_anything_else_is_empty(raw):
    assert clean_images(raw) == []


def test_the_response_model_defaults_to_no_images():
    assert ChatResponse(reply="Hi").model_dump() == {"reply": "Hi", "images": [], "mode": None, "places": [], "legs": [], "saved": None}


# ---------------------------------------------------------------- the agent attaches the photos

PLACE_CALLS = [
    ("web_search", {"query": "Hampi"}), ("get_weather", {"location": "Hampi"}),
    ("geocode_places", {"names": ["Hampi"], "context": "Karnataka, India"}),
    ("get_place_images", {"place": "Hampi", "region": "Karnataka, India"}),
]


class Env:
    results: dict = {}


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    """The explore required-tools check ON, a fake MCP server offering the four tools."""
    monkeypatch.setattr(modes, "REQUIRED_TOOLS", {"explore": ("web_search", "get_weather", "geocode_places", "get_place_images")})
    FakeMcp.calls, FakeMcp.fail_connect, FakeMcp.fail_list = [], False, False
    Env.results = {}
    monkeypatch.setattr(agent, "McpClient", FakeMcp)

    async def list_tools(self):
        return tool_list("web_search", "get_weather", "geocode_places", "get_place_images")

    async def call_tool(self, name, arguments):
        FakeMcp.calls.append((name, arguments))
        return Env.results.get(name, {"ok": True, "data": name})

    monkeypatch.setattr(FakeMcp, "list_tools", list_tools, raising=False)
    monkeypatch.setattr(FakeMcp, "call_tool", call_tool)


def photos_result(*images):
    return {"ok": True, "found": True, "images": list(images), "source": "Pexels"}


async def test_the_reply_carries_the_photos_of_a_successful_image_call(monkeypatch):
    Env.results["get_place_images"] = photos_result(img(1), img(2), img(3))
    script_model(monkeypatch, [tool_calls_reply(*PLACE_CALLS), text_reply("Hampi is lovely.")])
    reply = await run_agent("Tell me about Hampi")
    assert reply == "Hampi is lovely." and isinstance(reply, AgentReply)  # still just the text as a string
    assert [i["photographer"] for i in reply.images] == ["Photographer 1", "Photographer 2", "Photographer 3"]


async def test_the_reply_text_does_not_contain_the_photos(monkeypatch):
    Env.results["get_place_images"] = photos_result(img(1))
    script_model(monkeypatch, [tool_calls_reply(*PLACE_CALLS), text_reply("Hampi is lovely.")])
    reply = await run_agent("Tell me about Hampi")
    assert "pexels" not in str(reply).lower() and "Photographer" not in str(reply)


@pytest.mark.parametrize("result", [
    {"ok": True, "found": False, "images": [], "source": "Pexels"},
    {"ok": False, "error": "unavailable", "message": "x"},
    {"ok": False, "error": "not_configured", "message": "x"},
    {"ok": True, "found": True, "images": "nope"},
])
async def test_no_photos_when_the_image_call_found_none_or_failed(monkeypatch, result):
    Env.results["get_place_images"] = result
    script_model(monkeypatch, [tool_calls_reply(*PLACE_CALLS), text_reply("Hampi is lovely.")])
    assert (await run_agent("Tell me about Hampi")).images == []


async def test_photos_from_other_tools_are_ignored(monkeypatch):
    Env.results["web_search"] = {"ok": True, "answer": "x", "sources": [], "images": [img(1)]}
    Env.results["get_place_images"] = {"ok": False, "error": "unavailable"}
    script_model(monkeypatch, [tool_calls_reply(*PLACE_CALLS), text_reply("Fine.")])
    assert (await run_agent("Tell me about Hampi")).images == []


async def test_only_pexels_photos_with_a_credit_are_attached(monkeypatch):
    Env.results["get_place_images"] = photos_result(
        img(1, url="https://evil.example.com/a.jpg"), img(2, photographer=""), img(3, photographer_url="https://evil.example.com/me"), img(4))
    script_model(monkeypatch, [tool_calls_reply(*PLACE_CALLS), text_reply("Fine.")])
    assert [i["photographer"] for i in (await run_agent("Tell me about Hampi")).images] == ["Photographer 4"]


async def test_the_total_is_capped_and_duplicates_are_dropped(monkeypatch):
    Env.results["get_place_images"] = photos_result(*[img(n) for n in range(1, 9)], img(1))
    script_model(monkeypatch, [tool_calls_reply(*PLACE_CALLS), text_reply("Fine.")])
    reply = await run_agent("Tell me about Hampi")
    assert len(reply.images) == 6 and len({i["url"] for i in reply.images}) == 6


async def test_photos_are_attached_when_the_extra_round_fetched_them(monkeypatch):
    Env.results["get_place_images"] = photos_result(img(1))
    script_model(monkeypatch, [
        tool_reply(*PLACE_CALLS[0]), text_reply("Draft."), tool_calls_reply(*PLACE_CALLS[1:]), text_reply("Final."),
    ])
    reply = await run_agent("Tell me about Hampi")
    assert reply == "Final." and [i["photographer"] for i in reply.images] == ["Photographer 1"]


async def test_the_photos_stay_with_the_text_when_a_note_is_added(monkeypatch):
    Env.results["get_place_images"] = photos_result(img(1))
    Env.results["get_weather"] = {"ok": False, "error": "location_not_found", "message": "x"}
    script_model(monkeypatch, [tool_calls_reply(*PLACE_CALLS), text_reply("Hampi is lovely.")])
    reply = await run_agent("Tell me about Hampi")
    assert reply == "Hampi is lovely.\n\nNote: I could not check the weather right now." and len(reply.images) == 1


async def test_a_failed_web_search_gives_the_fixed_reply_with_no_photos(monkeypatch):
    Env.results["get_place_images"] = photos_result(img(1))
    Env.results["web_search"] = {"ok": False, "error": "unavailable", "message": "x"}
    script_model(monkeypatch, [tool_calls_reply(*PLACE_CALLS), text_reply("Never used.")])
    reply = await run_agent("Tell me about Hampi")
    assert reply == NO_LIVE_INFO_REPLY and getattr(reply, "images", []) == []


async def test_each_request_starts_without_photos_from_the_one_before(monkeypatch):
    Env.results["get_place_images"] = photos_result(img(1))
    script_model(monkeypatch, [tool_calls_reply(*PLACE_CALLS), text_reply("One."), text_reply("Hello!")])
    assert len((await run_agent("Tell me about Hampi")).images) == 1
    assert (await run_agent("hi")).images == []


async def test_the_audit_log_has_the_photo_count_but_no_addresses_or_names(monkeypatch, tmp_path):
    settings = audit.get_settings()
    path = tmp_path / "audit.log"
    monkeypatch.setattr(settings, "audit_log_enabled", True)
    monkeypatch.setattr(settings, "audit_log_path", str(path))
    audit.reset_audit_logger()
    try:
        Env.results["get_place_images"] = photos_result(img(1), img(2))
        script_model(monkeypatch, [tool_calls_reply(*PLACE_CALLS), text_reply("Fine.")])
        await run_agent("Tell me about Hampi")
        raw = path.read_text(encoding="utf-8")
    finally:
        audit.reset_audit_logger()
    final = [json.loads(x) for x in raw.splitlines() if "final_answer" in x][0]
    assert final["image_count"] == 2
    assert "pexels" not in raw.lower() and "Photographer" not in raw


# ---------------------------------------------------------------- the /chat response

@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_the_chat_response_has_the_images_of_the_reply(client, monkeypatch):
    async def fake_run_agent(message, **memory):
        return AgentReply("Hampi is lovely.", [img(1), img(2)])

    monkeypatch.setattr("app.api.chat.run_agent", fake_run_agent)
    body = client.post("/chat", json={"message": "Tell me about Hampi"}).json()
    assert body["reply"] == "Hampi is lovely."
    assert [i["photographer"] for i in body["images"]] == ["Photographer 1", "Photographer 2"]
    assert set(body["images"][0]) == {"url", "thumb", "alt", "photographer", "photographer_url", "pexels_url"}


def test_a_plain_string_reply_gives_an_empty_images_list(client, monkeypatch):
    async def fake_run_agent(message, **memory):
        return "Just text."

    monkeypatch.setattr("app.api.chat.run_agent", fake_run_agent)
    assert client.post("/chat", json={"message": "hi"}).json() == {"reply": "Just text.", "images": [], "mode": None, "places": [], "legs": [], "saved": None}


def test_the_route_checks_the_images_again_so_a_bad_one_never_reaches_the_browser(client, monkeypatch):
    async def fake_run_agent(message, **memory):
        reply = AgentReply("Hi.")
        reply.images = [img(1, url="https://evil.example.com/a.jpg"), img(2), "junk"]  # bypasses the agent's own check
        return reply

    monkeypatch.setattr("app.api.chat.run_agent", fake_run_agent)
    body = client.post("/chat", json={"message": "hi"}).json()
    assert [i["photographer"] for i in body["images"]] == ["Photographer 2"]
    assert "evil.example.com" not in json.dumps(body)


# ---------------------------------------------------------------- the prompts say the app shows the photos

@pytest.mark.parametrize("mode, label", [("explore", "trailwise.explore@6"), ("itinerary", "trailwise.itinerary@6")])
def test_explore_and_itinerary_prompts_never_let_the_model_talk_about_photos(mode, label):
    from app.agent.modes import build_system_prompt, prompt_version

    assert prompt_version(mode) == label  # the version label was bumped with the edit
    text = build_system_prompt(mode).text
    assert "Never list, describe, link or credit photos in your reply" in text
    assert "The app shows the photos itself" in text
    # The old rules that told the model to show and credit photos itself are gone.
    for old in ("Always show each photo", "never show a photo without its credit", "photographer_url", "say you found no photos"):
        assert old not in text
