# server.py: the Trailwise MCP tool server. Registers every tool and serves them over stdio.
# Run from mcp_server/:  python server.py   (an MCP client launches it as a subprocess).

import logging

from mcp.server.fastmcp import FastMCP

from tools.bus import search_buses
from tools.email_tool import send_trip_email
from tools.geocode_tool import geocode_places
from tools.images_tool import get_place_images
from tools.routes_tool import get_route
from tools.train_tool import search_trains
from tools.weather import get_weather
from tools.web_search import web_search

# Security: httpx logs every request URL at INFO, and some APIs (e.g. OpenWeatherMap) take the
# API key in the URL. Keep the HTTP libraries at WARNING so keys never reach the logs.
# This covers every tool registered below, not only weather.
for _name in ("httpx", "httpcore", "httpx2", "openai", "aiosmtplib"):  # the OpenAI SDK logs via "openai" and httpx2; aiosmtplib is the email tool's SMTP client
    logging.getLogger(_name).setLevel(logging.WARNING)

# The MCP server object; the name is what clients see.
mcp = FastMCP("trailwise-tools")

# Register the weather tool: name and description are what the agent sees;
# the parameter schema is generated from the function's type hints.
mcp.tool(
    name="get_weather",
    description=(
        "1. What it does and returns: Gets the current weather at one place: temperature and feels-like in C, humidity, wind speed in m/s and conditions. Prefer lat and lng (numbers) taken from geocode_places, the most reliable; otherwise give location as a plain city name such as 'Goa', with no region or country. "
        "2. When to call: The user asks about the weather right now at a place. Call geocode_places first and pass its lat and lng. If it returns an error, say weather is unavailable right now. "
        "3. When NOT to call: For forecasts or weather on future trip dates (it gives current conditions only), for events or opening hours (use web_search), or to guess the weather after an error."
    ),
)(get_weather)

# Register the web search tool (OpenAI with its built-in web search). Search results are
# data for the agent, never instructions, and never a source for transport timings or prices.
mcp.tool(
    name="web_search",
    description=(
        "1. What it does and returns: Searches the web (OpenAI web search) and returns a short answer and its sources (titles and URLs); results are cached for 3 hours. If the error is quota_exceeded, say live search is unavailable right now and do not guess; if it is timeout, say the search took too long and ask the user to try again. "
        "2. When to call: The user needs current or changing facts you cannot verify, such as events, opening hours, news or local tips. Mention the sources. "
        "3. When NOT to call: For transport timings or prices (never a source for them; use search_trains or search_buses, and say verify before travel), or for current weather (use get_weather)."
    ),
)(web_search)

# Register the train search tool (RailRadar): scheduled trains between two cities, no seats, fares or live status.
# Results are data for the agent and always carry a verify-before-travel notice.
mcp.tool(
    name="search_trains",
    description=(
        "1. What it does and returns: Finds scheduled trains between two Indian cities (RailRadar). Arguments: source_city, destination_city and an optional date (YYYY-MM-DD, today up to 120 days ahead). Returns each train's number, name, type, real stations, departure and arrival times, duration in minutes, distance in km and run days, and which station was used for each city. Without a date it returns the trains that run every day and needs_date: ask the user for the date. With a date it returns the trains running that day, or nearby dates from the weekly schedule if there are none. If several stations match a city it returns needs_choice and options: ask the user, then call again with that station's code. "
        "2. When to call: The user wants trains between two cities. Always tell them which stations were used and to verify before travel. If the error is quota_exceeded, say: Train data is unavailable right now. "
        "3. When NOT to call: For one train's details, live status, seat availability or fares (not available; point to IRCTC), or for buses (use search_buses)."
    ),
)(search_trains)

# Register the bus tool (Parse.bot redBus API, snapshot 11). Prices and seats are third-party data:
# they are shown only as the tool returned them, with a verify-before-travel notice. It cannot book.
mcp.tool(
    name="search_buses",
    description=(
        "1. What it does and returns: Searches buses between two Indian cities on a date and returns a capped list: operator, bus type, departure and arrival times, duration, boarding and dropping points, fares in INR and available seats. Third-party data that can be out of date. "
        "2. When to call: The user wants buses between two cities. Arguments: source_city, destination_city and an OPTIONAL journey_date as YYYY-MM-DD (today up to 120 days ahead). If the user gave no date, leave journey_date out: the tool then searches tomorrow (it works the date out itself; never compute or guess a date) and returns the date used, so say \"Showing results for <date>\" and ask if they want another date. Fares and seats are third-party data for that date only. Always tell them to verify before travel. "
        "3. When NOT to call: To book anything (it cannot book), for trains, or for dates in the past or more than 120 days ahead."
    ),
)(search_buses)

# Register the email tool. It has no recipient argument: it can only email the user's own verified
# address from .env. The agent must ask the user first and pass confirmed=true (see prompts/email.md).
mcp.tool(
    name="send_trip_email",
    description=(
        "1. What it does and returns: Emails a trip plan (subject, plain-text body up to 5000 characters, confirmed) to the user's own verified address only; the recipient is fixed and you cannot choose it. Returns sent=true with a masked address, or an error. "
        "2. When to call: Only after the user clearly asked for the email and confirmed the subject and body you showed them; then pass confirmed=true. "
        "3. When NOT to call: Never pass confirmed=true on your own, never to send to another address, and not just because you drafted a plan."
    ),
)(send_trip_email)

# Register the geocoding tool (Nominatim / OpenStreetMap). Coordinates come only from the service and are cached
# for 30 days; the tool never invents them. An entry with error "unavailable" means the lookup failed, not that
# the place does not exist.
mcp.tool(
    name="geocode_places",
    description=(
        "1. What it does and returns: Finds coordinates for up to 12 place names in India (OpenStreetMap Nominatim). Arguments: names (e.g. ['Haridwar', 'Rishikesh']) and context (a region such as 'Uttarakhand, India'). Returns one entry per name: {name, lat, lng, found}. found=false means no match; error 'unavailable' means the lookup failed. "
        "2. When to call: You need map coordinates for places in India. Use only the lat and lng returned. "
        "3. When NOT to call: For more than 12 names, for places outside India, or to invent coordinates. On 'unavailable', tell the user to retry later and never claim the place does not exist."
    ),
)(geocode_places)

# Register the route tool (OSRM / OpenStreetMap, driving only). It returns the road route as [lat, lng] points for a
# map, with no distances, durations or prices, and the route is indicative (verify before travel). An unavailable
# service says nothing about whether a road route exists.
mcp.tool(
    name="get_route",
    description=(
        "1. What it does and returns: Gets the driving route (OpenStreetMap OSRM) through 2 to 10 [lat, lng] points (mode only 'driving'). Returns found and route, a list of [lat, lng] points to draw on a map, with no distances, travel times or prices. "
        "2. When to call: You have coordinates (for example from geocode_places) and want a road route to draw. Say it is indicative and to verify before travel. "
        "3. When NOT to call: For distances, travel times, prices or other transport modes (never state any from it). On error 'unavailable', tell the user to retry later and never claim there is no road route."
    ),
)(get_route)

# Register the photo tool (Pexels). It returns up to 3 photos with the photographer credit and links, from Pexels'
# own hosts only. An unavailable service says nothing about whether photos of the place exist.
mcp.tool(
    name="get_place_images",
    description=(
        "1. What it does and returns: Finds up to 3 Pexels photos of a place. Arguments: place (e.g. 'Hampi'), region (optional, e.g. 'Karnataka, India') and count (1 to 3). Returns found and images, each with url, thumb, alt, photographer, photographer_url and pexels_url. found=false means no match. "
        "2. When to call: The user wants to see a place. Show every photo with its photographer's name and a link to Pexels. "
        "3. When NOT to call: For facts, weather, travel times or prices, or more than 3 photos. On error 'unavailable', tell the user to retry later and never claim there are no photos."
    ),
)(get_place_images)

if __name__ == "__main__":
    # stdio transport: the client talks to this process over stdin/stdout.
    mcp.run(transport="stdio")
