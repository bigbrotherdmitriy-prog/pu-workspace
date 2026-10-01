# Build from the repository root: docker build -f backend/Dockerfile --target runtime
#   --build-arg PU_RELEASE_REVISION=<40-character SHA> -t app-backend:<SHA> .
# Local-only development must explicitly use PU_BUILD_MODE=development and local-dev.
FROM node:22.20.0-bookworm-slim@sha256:b21fe589dfbe5cc39365d0544b9be3f1f33f55f3c86c87a76ff65a02f8f5848e AS frontend-build
WORKDIR /src/frontend
ARG PU_RELEASE_REVISION
ARG PU_BUILD_MODE=production
RUN case "$PU_BUILD_MODE:$PU_RELEASE_REVISION" in \
      production:*) test "${#PU_RELEASE_REVISION}" -eq 40 \
        && test "$(printf '%s' "$PU_RELEASE_REVISION" | tr -d '0-9a-f')" = "" ;; \
      development:local-dev) ;; \
      *) echo "Explicit production SHA or local-dev development mode is required" >&2; exit 1 ;; \
    esac
RUN npm install --global pnpm@10.17.1 \
    && test "$(node --version)" = "v22.20.0" \
    && test "$(pnpm --version)" = "10.17.1"
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm run check && pnpm run test && pnpm run build \
    && node scripts/write-build-info.mjs --lockfile pnpm-lock.yaml --output /src/frontend-build-info.json

FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c AS backend-base
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends antiword default-jre-headless poppler-utils tesseract-ocr tesseract-ocr-eng tesseract-ocr-rus tesseract-ocr-osd \
    && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
RUN python -c "import importlib, jpype, mpxj; jpype.startJVM(); assert importlib.import_module('org.mpxj.reader').UniversalProjectReader"

FROM backend-base AS application
ARG PU_RELEASE_REVISION
ARG PU_BUILD_MODE=production
ENV PU_RELEASE_REVISION=${PU_RELEASE_REVISION} \
    PU_BUILD_MODE=${PU_BUILD_MODE}
LABEL org.opencontainers.image.revision=${PU_RELEASE_REVISION} \
      com.pu-workspace.primary.revision=${PU_RELEASE_REVISION}
COPY backend/app ./app
COPY backend/scripts ./scripts
COPY backend/alembic.ini ./
COPY backend/migrations ./migrations
# Tracked legacy react_dist is excluded by .dockerignore. Only fresh frontend output
# is copied, after backend/app, so no later source COPY can replace it.
COPY --from=frontend-build /src/backend/app/react_dist ./app/react_dist
COPY --from=frontend-build /src/frontend-build-info.json ./frontend-build-info.json
RUN python -m scripts.frontend_manifest create \
    --root /app/app/react_dist --output /app/frontend-build-manifest.json \
    --revision "$PU_RELEASE_REVISION" --build-info /app/frontend-build-info.json \
    --mode "$PU_BUILD_MODE"

# CI adds tests without changing the runtime layers, tools, frontend or manifest.
FROM application AS testing
COPY backend/tests ./tests
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# The default/production target does not contain backend test fixtures.
FROM application AS runtime
CMD ["sh", "-c", "alembic -c alembic.ini upgrade head && python -m scripts.preflight && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
