# test_web_search.py: tests for the web_search tool and its registration in server.py.
# Gemini is faked, so no key, network or quota is needed.

from types import SimpleNamespace

import pytest

from tools import web_search


def make_response(text="Lisbon hosts a jazz festival.", chunks=None, queries=None):
    """Build a fake Gemini response with grounding metadata."""
    chunks = chunks if chunks is not None else [
        SimpleNamespace(web=SimpleNamespace(uri="https://example.com/a", title="example.com")),
        SimpleNamespace(web=SimpleNamespace(uri="https://example.com/a", title="dup")),  # duplicate URL
        SimpleNamespace(web=SimpleNamespace(uri="https://example.org/b", title="example.org")),
    ]
    meta = SimpleNamespace(grounding_chunks=chunks, web_search_queries=queries or ["lisbon events"])
    return SimpleNamespace(text=text, candidates=[SimpleNamespace(grounding_metadata=meta)])


class FakeGemini:
    """Stand-in for genai.Client: returns a canned response or raises. Counts calls."""

    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, 0
        self.last_config = None
        self.aio = SimpleNamespace(models=SimpleNamespace(generate_content=self._generate))

    async def _generate(self, model, contents, config):
        self.calls += 1
        self.last_config = config
        if self.error:
            raise self.error
        return self.response


@pytest.fixture
def setup(monkeypatch):
    """Fake key and a helper to install a fake Gemini client."""
    monkeypatch.setattr(web_search.get_settings(), "gemini_api_key", "fake-key")

    def install(client):
        monkeypatch.setattr(web_search, "_get_client", lambda: client)
        return client

    return install


async def test_calls_gemini_and_shapes_result(setup):
    client = setup(FakeGemini(make_response()))
    result = await web_search.web_search("events in Lisbon")
    assert result["ok"] and client.calls == 1
    assert result["answer"].startswith("Lisbon")
    # Duplicate URL removed; titles and URLs only.
    assert [s["url"] for s in result["sources"]] == ["https://example.com/a", "https://example.org/b"]
    assert result["queries"] == ["lisbon events"]
    # Only google_search is offered to the grounded call.
    assert client.last_config.tools[0].google_search is not None


@pytest.mark.parametrize("bad", ["", "   ", "ab", "x" * 201, "line1\nline2", "tab\there"])
async def test_bad_arguments(setup, bad):
    result = await web_search.web_search(bad)
    assert result["ok"] is False and result["error"] == "invalid_arguments"


async def test_gemini_failure_and_quota(setup):
    install = setup
    # A 429 quota error, as seen with grounding on the free tier, becomes a clean error object.
    install(FakeGemini(error=RuntimeError("429 RESOURCE_EXHAUSTED")))
    result = await web_search.web_search("events in Lisbon")
    assert result["ok"] is False and result["error"] == "unavailable"
    assert "429" not in result["message"]  # SDK details stay out of the reply


async def test_empty_answer(setup):
    install = setup
    install(FakeGemini(make_response(text="")))
    assert (await web_search.web_search("events in Lisbon"))["error"] == "no_results"


async def test_answer_and_sources_are_capped(setup):
    install = setup
    many = [SimpleNamespace(web=SimpleNamespace(uri=f"https://e.com/{i}", title="t")) for i in range(20)]
    install(FakeGemini(make_response(text="x" * 5000, chunks=many)))
    result = await web_search.web_search("events in Lisbon")
    assert len(result["answer"]) == web_search.MAX_ANSWER_CHARS
    assert len(result["sources"]) == web_search.MAX_SOURCES


async def test_missing_key(setup, monkeypatch):
    monkeypatch.setattr(web_search.get_settings(), "gemini_api_key", "")
    assert (await web_search.web_search("events in Lisbon"))["error"] == "not_configured"


async def test_tool_registered_in_server():
    # The tool must be listed by the real MCP server, with a query parameter.
    import server

    tools = await server.mcp.list_tools()
    tool = next(t for t in tools if t.name == "web_search")
    assert "query" in tool.inputSchema["properties"]
