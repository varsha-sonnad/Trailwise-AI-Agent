# test_mcp_client.py: integration tests for the MCP client against the real MCP server (stdio).
# No weather API call is made (the call test uses a bad argument), so no key or quota is needed.

from app.agent.mcp_client import McpClient


async def test_connect_and_discover_weather_tool():
    # Connecting succeeds and get_weather is discovered with a location parameter.
    async with McpClient() as client:
        tools = await client.list_tools()
    weather = next(t for t in tools if t["name"] == "get_weather")
    assert "location" in weather["input_schema"]["properties"]
    assert weather["description"]


async def test_call_tool_returns_error_object_for_bad_argument():
    # The server validates the argument and returns an error object, not an exception.
    async with McpClient() as client:
        result = await client.call_tool("get_weather", {"location": "bad;input"})
    assert result["ok"] is False and result["error"] == "invalid_arguments"


async def test_unknown_tool_returns_error_object():
    # A call the server rejects becomes a clean error object.
    async with McpClient() as client:
        result = await client.call_tool("no_such_tool", {})
    assert result["ok"] is False
