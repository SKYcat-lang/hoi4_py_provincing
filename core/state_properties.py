"""Read and update safe top-level properties in ``history/states`` files.

The state ``history = { ... }`` block is intentionally opaque to this module.
Only direct children of ``state = { ... }`` are parsed or replaced.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import re
from typing import Any


@dataclass(frozen=True)
class StateProperties:
    manpower: int
    state_category: str
    resources: dict[str, int]
    local_supplies: float


@dataclass(frozen=True)
class _Entry:
    key: str
    entry_start: int
    line_start: int
    value_start: int
    value_end: int


def _read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8-sig") as source:
            return source.read()
    except UnicodeDecodeError:
        with open(path, "r", encoding="cp1252", errors="replace") as source:
            return source.read()


def _matching_brace(text: str, opening: int) -> int:
    depth = 0
    in_string = False
    escaped = False
    in_comment = False
    for index in range(opening, len(text)):
        char = text[index]
        if in_comment:
            if char in "\r\n":
                in_comment = False
            continue
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == "#":
            in_comment = True
        elif char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    raise ValueError("state 파일의 중괄호가 닫히지 않았습니다.")


def _state_body(text: str) -> tuple[int, int]:
    match = re.search(r"\bstate\s*=\s*\{", text)
    if not match:
        raise ValueError("state = { ... } 블록을 찾을 수 없습니다.")
    opening = text.find("{", match.start())
    return opening + 1, _matching_brace(text, opening)


def _entries(text: str) -> tuple[dict[str, _Entry], int, int]:
    body_start, body_end = _state_body(text)
    entries: dict[str, _Entry] = {}
    index = body_start
    while index < body_end:
        char = text[index]
        if char.isspace():
            index += 1
            continue
        if char == "#":
            newline = text.find("\n", index)
            index = body_end if newline < 0 else newline + 1
            continue
        key_match = re.match(r"[A-Za-z_][A-Za-z0-9_]*", text[index:])
        if not key_match:
            index += 1
            continue
        entry_start = index
        key = key_match.group(0)
        index += len(key)
        while index < body_end and text[index] in " \t":
            index += 1
        if index >= body_end or text[index] != "=":
            continue
        index += 1
        while index < body_end and text[index] in " \t":
            index += 1
        value_start = index
        if index < body_end and text[index] == "{":
            value_end = _matching_brace(text, index) + 1
        elif index < body_end and text[index] == '"':
            index += 1
            escaped = False
            while index < body_end:
                if escaped:
                    escaped = False
                elif text[index] == "\\":
                    escaped = True
                elif text[index] == '"':
                    index += 1
                    break
                index += 1
            value_end = index
        else:
            line_end = text.find("\n", index, body_end)
            if line_end < 0:
                line_end = body_end
            comment = text.find("#", index, line_end)
            value_end = comment if comment >= 0 else line_end
            while value_end > value_start and text[value_end - 1].isspace():
                value_end -= 1
        line_start = text.rfind("\n", body_start, entry_start) + 1
        entries[key] = _Entry(
            key=key,
            entry_start=entry_start,
            line_start=line_start,
            value_start=value_start,
            value_end=value_end,
        )
        index = max(index, value_end)
    return entries, body_start, body_end


def _plain_value(text: str, entry: _Entry | None, default: str) -> str:
    if entry is None:
        return default
    return text[entry.value_start:entry.value_end].strip().strip('"')


def _parse_resources(raw: str) -> dict[str, int]:
    clean = re.sub(r"#.*", "", raw)
    resources: dict[str, int] = {}
    for name, amount in re.findall(
        r"\b([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(-?\d+)", clean
    ):
        resources[name] = int(amount)
    return resources


def read_state_properties(path: str) -> StateProperties:
    text = _read_text(path)
    entries, _, _ = _entries(text)
    manpower = int(_plain_value(text, entries.get("manpower"), "0"))
    category = _plain_value(text, entries.get("state_category"), "")
    local_supplies = float(
        _plain_value(text, entries.get("local_supplies"), "0")
    )
    resource_entry = entries.get("resources")
    resources = _parse_resources(
        text[resource_entry.value_start:resource_entry.value_end]
        if resource_entry else ""
    )
    return StateProperties(
        manpower=manpower,
        state_category=category,
        resources=resources,
        local_supplies=local_supplies,
    )


def read_state_source(path: str) -> str:
    """Return the complete state source for the read-only reference viewer."""
    return _read_text(path)


def read_state_history_block(path: str) -> str:
    """Return the direct ``history = { ... }`` block without line indentation."""
    text = _read_text(path)
    entries, _, _ = _entries(text)
    entry = entries.get("history")
    if entry is None:
        return "history = {\n}"
    return text[entry.entry_start:entry.value_end]


def _validated_history_block(history_block: str) -> str:
    block = str(history_block or "").lstrip("\ufeff").strip()
    match = re.match(r"history\s*=\s*\{", block)
    if not match:
        raise ValueError("history = { ... } 블록 전체를 입력해야 합니다.")
    opening = block.find("{", match.start())
    closing = _matching_brace(block, opening)
    if block[closing + 1:].strip():
        raise ValueError("history 블록 뒤에 다른 state 코드를 넣을 수 없습니다.")
    return block[:closing + 1]


def update_state_history_block(path: str, history_block: str) -> str:
    """Replace only the direct history block and write UTF-8 without BOM."""
    replacement = _validated_history_block(history_block)
    text = _read_text(path)
    entries, _, body_end = _entries(text)
    entry = entries.get("history")
    if entry is not None:
        text = text[:entry.entry_start] + replacement + text[entry.value_end:]
    else:
        line_ending = "\r\n" if "\r\n" in text else "\n"
        anchor = entries.get("provinces")
        insert_at = anchor.line_start if anchor else body_end
        indent = "\t"
        if anchor is not None:
            candidate = text[anchor.line_start:anchor.entry_start]
            if candidate and not candidate.strip():
                indent = candidate
        indented = (line_ending + indent).join(replacement.splitlines())
        text = text[:insert_at] + indent + indented + line_ending + text[insert_at:]
    with open(path, "w", encoding="utf-8", newline="") as output:
        output.write(text)
    return replacement


def update_state_source(path: str, source: str) -> str:
    """Write user-authored state source verbatim, except for removing a BOM."""
    normalized = str(source or "").lstrip("\ufeff")
    with open(path, "w", encoding="utf-8", newline="") as output:
        output.write(normalized)
    return normalized


def _format_number(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{float(value):.3f}".rstrip("0").rstrip(".")


def update_state_properties(
    path: str,
    *,
    manpower: int,
    state_category: str,
    resources: dict[str, int],
    local_supplies: float,
) -> StateProperties:
    manpower = int(manpower)
    local_supplies = float(local_supplies)
    category = str(state_category).strip()
    if manpower < 0:
        raise ValueError("인구는 0 이상이어야 합니다.")
    if local_supplies < 0:
        raise ValueError("지역 보급은 0 이상이어야 합니다.")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", category):
        raise ValueError("올바른 state_category를 선택하세요.")

    normalized_resources: dict[str, int] = {}
    for raw_name, raw_amount in resources.items():
        name = str(raw_name).strip()
        amount = int(raw_amount)
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ValueError(f"올바르지 않은 리소스 ID: {name}")
        if amount < 0:
            raise ValueError(f"리소스 {name} 수량은 0 이상이어야 합니다.")
        if amount:
            normalized_resources[name] = amount

    text = _read_text(path)
    entries, _, body_end = _entries(text)
    line_ending = "\r\n" if "\r\n" in text else "\n"
    first_entry = next(iter(entries.values()), None)
    top_indent = "\t"
    if first_entry is not None:
        indent_text = text[first_entry.line_start:first_entry.entry_start]
        if indent_text and not indent_text.strip():
            top_indent = indent_text
    inner_indent = top_indent + ("\t" if "\t" in top_indent else "    ")

    resource_lines = ["{"]
    resource_lines.extend(
        f"{inner_indent}{name} = {amount}"
        for name, amount in sorted(normalized_resources.items())
    )
    resource_lines.append(f"{top_indent}}}")
    values = {
        "manpower": str(manpower),
        "state_category": category,
        "resources": line_ending.join(resource_lines),
        "local_supplies": _format_number(local_supplies),
    }

    replacements: list[tuple[int, int, str]] = []
    missing: list[tuple[str, str]] = []
    for key, value in values.items():
        entry = entries.get(key)
        if entry is None:
            missing.append((key, value))
        else:
            replacements.append((entry.value_start, entry.value_end, value))
    for start, end, value in sorted(replacements, reverse=True):
        text = text[:start] + value + text[end:]

    if missing:
        # Re-scan after replacements so insertion offsets remain exact.
        entries, _, body_end = _entries(text)
        anchor = entries.get("history") or entries.get("provinces")
        insert_at = anchor.line_start if anchor else body_end
        block = "".join(
            f"{top_indent}{key} = {value}{line_ending}"
            for key, value in missing
        )
        text = text[:insert_at] + block + text[insert_at:]

    # 읽을 때는 기존 BOM을 허용하지만, HOI4 state 스크립트로 다시 쓸 때는 제거한다.
    with open(path, "w", encoding="utf-8", newline="") as output:
        output.write(text)
    return StateProperties(
        manpower=manpower,
        state_category=category,
        resources=normalized_resources,
        local_supplies=local_supplies,
    )
