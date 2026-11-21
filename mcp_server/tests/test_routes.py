# test_routes.py: tests for the get_route tool and its registration in server.py.
# The routing HTTP call (httpx) and Redis are both faked, and the waiting is skipped, so no network, no Redis
# and no real .env values are needed.

import logging

import httpx
import pytest

from tools import routes_tool
from tools.routes_tool import CACHE_TTL_SECONDS, MAX_ROUTE_POINTS, get_route

BASE_URL = "https://route.example.test"
A = [29.9457, 78.1642]  # lat, lng
B = [30.0869, 78.2676]
C = [30.3165, 78.0322]
# A Redis address with a password in it, to prove that neither the address nor the password ever reaches the logs.
REDIS_URL = "redis://redisuser:secretpw@cache.example.test:6379/0"


def osrm(coordinates, **extra):
    """A trimmed OSRM reply. GeoJSON coordinates are [lon, lat]. It ALSO carries distance, duration and legs, which
    the tool must never read or return."""
    body = {
        "code": "Ok",
        "routes": [
            {
                "geometry": {"type": "LineString", "coordinates": coordinates},
                "distance": 123456.7,
                "duration": 7654.3,
                "weight": 7700.1,
                "legs": [{"summary": "NH-34", "steps": [{"name": "Some Road", "distance": 99.9}]}],
            }
        ],
        "waypoints": [{"name": "Start Road", "location": coordinates[0] if coordinates else None}],
    }
    body.update(extra)
    return httpx.Response(200, json=body)


# The route the fake service returns for A -> B: three [lon, lat] points.
GEOMETRY = [[78.1642, 29.9457], [78.2, 30.0], [78.2676, 30.0869]]


class FakeClient:
    """Stand-in for httpx.AsyncClient. `script` is a list of answers used in order: an httpx.Response or an exception."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []  # (url, params) of every request

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None):
        self.calls.append((url, params))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeRedis:
    """Stand-in for a Redis client: a dict with get and set, and a log of every set call."""

    def __init__(self, broken=False, error=None):
        self.error = error  # what a broken Redis raises (a plain ConnectionError by default)
        self.data = {}
        self.sets = []  # (key, ex)
        self.broken = broken
        self.closed = False

    async def get(self, key):
        if self.broken:
            raise self.error or ConnectionError("redis down")
        return self.data.get(key)

    async def set(self, key, value, ex=None):
        if self.broken:
            raise self.error or ConnectionError("redis down")
        self.sets.append((key, ex))
        self.data[key] = value
        return True

    async def aclose(self):
        self.closed = True


@pytest.fixture
def env(monkeypatch):
    """Fake settings, a fake Redis, no waiting. Returns helpers to install a fake HTTP client."""
    monkeypatch.setattr(routes_tool.get_settings(), "routing_base_url", BASE_URL)
    monkeypatch.setattr(routes_tool.get_settings(), "redis_url", REDIS_URL)  # Redis is "set": tests never depend on the real .env
    fake_redis = FakeRedis()
    monkeypatch.setattr(routes_tool.redis, "from_url", lambda url, **kw: fake_redis)
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(routes_tool, "_sleep", fake_sleep)

    class Env:
        redis = fake_redis

        def __init__(self):
            self.sleeps = sleeps
            self.client = None

        def http(self, *script):
            self.client = FakeClient(script)
            monkeypatch.setattr(routes_tool.httpx, "AsyncClient", lambda **kw: self.client)
            return self.client

        def use_redis(self, replacement):
            monkeypatch.setattr(routes_tool.redis, "from_url", lambda url, **kw: replacement)
            return replacement

    return Env()


# ---------------------------------------------------------------- success


async def test_success_calls_the_api_correctly_and_swaps_lon_lat_to_lat_lng(env):
    client = env.http(osrm(GEOMETRY))
    result = await get_route([A, B])

    assert result == {
        "ok": True,
        "found": True,
        "route": [[29.9457, 78.1642], [30.0, 78.2], [30.0869, 78.2676]],  # [lat, lng]: swapped back from [lon, lat]
        "source": "OpenStreetMap OSRM",
        "notice": "Indicative route for the map only. Verify before travel.",
    }
    # The request: the driving profile, points in lon,lat order separated by semicolons, simplified GeoJSON geometry.
    url, params = client.calls[0]
    assert url == f"{BASE_URL}/route/v1/driving/78.16420,29.94570;78.26760,30.08690"
    assert params == {"overview": "simplified", "geometries": "geojson"}
    assert len(client.calls) == 1


async def test_a_trailing_slash_on_the_base_url_is_handled(env, monkeypatch):
    monkeypatch.setattr(routes_tool.get_settings(), "routing_base_url", BASE_URL + "/")
    client = env.http(osrm(GEOMETRY))
    await get_route([A, B])
    assert client.calls[0][0].startswith(f"{BASE_URL}/route/v1/driving/")


async def test_ten_points_in_order_are_all_in_the_url(env):
    points = [[10.0 + i, 70.0 + i] for i in range(10)]
    client = env.http(osrm(GEOMETRY))
    result = await get_route(points)
    assert result["found"] is True
    path = client.calls[0][0].split("/driving/")[1]
    assert path.split(";") == [f"{70.0 + i:.5f},{10.0 + i:.5f}" for i in range(10)]


async def test_integers_and_a_third_elevation_number_are_fine(env):
    env.http(osrm([[78, 30, 250.0], [79, 31, 300.0]]))
    result = await get_route([[30, 78], [31, 79]])
    assert result["route"] == [[30.0, 78.0], [31.0, 79.0]]  # the elevation number is ignored


# ---------------------------------------------------------------- no durations, distances or prices


async def test_no_distance_duration_or_other_api_fields_reach_the_result(env):
    env.http(osrm(GEOMETRY))
    result = await get_route([A, B])
    assert set(result) == {"ok", "found", "route", "source", "notice"}
    text = str(result).lower()
    for word in ("distance", "duration", "weight", "legs", "steps", "summary", "price", "fare", "nh-34", "some road", "start road"):
        assert word not in text
    for number in ("123456", "7654", "7700", "99.9"):
        assert number not in str(result)


async def test_the_distance_and_duration_are_never_read(env, monkeypatch):
    # Even a hostile reply whose distance and duration are odd things must not matter: they are not touched.
    body = {"code": "Ok", "routes": [{"geometry": {"coordinates": GEOMETRY}, "distance": "not a number", "duration": None}]}
    env.http(httpx.Response(200, json=body))
    result = await get_route([A, B])
    assert result["found"] is True


# ---------------------------------------------------------------- rounding and thinning to 300 points


async def test_input_points_are_rounded_to_5_decimals_and_share_a_cache_entry(env):
    client = env.http(osrm(GEOMETRY))
    await get_route([[29.9457111, 78.1642111], B])
    again = await get_route([[29.9457149, 78.1642149], B])  # differs only beyond the 5th decimal
    assert len(client.calls) == 1  # the second call was a cache hit
    assert "78.16421,29.94571" in client.calls[0][0]
    assert again["found"] is True


def long_geometry(count):
    """A route of `count` distinct [lon, lat] points."""
    return [[70.0 + i * 0.001, 20.0 + i * 0.0005] for i in range(count)]


async def test_a_route_of_exactly_300_points_is_returned_whole(env):
    geometry = long_geometry(MAX_ROUTE_POINTS)
    env.http(osrm(geometry))
    result = await get_route([A, B])
    assert len(result["route"]) == 300
    assert result["route"] == [[round(lat, 5), round(lon, 5)] for lon, lat in geometry]


async def test_a_route_of_301_points_is_thinned_to_300(env):
    env.http(osrm(long_geometry(301)))
    result = await get_route([A, B])
    assert len(result["route"]) == 300


async def test_a_long_route_keeps_first_and_last_and_picks_evenly_spaced_real_points(env):
    geometry = long_geometry(1000)
    env.http(osrm(geometry))
    result = await get_route([A, B])
    route = result["route"]
    real = [[round(lat, 5), round(lon, 5)] for lon, lat in geometry]
    assert len(route) == MAX_ROUTE_POINTS
    assert route[0] == real[0] and route[-1] == real[-1]  # first and last kept
    assert all(point in real for point in route)  # every returned point is a real point from the service: none invented
    positions = [real.index(point) for point in route]
    assert positions == sorted(set(positions))  # in order, no repeats
    gaps = [b - a for a, b in zip(positions, positions[1:])]
    assert max(gaps) - min(gaps) <= 1  # evenly spaced (rounding can make gaps differ by one)


def test_thin_never_invents_points_for_many_sizes():
    for count in (301, 302, 450, 599, 600, 2999, 5000):
        points = [[float(i), float(i)] for i in range(count)]
        thinned = routes_tool._thin(points)
        assert len(thinned) == MAX_ROUTE_POINTS
        assert thinned[0] == points[0] and thinned[-1] == points[-1]
        assert all(point in points for point in thinned)
        assert len({tuple(p) for p in thinned}) == MAX_ROUTE_POINTS  # no repeats


# ---------------------------------------------------------------- cache (6 hours, successes only)


async def test_success_is_cached_for_exactly_6_hours_and_second_call_makes_no_request(env):
    client = env.http(osrm(GEOMETRY))
    first = await get_route([A, B])
    assert env.redis.sets and all(ex == CACHE_TTL_SECONDS for _, ex in env.redis.sets)
    assert CACHE_TTL_SECONDS == 6 * 60 * 60 == 21600
    assert len(env.redis.sets) == 1

    second = await get_route([A, B])  # cache hit: the fake HTTP script is already empty
    assert len(client.calls) == 1
    assert second == first


async def test_the_order_of_the_points_matters_for_the_cache(env):
    client = env.http(osrm(GEOMETRY), osrm(list(reversed(GEOMETRY))))
    forward = await get_route([A, B])
    backward = await get_route([B, A])
    assert len(client.calls) == 2  # a different order is a different route
    assert forward["route"] != backward["route"]


async def test_a_damaged_cache_entry_counts_as_a_miss(env):
    env.http(osrm(GEOMETRY))
    await get_route([A, B])
    key = next(iter(env.redis.data))
    for damaged in ("not json", '{"route": "x"}', '{"route": [[1, 2]]}', '{"route": [[999, 2], [3, 4]]}', '{"other": 1}'):
        env.redis.data[key] = damaged
        client = env.http(osrm(GEOMETRY))
        result = await get_route([A, B])
        assert len(client.calls) == 1 and result["found"] is True


async def test_failures_and_no_route_are_never_cached(env):
    env.http(httpx.Response(503), httpx.Response(503))
    await get_route([A, B])
    env.http(httpx.Response(400, json={"code": "NoRoute"}))
    await get_route([A, B])
    env.http(httpx.Response(200, json={"code": "Ok", "routes": []}))
    await get_route([A, B])
    assert env.redis.data == {} and env.redis.sets == []


# ---------------------------------------------------------------- failures: found=false, "unavailable" only when the service failed


async def test_service_failure_after_one_retry_is_found_false_with_unavailable(env):
    client = env.http(httpx.Response(503), httpx.Response(503))
    result = await get_route([A, B])
    assert result == {"ok": True, "found": False, "error": "unavailable"}
    assert len(client.calls) == 1 + routes_tool.MAX_RETRIES == 2
    assert env.sleeps == [1.0]  # one retry, after one second


async def test_timeout_is_retried_once_and_can_still_succeed(env):
    client = env.http(httpx.ConnectTimeout("slow"), osrm(GEOMETRY))
    result = await get_route([A, B])
    assert result["found"] is True and "error" not in result
    assert len(client.calls) == 2 and env.sleeps == [1.0]


async def test_a_network_error_is_retried(env):
    client = env.http(httpx.ConnectError("no route to host"), osrm(GEOMETRY))
    result = await get_route([A, B])
    assert result["found"] is True and len(client.calls) == 2


async def test_two_timeouts_in_a_row_give_unavailable(env):
    client = env.http(httpx.ReadTimeout("slow"), httpx.ReadTimeout("slow"))
    result = await get_route([A, B])
    assert result == {"ok": True, "found": False, "error": "unavailable"}
    assert len(client.calls) == 2


async def test_429_is_retried(env):
    client = env.http(httpx.Response(429), osrm(GEOMETRY))
    result = await get_route([A, B])
    assert result["found"] is True and len(client.calls) == 2


async def test_a_500_is_retried(env):
    client = env.http(httpx.Response(500), osrm(GEOMETRY))
    result = await get_route([A, B])
    assert result["found"] is True and len(client.calls) == 2


@pytest.mark.parametrize("status", [400, 401, 403, 404])
async def test_other_client_errors_are_not_retried_and_are_unavailable(env, status):
    client = env.http(httpx.Response(status))
    result = await get_route([A, B])
    assert result == {"ok": True, "found": False, "error": "unavailable"}
    assert len(client.calls) == 1 and env.sleeps == []


async def test_a_400_with_invalid_query_is_unavailable_not_no_route(env):
    env.http(httpx.Response(400, json={"code": "InvalidQuery", "message": "bad"}))
    result = await get_route([A, B])
    assert result == {"ok": True, "found": False, "error": "unavailable"}


@pytest.mark.parametrize("reply", [httpx.Response(400, json={"code": "NoRoute"}), httpx.Response(200, json={"code": "NoRoute"}),
                                    httpx.Response(200, json={"code": "NoSegment"})])
async def test_no_route_is_a_plain_found_false_without_an_error_key(env, reply):
    client = env.http(reply)
    result = await get_route([A, B])
    assert result == {"ok": True, "found": False}
    assert len(client.calls) == 1  # a "no route" answer is final: not retried


@pytest.mark.parametrize(
    "reply",
    [
        httpx.Response(200, json={"code": "Ok"}),  # no routes
        httpx.Response(200, json={"code": "Ok", "routes": []}),
        httpx.Response(200, json={"code": "Ok", "routes": [{}]}),  # no geometry
        httpx.Response(200, json={"code": "Ok", "routes": [{"geometry": {"coordinates": "x"}}]}),
        osrm([[78.1, 29.9]]),  # only one point
        osrm([]),
        osrm([[78.1, 29.9], [78.2]]),  # a short pair
        osrm([[78.1, 29.9], ["a", "b"]]),  # not numbers
        osrm([[78.1, 29.9], [None, 30.0]]),
        osrm([[78.1, 29.9], [True, False]]),  # bools are not coordinates
        osrm([[78.1, 29.9], [78.2, 95.0]]),  # latitude out of range
        osrm([[78.1, 29.9], [181.0, 30.0]]),  # longitude out of range
        # A reply with NaN in it (not valid JSON, but some servers send it; Python reads it as a float).
        httpx.Response(200, content=b'{"code":"Ok","routes":[{"geometry":{"coordinates":[[78.1,29.9],[78.2,NaN]]}}]}'),
        httpx.Response(200, json={"code": "Error"}),  # an unknown code
        httpx.Response(200, json=["not", "a", "dict"]),
        httpx.Response(200, content=b"<html>not json</html>"),
    ],
)
async def test_a_bad_reply_never_produces_a_route(env, reply):
    env.http(reply)
    result = await get_route([A, B])
    assert result == {"ok": True, "found": False, "error": "unavailable"}
    assert env.redis.data == {}


async def test_the_tool_never_raises(env, monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(routes_tool, "_fetch", boom)
    env.http()
    result = await get_route([A, B])
    assert result == {"ok": True, "found": False, "error": "unavailable"}


# ---------------------------------------------------------------- bad arguments and configuration


@pytest.mark.parametrize(
    "points",
    [
        [],  # none
        [A],  # one point
        [A] * 11,  # eleven points
        "not a list",
        None,
        [A, "B"],  # a point that is not a pair
        [A, [30.0]],  # a short pair
        [A, [30.0, 78.0, 5.0]],  # a long pair
        [A, ["30.0", "78.0"]],  # strings, not numbers
        [A, [True, False]],  # bools
        [A, [None, 78.0]],
        [A, [91.0, 78.0]],  # latitude out of range
        [A, [-90.5, 78.0]],
        [A, [30.0, 180.5]],  # longitude out of range
        [A, [30.0, -181.0]],
        [A, [float("nan"), 78.0]],
        [A, [30.0, float("inf")]],
        [A, {"lat": 30.0, "lng": 78.0}],  # a dict, not a pair
    ],
)
async def test_bad_points_are_rejected_without_any_request(env, points):
    client = env.http()
    result = await get_route(points)
    assert result["ok"] is False and result["error"] == "invalid_arguments"
    assert client.calls == []


@pytest.mark.parametrize("mode", ["walking", "cycling", "transit", "", "Driving", "DRIVING", " driving", "driving "])
async def test_only_the_exact_mode_driving_is_accepted(env, mode):
    client = env.http()
    result = await get_route([A, B], mode)
    assert result["ok"] is False and result["error"] == "invalid_arguments"
    assert client.calls == []


async def test_mode_defaults_to_driving(env):
    env.http(osrm(GEOMETRY))
    result = await get_route([A, B])
    assert result["found"] is True


@pytest.mark.parametrize("count", [2, 10])
async def test_the_smallest_and_largest_allowed_requests_work(env, count):
    env.http(osrm(GEOMETRY))
    result = await get_route([[10.0 + i, 70.0 + i] for i in range(count)])
    assert result["found"] is True


@pytest.mark.parametrize("value", ["", "   ", "ftp://x", "route.example.test"])
async def test_not_configured_returns_an_error_object(env, monkeypatch, value):
    monkeypatch.setattr(routes_tool.get_settings(), "routing_base_url", value)
    client = env.http()
    result = await get_route([A, B])
    assert result["ok"] is False and result["error"] == "not_configured"
    assert client.calls == []


# ---------------------------------------------------------------- Redis trouble


async def test_redis_down_still_works_without_the_cache_and_warns_once(env, caplog):
    env.use_redis(FakeRedis(broken=True))
    client = env.http(osrm(GEOMETRY), osrm(GEOMETRY))
    with caplog.at_level(logging.WARNING):
        first = await get_route([A, B])
        second = await get_route([A, B])
    assert first["found"] is True and second["found"] is True
    assert len(client.calls) == 2  # no cache, so both calls went to the service
    assert caplog.text.count("Redis unavailable") == 2  # once per call, never more, with no URL or error text


async def test_a_bad_redis_url_does_not_break_the_tool(env, monkeypatch):
    def broken_from_url(url, **kw):
        raise ValueError("bad url with a password in it")

    monkeypatch.setattr(routes_tool.redis, "from_url", broken_from_url)
    env.http(osrm(GEOMETRY))
    result = await get_route([A, B])
    assert result["found"] is True


async def test_the_redis_connection_is_closed(env):
    env.http(osrm(GEOMETRY))
    await get_route([A, B])
    assert env.redis.closed is True


# ---------------------------------------------------------------- Redis is optional


@pytest.mark.parametrize("unset", ["", "   "])
async def test_redis_url_not_set_skips_the_cache_silently_and_still_works(env, monkeypatch, caplog, unset):
    monkeypatch.setattr(routes_tool.get_settings(), "redis_url", unset)

    def no_client_allowed(*args, **kwargs):
        raise AssertionError("no Redis client may even be created when REDIS_URL is not set")

    monkeypatch.setattr(routes_tool.redis, "from_url", no_client_allowed)
    client = env.http(osrm(GEOMETRY), osrm(GEOMETRY))
    with caplog.at_level(logging.DEBUG):
        first = await get_route([A, B])
        second = await get_route([A, B])
    assert first["found"] is True and second["found"] is True
    assert len(client.calls) == 2  # no cache: the second call went to the service again
    assert "redis" not in caplog.text.lower()  # not a warning, not even a debug line: silence


@pytest.mark.parametrize(
    "error",
    [None, OSError("network unreachable"), TimeoutError("timed out")],
    ids=["connection refused", "os error", "timeout"],
)
async def test_redis_set_but_unreachable_logs_one_short_warning_per_call_and_still_works(env, caplog, error):
    env.use_redis(FakeRedis(broken=True, error=error))
    client = env.http(osrm(GEOMETRY), osrm(GEOMETRY))
    with caplog.at_level(logging.DEBUG):
        first = await get_route([A, B])
        after_first = [r for r in caplog.records if "Redis" in r.getMessage()]
        second = await get_route([A, B])  # a new call: one more warning (nothing was cached, so it asks the service again)
    assert first["found"] is True and second["found"] is True  # works without the cache
    assert len(client.calls) == 2
    warnings = [r for r in caplog.records if "Redis" in r.getMessage()]
    assert len(after_first) == 1 and len(warnings) == 2  # one warning per tool call, even though get and set both failed
    assert all(r.levelno == logging.WARNING and len(r.getMessage()) < 80 for r in warnings)  # short, and a warning
    # The address, the user name and the password never reach the log, and nor does the error text.
    for text in ("secretpw", "redisuser", "cache.example.test", "redis://", "network unreachable", "timed out"):
        assert text not in caplog.text


async def test_a_reachable_redis_logs_no_warning(env, caplog):
    env.http(osrm(GEOMETRY))
    with caplog.at_level(logging.DEBUG):
        result = await get_route([A, B])
    assert result["found"] is True
    assert "redis" not in caplog.text.lower()


async def test_the_redis_client_gets_short_timeouts_so_it_cannot_hold_a_tool_call(env, monkeypatch):
    seen = {}

    def spy(url, **kwargs):
        seen["url"], seen["kwargs"] = url, kwargs
        return env.redis

    monkeypatch.setattr(routes_tool.redis, "from_url", spy)
    env.http(osrm(GEOMETRY))
    await get_route([A, B])
    assert seen["url"] == REDIS_URL
    assert seen["kwargs"] == {"socket_connect_timeout": 2.0, "socket_timeout": 2.0}
    assert routes_tool.REDIS_TIMEOUT_SECONDS == 2.0


# ---------------------------------------------------------------- logging and registration


async def test_logs_never_contain_coordinates_or_the_service_address(env, caplog):
    env.http(httpx.Response(503), httpx.Response(503))
    with caplog.at_level(logging.DEBUG):
        await get_route([[11.11111, 22.22222], [33.33333, 44.44444]])
    for text in ("11.11111", "22.22222", "33.33333", "44.44444", BASE_URL, "route.example.test"):
        assert text not in caplog.text


async def test_tool_registered_in_server():
    # The tool must be listed by the real MCP server, with points and mode parameters.
    import server

    tools = await server.mcp.list_tools()
    tool = next(t for t in tools if t.name == "get_route")
    assert set(tool.inputSchema["properties"]) == {"points", "mode"}
    assert tool.inputSchema["required"] == ["points"]
    description = tool.description.lower()
    assert "driving" in description and "unavailable" in description and "2 to 10" in description
    assert "no distances" in description
