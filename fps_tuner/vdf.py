"""Minimal parser for Valve's text KeyValues format (.vdf / .acf files)."""

from __future__ import annotations

import re

_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])|(//[^\n]*)')


def loads(text: str) -> dict:
    """Parse KeyValues text into nested dicts. Keys are kept as written."""
    root: dict = {}
    stack = [root]
    pending_key: str | None = None

    for match in _TOKEN.finditer(text):
        string, brace, comment = match.groups()
        if comment is not None:
            continue
        if brace == "{":
            if pending_key is None:
                raise ValueError("Unexpected '{' without a key")
            child: dict = {}
            stack[-1][pending_key] = child
            stack.append(child)
            pending_key = None
        elif brace == "}":
            if len(stack) == 1:
                raise ValueError("Unbalanced '}'")
            stack.pop()
        else:
            value = string.replace('\\\\', '\\').replace('\\"', '"')
            if pending_key is None:
                pending_key = value
            else:
                stack[-1][pending_key] = value
                pending_key = None

    if len(stack) != 1:
        raise ValueError("Unbalanced braces")
    return root


def get_ci(mapping: dict, key: str, default=None):
    """Case-insensitive dict lookup - Valve files are inconsistent about key case."""
    lowered = key.lower()
    for k, v in mapping.items():
        if k.lower() == lowered:
            return v
    return default
