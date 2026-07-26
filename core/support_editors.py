"""Domain logic for the optional height, river, and supply map editors."""
from __future__ import annotations

from collections import deque
from typing import Iterable

import numpy as np

from .definitions import Province
from .province_analyzer import find_adjacent_colors, find_used_colors


def apply_terrain_stroke(
    terrain: np.ndarray, pixels: Iterable[Iterable[int]], terrain_index: int
) -> int:
    height, width = terrain.shape
    index = int(terrain_index)
    if not 0 <= index <= 255:
        raise ValueError("지형 팔레트 인덱스가 범위를 벗어났습니다.")
    applied = 0
    for pixel in pixels:
        values = list(pixel)
        if len(values) < 2:
            continue
        x, y = int(values[0]), int(values[1])
        if not (0 <= x < width and 0 <= y < height):
            continue
        if int(terrain[y, x]) != index:
            terrain[y, x] = index
            applied += 1
    return applied


def apply_terrain_changes(
    terrain: np.ndarray, changes: Iterable[Iterable[int]]
) -> int:
    height, width = terrain.shape
    applied = 0
    for change in changes:
        values = list(change)
        if len(values) < 3:
            continue
        x, y, index = int(values[0]), int(values[1]), int(values[2])
        if not (0 <= x < width and 0 <= y < height and 0 <= index <= 255):
            continue
        if int(terrain[y, x]) != index:
            terrain[y, x] = index
            applied += 1
    return applied


def province_color_at(provinces: np.ndarray, x: int, y: int) -> tuple[int, int, int]:
    """Return the live province colour at a map coordinate."""
    height, width = provinces.shape[:2]
    x, y = int(x), int(y)
    if not (0 <= x < width and 0 <= y < height):
        raise ValueError("범위를 벗어난 좌표입니다.")
    return tuple(int(channel) for channel in provinces[y, x, :3])


def province_mask_at(provinces: np.ndarray, x: int, y: int) -> np.ndarray:
    """Return the provinces.bmp colour mask selected by ``x``/``y``."""
    colour = province_color_at(provinces, x, y)
    return np.all(provinces[..., :3] == colour, axis=2)


def _connected_coordinates(
    eligible: np.ndarray, x: int, y: int
) -> list[tuple[int, int]]:
    """Return the 4-way connected component containing ``x``/``y``."""
    height, width = eligible.shape
    x, y = int(x), int(y)
    if not (0 <= x < width and 0 <= y < height) or not eligible[y, x]:
        return []
    visited = np.zeros_like(eligible, dtype=bool)
    visited[y, x] = True
    queue = deque([(x, y)])
    coordinates: list[tuple[int, int]] = []
    while queue:
        current_x, current_y = queue.popleft()
        coordinates.append((current_x, current_y))
        for next_x, next_y in (
            (current_x - 1, current_y),
            (current_x + 1, current_y),
            (current_x, current_y - 1),
            (current_x, current_y + 1),
        ):
            if (
                0 <= next_x < width
                and 0 <= next_y < height
                and eligible[next_y, next_x]
                and not visited[next_y, next_x]
            ):
                visited[next_y, next_x] = True
                queue.append((next_x, next_y))
    return coordinates


def protected_province_colors(
    province_definitions: Iterable[Province],
    respect_lakes: bool = False,
    respect_sea: bool = False,
) -> set[tuple[int, int, int]]:
    """Build the protection colour set shared by province and height editing."""
    protected_types: set[str] = set()
    if respect_lakes:
        protected_types.add("lake")
    if respect_sea:
        protected_types.add("sea")
    return {
        province.rgb
        for province in province_definitions
        if province.type in protected_types
    }


def fill_scalar_layer_in_province(
    provinces: np.ndarray,
    layer: np.ndarray,
    x: int,
    y: int,
    new_value: int,
    *,
    allowed_values: set[int] | None = None,
) -> list[list[int]]:
    """Flood-fill equal scalar values, clipped by the clicked province.

    Returned triples are ``[x, y, old_value]`` for exact undo/redo.
    """
    if layer.ndim != 2 or layer.shape != provinces.shape[:2]:
        raise ValueError("편집 레이어와 provinces.bmp의 크기가 일치하지 않습니다.")
    value = int(new_value)
    if allowed_values is not None and value not in allowed_values:
        raise ValueError("허용되지 않은 팔레트 인덱스입니다.")
    if not 0 <= value <= 255:
        raise ValueError("값은 0~255 범위여야 합니다.")

    province_mask = province_mask_at(provinces, x, y)
    target = int(layer[int(y), int(x)])
    if target == value:
        return []
    eligible = province_mask & (layer == target)
    coordinates = _connected_coordinates(eligible, x, y)
    for pixel_x, pixel_y in coordinates:
        layer[pixel_y, pixel_x] = value
    return [[pixel_x, pixel_y, target] for pixel_x, pixel_y in coordinates]


def flood_fill_rgb_connected(
    provinces: np.ndarray, x: int, y: int, new_color: Iterable[int]
) -> tuple[tuple[int, int, int], list[list[int]]]:
    """Flood-fill only the clicked 4-way connected RGB component."""
    target = province_color_at(provinces, x, y)
    values = tuple(int(channel) for channel in new_color)
    if len(values) != 3 or any(not 0 <= channel <= 255 for channel in values):
        raise ValueError("RGB 색상은 0~255 범위의 세 값이어야 합니다.")
    if target == values:
        return target, []

    eligible = province_mask_at(provinces, x, y)
    coordinates = _connected_coordinates(eligible, x, y)
    changed: list[list[int]] = []
    for pixel_x, pixel_y in coordinates:
        provinces[pixel_y, pixel_x, :3] = values
        changed.append([
            pixel_x, pixel_y, target[0], target[1], target[2]
        ])
    return target, changed


def flood_fill_terrain(
    provinces: np.ndarray,
    terrain: np.ndarray,
    x: int,
    y: int,
    terrain_index: int,
) -> list[list[int]]:
    """Fill terrain.bmp up to the clicked provinces.bmp boundary."""
    return fill_scalar_layer_in_province(
        provinces, terrain, x, y, terrain_index
    )


def apply_heightmap_changes(
    heightmap: np.ndarray,
    changes: Iterable[Iterable[int]],
    provinces: np.ndarray | None = None,
    protected_colors: set[tuple[int, int, int]] | None = None,
) -> int:
    """Apply exact height values and optionally protect water provinces."""
    height, width = heightmap.shape
    use_protection = bool(protected_colors) and provinces is not None
    if use_protection and provinces.shape[:2] != heightmap.shape:
        raise ValueError("heightmap.bmp와 provinces.bmp의 크기가 일치하지 않습니다.")
    applied = 0
    for change in changes:
        values = list(change)
        if len(values) < 3:
            continue
        x, y, value = int(values[0]), int(values[1]), int(values[2])
        if not (0 <= x < width and 0 <= y < height):
            continue
        if use_protection:
            colour = tuple(int(channel) for channel in provinces[y, x, :3])
            if colour in protected_colors:
                continue
        value = max(0, min(255, value))
        if int(heightmap[y, x]) == value:
            continue
        heightmap[y, x] = value
        applied += 1
    return applied


def smooth_heightmap_coast(
    provinces: np.ndarray,
    heightmap: np.ndarray,
    province_definitions: Iterable[Province],
    x: int,
    y: int,
    width: int = 12,
    strength: float = 1.0,
) -> dict:
    """Build a soft land-side height ramp along one sea province.

    Only land provinces that touch the clicked sea province orthogonally are
    eligible.  Sea/lake pixels and land provinces behind the first row of
    coastal provinces are therefore never modified.  ``changedPixels`` uses
    ``[x, y, old_value, new_value]`` records so the caller can render and
    register one exact undo step.
    """
    if heightmap.ndim != 2 or heightmap.shape != provinces.shape[:2]:
        raise ValueError("heightmap.bmp와 provinces.bmp의 크기가 일치하지 않습니다.")

    height, map_width = heightmap.shape
    x, y = int(x), int(y)
    if not (0 <= x < map_width and 0 <= y < height):
        raise ValueError("범위를 벗어난 좌표입니다.")
    ramp_width = max(1, min(128, int(width)))
    ramp_strength = max(0.01, min(1.0, float(strength)))

    province_definitions = tuple(province_definitions)
    definitions_by_rgb = {
        province.rgb: province for province in province_definitions
    }
    sea_rgb = province_color_at(provinces, x, y)
    sea_province = definitions_by_rgb.get(sea_rgb)
    if sea_province is None:
        raise ValueError("definition.csv에 등록되지 않은 프로빈스입니다.")
    if sea_province.type != "sea":
        raise ValueError("해안 다듬기 도구로 바다 프로빈스를 클릭하세요.")

    rgb = provinces[..., :3]
    sea_mask = np.all(rgb == sea_rgb, axis=2)

    # Pixels on the land side of the selected sea/land boundary.
    touches_sea = np.zeros((height, map_width), dtype=bool)
    touches_sea[1:, :] |= sea_mask[:-1, :]
    touches_sea[:-1, :] |= sea_mask[1:, :]
    touches_sea[:, 1:] |= sea_mask[:, :-1]
    touches_sea[:, :-1] |= sea_mask[:, 1:]

    packed = (
        (rgb[..., 0].astype(np.uint32) << 16)
        | (rgb[..., 1].astype(np.uint32) << 8)
        | rgb[..., 2].astype(np.uint32)
    )
    land_keys = np.fromiter(
        (
            (province.r << 16) | (province.g << 8) | province.b
            for province in province_definitions
            if province.type == "land"
        ),
        dtype=np.uint32,
    )
    if not land_keys.size:
        return {
            "changedPixels": [],
            "seaProvinceId": sea_province.id,
            "adjacentProvinceIds": [],
            "seaLevel": int(np.median(heightmap[sea_mask])),
            "width": ramp_width,
        }

    is_land = np.isin(packed, land_keys)
    boundary = touches_sea & is_land
    adjacent_keys = np.unique(packed[boundary])
    if not adjacent_keys.size:
        return {
            "changedPixels": [],
            "seaProvinceId": sea_province.id,
            "adjacentProvinceIds": [],
            "seaLevel": int(np.median(heightmap[sea_mask])),
            "width": ramp_width,
        }

    allowed = np.isin(packed, adjacent_keys)
    adjacent_key_set = {int(value) for value in adjacent_keys}
    adjacent_ids = sorted(
        province.id
        for province in province_definitions
        if province.type == "land"
        and ((province.r << 16) | (province.g << 8) | province.b)
        in adjacent_key_set
    )

    # Use the local coastal water height where possible.  It follows custom
    # maps whose sea level differs from vanilla's usual value of 95.
    touches_land = np.zeros((height, map_width), dtype=bool)
    touches_land[1:, :] |= is_land[:-1, :]
    touches_land[:-1, :] |= is_land[1:, :]
    touches_land[:, 1:] |= is_land[:, :-1]
    touches_land[:, :-1] |= is_land[:, 1:]
    sea_edge = sea_mask & touches_land
    sea_samples = heightmap[sea_edge] if np.any(sea_edge) else heightmap[sea_mask]
    sea_level = int(np.median(sea_samples))
    shore_height = min(255, sea_level + 1)

    distance = np.full((height, map_width), -1, dtype=np.int16)
    frontier = boundary.copy()
    distance[frontier] = 0
    for step in range(1, ramp_width):
        expanded = np.zeros((height, map_width), dtype=bool)
        expanded[1:, :] |= frontier[:-1, :]
        expanded[:-1, :] |= frontier[1:, :]
        expanded[:, 1:] |= frontier[:, :-1]
        expanded[:, :-1] |= frontier[:, 1:]
        frontier = expanded & allowed & (distance < 0)
        if not np.any(frontier):
            break
        distance[frontier] = step

    band_y, band_x = np.where(distance >= 0)
    changed: list[list[int]] = []
    for pixel_y, pixel_x in zip(band_y.tolist(), band_x.tolist()):
        step = int(distance[pixel_y, pixel_x])
        # Smoothstep falloff: strongest at the shore and approaches zero at
        # the outer edge, so the generated ramp joins existing relief softly.
        progress = step / ramp_width
        influence = (1.0 - progress * progress * (3.0 - 2.0 * progress))
        influence *= ramp_strength
        old_value = int(heightmap[pixel_y, pixel_x])
        new_value = int(round(old_value + (shore_height - old_value) * influence))
        new_value = max(0, min(255, new_value))
        if new_value == old_value:
            continue
        heightmap[pixel_y, pixel_x] = new_value
        changed.append([pixel_x, pixel_y, old_value, new_value])

    return {
        "changedPixels": changed,
        "seaProvinceId": sea_province.id,
        "adjacentProvinceIds": adjacent_ids,
        "seaLevel": sea_level,
        "width": ramp_width,
    }


def apply_river_changes(
    rivers: np.ndarray, changes: Iterable[Iterable[int]]
) -> int:
    """Apply editable river indices while retaining comment/background pixels."""
    height, width = rivers.shape
    allowed = set(range(12)) | {254, 255}
    applied = 0
    for change in changes:
        values = list(change)
        if len(values) < 3:
            continue
        x, y, index = int(values[0]), int(values[1]), int(values[2])
        if not (0 <= x < width and 0 <= y < height and index in allowed):
            continue
        if int(rivers[y, x]) == index:
            continue
        rivers[y, x] = index
        applied += 1
    return applied


def validate_river_topology(rivers: np.ndarray, max_issues: int = 200) -> dict:
    """Validate one-pixel, orthogonal river components and source markers."""
    height, width = rivers.shape
    issues: list[dict] = []
    river = rivers <= 11
    visited = np.zeros_like(river, dtype=bool)
    component_count = 0
    source_count = 0

    for start_y, start_x in zip(*np.where(river & ~visited)):
        if visited[start_y, start_x]:
            continue
        component_count += 1
        queue = deque([(int(start_x), int(start_y))])
        visited[start_y, start_x] = True
        component: list[tuple[int, int]] = []
        sources: list[tuple[int, int]] = []
        edge_count = 0
        while queue:
            x, y = queue.popleft()
            component.append((x, y))
            if int(rivers[y, x]) == 0:
                sources.append((x, y))
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if 0 <= nx < width and 0 <= ny < height and river[ny, nx]:
                    edge_count += 1
                    if not visited[ny, nx]:
                        visited[ny, nx] = True
                        queue.append((nx, ny))
        edge_count //= 2
        source_count += len(sources)
        if len(sources) != 1 and len(issues) < max_issues:
            x, y = component[0]
            issues.append({
                "kind": "source_count", "x": x, "y": y,
                "count": len(sources), "pixels": len(component),
            })
        if edge_count >= len(component) and len(issues) < max_issues:
            x, y = component[0]
            issues.append({"kind": "cycle", "x": x, "y": y})

    if height > 1 and width > 1:
        blocks = (
            river[:-1, :-1] & river[:-1, 1:] &
            river[1:, :-1] & river[1:, 1:]
        )
        thick_y, thick_x = np.where(blocks)
        for x, y in zip(thick_x, thick_y):
            if len(issues) >= max_issues:
                break
            issues.append({"kind": "thick_2x2", "x": int(x), "y": int(y)})

    return {
        "ok": True,
        "valid": not issues,
        "issues": issues,
        "truncated": len(issues) >= max_issues,
        "componentCount": component_count,
        "sourceCount": source_count,
    }


def normalize_supply_network(
    nodes: list[dict], railways: list[dict]
) -> tuple[list[dict], list[dict]]:
    normalized_nodes = [
        {"level": int(node["level"]), "province": int(node["province"])}
        for node in nodes
    ]
    normalized_railways = [
        {
            "level": int(railway["level"]),
            "provinces": [int(value) for value in railway["provinces"]],
        }
        for railway in railways
    ]
    return normalized_nodes, normalized_railways


def delete_supply_railway(railways: list[dict], index: int) -> dict:
    """Delete exactly one railway without validating unrelated records."""
    index = int(index)
    if not 0 <= index < len(railways):
        raise ValueError("삭제할 철도 인덱스가 범위를 벗어났습니다.")
    return railways.pop(index)


def insert_supply_railway(
    railways: list[dict], index: int, railway: dict
) -> dict:
    """Restore one exact railway at its original list position."""
    index = int(index)
    if not 0 <= index <= len(railways):
        raise ValueError("복원할 철도 인덱스가 범위를 벗어났습니다.")
    try:
        restored = {
            "level": int(railway["level"]),
            "provinces": [int(value) for value in railway["provinces"]],
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"복원할 철도 데이터가 잘못되었습니다: {exc}") from exc
    railways.insert(index, restored)
    return restored


def validate_supply_network(
    provinces_arr: np.ndarray,
    provinces: list[Province],
    assignments: dict[int, int],
    nodes: list[dict],
    railways: list[dict],
    explicit_adjacency_pairs: set[frozenset[int]] | None = None,
) -> dict:
    """Validate stateful land nodes and contiguous railway province paths."""
    by_id = {province.id: province for province in provinces}
    live_colors = find_used_colors(provinces_arr)
    issues: list[dict] = []
    warnings: list[dict] = []
    explicit_adjacency_pairs = explicit_adjacency_pairs or set()
    seen_nodes: set[int] = set()

    for index, node in enumerate(nodes):
        try:
            level = int(node["level"])
            province_id = int(node["province"])
        except (KeyError, TypeError, ValueError):
            issues.append({"kind": "invalid_node", "index": index})
            continue
        province = by_id.get(province_id)
        if level != 1:
            issues.append({"kind": "node_level", "index": index, "level": level})
        if province is None or province.type != "land" or province.rgb not in live_colors:
            issues.append({
                "kind": "invalid_node_province", "index": index,
                "province": province_id,
            })
        elif province_id not in assignments:
            issues.append({
                "kind": "stateless_node", "index": index,
                "province": province_id,
            })
        if province_id in seen_nodes:
            issues.append({
                "kind": "duplicate_node", "index": index,
                "province": province_id,
            })
        seen_nodes.add(province_id)

    rail_ids = {
        int(province_id)
        for railway in railways
        for province_id in railway.get("provinces", [])
        if str(province_id).lstrip("-").isdigit()
    }
    target_rgbs = {by_id[pid].rgb for pid in rail_ids if pid in by_id}
    color_adjacency = find_adjacent_colors(provinces_arr, target_rgbs)
    id_by_rgb = {province.rgb: province.id for province in provinces}
    id_adjacency = {
        id_by_rgb[rgb]: {id_by_rgb[n] for n in neighbours if n in id_by_rgb}
        for rgb, neighbours in color_adjacency.items() if rgb in id_by_rgb
    }

    for index, railway in enumerate(railways):
        try:
            level = int(railway["level"])
            province_ids = [int(value) for value in railway["provinces"]]
        except (KeyError, TypeError, ValueError):
            issues.append({"kind": "invalid_railway", "index": index})
            continue
        if not 1 <= level <= 5:
            issues.append({"kind": "railway_level", "index": index, "level": level})
        if len(province_ids) < 2:
            issues.append({"kind": "railway_too_short", "index": index})
        for province_id in province_ids:
            province = by_id.get(province_id)
            if province is None or province.type != "land" or province.rgb not in live_colors:
                issues.append({
                    "kind": "invalid_railway_province", "index": index,
                    "province": province_id,
                })
            elif province_id not in assignments:
                issues.append({
                    "kind": "stateless_railway", "index": index,
                    "province": province_id,
                })
        for start, end in zip(province_ids, province_ids[1:]):
            connected_by_pixels = end in id_adjacency.get(start, set())
            connected_explicitly = frozenset((start, end)) in explicit_adjacency_pairs
            if not connected_by_pixels and not connected_explicitly:
                # Vanilla contains a handful of railway records that bridge
                # tiny raster gaps, so lack of a literal 4-pixel border is a
                # useful visual warning but not a reason to reject the file.
                warnings.append({
                    "kind": "disjointed_railway", "index": index,
                    "from": start, "to": end,
                })
    return {
        "ok": True,
        "valid": not issues,
        "issues": issues,
        "warnings": warnings,
    }
