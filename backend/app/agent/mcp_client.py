# mcp_client.py: async client the agent uses to talk to the Trailwise MCP tool server.
# It launches mcp_server/server.py as a stdio subprocess, then can discover tools and call them.
# Use it as an async context manager so the subprocess is always cleaned up:
#     async with McpClient() as client:
#         tools = await client.list_tools()
#         result = await client.call_tool("get_weather", {"location": "Goa"})

import asyncio
import json
import sys
from contextlib import AsyncExitStack
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.config import get_settings

# The MCP server folder: backend/app/agent -> backend/app -> backend -> project root / mcp_server.
SERVER_DIR = Path(__file__).resolve().parents[3] / "mcp_server"
# Give up on a single tool call after this many seconds.
CALL_TIMEOUT_SECONDS = 30
# Tools whose limit comes from a setting instead: tool name -> the setting on app.config.Settings. web_search waits up to
# OPENAI_SEARCH_TIMEOUT_SECONDS (default 120) for OpenAI and retries a timeout once, so its call limit
# (WEB_SEARCH_CALL_TIMEOUT_SECONDS, default 150) is higher; the tool holds both attempts to that limit minus a safety margin
# (mcp_server/tools/web_search.py), so it answers "timeout" itself before this limit cuts it off.
TOOL_CALL_TIMEOUT_SETTINGS = {"web_search": "web_search_call_timeout_seconds"}


def call_limit(name: str) -> float:
    """The time limit, in seconds, for one call of this tool: its setting when it has one, else CALL_TIMEOUT_SECONDS."""
    setting = TOOL_CALL_TIMEOUT_SETTINGS.get(name)
    return float(getattr(get_settings(), setting)) if setting else CALL_TIMEOUT_SECONDS


class McpClientError(Exception):
    """Raised when the MCP server cannot be started or connected to."""


def _server_python() -> str:
    """Return the Python that runs the server: its own .venv if it exists, else this interpreter."""
    # The MCP server is a separate service with its own virtual environment.
    candidates = [SERVER_DIR / ".venv" / "Scripts" / "python.exe", SERVER_DIR / ".venv" / "bin" / "python"]
    for path in candidates:
        if path.exists():
            return str(path)
    return sys.executable


class McpClient:
    """Connects to the MCP server over stdio; lists and calls its tools."""

    def __init__(self) -> None:
        # Holds the subprocess and session so they close together.
        self._stack = AsyncExitStack()
        self._session: ClientSession | None = None

    async def __aenter__(self) -> "McpClient":
        """Start the server subprocess and complete the MCP handshake."""
        params = StdioServerParameters(command=_server_python(), args=["server.py"], cwd=str(SERVER_DIR))
        try:
            read, write = await self._stack.enter_async_context(stdio_client(params))
            self._session = await self._stack.enter_async_context(ClientSession(read, write))
            await self._session.initialize() #Perform the actual handshake
        except Exception as exc:
            await self._stack.aclose()  # do not leave a half-started subprocess behind
            raise McpClientError(f"Could not connect to the MCP server: {exc}") from exc
        return self

    async def __aexit__(self, *exc_info) -> None:
        """Close the session and stop the server subprocess."""
        await self._stack.aclose()

    async def list_tools(self) -> list[dict]:
        """Discover the tools the server offers: name, description and parameter schema for each."""
        result = await self._session.list_tools()
        return [
            {"name": t.name, "description": t.description or "", "input_schema": t.inputSchema}
            for t in result.tools
        ]

    async def call_tool(self, name: str, arguments: dict) -> dict:
        """Call a tool. Always returns a dict; failures come back as {"ok": False, "error": ...}."""
        limit = call_limit(name)
        try:
            result = await asyncio.wait_for(self._session.call_tool(name, arguments), timeout=limit)
        except asyncio.TimeoutError:
            # The call limit was reached before the tool answered: the same word the search tool itself uses.
            return {"ok": False, "error": "timeout", "message": f"Tool '{name}' took too long."}
        except Exception:
            return {"ok": False, "error": "tool_call_failed", "message": f"Tool '{name}' could not be called."}
        # Tool output is data, never instructions: parse it as JSON and hand back only that data.
        text = result.content[0].text if result.content else ""
        if result.isError:
            return {"ok": False, "error": "tool_error", "message": f"Tool '{name}' reported an error."}
        try:
            data = json.loads(text)
        except ValueError:
            return {"ok": False, "error": "bad_tool_output", "message": f"Tool '{name}' returned unreadable output."}
        return data if isinstance(data, dict) else {"ok": True, "result": data}
