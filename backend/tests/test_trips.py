# test_trips.py: tests for saved trips, explore history and DELETE /me/data, plus the explore save inside POST /chat.
# MongoDB is replaced by a small in-memory fake (FakeDb below), so nothing touches Atlas. Sign-in is replaced by
# overriding the auth dependencies with a chosen user id, which also lets one test act as two different users.

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.api import chat as chat_module
from app.core.auth import Principal, get_optional_principal, require_user
from app.core.mongo import get_db
from app.main import app
from app.services import trips_store


# ------------------------------------------------------------------ the fake MongoDB

class FakeResult:
    """What delete_one / delete_many return: just the deleted_count."""

    def __init__(self, n):
        self.deleted_count = n


class FakeCursor:
    """find(...) result: supports .sort().skip().limit() and async iteration, like PyMongo's cursor."""

    def __init__(self, docs):
        self.docs = list(docs)

    def sort(self, keys):
        """Sort by a list of (field, direction) pairs; the last key is applied first (stable sorts)."""
        for field, direction in reversed(keys):
            self.docs.sort(key=lambda d: d[field], reverse=direction == -1)
        return self

    def skip(self, n):
        self.docs = self.docs[n:]
        return self

    def limit(self, n):
        self.docs = self.docs[:n]
        return self

    async def __aiter__(self):
        for d in self.docs:
            yield d


def _matches(doc, flt):
    """Equality on every field, plus {"$in": [...]} for one field."""
    for key, want in flt.items():
        if isinstance(want, dict) and "$in" in want:
            if doc.get(key) not in want["$in"]:
                return False
        elif doc.get(key) != want:
            return False
    return True


class FakeCollection:
    """An in-memory stand-in for an AsyncCollection with just the calls the store uses."""

    def __init__(self):
        self.docs = []
        self.indexes = []
        self.fail_index = False
        self.fail_insert = False

    async def create_index(self, keys, name=None):
        if self.fail_index:
            raise RuntimeError("mongodb+srv://user:secret@host failed")  # a message that must never be logged
        self.indexes.append((tuple(keys), name))

    async def insert_one(self, doc):
        if self.fail_insert:
            raise RuntimeError("insert failed")
        self.docs.append(dict(doc))

    async def count_documents(self, flt, limit=0):
        n = sum(1 for d in self.docs if _matches(d, flt))
        return min(n, limit) if limit else n

    def find(self, flt, projection=None):
        return FakeCursor(d for d in self.docs if _matches(d, flt))

    async def find_one(self, flt):
        return next((d for d in self.docs if _matches(d, flt)), None)

    async def delete_one(self, flt):
        for d in self.docs:
            if _matches(d, flt):
                self.docs.remove(d)
                return FakeResult(1)
        return FakeResult(0)

    async def delete_many(self, flt):
        keep = [d for d in self.docs if not _matches(d, flt)]
        n = len(self.docs) - len(keep)
        self.docs = keep
        return FakeResult(n)


class FakeDb:
    """db["name"] gives the same FakeCollection every time."""

    def __init__(self):
        self.cols = {}

    def __getitem__(self, name):
        return self.cols.setdefault(name, FakeCollection())


# ------------------------------------------------------------------ fixtures

@pytest.fixture
def db():
    return FakeDb()


@pytest.fixture
def api(db):
    """A client whose database is the fake and whose signed-in user can be switched with api.login(id)."""
    state = {"user": None}

    async def fake_require_user():
        from app.core.auth import _unauthorized

        if state["user"] is None:
            raise _unauthorized()
        return Principal(kind="user", id=state["user"])

    async def fake_optional():
        return Principal(kind=state["kind"], id=state["user"]) if state["user"] else None

    state["kind"] = "user"
    app.dependency_overrides[require_user] = fake_require_user
    app.dependency_overrides[get_optional_principal] = fake_optional
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as client:
        client.login = lambda user, kind="user": state.update(user=user, kind=kind)
        yield client
    app.dependency_overrides.clear()


def trip(**over):
    body = {"title": "Hampi weekend", "places": [{"name": "Hampi", "lat": 15.33, "lng": 76.46}], "summary": "Ruins", "reply": "Day 1 ..."}
    body.update(over)
    return body


# ------------------------------------------------------------------ trips

def test_guest_and_anonymous_get_401(api):
    assert api.get("/trips").status_code == 401
    assert api.post("/trips", json=trip()).status_code == 401
    assert api.get("/explore-history").status_code == 401
    assert api.delete("/me/data").status_code == 401


def test_create_and_read_a_trip(api, db):
    api.login("alice")
    r = api.post("/trips", json=trip())
    assert r.status_code == 201
    body = r.json()
    assert body["title"] == "Hampi weekend" and body["reply"] == "Day 1 ..." and "owner_id" not in body
    assert db["trips"].docs[0]["owner_id"] == "alice"  # stored with the token's sub
    assert api.get(f"/trips/{body['id']}").json()["places"][0]["name"] == "Hampi"


def test_owner_id_in_the_body_is_refused(api):
    api.login("alice")
    assert api.post("/trips", json=trip(owner_id="bob")).status_code == 422


@pytest.mark.parametrize("bad", [
    {"title": ""}, {"title": "   "}, {"title": "x" * 101}, {"summary": "x" * 501}, {"reply": ""}, {"reply": "x" * 8001},
    {"places": [{"name": "P", "lat": 0, "lng": 0}] * 13}, {"places": [{"name": "P", "lat": 99, "lng": 0}]},
    {"title": "bad\x00title"}, {"created_at": "2020-01-01T00:00:00"}, {"id": "abc"},
])
def test_trip_validation_and_size_caps(api, bad):
    api.login("alice")
    assert api.post("/trips", json=trip(**bad)).status_code == 422


def test_limits_exactly_at_the_caps_are_accepted(api):
    api.login("alice")
    places = [{"name": f"Place {i}", "lat": 1, "lng": 1} for i in range(12)]
    assert api.post("/trips", json=trip(title="x" * 100, summary="y" * 500, reply="z" * 8000, places=places)).status_code == 201


def test_another_users_trip_is_404_for_read_and_delete(api, db):
    api.login("alice")
    trip_id = api.post("/trips", json=trip()).json()["id"]
    api.login("bob")
    assert api.get(f"/trips/{trip_id}").status_code == 404
    assert api.delete(f"/trips/{trip_id}").status_code == 404
    assert api.get("/trips").json()["items"] == []
    assert len(db["trips"].docs) == 1  # still there
    api.login("alice")
    assert api.delete(f"/trips/{trip_id}").status_code == 204
    assert api.get(f"/trips/{trip_id}").status_code == 404


def test_a_malformed_trip_id_is_404(api):
    api.login("alice")
    assert api.get("/trips/not-an-id").status_code == 404
    assert api.delete("/trips/%24ne").status_code == 404


def test_trip_limit_is_100_per_user_with_a_clear_error(api):
    api.login("alice")
    for i in range(100):
        assert api.post("/trips", json=trip(title=f"T{i}")).status_code == 201
    r = api.post("/trips", json=trip())
    assert r.status_code == 409 and "100" in r.json()["detail"]
    api.login("bob")  # the limit is per user
    assert api.post("/trips", json=trip()).status_code == 201


def test_list_is_newest_first_and_paged(api, db):
    api.login("alice")
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(5):  # insert with known, increasing times
        db["trips"].docs.append({"_id": f"{i:032x}", "owner_id": "alice", "title": f"T{i}", "places": [], "summary": "",
                                 "reply": "r", "created_at": base + timedelta(days=i)})
    first = api.get("/trips?page=1&page_size=2").json()
    assert [t["title"] for t in first["items"]] == ["T4", "T3"]
    assert [t["title"] for t in api.get("/trips?page=3&page_size=2").json()["items"]] == ["T0"]
    assert "reply" not in first["items"][0]  # the list leaves out the long reply


@pytest.mark.parametrize("query", ["page_size=51", "page_size=0", "page=0", "page=abc"])
def test_page_size_is_capped_at_50(api, query):
    api.login("alice")
    assert api.get(f"/trips?{query}").status_code == 422
    assert api.get("/trips?page_size=50").status_code == 200


def test_a_database_failure_is_a_generic_503(api, db):
    from pymongo.errors import PyMongoError

    async def boom(*a, **k):
        raise PyMongoError("mongodb+srv://user:secret@host")

    db["trips"].insert_one = boom
    api.login("alice")
    r = api.post("/trips", json=trip())
    assert r.status_code == 503 and "secret" not in r.text


# ------------------------------------------------------------------ explore history

def test_explore_history_keeps_the_latest_50_newest_first(api, db):
    api.login("alice")
    for i in range(55):
        asyncio.run(trips_store.add_explore(db, "alice", f"Place {i}"))
    asyncio.run(trips_store.add_explore(db, "bob", "Goa"))
    items = api.get("/explore-history").json()["items"]
    assert len(items) == 50 and items[0]["place"] == "Place 54" and items[-1]["place"] == "Place 5"
    assert len([d for d in db["explore_history"].docs if d["owner_id"] == "alice"]) == 50
    assert len([d for d in db["explore_history"].docs if d["owner_id"] == "bob"]) == 1  # bob's was not trimmed


def test_explore_history_is_per_owner_and_deletable(api, db):
    asyncio.run(trips_store.add_explore(db, "alice", "Hampi"))
    asyncio.run(trips_store.add_explore(db, "bob", "Goa"))
    api.login("bob")
    assert [i["place"] for i in api.get("/explore-history").json()["items"]] == ["Goa"]
    assert api.delete("/explore-history").json() == {"deleted": 1}
    assert [d["owner_id"] for d in db["explore_history"].docs] == ["alice"]


def test_delete_my_data_removes_only_my_trips_and_history(api, db):
    api.login("alice")
    api.post("/trips", json=trip())
    api.post("/trips", json=trip())
    asyncio.run(trips_store.add_explore(db, "alice", "Hampi"))
    api.login("bob")
    api.post("/trips", json=trip())
    asyncio.run(trips_store.add_explore(db, "bob", "Goa"))
    api.login("alice")
    assert api.delete("/me/data").json() == {"trips_deleted": 2, "explore_deleted": 1}
    assert [d["owner_id"] for d in db["trips"].docs] == ["bob"]
    assert [d["owner_id"] for d in db["explore_history"].docs] == ["bob"]


# ------------------------------------------------------------------ indexes

async def test_indexes_are_created_on_both_collections(db):
    await trips_store.ensure_indexes(db)
    assert db["trips"].indexes == [((("owner_id", 1), ("created_at", -1)), "trips_owner_created")]
    assert db["explore_history"].indexes[0][1] == "explore_owner_created"


async def test_a_failed_index_logs_only_its_name_and_carries_on(db, caplog):
    db["trips"].fail_index = True
    with caplog.at_level("WARNING"):
        await trips_store.ensure_indexes(db)
    assert "trips_owner_created" in caplog.text and "secret" not in caplog.text and "mongodb" not in caplog.text
    assert db["explore_history"].indexes  # the second index was still created


# ------------------------------------------------------------------ the explore save inside POST /chat

class Reply(str):
    """Like AgentReply: a str with the mode and the places."""

    mode = "explore"
    images = []
    places = [{"name": "Hampi", "lat": 15.33, "lng": 76.46}]


@pytest.fixture
def chat_api(api, db, monkeypatch):
    async def fake_run_agent(message, **kwargs):
        return Reply("Hampi is lovely.")

    monkeypatch.setattr("app.api.chat.run_agent", fake_run_agent)
    # chat.py calls get_db directly (not as a dependency, so guests never need a database), so patch it here.
    monkeypatch.setattr("app.api.chat.get_db", lambda request: db)
    return api


def test_a_signed_in_explore_reply_records_one_entry(chat_api, db):
    chat_api.login("alice")
    r = chat_api.post("/chat", json={"message": "tell me about Hampi"})
    assert r.status_code == 200 and r.json()["saved"] is True
    doc = db["explore_history"].docs[0]
    assert doc["owner_id"] == "alice" and doc["place"] == "Hampi"


def test_guests_store_nothing(chat_api, db):
    chat_api.login("g" * 30, kind="guest")
    r = chat_api.post("/chat", json={"message": "tell me about Hampi"})
    assert r.json()["saved"] is None and db["explore_history"].docs == []


def test_a_non_explore_reply_is_not_recorded(chat_api, db, monkeypatch):
    class Itinerary(Reply):
        mode = "itinerary"

    async def fake(message, **kwargs):
        return Itinerary("plan")

    monkeypatch.setattr("app.api.chat.run_agent", fake)
    chat_api.login("alice")
    assert chat_api.post("/chat", json={"message": "plan 3 days"}).json()["saved"] is None
    assert db["explore_history"].docs == []


def test_a_failed_save_still_returns_the_reply_with_saved_false(chat_api, db, caplog):
    db["explore_history"].fail_insert = True
    chat_api.login("alice")
    with caplog.at_level("WARNING"):
        r = chat_api.post("/chat", json={"message": "tell me about Hampi, a secret question"})
    assert r.status_code == 200 and r.json()["reply"] == "Hampi is lovely." and r.json()["saved"] is False
    assert "secret question" not in caplog.text and "Hampi" not in caplog.text  # no message or place text in the log
    assert "RuntimeError" in caplog.text


def test_a_slow_save_times_out_with_saved_false(chat_api, db, monkeypatch):
    async def slow(*a, **k):
        await asyncio.sleep(5)

    monkeypatch.setattr(trips_store, "add_explore", slow)
    monkeypatch.setattr(chat_module, "SAVE_TIMEOUT_SECONDS", 0.05)
    chat_api.login("alice")
    r = chat_api.post("/chat", json={"message": "tell me about Hampi"})
    assert r.status_code == 200 and r.json()["saved"] is False
