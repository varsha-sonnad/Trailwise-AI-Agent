# Dockerfile: builds the Trailwise frontend image in two stages.
#   Stage 1 (node): installs the exact packages from package-lock.json and builds the React app.
#   Stage 2 (nginx): serves only the built files, as a non-root user, on port 8080.
# Build context is this folder:  docker compose build frontend
# The image holds no secrets. VITE_API_URL is the (non-secret) backend address the BROWSER will call;
# it is baked into the JavaScript at build time, so changing it needs a rebuild.

# ---- Stage 1: build the app ----
FROM node:22-alpine AS build
WORKDIR /app

# Install packages first so rebuilds are fast when only the source changes.
COPY package.json package-lock.json ./
RUN npm ci

# Copy the source and build it. The result is the dist/ folder.
COPY . .
ARG VITE_API_URL=http://localhost:8000
ENV VITE_API_URL=$VITE_API_URL
# Non-secret Keycloak settings for the browser (where Keycloak is, which realm, which client).
# The map tile address (public, not a secret; it ends up in the browser bundle). Must contain {z}, {x} and {y}.
ARG VITE_MAP_TILE_URL=https://tile.openstreetmap.org/{z}/{x}/{y}.png
ENV VITE_MAP_TILE_URL=$VITE_MAP_TILE_URL

ARG VITE_KEYCLOAK_URL=http://localhost:8080
ARG VITE_KEYCLOAK_REALM=trailwise
ARG VITE_KEYCLOAK_CLIENT_ID=trailwise-web
ENV VITE_KEYCLOAK_URL=$VITE_KEYCLOAK_URL VITE_KEYCLOAK_REALM=$VITE_KEYCLOAK_REALM VITE_KEYCLOAK_CLIENT_ID=$VITE_KEYCLOAK_CLIENT_ID
RUN npm run build

# ---- Stage 2: serve the built files ----
# The "unprivileged" nginx image runs as a normal user, so it listens on 8080, not 80.
FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
# Site settings: send unknown paths (such as /app) to index.html so the React app can show them.
COPY nginx.conf /etc/nginx/conf.d/default.conf

# The web server port inside the container (docker-compose.yml maps 5173 on your machine to it).
EXPOSE 8080
