"""Read initial state ownership without applying dated history or effects."""
from __future__ import annotations

import re


_TOKENS = re.compile(r'"(?:\\.|[^"\\])*"|#[^\r\n]*|[{}=]|[^\s{}=#"]+')


def parse_state_owner(source: str) -> str | None:
    """Return the direct state/history/owner tag; missing ownership is unassigned."""
    tokens = [match.group() for match in _TOKENS.finditer(source.lstrip("\ufeff"))
              if not match.group().startswith("#")]
    scopes: list[str] = []
    owner = None
    for index, token in enumerate(tokens):
        if token == "{":
            key = tokens[index - 2] if index >= 2 and tokens[index - 1] == "=" else ""
            scopes.append(key)
        elif token == "}":
            if scopes:
                scopes.pop()
        elif (token == "owner" and scopes == ["state", "history"]
              and tokens[index + 1:index + 2] == ["="] and index + 2 < len(tokens)):
            tag = tokens[index + 2].strip('"')
            owner = tag if re.fullmatch(r"[A-Za-z0-9_]+", tag) else None
    return owner
