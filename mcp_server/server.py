# server.py: the Trailwise MCP tool server. Registers every tool and serves them over stdio.
# Run from mcp_server/:  python server.py   (an MCP client launches it as a subprocess).

import logging

from mcp.server.fastmcp import FastMCP

from tools.bus import search_buses
from tools.email_tool import send_trip_email
from tools.train_tool import get_train_details
from tools.weather import get_weather
from tools.web_search import web_search

# Security: httpx logs every request URL at INFO, and some APIs (e.g. OpenWeatherMap) take the
# API key in the URL. Keep the HTTP libraries at WARNING so keys never reach the logs.
# This covers every tool registered below, not only weather.
for _name in ("httpx", "httpcore", "httpx2", "google_genai", "aiosmtplib"):  # the Gemini SDK logs via httpx2 and google_genai; aiosmtplib is the email tool's SMTP client
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

# Register the train tool (RailRadar). Scheduled details only: no live status, seat counts or fares.
# Results are data for the agent and always carry a verify-before-travel notice.
mcp.tool(
    name="get_train_details",
    description=(
        "Get an Indian train's details by its number (e.g. '12919'): name, type, route ends, run days, "
        "classes and scheduled stops with times. Scheduled data only: no live status, seat availability "
        "or fares. Always tell the user to verify before travel."
    ),
)(get_train_details)

# Register the bus tool (Parse.bot redBus API, snapshot 11). Prices and seats are third-party data:
# they are shown only as the tool returned them, with a verify-before-travel notice. It cannot book.
mcp.tool(
    name="search_buses",
    description=(
        "Search buses between two Indian cities on a date. Arguments: source_city, destination_city "
        "(names such as 'Bangalore') and journey_date as YYYY-MM-DD (today up to 120 days ahead). "
        "Returns operators, departure and arrival times, bus types, fares in INR and available seats. "
        "Third-party data that can be out of date: always tell the user to verify before travel. Cannot book."
    ),
)(search_buses)

# Register the email tool. It has no recipient argument: it can only email the user's own verified
# address from .env. The agent must ask the user first and pass confirmed=true (see prompts/email.md).
mcp.tool(
    name="send_trip_email",
    description=(
        "Email a trip plan to the user's own verified address (the recipient is fixed; you cannot choose it). "
        "Arguments: subject (one line), body (plain text, up to 5000 characters) and confirmed. "
        "Only call it after the user clearly asked for the email and confirmed the subject and body you showed "
        "them, then pass confirmed=true. Never pass confirmed=true on your own."
    ),
)(send_trip_email)

if __name__ == "__main__":
    # stdio transport: the client talks to this process over stdin/stdout.
    mcp.run(transport="stdio")
