# Dockerfile: builds one image that runs the Trailwise backend AND its STDIO MCP server.
# Build from the PROJECT ROOT (the context must reach both backend/ and mcp_server/):
#     docker compose build backend
# The backend starts mcp_server/server.py as a subprocess, so both folders live in one image,
# laid out as the code expects: /app/backend/app/... and /app/mcp_server/...
# No secrets are baked in: the real .env is mounted read-only at /app/.env by docker-compose.yml.

FROM python:3.12-slim

# Print logs straight away and do not write .pyc files into the image.
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

# Work in /app/backend; mcp_client.py finds the MCP server at /app/mcp_server from here.
WORKDIR /app/backend

# Install the packages first so rebuilds are fast when only the code changes.
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The backend code, its tests, the pytest settings, and the MCP server (the .venv folders are ignored).
COPY backend/app ./app
COPY backend/tests ./tests
COPY backend/pytest.ini .
COPY mcp_server /app/mcp_server

# Run as a normal user; the audit log needs a writable logs/ folder.
RUN useradd --create-home trailwise \
    && mkdir -p /app/backend/logs \
    && chown -R trailwise:trailwise /app
USER trailwise

# The API port.
EXPOSE 8000

# Start the API. 0.0.0.0 is needed so the published port reaches it from outside the container.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
