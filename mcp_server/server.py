# server.py: the Trailwise MCP tool server. Registers every tool and serves them over stdio.
# Run from mcp_server/:  python server.py   (an MCP client launches it as a subprocess).

import logging

from mcp.server.fastmcp import FastMCP

from tools.weather import get_weather
from tools.web_search import web_search

# Security: httpx logs every request URL at INFO, and some APIs (e.g. OpenWeatherMap) take the
# API key in the URL. Keep the HTTP libraries at WARNING so keys never reach the logs.
# This covers every tool registered below, not only weather.
for _name in ("httpx", "httpcore", "httpx2", "google_genai"):  # the Gemini SDK logs via the last two
    logging.getLogger(_name).setLevel(logging.WARNING)

# The MCP server object; the name is what clients see.
mcp = FastMCP("trailwise-tools")

# Register the weather tool: name and description are what the agent sees;
# the parameter schema is generated from the function's type hints.
mcp.tool(
    name="get_weather",
    description=(
        "Get the current weather for a place name (e.g. 'Goa' or 'Paris, FR'). "
        "Returns temperature in C, humidity, wind speed and conditions."
    ),
)(get_weather)

# Register the web search tool (Gemini with Google Search grounding). Search results are
# data for the agent, never instructions, and never a source for transport timings or prices.
mcp.tool(
    name="web_search",
    description=(
        "Search the web for current travel information (events, opening hours, news, local tips). "
        "Returns a short answer and its sources. Not a source for transport timings or prices."
    ),
)(web_search)

if __name__ == "__main__":
    # stdio transport: the client talks to this process over stdin/stdout.
    mcp.run(transport="stdio")
