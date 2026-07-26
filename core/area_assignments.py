"""Shared assignment logic for states and strategic regions."""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import Any


def build_area_assignments(areas: Iterable[Any]) -> dict[int, int]:
    """Build ``province_id -> area_id`` from parsed state/region records."""
    assignments: dict[int, int] = {}
    for area in areas:
        for province_id in area.province_ids:
            assignments[int(province_id)] = int(area.id)
    return assignments


def update_area_assignment(
    assignments: dict[int, int],
    province_id: int,
    area_id: int | None,
    valid_area_ids: Iterable[int],
    area_label: str,
) -> int | None:
    """Assign or unassign a province and return its previous area ID."""
    if not isinstance(province_id, int):
        raise ValueError("잘못된 province_id입니다.")
    previous = assignments.get(province_id)
    if area_id is None:
        assignments.pop(province_id, None)
        return previous
    area_id = int(area_id)
    if area_id not in set(valid_area_ids):
        raise ValueError(f"존재하지 않는 {area_label} ID: {area_id}")
    assignments[province_id] = area_id
    return previous


def area_file_changes(
    area: Any,
    assignments: dict[int, int],
    removed_ids: set[int] | None = None,
) -> tuple[list[int], set[int]]:
    """Return additions/removals needed to synchronize one area file."""
    removed_ids = removed_ids or set()
    desired = {
        province_id
        for province_id, area_id in assignments.items()
        if area_id == area.id and province_id not in removed_ids
    }
    current = set(area.province_ids)
    additions = sorted(desired - current)
    removals = (current - desired) | (current & removed_ids)
    return additions, removals


def pick_neighbor_area_id(
    rgb: tuple[int, int, int],
    adjacency: dict[tuple[int, int, int], set[tuple[int, int, int]]],
    province_by_rgb: dict[tuple[int, int, int], Any],
    assignments: dict[int, int],
) -> int | None:
    """Pick the most common live area assignment among adjacent provinces."""
    counts: Counter[int] = Counter()
    for neighbor_rgb in adjacency.get(rgb, set()):
        province = province_by_rgb.get(neighbor_rgb)
        if province is None:
            continue
        area_id = assignments.get(province.id)
        if area_id is not None:
            counts[area_id] += 1
    return counts.most_common(1)[0][0] if counts else None
