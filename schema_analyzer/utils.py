from __future__ import annotations

import hashlib
import json
import re
from typing import Any


_AQL_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def assert_aql_identifier(name: str, value: str) -> None:
    if not isinstance(value, str) or not _AQL_IDENT_RE.match(value):
        raise ValueError(f"Invalid AQL identifier for {name}")


def stable_dumps(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def extract_first_json_object(text: str) -> str:
    """
    Extract the first top-level JSON object from a string.
    Works with model outputs that include preamble/postamble.
    """
    if not isinstance(text, str):
        raise ValueError("text must be a string")

    start = text.find("{")
    if start == -1:
        raise ValueError("No JSON object start found")

    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue

        if ch == '"':
            in_str = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]

    raise ValueError("Unterminated JSON object")

