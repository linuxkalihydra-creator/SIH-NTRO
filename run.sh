#!/usr/bin/env bash
# Start the Bitcoin Investigation Platform (API + dashboard) on this machine.
#
#   bash run.sh              start on http://127.0.0.1:8000
#   bash run.sh --demo       analyse the bundled synthetic capture first, so the dashboard opens on results
#   bash run.sh --neo4j      also start the optional Neo4j graph database with Docker Compose
#   HOST=0.0.0.0 PORT=9000 bash run.sh
#
# Nothing here needs the internet once the dependencies are installed (see README, "Going offline").
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
PYTHON_BIN="${PYTHON:-python3}"
DEMO=0
NEO4J=0
for argument in "$@"; do
  case "$argument" in
    --demo) DEMO=1 ;;
    --neo4j) NEO4J=1 ;;
    -h|--help) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $argument (try --help)" >&2; exit 2 ;;
  esac
done

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }

need_python() {
  "$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null \
    || { echo "Python 3.12 or newer is required (found: $("$PYTHON_BIN" --version 2>&1 || echo none))." >&2; exit 1; }
}

# 1. Python environment. In order: an environment from an earlier run; uv with the lock file; python3 -m venv
#    with pip (from ./wheelhouse when it exists, so no network); and, on a machine without the venv module,
#    a plain install next to the code using the pip wheel that the offline bundle carries.
DEPS_PATH="$ROOT/.deps${PYTHONPATH:+:$PYTHONPATH}"
if [ -x .venv/bin/python ]; then
  PY=(.venv/bin/python)
elif [ -d .deps ]; then
  PY=(env "PYTHONPATH=$DEPS_PATH" "$PYTHON_BIN")
elif command -v uv >/dev/null 2>&1 && [ ! -d wheelhouse ]; then
  say "Creating .venv with uv (locked dependencies)"
  uv sync --no-dev
  PY=(.venv/bin/python)
else
  need_python
  if "$PYTHON_BIN" -m venv .venv >/dev/null 2>&1; then
    if [ -d wheelhouse ]; then
      say "Installing dependencies into .venv from ./wheelhouse (no network)"
      .venv/bin/python -m pip install --quiet --no-index --find-links wheelhouse -r requirements.txt
    else
      say "Installing dependencies into .venv from PyPI"
      .venv/bin/python -m pip install --quiet -r requirements.txt
    fi
    PY=(.venv/bin/python)
  elif ls wheelhouse/pip-*.whl >/dev/null 2>&1; then
    rm -rf .venv
    say "This Python has no venv module: installing dependencies into ./.deps with the bundled pip (no network)"
    "$PYTHON_BIN" "$(ls wheelhouse/pip-*.whl | head -n 1)/pip" install --quiet --no-index --find-links wheelhouse --target .deps -r requirements.txt
    PY=(env "PYTHONPATH=$DEPS_PATH" "$PYTHON_BIN")
  else
    rm -rf .venv
    echo "Cannot create a Python environment. Install uv (https://docs.astral.sh/uv/) or the venv module (sudo apt install python3-venv)." >&2
    exit 1
  fi
fi

# 2. Configuration.
[ -f .env ] || { cp .env.example .env; say "Created .env from .env.example"; }

# 3. Dashboard: a committed/bundled build is used as is; otherwise build it when Node.js is available.
if [ ! -f frontend/dist/index.html ]; then
  if command -v npm >/dev/null 2>&1; then
    say "Building the dashboard (one-time)"
    (cd frontend && { [ -d node_modules ] || npm ci --no-audit --no-fund; } && npm run build)
  else
    say "frontend/dist is missing and Node.js is not installed: starting the API only (see README)"
  fi
fi

# 4. GeoIP: optional, but say so when it is missing.
if ! ls data/geoip/*.mmdb >/dev/null 2>&1; then
  say "No GeoIP database in data/geoip/: country/ASN will come from the dataset's own columns."
  say "  Online once:  python3 scripts/download_geoip.py"
fi

# 5. Optional Neo4j.
if [ "$NEO4J" -eq 1 ]; then
  if command -v docker >/dev/null 2>&1; then
    say "Starting Neo4j (docker compose)"
    docker compose up -d neo4j
  else
    say "Docker is not installed: continuing without Neo4j (the link graph is served from the stored analysis)"
  fi
fi

# 6. Optional demo data.
if [ "$DEMO" -eq 1 ]; then
  say "Analysing the bundled synthetic capture"
  if [ "$NEO4J" -eq 1 ]; then "${PY[@]}" scripts/load_demo.py --neo4j; else "${PY[@]}" scripts/load_demo.py; fi
fi

say "Dashboard:  http://$HOST:$PORT/"
say "API docs:   http://$HOST:$PORT/docs"
exec "${PY[@]}" -m uvicorn backend.api.main:app --host "$HOST" --port "$PORT"
