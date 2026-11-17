# main.py: creates the FastAPI app and defines the /health and /ready endpoints.
# Run it with: uvicorn app.main:app --reload

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.chat import router as chat_router
from app.config import get_settings


def create_app() -> FastAPI:
    """Build and return the FastAPI app with its middleware and endpoints."""
    settings = get_settings()  # fails fast here if the config is invalid
    app = FastAPI(title="Trailwise")

    # Only the frontend's own address may call the API from a browser.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,  # needed later for httpOnly cookie login
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["*"],
    )

    # Chat endpoint: POST /chat.
    app.include_router(chat_router)

    @app.get("/health")
    async def health():
        """Liveness check: returns ok if the process is running."""
        return {"status": "ok"}

    @app.get("/ready")
    async def ready():
        """Readiness check: no external dependencies yet, so it is ok once the app is up."""
        return {"status": "ok"}

    return app


# The app object that uvicorn loads.
app = create_app()
