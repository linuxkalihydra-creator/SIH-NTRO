#!/usr/bin/env bash
# Build a self-contained bundle for an air-gapped Linux machine.
#
# Run this ONCE on an internet-connected Linux machine with the same CPU architecture and Python
# minor version (3.12+) as the target. It collects everything the target needs:
#
#   wheelhouse/        every Python dependency as a wheel (installed later with pip --no-index)
#   frontend/dist/     the built dashboard (so the target needs no Node.js)
#   data/geoip/*.mmdb  the open DB-IP Lite country + ASN databases
#   the source tree, the synthetic demo capture and the docs
#
#   ./scripts/make_offline_bundle.sh                 -> offline-bundle/bitcoin-investigation-platform-offline.tar.gz
#   ./scripts/make_offline_bundle.sh /path/to/out
#
# On the target:  tar xzf bitcoin-investigation-platform-offline.tar.gz && cd bitcoin-investigation-platform && bash run.sh --demo
# The target needs only Python (same minor version). run.sh installs from the wheelhouse, with or without the venv module.
# Optional Neo4j for the target: `docker save neo4j:5-community | gzip > neo4j-image.tar.gz` here, `docker load` there.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
OUT="${1:-$ROOT/offline-bundle}"
NAME="bitcoin-investigation-platform"
STAGE="$OUT/$NAME"
PYTHON_BIN="${PYTHON:-python3}"

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }

"$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' \
  || { echo "Python 3.12 or newer is required to build the bundle (found $("$PYTHON_BIN" --version 2>&1))." >&2; exit 1; }

rm -rf "$STAGE"
mkdir -p "$STAGE"

say "Dashboard"
if [ ! -f frontend/dist/index.html ]; then
  command -v npm >/dev/null 2>&1 || { echo "frontend/dist is missing and npm is not installed." >&2; exit 1; }
  (cd frontend && { [ -d node_modules ] || npm ci --no-audit --no-fund; } && npm run build)
fi

say "GeoIP databases"
if ! ls data/geoip/*.mmdb >/dev/null 2>&1; then
  "$PYTHON_BIN" scripts/download_geoip.py || echo "   GeoIP download failed: the bundle will fall back to the dataset's own country/ASN columns."
fi

say "Source tree"
tar --exclude='./.git' --exclude='./.venv' --exclude='./offline-bundle' --exclude='./wheelhouse' --exclude='./frontend/node_modules' \
    --exclude='./data/raw' --exclude='./data/processed' --exclude='__pycache__' --exclude='.pytest_cache' --exclude='./.env' --exclude='./.deps' \
    -cf - . | tar -xf - -C "$STAGE"

say "Python wheels for $("$PYTHON_BIN" --version 2>&1) on $(uname -m)"
if "$PYTHON_BIN" -m pip --version >/dev/null 2>&1; then
  PIP=("$PYTHON_BIN" -m pip)
elif command -v uv >/dev/null 2>&1; then   # this Python has no pip (Debian/Kali without python3-pip): borrow one through uv
  # The full path makes uv use exactly this interpreter (by name it may prefer a Python of its own, of another version).
  uv venv --quiet --seed --python "$(command -v "$PYTHON_BIN")" "$OUT/.pip-env"
  PIP=("$OUT/.pip-env/bin/python" -m pip)
else
  echo "pip is needed to download the wheels: install python3-pip or uv." >&2; exit 1
fi
"${PIP[@]}" download --quiet --only-binary=:all: --dest "$STAGE/wheelhouse" -r requirements.txt pip
rm -rf "$OUT/.pip-env"

say "Checking that the wheels install with $("$PYTHON_BIN" --version 2>&1) and no network"
# Exactly what run.sh does on the target, as a dry run: a wheel for the wrong Python or platform fails here, not there.
"$PYTHON_BIN" "$(ls "$STAGE"/wheelhouse/pip-*.whl | head -n 1)/pip" install --quiet --dry-run --no-index --find-links "$STAGE/wheelhouse" \
    --target "$OUT/.check" -r requirements.txt \
  || { echo "The downloaded wheels do not match $("$PYTHON_BIN" --version 2>&1). Set PYTHON to the interpreter the target will use." >&2; exit 1; }
rm -rf "$OUT/.check"
( cd "$STAGE/wheelhouse" && ls | wc -l | xargs printf '   %s wheels\n' )

say "Archive"
tar -C "$OUT" -czf "$OUT/$NAME-offline.tar.gz" "$NAME"
( cd "$OUT" && sha256sum "$NAME-offline.tar.gz" > "$NAME-offline.tar.gz.sha256" )
du -h "$OUT/$NAME-offline.tar.gz" | awk '{print "   " $2 " (" $1 ")"}'
say "Done. Copy the archive (and its .sha256) to the target, extract it and run: bash run.sh --demo"
