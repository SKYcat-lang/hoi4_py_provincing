"""Locate one defined province on the current editable RGB map."""
from __future__ import annotations

import re

import numpy as np

from core.definitions import Province


def locate_province(
    pixels: np.ndarray, provinces: list[Province], province_id: object,
) -> dict:
    identifier = str(province_id).strip()
    if not re.fullmatch(r"[0-9]+", identifier) or len(identifier) > 16:
        return {"ok": False, "error": "프로빈스 ID는 양의 정수로 입력해주세요."}
    identifier = int(identifier)
    if identifier <= 0:
        return {"ok": False, "error": "프로빈스 ID는 양의 정수로 입력해주세요."}
    province = next((item for item in provinces if item.id == identifier), None)
    if province is None or province.rgb == (0, 0, 0):
        return {"ok": False, "error": f"프로빈스 ID {identifier}가 없습니다. 새 프로빈스는 저장 후 ID가 부여됩니다."}

    matches = pixels[:, :, 0] == province.r
    matches &= pixels[:, :, 1] == province.g
    matches &= pixels[:, :, 2] == province.b
    rows, columns = np.nonzero(matches)
    if rows.size == 0:
        return {"ok": False, "error": f"ID {identifier}는 등록되어 있지만 현재 지도에 픽셀이 없습니다."}
    # A centroid can fall outside a concave province or between islands.
    nearest = np.argmin((columns - columns.mean()) ** 2 + (rows - rows.mean()) ** 2)
    return {
        "ok": True, "provinceId": identifier,
        "x": int(columns[nearest]), "y": int(rows[nearest]),
        "bounds": [int(columns.min()), int(rows.min()), int(columns.max()), int(rows.max())],
        "pixelCount": int(rows.size), "rgb": list(province.rgb),
    }
