"""Generate HOI4's 24-bit world_normal.bmp from an 8-bit heightmap."""
from __future__ import annotations

import numpy as np


def generate_world_normal(heightmap: np.ndarray) -> np.ndarray:
    """Return an RGB normal map derived from HOI4 height values.

    Heightmap values map to world height by ``value / 10``. The map wraps
    east/west, while its image Y axis points south, so the encoded northward
    normal component uses the opposite image-axis convention.
    """
    if heightmap is None or getattr(heightmap, "ndim", 0) != 2:
        raise ValueError("heightmap must be a two-dimensional 8-bit image")
    if heightmap.size == 0:
        raise ValueError("heightmap must not be empty")

    heights = heightmap.astype(np.float32, copy=False) / 10.0
    height, width = heights.shape

    if width > 1:
        # HOI4 maps wrap around the east/west seam.
        slope_x = (np.roll(heights, -1, axis=1) -
                   np.roll(heights, 1, axis=1)) * 0.5
    else:
        slope_x = np.zeros_like(heights)

    slope_y = np.zeros_like(heights)
    if height > 1:
        slope_y[0] = heights[1] - heights[0]
        slope_y[-1] = heights[-1] - heights[-2]
    if height > 2:
        slope_y[1:-1] = (heights[2:] - heights[:-2]) * 0.5

    # Image X is east and image Y is south. For a surface z=h(x, north),
    # the normal is (-dh/dx, -dh/dnorth, 1), hence +slope_y below.
    normal_x = -slope_x
    normal_y = slope_y
    normal_z = np.ones_like(heights)
    length = np.sqrt(
        normal_x * normal_x + normal_y * normal_y + normal_z * normal_z
    )
    normal_x /= length
    normal_y /= length
    normal_z /= length

    result = np.empty((height, width, 3), dtype=np.uint8)
    result[..., 0] = np.rint((normal_x * 0.5 + 0.5) * 255.0).clip(0, 255)
    result[..., 1] = np.rint((normal_y * 0.5 + 0.5) * 255.0).clip(0, 255)
    result[..., 2] = np.rint((normal_z * 0.5 + 0.5) * 255.0).clip(0, 255)
    return result
