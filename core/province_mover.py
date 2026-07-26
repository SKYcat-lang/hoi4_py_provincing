"""Exact pixel translation for one or more province colours."""
from __future__ import annotations

from collections.abc import Iterable

import numpy as np


Rgb = tuple[int, int, int]


def move_province_group(
    provinces_arr: np.ndarray,
    selected_rgbs: Iterable[Rgb],
    dx: int,
    dy: int,
) -> dict:
    """Move every pixel matching ``selected_rgbs`` by an integer offset.

    Source pixels are cleared to the reserved invalid colour (0, 0, 0), then
    the original selected pixels are written at the destination.  Destination
    pixels are deliberately overwritten.  The move is rejected if any selected
    pixel would leave the bitmap, so an accidental drag cannot silently destroy
    map data.
    """
    if provinces_arr.ndim != 3 or provinces_arr.shape[2] != 3:
        raise ValueError("provinces.bmp 버퍼가 RGB 형식이 아닙니다.")

    offset_x = int(dx)
    offset_y = int(dy)
    colors = {
        (int(rgb[0]), int(rgb[1]), int(rgb[2]))
        for rgb in selected_rgbs
        if len(rgb) >= 3
    }
    colors.discard((0, 0, 0))
    if not colors:
        raise ValueError("이동할 프로빈스를 하나 이상 선택하세요.")
    if offset_x == 0 and offset_y == 0:
        return {
            "changes": [],
            "selectedPixelCount": 0,
            "bounds": None,
            "dx": 0,
            "dy": 0,
        }

    rgb = provinces_arr[..., :3]
    packed = (
        rgb[..., 0].astype(np.int32) << 16
        | rgb[..., 1].astype(np.int32) << 8
        | rgb[..., 2].astype(np.int32)
    )
    selected_packed = np.fromiter(
        ((r << 16) | (g << 8) | b for r, g, b in colors),
        dtype=np.int32,
        count=len(colors),
    )
    mask = np.isin(packed, selected_packed)
    if not mask.any():
        raise ValueError("선택한 프로빈스의 픽셀이 현재 맵에 없습니다.")

    ys, xs = np.where(mask)
    height, width = rgb.shape[:2]
    dst_xs = xs + offset_x
    dst_ys = ys + offset_y
    if (
        int(dst_xs.min()) < 0
        or int(dst_ys.min()) < 0
        or int(dst_xs.max()) >= width
        or int(dst_ys.max()) >= height
    ):
        raise ValueError("이동 결과가 provinces.bmp 바깥으로 나갑니다.")

    flat = rgb.reshape(-1, 3)
    source_indices = ys.astype(np.int64) * width + xs.astype(np.int64)
    destination_indices = dst_ys.astype(np.int64) * width + dst_xs.astype(np.int64)
    affected = np.unique(np.concatenate((source_indices, destination_indices)))
    old_values = flat[affected].copy()
    moved_values = flat[source_indices].copy()

    # Read first, clear second, paste last: this preserves overlapping moves.
    flat[source_indices] = 0
    flat[destination_indices] = moved_values

    new_values = flat[affected]
    changed_mask = np.any(old_values != new_values, axis=1)
    changed_indices = affected[changed_mask]
    old_changed = old_values[changed_mask]
    new_changed = new_values[changed_mask]

    changes: list[list[int]] = []
    for index, old, new in zip(
        changed_indices.tolist(), old_changed.tolist(), new_changed.tolist()
    ):
        y, x = divmod(int(index), width)
        changes.append([
            x, y,
            int(old[0]), int(old[1]), int(old[2]),
            int(new[0]), int(new[1]), int(new[2]),
        ])

    return {
        "changes": changes,
        "selectedPixelCount": int(source_indices.size),
        "bounds": [
            int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()),
        ],
        "dx": offset_x,
        "dy": offset_y,
    }
