# Single app image (ADR-13.3): FastAPI serving the API + the built Vite SPA on one origin.
# Deployed to Amazon ECS Express Mode (Fargate + ALB); CI builds this on every push so it can
# never rot.

# ── Stage 1: build the SPA ───────────────────────────────────────────────────────────────────
# Node 24: react-router@8 requires >=22.22.0, and pinning the major keeps CI and the image on
# the same runtime as the lockfile was resolved against.
FROM node:24-slim AS web

WORKDIR /web

# Copy the manifests alone first so `npm ci` is cached and only re-runs when deps change,
# not on every source edit.
COPY web/package.json web/package-lock.json ./
RUN npm ci

COPY web/ ./
RUN npm run build

# ── Stage 2: the app image ───────────────────────────────────────────────────────────────────
FROM python:3.12-slim

# Never run as root in production.
RUN useradd --create-home app
WORKDIR /app

# Install the project (non-editable). pyproject metadata needs README + LICENSE.
COPY pyproject.toml README.md LICENSE ./
COPY engine/ engine/
COPY agent/ agent/
COPY api/ api/
COPY cli/ cli/
COPY evals/ evals/
RUN pip install --no-cache-dir .

# The built SPA. `api/spa.py` resolves this path relative to the working directory, and mounts
# it only if index.html is present — so a build that skipped stage 1 still serves the API.
COPY --from=web /web/dist ./web/dist

# CockroachDB Cloud's serving cert chains to this root (public trust-anchor data, not a
# secret — see docs/deploy.md → Runtime configuration). DATABASE_URL's sslrootcert points
# here so `sslmode=verify-full` has somewhere to find it; nothing else on the image provides
# one, and libpq refuses to connect without it.
# The directory is created (and chmod'd) *before* the COPY on purpose: COPY --chmod applies
# the same mode to any directory it auto-creates, and 444 on a directory strips its execute
# bit, making it untraversable by the non-root `app` user — caught by running the image
# locally as that user before this ever reached ECS.
RUN mkdir -p /app/certs && chmod 755 /app/certs
COPY --chmod=444 deploy/cockroachdb-ca.crt /app/certs/cockroachdb-ca.crt

USER app
EXPOSE 8080

# Container port 8080; ALB health check: GET /healthz
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8080"]
