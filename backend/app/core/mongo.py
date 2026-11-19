# mongo.py: the one shared MongoDB client (PyMongo's AsyncMongoClient). main.py creates it at startup and closes
# it at shutdown (FastAPI lifespan); everything else gets the database through get_db(). The URL comes from
# settings (MONGODB_URI), so it can point at MongoDB Atlas (mongodb+srv:// or mongodb://). It is required.
# The URL holds a password, so nothing in this file ever logs it.

import asyncio

from fastapi import Request
from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from app.config import Settings, get_settings

# How long to wait for the server to be found, and for a ping to answer, in seconds. Short, so a slow or
# unreachable Atlas cannot hang startup or /ready (pymongo's own default is 30 seconds).
SERVER_TIMEOUT_SECONDS = 5
PING_TIMEOUT_SECONDS = 5


def create_client(settings: Settings) -> AsyncMongoClient:
    """Build the shared client. It connects lazily: no network call happens until the first command."""
    return AsyncMongoClient(
        settings.mongo_url,
        serverSelectionTimeoutMS=SERVER_TIMEOUT_SECONDS * 1000,
        appname="trailwise",  # shows up in Atlas, so you can tell which app is connected
    )


async def ping(client: AsyncMongoClient) -> bool:
    """Ask MongoDB "are you there?". True if it answers in time, False on any failure (never raises)."""
    try:
        await asyncio.wait_for(client.admin.command("ping"), timeout=PING_TIMEOUT_SECONDS)
        return True
    except Exception:  # noqa: BLE001 - any failure (timeout, auth, network) simply means "not ready"
        return False


def get_db(request: Request) -> AsyncDatabase:
    """The database named by settings.mongo_db, taken from the shared client. Use it as a FastAPI dependency."""
    return request.app.state.mongo[get_settings().mongo_db]
