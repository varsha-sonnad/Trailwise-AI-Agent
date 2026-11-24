# legs.py: pure helpers for route mode's gateway flow (remote places such as Kedarnath that have no station or bus route).
#   GeoState   remembers which places geocode_places found in this request (and in what order the last journey list came).
#   check_*    refuse a train, bus or get_route call when an end was not geocoded ("only search pairs where both ends are geocoded").
#   is_gateway_signal  true when a tool says the DESTINATION has no station or city (the signal to use the gateway flow).
#   build_legs the map lines: road legs from get_route (solid) and, in the gateway flow, straight segments between
#              consecutive geocoded journey places that no road leg covers (drawn dashed). The server draws them, the model
#              never supplies coordinates. No timings, distances or prices exist here.
# Nothing here stores anything: the state lives for one request only.

import math

# Two coordinates closer than this (degrees, about 10 m) are the same place.
COORD_TOLERANCE = 1e-4
# Tools whose city names must be geocoded places.
CITY_TOOLS = ("search_trains", "search_buses")
# The refusal the model gets (as data) when an end of a search is not a located place.
NOT_LOCATED = "place_not_located"


def norm(text: object) -> str:
    """A place name for comparing: lower case, spaces squeezed."""
    return " ".join(str(text or "").lower().split())


def _first_part(key: str) -> str:
    """The text before the first comma ("pune, maharashtra" -> "pune")."""
    return key.split(",")[0].strip()


class GeoState:
    """What geocode_places found in this request."""

    def __init__(self) -> None:
        self.found: dict[str, tuple[float, float]] = {}  # normalised requested name -> (lat, lng)
        self.unavailable: set[str] = set()  # names whose lookup failed (the service was down): never held against the user
        self.any_ok = False  # at least one geocode_places call returned data
        self.journey: list[dict] = []  # found places of the latest call that found at least two, in the order asked

    def add(self, result: dict) -> None:
        """Record one successful geocode_places result (places come back in the order they were asked)."""
        places = result.get("places") if isinstance(result.get("places"), list) else []
        self.any_ok = True
        ordered = []
        for p in places:
            if not isinstance(p, dict):
                continue
            key = norm(p.get("name"))
            if p.get("found") is True and isinstance(p.get("lat"), (int, float)) and isinstance(p.get("lng"), (int, float)):
                self.found[key] = (float(p["lat"]), float(p["lng"]))
                ordered.append({"name": p.get("name"), "lat": float(p["lat"]), "lng": float(p["lng"])})
            elif p.get("error") == "unavailable":
                self.unavailable.add(key)
        if len(ordered) >= 2:
            self.journey = ordered

    def knows(self, name: object) -> bool:
        """True if this city name is a geocoded place (exact, or the same text before a comma)."""
        key = norm(name)
        if key in self.found:
            return True
        first = _first_part(key)
        return any(_first_part(k) == first for k in self.found)

    def has_point(self, lat: float, lng: float) -> bool:
        """True if these coordinates belong to a geocoded place."""
        return any(abs(lat - a) <= COORD_TOLERANCE and abs(lng - b) <= COORD_TOLERANCE for a, b in self.found.values())


def _refusal(end: str) -> dict:
    """The data the model gets when an end of a search was not located."""
    return {
        "ok": False, "error": NOT_LOCATED, "failed_end": end,
        "message": "That place was not located on the map, so it was not searched. Use a located place (a gateway city for a remote destination).",
    }


def check_city_pair(state: GeoState, args: dict) -> dict | None:
    """None if search_trains or search_buses may run; otherwise the refusal. Only enforced once a geocode succeeded.

    A place whose lookup merely failed (service down) is allowed: an outage must not block the search.
    """
    if not state.any_ok:
        return None
    for end, field in (("source", "source_city"), ("destination", "destination_city")):
        name = args.get(field)
        if norm(name) in state.unavailable:
            continue
        if not state.knows(name):
            return _refusal(end)
    return None


def check_route_points(state: GeoState, args: dict) -> dict | None:
    """None if get_route may run; otherwise the refusal. Every point must be a geocoded place's coordinates (never invented)."""
    if not state.any_ok:
        return None
    points = args.get("points")
    for point in points if isinstance(points, list) else []:
        ok = (
            isinstance(point, (list, tuple)) and len(point) == 2
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in point)
            and state.has_point(point[0], point[1])
        )
        if not ok:
            return _refusal("source")
    return None


def is_gateway_signal(tool: str, result: dict) -> bool:
    """True when train or bus search says the DESTINATION has no station or city (or was refused as not located)."""
    return tool in CITY_TOOLS and result.get("ok") is False and result.get("failed_end") == "destination"


def _key(point) -> tuple[float, float]:
    """A coordinate pair rounded so the same place always gives the same key."""
    return (round(float(point[0]), 4), round(float(point[1]), 4))


def build_legs(roads: list[dict], journey: list[dict], gateway: bool) -> list[dict]:
    """The lines for the map. roads: [{"inputs": [[lat, lng], ...], "points": [[lat, lng], ...]}] from get_route.

    Every road is a solid leg. In the gateway flow (and only with at least 3 journey places) each pair of consecutive
    journey places that no road covers becomes a straight "other" leg (dashed on the map).
    """
    legs = [{"kind": "road", "points": r["points"]} for r in roads]
    if gateway and len(journey) >= 3:
        covered = {frozenset((_key(a), _key(b))) for r in roads for a, b in zip(r["inputs"], r["inputs"][1:])}
        for a, b in zip(journey, journey[1:]):
            pair = (_key((a["lat"], a["lng"])), _key((b["lat"], b["lng"])))
            if pair[0] != pair[1] and frozenset(pair) not in covered:
                legs.append({"kind": "other", "points": [[a["lat"], a["lng"]], [b["lat"], b["lng"]]]})
    return legs
