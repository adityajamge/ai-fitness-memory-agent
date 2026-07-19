# Single app image (ADR-13.3): FastAPI serving API + (from Phase 6) the built Vite SPA.
# Deployed to Amazon ECS Express Mode (Fargate + ALB); CI builds this on every push
# so it can never rot. Phase 6 adds a node build stage for web/ and copies its dist/.

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

USER app
EXPOSE 8080

# Container port 8080; ALB health check: GET /healthz
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8080"]
