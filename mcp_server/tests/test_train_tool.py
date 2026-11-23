# test_train_tool.py: tests for the search_trains tool and its registration in server.py.
# The RailRadar API (httpx) and Redis are faked, and "today" is fixed, so no key, network or real date is needed.
# Today is 2026-11-20, a Friday (the date RailRadar's own check used for 2026-11-27 is also a Friday).

import datetime as dt
import logging

import httpx
import pytest

from tools import train_tool
from tools.train_tool import (
    CACHE_PREFIX, MAX_REQUESTS, QUOTA_MESSAGE, SEARCH_CACHE_TTL_SECONDS, STATION_CACHE_TTL_SECONDS, runs_on, search_trains,
)

KEY = "RAILRADAR-TEST-KEY-123"
REDIS_URL = "redis://redisuser:secretpw@cache.example.test:6379/0"
TODAY = dt.date(2026, 11, 20)
FRIDAY = "2026-11-27"
ALL_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


# ---------------------------------------------------------------- builders for RailRadar's answers

def station(code, name, popularity=100, active=True):
    """One station of the station search."""
    return {"code": code, "name": name, "city": None, "popularity": popularity, "isActive": active, "lat": 1.0, "lng": 2.0, "haltCount": popularity}


def lookup_reply(*stations):
    """The station search answer (HTTP 200) with these stations."""
    return httpx.Response(200, json={"success": True, "data": list(stations), "meta": {"source": "database"}})


def train(number, days=None, dep="06:05", arr="09:20", from_code="PUNE", to_code="CSMT", from_day=1, to_day=None, duration=195, distance=191.3,
          halts=3, name=None, kind="Superfast Express", **extra):
    """One item of the trains-between answer, in RailRadar's shape (days None means every day)."""
    item = {
        "train": {"number": number, "name": name or f"Train {number}", "type": kind, "runDays": ALL_DAYS if days is None else days, "runningDaysBitmap": 127},
        "from": {"code": from_code, "name": f"{from_code} station", "city": "Pune", "departure": dep, "day": from_day, "sequence": 1},
        "to": {"code": to_code, "name": f"{to_code} station", "city": "Mumbai", "arrival": arr, "day": from_day if to_day is None else to_day, "sequence": 9},
        "distance": distance, "duration": duration, "totalHaltsBetween": halts,
    }
    item.update(extra)
    return item


def between_reply(*items):
    """The trains-between answer (HTTP 200) with these items."""
    return httpx.Response(200, json={"success": True, "data": {"from": {}, "to": {}, "trains": list(items), "count": len(items)}, "meta": {}})


class FakeRailRadar:
    """Stand-in for httpx.AsyncClient. `lookups` maps the searched text to a reply (or an exception); `between` is a queue
    of replies (or exceptions) used in order. Asking for more than was scripted fails the test."""

    def __init__(self, lookups=None, between=None):
        self.lookups = dict(lookups or {})
        self.queue = list(between or [])
        self.calls = []  # {"path", "params", "headers"} of every request

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None, headers=None):
        path = url.removeprefix(train_tool.API_BASE_URL)
        self.calls.append({"path": path, "params": dict(params or {}), "headers": headers, "url": url})
        if path.startswith("/v1/lookup/search/stations"):
            item = self.lookups[params["q"]]
        else:
            assert self.queue, f"an unexpected extra request: {path} {params}"
            item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    @property
    def lookup_calls(self):
        return [c for c in self.calls if c["path"].startswith("/v1/lookup")]

    @property
    def between_calls(self):
        return [c for c in self.calls if c["path"].startswith("/v1/trains/between")]


class FakeRedis:
    """Stand-in for a Redis client: a dict with get and set, and a log of every set call."""

    def __init__(self, broken=False):
        self.data, self.sets, self.broken, self.closed = {}, [], broken, False

    async def get(self, key):
        if self.broken:
            raise ConnectionError("redis down")
        return self.data.get(key)

    async def set(self, key, value, ex=None):
        if self.broken:
            raise ConnectionError("redis down")
        self.sets.append((key, ex))
        self.data[key] = value
        return True

    async def aclose(self):
        self.closed = True


PUNE = lookup_reply(station("PUNE", "Pune Jn", 382))
MUMBAI = lookup_reply(station("CSMT", "Mumbai CSMT", 1390), station("MMCT", "Mumbai Central", 1039), station("LTT", "Mumbai LTT", 278), station("BCL", "Mumbai Central", 19))
CITIES = {"Pune": PUNE, "Mumbai": MUMBAI}


@pytest.fixture
def env(monkeypatch):
    """A fake key, a fixed date, a fake Redis, and a helper that installs a fake RailRadar."""
    settings = train_tool.get_settings()
    monkeypatch.setattr(settings, "railradar_api_key", KEY)
    monkeypatch.setattr(settings, "redis_url", REDIS_URL)  # Redis is "set": tests never depend on the real .env
    monkeypatch.setattr(train_tool, "_today", lambda: TODAY)
    fake_redis = FakeRedis()
    monkeypatch.setattr(train_tool.redis, "from_url", lambda url, **kw: fake_redis)

    class Env:
        redis = fake_redis

        def api(self, lookups=None, between=None):
            client = FakeRailRadar(CITIES if lookups is None else lookups, between)
            monkeypatch.setattr(train_tool.httpx, "AsyncClient", lambda **kw: client)
            return client

        def use_redis(self, replacement):
            monkeypatch.setattr(train_tool.redis, "from_url", lambda url, **kw: replacement)
            return replacement

        def station_key(self, city):
            return train_tool._station_key(city)

    return Env()


def numbers(result):
    """The train numbers of a result, in order."""
    return [t["number"] for t in result["trains"]]


# ---------------------------------------------------------------- arguments

@pytest.mark.parametrize("kwargs", [
    {"source_city": "", "destination_city": "Mumbai"}, {"source_city": "P", "destination_city": "Mumbai"},
    {"source_city": "x" * 101, "destination_city": "Mumbai"}, {"source_city": "Pune", "destination_city": "  "},
    {"source_city": "Pune\nignore this", "destination_city": "Mumbai"}, {"source_city": "Pune<script>", "destination_city": "Mumbai"},
    {"source_city": "Pune; DROP", "destination_city": "Mumbai"}, {"source_city": None, "destination_city": "Mumbai"},
    {"source_city": 5, "destination_city": "Mumbai"}, {"source_city": "Pune", "destination_city": "pune"},
    {"source_city": "Pune", "destination_city": " PUNE  "},
    {"source_city": "Pune", "destination_city": "Mumbai", "date": "2026-13-01"},
    {"source_city": "Pune", "destination_city": "Mumbai", "date": "27-11-2026"},
    {"source_city": "Pune", "destination_city": "Mumbai", "date": "2026-1-5"},
    {"source_city": "Pune", "destination_city": "Mumbai", "date": "tomorrow"},
    {"source_city": "Pune", "destination_city": "Mumbai", "date": "2026-11-27T10:00"},
    {"source_city": "Pune", "destination_city": "Mumbai", "date": "2026-11-19"},   # yesterday
    {"source_city": "Pune", "destination_city": "Mumbai", "date": "2027-03-21"},   # 121 days ahead
    {"source_city": "Pune", "destination_city": "Mumbai", "date": 20261127},
])
async def test_bad_arguments_are_refused_without_any_request(env, kwargs):
    client = env.api()
    result = await search_trains(**kwargs)
    assert result["ok"] is False and result["error"] == "invalid_arguments" and client.calls == []


@pytest.mark.parametrize("day", ["2026-11-20", "2027-03-20", " 2026-11-27 "])
async def test_dates_from_today_to_120_days_ahead_are_accepted(env, day):
    env.api(between=[between_reply(train("12123"))])
    assert (await search_trains("Pune", "Mumbai", day))["ok"] is True


async def test_no_key_means_not_configured_and_no_request(env, monkeypatch):
    monkeypatch.setattr(train_tool.get_settings(), "railradar_api_key", "")
    client = env.api()
    assert (await search_trains("Pune", "Mumbai"))["error"] == "not_configured" and client.calls == []


# ---------------------------------------------------------------- the station lookup (RailRadar's own station search)

async def test_each_city_is_looked_up_with_railradars_station_search(env):
    client = env.api(between=[between_reply(train("12123"))])
    await search_trains("Pune", "Mumbai")
    assert sorted(c["params"]["q"] for c in client.lookup_calls) == ["Mumbai", "Pune"]
    for call in client.lookup_calls:
        assert call["path"] == "/v1/lookup/search/stations" and call["params"]["limit"] == 10
        assert call["headers"] == {"Authorization": f"Bearer {KEY}"}  # the key only in the header


async def test_the_main_station_is_the_most_popular_active_one_and_the_result_says_which_was_used(env):
    env.api(between=[between_reply(train("12123"))])
    result = await search_trains("Pune", "Mumbai")
    assert result["stations"]["from"] == {"searched_as": "Pune", "code": "PUNE", "name": "Pune Jn"}
    assert result["stations"]["to"] == {"searched_as": "Mumbai", "code": "CSMT", "name": "Mumbai CSMT"}  # not Mumbai Central or LTT
    note = result["stations"]["note"]
    assert "Pune Jn (PUNE)" in note and "Mumbai CSMT (CSMT)" in note and "Tell the user which stations were used" in note
    assert "Tell the user which stations were used" in result["message"]


async def test_an_inactive_station_is_never_picked(env):
    lookups = {"Pune": PUNE, "Mumbai": lookup_reply(station("OLD", "Mumbai Old", 5000, active=False), station("CSMT", "Mumbai CSMT", 10))}
    client = env.api(lookups, between=[between_reply(train("12123"))])
    result = await search_trains("Pune", "Mumbai")
    assert result["stations"]["to"]["code"] == "CSMT" and client.between_calls[0]["path"].endswith("/PUNE/CSMT")


async def test_the_station_codes_go_into_the_trains_between_request_with_bycity(env):
    client = env.api(between=[between_reply(train("12123"))])
    await search_trains("Pune", "Mumbai")
    call = client.between_calls[0]
    assert call["path"] == "/v1/trains/between/PUNE/CSMT"
    assert call["params"] == {"byCity": "true"}  # no date was asked for, so none is sent
    assert call["headers"] == {"Authorization": f"Bearer {KEY}"}


async def test_several_stations_with_the_same_name_return_the_options_and_no_trains_request(env):
    lookups = {"Aurangabad": lookup_reply(station("AWB", "Aurangabad", 300), station("AGB", "Aurangabad Jn", 200), station("TINY", "Aurangabad", 5)), "Pune": PUNE}
    client = env.api(lookups)
    result = await search_trains("Aurangabad", "Pune")
    assert result["ok"] is True and result["needs_choice"] is True and result["which"] == "source" and result["city"] == "Aurangabad"
    assert result["options"] == [{"code": "AWB", "name": "Aurangabad"}, {"code": "AGB", "name": "Aurangabad Jn"}]  # the tiny halt is not offered
    assert "Ask the user which one" in result["message"] and "station's code" in result["message"]
    assert client.between_calls == []


async def test_the_source_is_asked_about_first_when_both_are_unclear(env):
    two = lambda a, b: lookup_reply(station(a, "Bilaspur", 300), station(b, "Bilaspur Jn", 250))  # noqa: E731
    env.api({"Bilaspur": two("BSP", "BSPJ"), "Hamirpur": two("HMR", "HMRJ"), "Pune": PUNE})
    result = await search_trains("Bilaspur", "Hamirpur")
    assert result["needs_choice"] is True and result["which"] == "source" and result["city"] == "Bilaspur"
    result = await search_trains("Pune", "Bilaspur")  # only the destination is unclear
    assert result["needs_choice"] is True and result["which"] == "destination" and result["city"] == "Bilaspur"


async def test_a_station_with_a_tiny_share_of_the_popularity_is_not_a_rival(env):
    lookups = {"Mumbai Central": lookup_reply(station("MMCT", "Mumbai Central", 1039), station("BCL", "Mumbai Central", 19)), "Pune": PUNE}
    env.api(lookups, between=[between_reply(train("12123", to_code="MMCT"))])
    result = await search_trains("Mumbai Central", "Pune")
    assert result.get("needs_choice") is None and result["stations"]["from"]["code"] == "MMCT"


async def test_a_typed_station_code_is_taken_as_the_users_choice(env):
    lookups = {"AGB": lookup_reply(station("AWB", "Aurangabad", 300), station("AGB", "Aurangabad Jn", 200)), "Pune": PUNE}
    client = env.api(lookups, between=[between_reply(train("12123", from_code="AGB"))])
    result = await search_trains("AGB", "Pune")
    assert result["stations"]["from"]["code"] == "AGB" and client.between_calls[0]["path"].endswith("/AGB/PUNE")


async def test_an_unknown_city_is_station_not_found_and_names_the_city(env):
    client = env.api({"Atlantis": lookup_reply(), "Pune": PUNE})
    result = await search_trains("Atlantis", "Pune")
    assert result["ok"] is False and result["error"] == "station_not_found" and "Atlantis" in result["message"] and client.between_calls == []
    result = await search_trains("Pune", "Atlantis")
    assert result["error"] == "station_not_found" and "Atlantis" in result["message"]


async def test_station_not_found_says_which_end_failed(env):
    """failed_end tells the agent whether the destination (the gateway-flow signal) or the source had no station."""
    env.api({"Atlantis": lookup_reply(), "Pune": PUNE})
    assert (await search_trains("Pune", "Atlantis"))["failed_end"] == "destination"
    assert (await search_trains("Atlantis", "Pune"))["failed_end"] == "source"


async def test_a_lookup_404_is_station_not_found(env):
    env.api({"Pune": httpx.Response(404), "Mumbai": MUMBAI})
    assert (await search_trains("Pune", "Mumbai"))["error"] == "station_not_found"


async def test_a_question_comes_after_a_real_problem(env):
    amb = lookup_reply(station("AWB", "Aurangabad", 300), station("AGB", "Aurangabad Jn", 200))
    env.api({"Aurangabad": amb, "Atlantis": lookup_reply()})
    assert (await search_trains("Aurangabad", "Atlantis"))["error"] == "station_not_found"


async def test_the_list_of_known_stations_starts_empty(env):
    assert train_tool.KNOWN_STATIONS == {}


async def test_a_city_in_the_known_list_skips_the_lookup(env, monkeypatch):
    monkeypatch.setitem(train_tool.KNOWN_STATIONS, "pune", ("PUNE", "Pune Jn"))
    client = env.api({"Mumbai": MUMBAI}, between=[between_reply(train("12123"))])
    result = await search_trains(" PUNE ", "Mumbai")
    assert [c["params"]["q"] for c in client.lookup_calls] == ["Mumbai"]  # only the other city was looked up
    assert result["stations"]["from"] == {"searched_as": "PUNE", "code": "PUNE", "name": "Pune Jn"}


async def test_two_known_cities_need_no_lookup_at_all(env, monkeypatch):
    monkeypatch.setitem(train_tool.KNOWN_STATIONS, "pune", ("PUNE", "Pune Jn"))
    monkeypatch.setitem(train_tool.KNOWN_STATIONS, "mumbai", ("CSMT", "Mumbai CSMT"))
    client = env.api({}, between=[between_reply(train("12123"))])
    assert (await search_trains("Pune", "Mumbai"))["found"] is True
    assert client.lookup_calls == [] and len(client.calls) == 1


# ---------------------------------------------------------------- all trains, each with its real station

async def test_every_train_is_returned_with_no_limit_and_in_the_order_given(env):
    items = [train(f"{10000 + i}", dep=f"{i // 60:02d}:{i % 60:02d}") for i in range(120)]
    client = env.api(between=[between_reply(*items)])
    result = await search_trains("Pune", "Mumbai")
    assert result["train_count"] == 120 and len(result["trains"]) == 120 and numbers(result)[:3] == ["10000", "10001", "10002"]
    assert len(client.between_calls) == 1  # one request brings every train (RailRadar has no pagination)


async def test_each_train_shows_its_real_stations_not_just_the_searched_city(env):
    env.api(between=[between_reply(train("11", to_code="CSMT"), train("22", to_code="DR"), train("33", to_code="LTT", arr="04:00"))])
    result = await search_trains("Pune", "Mumbai")
    assert [t["to"]["code"] for t in result["trains"]] == ["CSMT", "DR", "LTT"]
    assert result["trains"][2]["to"] == {"code": "LTT", "name": "LTT station", "city": "Mumbai", "arrival": "04:00", "day": 1}
    assert result["trains"][0]["from"]["code"] == "PUNE" and result["trains"][0]["from"]["departure"] == "06:05"


async def test_the_train_fields(env):
    item = train("12123", days=["wed", "mon", "fri"], dep="23:50", arr="03:40", from_day=1, to_day=2, duration=230, distance=191.3, halts=4,
                 name="Deccan Queen", kind="Superfast", photo="https://evil.example/x", classes=["3A"], fare=999)
    env.api(between=[between_reply(item)])
    result = await search_trains("Pune", "Mumbai")
    assert result["trains"][0] == {
        "number": "12123", "name": "Deccan Queen", "type": "Superfast", "run_days": ["mon", "wed", "fri"], "runs_daily": False,  # week order
        "from": {"code": "PUNE", "name": "PUNE station", "city": "Pune", "departure": "23:50", "day": 1},
        "to": {"code": "CSMT", "name": "CSMT station", "city": "Mumbai", "arrival": "03:40", "day": 2},
        "arrives_after_days": 1, "duration_minutes": 230, "distance_km": 191.3, "halts_between": 4,
    }
    assert "evil.example" not in repr(result) and "fare" not in repr(result["trains"][0]) and "classes" not in repr(result["trains"][0])


async def test_unusable_trains_are_skipped_and_odd_values_become_none(env):
    items = [
        {"nonsense": True}, "text", None, train("", days=ALL_DAYS), train("11", from_code="not valid!"),
        train("22", duration="long", distance=-5, halts=True, days=["funday", "MON"]),
    ]
    env.api(between=[between_reply(*items)])
    result = await search_trains("Pune", "Mumbai")
    assert numbers(result) == ["22"]
    t = result["trains"][0]
    assert t["duration_minutes"] is None and t["distance_km"] is None and t["halts_between"] is None and t["run_days"] == ["mon"]


async def test_long_third_party_text_is_cut(env):
    env.api(between=[between_reply(train("1" * 50, name="N" * 500, kind="T" * 500))])
    t = (await search_trains("Pune", "Mumbai"))["trains"][0]
    assert len(t["number"]) == 10 and len(t["name"]) == 100 and len(t["type"]) == 100


# ---------------------------------------------------------------- no date: the daily trains first

async def test_without_a_date_the_daily_trains_are_returned_in_full_and_the_date_is_asked_for(env):
    client = env.api(between=[between_reply(train("11"), train("22", days=["tue", "thu"]), train("33"), train("44", days=["sat"]))])
    result = await search_trains("Pune", "Mumbai")
    assert result["ok"] and result["found"] and result["needs_date"] is True and result["searched_date"] is None
    assert numbers(result) == ["11", "33"] and result["train_count"] == 2 and result["daily_trains_only"] is True
    assert result["other_trains_count"] == 2
    assert all(t["runs_daily"] and len(t["run_days"]) == 7 for t in result["trains"])
    assert "Ask the user for their travel date" in result["message"] and "2 more run only on some days" in result["message"]
    assert result["disclaimer"].startswith("Scheduled information only") and result["source"] == "railradar"
    assert len(client.calls) == 3 and len(client.between_calls) == 1  # two lookups and one search


async def test_without_a_date_and_without_daily_trains_the_others_are_shown_with_their_run_days(env):
    env.api(between=[between_reply(train("22", days=["tue", "thu"]), train("44", days=["sat"]))])
    result = await search_trains("Pune", "Mumbai")
    assert numbers(result) == ["22", "44"] and result["daily_trains_only"] is False and result["needs_date"] is True
    assert result["other_trains_count"] == 0 and "No train runs every day" in result["message"]


async def test_no_trains_at_all_is_found_false_and_never_says_none_exist(env):
    env.api(between=[between_reply()])
    result = await search_trains("Pune", "Mumbai")
    assert result["ok"] is True and result["found"] is False and result["trains"] == [] and result["needs_date"] is False
    assert "in this data source" in result["message"] and "does not mean none run" in result["message"]


# ---------------------------------------------------------------- a date: that day's trains

async def test_with_a_date_the_trains_running_that_day_are_returned(env):
    client = env.api(between=[between_reply(train("11"), train("22", days=["fri"]))])
    result = await search_trains("Pune", "Mumbai", FRIDAY)
    assert result["found"] is True and result["searched_date"] == FRIDAY and result["needs_date"] is False and numbers(result) == ["11", "22"]
    assert client.between_calls[0]["params"] == {"byCity": "true", "date": FRIDAY}
    assert len(client.calls) == 3  # two lookups and the dated search; nothing else was needed


async def test_the_dated_search_is_the_first_search_and_when_it_has_trains_it_is_the_only_one(env):
    client = env.api(between=[between_reply(train("11")), between_reply(train("99"))])
    await search_trains("Pune", "Mumbai", FRIDAY)
    assert len(client.between_calls) == 1


# ---------------------------------------------------------------- no trains that day: nearby dates, worked out locally

def warm(env, client_before=None):
    """Put both cities in the station cache, so a later search spends no request on lookups."""
    env.redis.data[env.station_key("Pune")] = '[{"code": "PUNE", "name": "Pune Jn", "popularity": 382}]'
    env.redis.data[env.station_key("Mumbai")] = '[{"code": "CSMT", "name": "Mumbai CSMT", "popularity": 1390}]'


async def test_nearby_dates_come_from_the_weekly_run_days_nearest_first(env):
    warm(env)
    client = env.api({}, between=[between_reply(), between_reply(train("A", days=["mon"]), train("B", days=["wed"]))])
    result = await search_trains("Pune", "Mumbai", FRIDAY)
    assert result["found"] is False and result["no_trains_on_date"] is True and result["trains"] == [] and result["nearby_checked"] is True
    assert [d["date"] for d in result["nearby_dates"]] == ["2026-11-25", "2026-11-30", "2026-11-23", "2026-12-02"]
    assert [d["days_from_requested"] for d in result["nearby_dates"]] == [-2, 3, -4, 5]
    assert [d["weekday"] for d in result["nearby_dates"]] == ["wed", "mon", "mon", "wed"]
    assert [numbers_of(d) for d in result["nearby_dates"]] == [["B"], ["A"], ["A"], ["B"]] and result["nearby_dates"][0]["train_count"] == 1
    assert result["nearby_note"] == "Based on the weekly schedule, verify before travel." and "verify before travel" in result["message"]
    assert [c["params"].get("date") for c in client.between_calls] == [FRIDAY, None]  # the dated search, then the undated one
    assert len(client.calls) == 2


def numbers_of(day):
    """The train numbers of one nearby date."""
    return [t["number"] for t in day["trains"]]


async def test_a_nearby_date_has_the_trains_with_full_details(env):
    warm(env)
    env.api({}, between=[between_reply(), between_reply(train("A", days=["mon"], dep="07:15"))])
    result = await search_trains("Pune", "Mumbai", FRIDAY)
    first = result["nearby_dates"][0]
    assert first["trains"][0]["number"] == "A" and first["trains"][0]["from"]["departure"] == "07:15" and first["trains"][0]["duration_minutes"] == 195


async def test_at_most_two_nearby_dates_each_side_and_none_outside_seven_days(env):
    warm(env)
    env.api({}, between=[between_reply(), between_reply(train("D"))])  # a daily train: every date qualifies
    result = await search_trains("Pune", "Mumbai", FRIDAY)
    assert [d["days_from_requested"] for d in result["nearby_dates"]] == [1, -1, 2, -2]  # the later date first on a tie
    # With a train that runs only on Mondays, only the two Mondays inside the 7-day window qualify (the 23rd and the 30th).
    env.redis.data.clear()
    warm(env)
    env.api({}, between=[between_reply(), between_reply(train("A", days=["mon"]))])
    result = await search_trains("Pune", "Mumbai", FRIDAY)
    assert [d["date"] for d in result["nearby_dates"]] == ["2026-11-30", "2026-11-23"] and all(abs(d["days_from_requested"]) <= 7 for d in result["nearby_dates"])


async def test_the_later_date_comes_first_when_two_dates_are_equally_near(env):
    warm(env)
    env.api({}, between=[between_reply(), between_reply(train("T", days=["thu", "sat"]))])
    result = await search_trains("Pune", "Mumbai", FRIDAY)
    assert [d["date"] for d in result["nearby_dates"]][:2] == ["2026-11-28", "2026-11-26"]  # Saturday (+1) before Thursday (-1)


async def test_dates_in_the_past_are_never_suggested(env):
    warm(env)
    env.api({}, between=[between_reply(), between_reply(train("T", days=["thu", "sat"]))])
    result = await search_trains("Pune", "Mumbai", "2026-11-21")  # tomorrow; Thursday the 19th is already past
    assert "2026-11-19" not in [d["date"] for d in result["nearby_dates"]]
    assert min(d["date"] for d in result["nearby_dates"]) >= TODAY.isoformat()


async def test_a_train_that_is_here_on_the_second_day_of_its_journey_runs_a_day_later_than_its_run_days(env):
    warm(env)
    env.api({}, between=[between_reply(), between_reply(train("N", days=["thu"], from_day=2))])  # starts Thursday, here on Friday
    result = await search_trains("Pune", "Mumbai", "2026-11-28")  # Saturday: nothing that day
    assert [d["date"] for d in result["nearby_dates"]][:1] == ["2026-11-27"]  # Friday
    assert "2026-11-26" not in [d["date"] for d in result["nearby_dates"]]  # not Thursday


def test_runs_on_uses_the_weekday_at_the_station_not_at_the_origin():
    t = {"run_days": ["thu"], "from": {"day": 2}}
    assert runs_on(t, dt.date(2026, 11, 27)) is True and runs_on(t, dt.date(2026, 11, 26)) is False
    assert runs_on({"run_days": ["mon"], "from": {"day": 1}}, dt.date(2026, 11, 23)) is True
    assert runs_on({"run_days": ["sun"], "from": {"day": 3}}, dt.date(2026, 11, 24)) is True  # Sunday + 2 = Tuesday
    assert runs_on({"run_days": ALL_DAYS, "from": {"day": 5}}, dt.date(2026, 11, 20)) is True
    assert runs_on({"run_days": [], "from": {"day": 1}}, dt.date(2026, 11, 20)) is False
    assert runs_on({"run_days": ["fri"], "from": {"day": None}}, dt.date(2026, 11, 20)) is True  # an unknown day counts as the first


async def test_no_trains_anywhere_near_the_date_says_so(env):
    warm(env)
    env.api({}, between=[between_reply(), between_reply(train("Z", days=[]))])  # a train with no run days never runs
    result = await search_trains("Pune", "Mumbai", FRIDAY)
    assert result["nearby_dates"] == [] and result["nearby_checked"] is True and "within 7 days either" in result["message"]


async def test_when_the_budget_is_spent_nearby_dates_are_not_worked_out_and_the_user_is_asked_for_another_date(env):
    client = env.api(between=[between_reply(), between_reply(train("A", days=["mon"]))])  # two cold lookups, the dated search, then NO more requests
    result = await search_trains("Pune", "Mumbai", FRIDAY)
    assert len(client.calls) == MAX_REQUESTS == 3 and len(client.between_calls) == 1  # the undated search was not made
    assert result["found"] is False and result["no_trains_on_date"] is True and result["nearby_dates"] == [] and result["nearby_checked"] is False
    assert "No trains were found on 2026-11-27 in this data source" in result["message"] and "does not mean none run" in result["message"]
    assert "another date" in result["message"] and "none exist" not in result["message"].replace("does not mean none", "")


async def test_a_cached_undated_list_gives_nearby_dates_even_when_the_lookups_were_cold(env):
    env.redis.data[train_tool._search_key("PUNE", "CSMT", "")] = __import__("json").dumps([
        train_tool._shape_train(train("A", days=["mon"]))])
    client = env.api(between=[between_reply()])  # only the dated search is made
    result = await search_trains("Pune", "Mumbai", FRIDAY)
    assert len(client.calls) == 3 and result["nearby_checked"] is True and result["nearby_dates"][0]["trains"][0]["number"] == "A"


async def test_the_undated_search_that_follows_a_dated_one_costs_one_request_and_is_made_only_when_needed(env):
    warm(env)
    client = env.api({}, between=[between_reply(train("11"))])
    await search_trains("Pune", "Mumbai", FRIDAY)
    assert len(client.between_calls) == 1  # trains were found: no undated search


async def test_a_search_never_makes_more_than_three_requests(env):
    scenarios = [
        dict(date="", between=[between_reply(train("11"))]),
        dict(date=FRIDAY, between=[between_reply(train("11"))]),
        dict(date=FRIDAY, between=[between_reply(), between_reply(train("11"))]),
        dict(date=FRIDAY, between=[between_reply(), between_reply(), between_reply()]),
    ]
    for scenario in scenarios:
        client = env.api(between=scenario["between"])
        await search_trains("Pune", "Mumbai", scenario["date"])
        assert len(client.calls) <= 3
        env.redis.data.clear()


# ---------------------------------------------------------------- failures

@pytest.mark.parametrize("where", ["lookup", "between", "second_between"])
async def test_a_429_means_quota_exceeded_with_the_exact_message(env, where):
    if where == "lookup":
        env.api({"Pune": httpx.Response(429), "Mumbai": MUMBAI})
    elif where == "between":
        env.api(between=[httpx.Response(429)])
    else:
        warm(env)
        env.api({}, between=[between_reply(), httpx.Response(429)])
    date = FRIDAY if where == "second_between" else ""
    result = await search_trains("Pune", "Mumbai", date)
    assert result == {"ok": False, "error": "quota_exceeded", "message": "Train data is unavailable right now."} and QUOTA_MESSAGE == result["message"]


@pytest.mark.parametrize("failure", [httpx.Response(400), httpx.Response(401), httpx.Response(403), httpx.Response(500), httpx.Response(503),
                                     httpx.ReadTimeout("slow"), httpx.ConnectError("down"), httpx.Response(200, text="not json"),
                                     httpx.Response(200, json=["a list"])])
async def test_every_other_failure_is_unavailable_and_is_not_retried(env, failure):
    client = env.api(between=[failure, between_reply(train("11"))])
    result = await search_trains("Pune", "Mumbai")
    assert result == {"ok": False, "error": "unavailable", "message": "Train information is unavailable right now."}
    assert len(client.between_calls) == 1  # no retry


@pytest.mark.parametrize("reply", [httpx.Response(200, json={"data": {"trains": "nope"}}), httpx.Response(200, json={"data": []}), httpx.Response(200, json={})])
async def test_a_reply_that_is_not_shaped_like_railradars_is_unavailable(env, reply):
    env.api(between=[reply])
    assert (await search_trains("Pune", "Mumbai"))["error"] == "unavailable"


async def test_a_failed_lookup_is_unavailable_and_nothing_else_is_asked(env):
    client = env.api({"Pune": httpx.Response(500), "Mumbai": MUMBAI})
    assert (await search_trains("Pune", "Mumbai"))["error"] == "unavailable" and client.between_calls == []


async def test_a_between_404_is_station_not_found(env):
    env.api(between=[httpx.Response(404)])
    assert (await search_trains("Pune", "Mumbai"))["error"] == "station_not_found"


async def test_both_cities_resolving_to_the_same_station_is_refused(env):
    same = lookup_reply(station("PUNE", "Pune Jn", 382))
    client = env.api({"Pune": PUNE, "Poona": same})
    result = await search_trains("Pune", "Poona")
    assert result["error"] == "invalid_arguments" and client.between_calls == []


async def test_the_tool_never_raises(env, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("surprise")

    monkeypatch.setattr(train_tool, "_find_station", boom)
    env.api()
    assert (await search_trains("Pune", "Mumbai"))["error"] == "unavailable"


# ---------------------------------------------------------------- the cache

async def test_a_station_lookup_is_cached_for_30_days_and_asked_only_once(env):
    client = env.api(between=[between_reply(train("11")), between_reply(train("11"))])
    await search_trains("Pune", "Mumbai")
    assert len(client.lookup_calls) == 2
    station_sets = [(k, ex) for k, ex in env.redis.sets if ":station:" in k]
    assert len(station_sets) == 2 and all(ex == STATION_CACHE_TTL_SECONDS == 30 * 24 * 60 * 60 for _, ex in station_sets)
    env.redis.data = {k: v for k, v in env.redis.data.items() if ":station:" in k}  # keep only the station cache
    await search_trains("Pune", "Mumbai")
    assert len(client.lookup_calls) == 2  # the second search made no lookup request at all
    assert len(client.between_calls) == 2


async def test_the_station_cache_ignores_case_and_extra_spaces(env):
    client = env.api(between=[between_reply(train("11")), between_reply(train("11"))])
    await search_trains("Pune", "Mumbai")
    env.redis.data = {k: v for k, v in env.redis.data.items() if ":station:" in k}
    await search_trains("  PUNE ", "mumbai")
    assert len(client.lookup_calls) == 2


async def test_search_results_are_cached_for_6_hours_and_the_second_search_makes_no_request(env):
    client = env.api(between=[between_reply(train("11"), train("22", days=["mon"]))])
    first = await search_trains("Pune", "Mumbai")
    sets = [(k, ex) for k, ex in env.redis.sets if ":search:" in k]
    assert sets == [(f"{CACHE_PREFIX}search:PUNE:CSMT:any", 21600)] and SEARCH_CACHE_TTL_SECONDS == 6 * 60 * 60
    again = await search_trains("Pune", "Mumbai")
    assert again == first and len(client.calls) == 3  # nothing new was requested (the lookups are cached too)


async def test_a_dated_and_an_undated_search_are_cached_separately(env):
    env.api(between=[between_reply(train("11")), between_reply(train("22"))])
    await search_trains("Pune", "Mumbai")
    await search_trains("Pune", "Mumbai", FRIDAY)
    keys = [k for k, _ in env.redis.sets if ":search:" in k]
    assert f"{CACHE_PREFIX}search:PUNE:CSMT:any" in keys and f"{CACHE_PREFIX}search:PUNE:CSMT:{FRIDAY}" in keys


async def test_failures_and_empty_results_are_never_cached(env):
    env.api(between=[httpx.Response(500), httpx.Response(429), between_reply()])
    for _ in range(3):
        await search_trains("Pune", "Mumbai")
    assert [k for k, _ in env.redis.sets if ":search:" in k] == []


async def test_failed_lookups_and_empty_lookups_are_never_cached(env):
    env.api({"Pune": httpx.Response(500), "Mumbai": lookup_reply()})
    await search_trains("Pune", "Mumbai")
    assert [k for k, _ in env.redis.sets if ":station:" in k] == []


async def test_a_damaged_cache_entry_counts_as_a_miss(env):
    env.redis.data[train_tool._search_key("PUNE", "CSMT", "")] = "not json"
    warm(env)
    client = env.api({}, between=[between_reply(train("11"))])
    assert (await search_trains("Pune", "Mumbai"))["found"] is True and len(client.between_calls) == 1


async def test_a_cached_train_that_no_longer_has_the_right_shape_counts_as_a_miss(env):
    env.redis.data[train_tool._search_key("PUNE", "CSMT", "")] = '[{"number": "x", "from": {"code": "evil!"}, "to": {}}]'
    warm(env)
    client = env.api({}, between=[between_reply(train("11"))])
    result = await search_trains("Pune", "Mumbai")
    assert numbers(result) == ["11"] and len(client.between_calls) == 1


async def test_redis_down_still_works_and_warns_once_per_search(env, caplog):
    env.use_redis(FakeRedis(broken=True))
    client = env.api(between=[between_reply(train("11"))])
    with caplog.at_level(logging.WARNING):
        result = await search_trains("Pune", "Mumbai")
    assert result["found"] is True and len(client.calls) == 3
    assert caplog.text.count("Redis unavailable") == 1
    for text in ("redisuser", "secretpw", "cache.example.test", REDIS_URL, KEY):
        assert text not in caplog.text


async def test_redis_not_set_skips_the_cache_silently(env, monkeypatch, caplog):
    monkeypatch.setattr(train_tool.get_settings(), "redis_url", "")
    monkeypatch.setattr(train_tool.redis, "from_url", lambda *a, **k: pytest.fail("Redis must not be used"))
    env.api(between=[between_reply(train("11"))])
    with caplog.at_level(logging.DEBUG):
        assert (await search_trains("Pune", "Mumbai"))["found"] is True
    assert "Redis" not in caplog.text


async def test_the_redis_client_gets_short_timeouts_and_is_closed(env, monkeypatch):
    seen = {}

    def spy(url, **kw):
        seen.update(kw)
        return env.redis

    monkeypatch.setattr(train_tool.redis, "from_url", spy)
    env.api(between=[between_reply(train("11"))])
    await search_trains("Pune", "Mumbai")
    assert seen == {"socket_connect_timeout": 2.0, "socket_timeout": 2.0} and env.redis.closed is True


# ---------------------------------------------------------------- the key

async def test_the_key_is_never_in_the_url_the_params_the_logs_or_the_results(env, caplog):
    client = env.api({"Pune": httpx.Response(500), "Mumbai": MUMBAI})
    with caplog.at_level(logging.DEBUG):
        failed = await search_trains("Pune", "Mumbai")
        client = env.api(between=[between_reply(train("11"))])
        worked = await search_trains("Pune", "Mumbai")
        env.api(between=[httpx.Response(429)])
        quota = await search_trains("Pune", "Mumbai", "2026-12-01")
    assert KEY not in caplog.text and KEY not in repr(failed) and KEY not in repr(worked) and KEY not in repr(quota)
    assert all(KEY not in c["url"] and KEY not in repr(c["params"]) for c in client.calls)
    assert "status 500" in caplog.text and "status 429" in caplog.text  # only the status codes are logged
    assert "Pune" not in caplog.text and "Mumbai" not in caplog.text  # nor the cities the user asked about


# ---------------------------------------------------------------- registration and the description

async def test_the_tool_is_registered_as_search_trains_and_the_old_name_is_gone():
    import server

    tools = {t.name: t for t in await server.mcp.list_tools()}
    old_name = "get_train" + "_details"  # in two pieces: the old name must not appear anywhere in the project
    assert "search_trains" in tools and old_name not in tools
    tool = tools["search_trains"]
    assert set(tool.inputSchema["properties"]) == {"source_city", "destination_city", "date"}
    assert tool.inputSchema["required"] == ["source_city", "destination_city"]  # the date is optional


async def test_the_description_is_three_parts_and_explains_the_conversation():
    import server

    description = next(t for t in await server.mcp.list_tools() if t.name == "search_trains").description
    assert description.index("1. What it does and returns:") < description.index("2. When to call:") < description.index("3. When NOT to call:")
    for text in ("needs_date", "needs_choice", "which station was used", "weekly schedule", "Train data is unavailable right now", "verify before travel", "search_buses"):
        assert text in description
    assert ("get_train" + "_details") not in description and "train number" not in description.split("3. When NOT")[0]
