"""Rank useful state-file examples by boundary, connection, then proximity."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import math
from typing import Iterable, Literal

import numpy as np

from .definitions import Province


@dataclass(frozen=True)
class StateNeighbour:
    state_id: int
    relation: Literal["border", "connection", "nearby"]
    shared_edges: int = 0
    connection_count: int = 0
    distance: float | None = None


def _packed_rgb(rgb: tuple[int, int, int]) -> int:
    return (int(rgb[0]) << 16) | (int(rgb[1]) << 8) | int(rgb[2])


def rank_adjacent_states(
    provinces_arr: np.ndarray,
    provinces: list[Province],
    assignments: dict[int, int],
    target_state_id: int,
    *,
    connected_province_pairs: Iterable[tuple[int, int]] = (),
    limit: int = 3,
) -> list[StateNeighbour]:
    """Prefer pixel neighbours, then explicit connections, then nearby states."""
    if provinces_arr.ndim != 3 or provinces_arr.shape[2] < 3 or limit <= 0:
        return []
    packed = (
        provinces_arr[..., 0].astype(np.int32) << 16
        | provinces_arr[..., 1].astype(np.int32) << 8
        | provinces_arr[..., 2].astype(np.int32)
    )
    unique_colors, inverse = np.unique(packed, return_inverse=True)
    province_id_by_color = {_packed_rgb(p.rgb): p.id for p in provinces}
    state_by_unique = np.zeros(unique_colors.size, dtype=np.int32)
    for index, color in enumerate(unique_colors.tolist()):
        province_id = province_id_by_color.get(int(color))
        if province_id is not None:
            state_by_unique[index] = int(assignments.get(province_id, 0) or 0)
    pixel_states = state_by_unique[inverse].reshape(packed.shape)
    target = int(target_state_id)
    counts: Counter[int] = Counter()

    for first, second in (
        (pixel_states[:, :-1], pixel_states[:, 1:]),
        (pixel_states[:-1, :], pixel_states[1:, :]),
    ):
        mask = first != second
        if not mask.any():
            continue
        first_values = first[mask]
        second_values = second[mask]
        neighbours = np.concatenate((
            second_values[first_values == target],
            first_values[second_values == target],
        ))
        neighbours = neighbours[(neighbours > 0) & (neighbours != target)]
        if neighbours.size:
            ids, edge_counts = np.unique(neighbours, return_counts=True)
            counts.update({int(sid): int(count) for sid, count in zip(ids, edge_counts)})
    ranked: list[StateNeighbour] = [
        StateNeighbour(state_id=sid, relation="border", shared_edges=count)
        for sid, count in sorted(
            counts.items(), key=lambda item: (-item[1], item[0])
        )
    ]
    selected = {item.state_id for item in ranked}

    connection_counts: Counter[int] = Counter()
    for first_province_id, second_province_id in connected_province_pairs:
        first_state = int(assignments.get(int(first_province_id), 0) or 0)
        second_state = int(assignments.get(int(second_province_id), 0) or 0)
        if first_state == target and second_state > 0 and second_state != target:
            connection_counts[second_state] += 1
        elif second_state == target and first_state > 0 and first_state != target:
            connection_counts[first_state] += 1
    for sid, count in sorted(
        connection_counts.items(), key=lambda item: (-item[1], item[0])
    ):
        if sid in selected:
            continue
        ranked.append(StateNeighbour(
            state_id=sid,
            relation="connection",
            connection_count=count,
        ))
        selected.add(sid)

    if len(ranked) < limit:
        valid_mask = pixel_states > 0
        if valid_mask.any():
            y_coords, x_coords = np.nonzero(valid_mask)
            visible_states = pixel_states[valid_mask]
            state_ids, inverse = np.unique(visible_states, return_inverse=True)
            pixel_counts = np.bincount(inverse)
            center_x = np.bincount(inverse, weights=x_coords) / pixel_counts
            center_y = np.bincount(inverse, weights=y_coords) / pixel_counts
            target_indices = np.flatnonzero(state_ids == target)
            if target_indices.size:
                target_index = int(target_indices[0])
                target_x = float(center_x[target_index])
                target_y = float(center_y[target_index])
                width = float(pixel_states.shape[1])
                nearby: list[tuple[float, int]] = []
                for index, raw_state_id in enumerate(state_ids.tolist()):
                    sid = int(raw_state_id)
                    if sid == target or sid in selected:
                        continue
                    dx = abs(float(center_x[index]) - target_x)
                    if width > 0:
                        dx = min(dx, width - dx)
                    dy = float(center_y[index]) - target_y
                    nearby.append((math.hypot(dx, dy), sid))
                for distance, sid in sorted(nearby, key=lambda item: (item[0], item[1])):
                    ranked.append(StateNeighbour(
                        state_id=sid,
                        relation="nearby",
                        distance=distance,
                    ))
                    selected.add(sid)
                    if len(ranked) >= limit:
                        break

    return ranked[:limit]
