"""Command-line entry point: ``bitcoin-investigation-platform <command>`` (run from the repository root).

    bitcoin-investigation-platform serve [--host 127.0.0.1] [--port 8000]
    bitcoin-investigation-platform analyze --input capture.csv [--seeds seeds.txt] [--output-dir out]
    bitcoin-investigation-platform generate [--records 10000] [--seed 42]
    bitcoin-investigation-platform evaluate
    bitcoin-investigation-platform benchmark

The application code lives in ``backend/`` and ``scripts/`` next to this package; the commands are thin
launchers so that ``uv run bitcoin-investigation-platform ...`` works without remembering script paths.
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

COMMANDS = {
    "analyze": "scripts/run_analysis.py",
    "generate": "scripts/generate_dataset.py",
    "evaluate": "scripts/evaluate_models.py",
    "benchmark": "scripts/benchmark.py",
    "load-demo": "scripts/load_demo.py",
    "download-geoip": "scripts/download_geoip.py",
}


def _root() -> Path:
    for candidate in (Path.cwd(), *Path(__file__).resolve().parents):
        if (candidate / "backend" / "api" / "main.py").is_file():
            return candidate
    raise SystemExit("Run this command from the repository root (the folder that contains backend/ and scripts/).")


def main() -> None:
    arguments = sys.argv[1:]
    if not arguments or arguments[0] in ("-h", "--help"):
        print(__doc__)
        return
    command, rest = arguments[0], arguments[1:]
    root = _root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    if command == "serve":
        import argparse

        import uvicorn

        parser = argparse.ArgumentParser(prog="bitcoin-investigation-platform serve")
        parser.add_argument("--host", default="127.0.0.1")
        parser.add_argument("--port", type=int, default=8000)
        options = parser.parse_args(rest)
        uvicorn.run("backend.api.main:app", host=options.host, port=options.port)
        return
    if command not in COMMANDS:
        raise SystemExit(f"Unknown command: {command}. Choose one of: serve, {', '.join(COMMANDS)}.")
    sys.argv = [COMMANDS[command], *rest]
    runpy.run_path(str(root / COMMANDS[command]), run_name="__main__")
