"""Minimal parser for Valve's text KeyValues (.vdf / .acf) format."""

from __future__ import annotations


def _tokenize(text: str):
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
        elif c == "/" and text.startswith("//", i):
            nl = text.find("\n", i)
            i = n if nl == -1 else nl + 1
        elif c in "{}":
            yield c
            i += 1
        elif c == '"':
            i += 1
            buf = []
            while i < n and text[i] != '"':
                if text[i] == "\\" and i + 1 < n:
                    nxt = text[i + 1]
                    buf.append({"n": "\n", "t": "\t", "\\": "\\", '"': '"'}.get(nxt, nxt))
                    i += 2
                else:
                    buf.append(text[i])
                    i += 1
            i += 1
            yield ("str", "".join(buf))
        else:
            start = i
            while i < n and not text[i].isspace() and text[i] not in '{}"':
                i += 1
            yield ("str", text[start:i])


def loads(text: str) -> dict:
    """Parse KeyValues text into nested dicts. Keys keep their original case."""
    root: dict = {}
    stack = [root]
    key = None
    for tok in _tokenize(text):
        if tok == "{":
            child: dict = {}
            stack[-1][key if key is not None else ""] = child
            stack.append(child)
            key = None
        elif tok == "}":
            if len(stack) > 1:
                stack.pop()
            key = None
        else:
            value = tok[1]
            if key is None:
                key = value
            else:
                stack[-1][key] = value
                key = None
    return root


def load(path) -> dict:
    with open(path, encoding="utf-8", errors="replace") as fh:
        return loads(fh.read())


def get_ci(d: dict, key: str, default=None):
    """Case-insensitive dict lookup (Valve files are inconsistent about case)."""
    if key in d:
        return d[key]
    low = key.lower()
    for k, v in d.items():
        if k.lower() == low:
            return v
    return default
