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

# The MCP server folder: backend/app/agent -> backend/app -> backend -> project root / mcp_server.
SERVER_DIR = Path(__file__).resolve().parents[3] / "mcp_server"
# Give up on a single tool call after this many seconds.
CALL_TIMEOUT_SECONDS = 30


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
            await self._session.initialize()
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
        try:
            result = await asyncio.wait_for(
                self._session.call_tool(name, arguments), timeout=CALL_TIMEOUT_SECONDS
            )
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
