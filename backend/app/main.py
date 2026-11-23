# main.py: creates the FastAPI app and defines the /health and /ready endpoints.
# Run it with: uvicorn app.main:app --reload

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.agent.modes import validate_prompts
from app.api.chat import router as chat_router
from app.api.me import router as me_router
from app.api.trips import router as trips_router
from app.config import get_settings
from app.core.debug_log import warn_if_debug_logging
from app.core.mongo import create_client, ping
from app.services.trips_store import ensure_indexes

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown: create the shared MongoDB client, ping it once, and close it at the end."""
    warn_if_debug_logging()  # a warning when the local-testing debug log (DEBUG_LOG_FILE / LOG_MESSAGES) is on
    client = create_client(get_settings())
    app.state.mongo = client
    # The client connects lazily, so a down database does not stop the app: warn and carry on. /ready reports it.
    if not await ping(client):
        logger.warning("MongoDB did not answer the startup ping; /ready will return 503 until it does")
    else:
        # Create the (owner_id, created_at) indexes; a failure logs the index name only and startup carries on.
        await ensure_indexes(client[get_settings().mongo_db])
    try:
        yield
    finally:
        await client.close()  # release the connections cleanly


def create_app() -> FastAPI:
    """Build and return the FastAPI app with its middleware and endpoints."""
    settings = get_settings()  # fails fast here if the config is invalid
    validate_prompts()  # fails fast here if any prompt file has a bad heading (see docs/prompts.md)
    app =FastAPI(title="Trailwise", lifespan=lifespan)

    # Only the frontend's own address may call the API from a browser.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,  # needed later for httpOnly cookie login
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["*"],
    )

    @app.exception_handler(RequestValidationError)
    async def bad_request(request: Request, exc: RequestValidationError):
        """A request that breaks the rules gets 422 with where and why, never the values that were sent.

        FastAPI's own 422 body repeats the sent values, which here would be the user's text and the history, and it even
        fails (a 500) when a value is NaN or Infinity because those cannot be written as JSON. Only the location, the
        message and the kind of each error are returned.
        """
        detail = [{"loc": list(e.get("loc", ())), "msg": str(e.get("msg", "")), "type": str(e.get("type", ""))} for e in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": detail})

    # Chat endpoint: POST /chat.
    app.include_router(chat_router)
    # Who am I: GET /me (needs a Keycloak token).
    app.include_router(me_router)
    # Saved trips and explore history: /trips, /explore-history (signed-in users only).
    app.include_router(trips_router)

    @app.get("/health")
    async def health():
        """Liveness check: returns ok if the process is running."""
        return {"status": "ok"}

    @app.get("/ready")
    async def ready(request: Request):
        """Readiness check: ok only if MongoDB answers a ping, otherwise HTTP 503."""
        client = getattr(request.app.state, "mongo", None)
        if client is None or not await ping(client):
            # A generic message: no connection details or errors are exposed.
            raise HTTPException(status_code=503, detail="Not ready")
        return {"status": "ok"}

    return app


# The app object that uvicorn loads.
app = create_app()
