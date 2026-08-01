"""Create a minimal HOI4 state file without touching localisation."""
from __future__ import annotations

from dataclasses import dataclass
import os
import re


@dataclass(frozen=True)
class CreatedState:
    state_id: int
    file_label: str
    localisation_key: str
    state_file: str


def _safe_filename(value: str) -> str:
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    safe = re.sub(r"\s+", " ", safe)
    return (safe or "New State")[:80]


def create_state(
    states_dir: str,
    state_id: int,
    file_label: str,
    *,
    state_category: str = "rural",
) -> CreatedState:
    state_id = int(state_id)
    label = str(file_label or "").strip()
    category = str(state_category or "").strip()
    if state_id <= 0:
        raise ValueError("스테이트 ID는 1 이상이어야 합니다.")
    if not label:
        raise ValueError("스테이트 파일명을 입력해주세요.")
    if len(label) > 120:
        raise ValueError("스테이트 파일명은 120자 이하여야 합니다.")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", category):
        raise ValueError("올바른 state_category가 필요합니다.")

    os.makedirs(states_dir, exist_ok=True)
    state_path = os.path.join(states_dir, f"{state_id}-{_safe_filename(label)}.txt")
    if os.path.exists(state_path):
        raise FileExistsError(f"이미 존재하는 스테이트 파일입니다: {state_path}")

    key = f"STATE_{state_id}"
    text = (
        "state={\r\n"
        f"\tid={state_id}\r\n"
        f'\tname="{key}"\r\n'
        "\tmanpower=0\r\n"
        f"\tstate_category={category}\r\n"
        "\tresources={\r\n\t}\r\n"
        "\thistory={\r\n\t}\r\n"
        "\tprovinces={\r\n\t}\r\n"
        "\tlocal_supplies=0\r\n"
        "}\r\n"
    )
    # HOI4 history/states 스크립트는 UTF-8 BOM 없이 저장한다.
    with open(state_path, "w", encoding="utf-8", newline="") as output:
        output.write(text)
    return CreatedState(
        state_id=state_id,
        file_label=label,
        localisation_key=key,
        state_file=state_path,
    )
