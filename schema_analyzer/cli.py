from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .tool import run_tool


def _read_json(path: str | None) -> dict[str, Any]:
    if path:
        p = Path(path)
        return json.loads(p.read_text(encoding="utf-8"))
    raw = sys.stdin.read()
    if not raw.strip():
        raise SystemExit("No input provided. Pass --request <file> or pipe JSON to stdin.")
    return json.loads(raw)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="arangodb-schema-analyzer", add_help=True)
    parser.add_argument("--request", help="Path to request JSON. If omitted, read from stdin.")
    parser.add_argument("--out", help="Write response JSON to this path (default: stdout).")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output (indent=2).")
    args = parser.parse_args(argv)

    req = _read_json(args.request)
    resp = run_tool(req)
    text = json.dumps(resp, indent=2 if args.pretty else None, sort_keys=True)

    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    else:
        sys.stdout.write(text + "\n")
    return 0 if resp.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())

