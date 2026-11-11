# Bitcoin Investigation Platform: API + dashboard in one image.
# Build once while online (docker compose build); the resulting image runs offline.

# ── 1. dashboard ────────────────────────────────────────────────────────────
FROM node:22-slim AS dashboard
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ── 2. runtime ──────────────────────────────────────────────────────────────
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install -r requirements.txt
COPY backend/ backend/
COPY scripts/ scripts/
COPY data/synthetic/ data/synthetic/
COPY .env.example pyproject.toml README.md ./
COPY --from=dashboard /build/dist frontend/dist
RUN useradd --create-home --uid 10001 analyst && mkdir -p data/raw/uploads data/geoip data/processed && chown -R analyst:analyst /app
USER analyst
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/datasets', timeout=4)" || exit 1
CMD ["python", "-m", "uvicorn", "backend.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
