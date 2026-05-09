"""provinces.bmp 픽셀 분석.

저장 시점에 한 번 호출되어:
1. 이미지 전체에서 각 RGB의 픽셀 좌표를 모은다.
2. 각 RGB의 인접 RGB 집합을 계산한다 (대륙 자동 추론용).
3. 각 RGB의 terrain.bmp 픽셀 분포를 모은다 (지형 자동 추론용).
4. 새로 그려진 RGB와 사라진 RGB(있으면)를 식별한다.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Optional

import numpy as np

from .definitions import Province, TerrainCategory


def rgb_to_int(arr_or_rgb) -> np.ndarray | int:
    """RGB(N,3) ndarray 또는 (r,g,b) 튜플을 int32로 패킹."""
    if isinstance(arr_or_rgb, tuple):
        r, g, b = arr_or_rgb
        return (int(r) << 16) | (int(g) << 8) | int(b)
    arr = arr_or_rgb
    return (
        arr[..., 0].astype(np.int32) << 16
        | arr[..., 1].astype(np.int32) << 8
        | arr[..., 2].astype(np.int32)
    )


def int_to_rgb(n: int) -> tuple[int, int, int]:
    return ((n >> 16) & 0xFF, (n >> 8) & 0xFF, n & 0xFF)


def find_used_colors(provinces_arr: np.ndarray) -> set[tuple[int, int, int]]:
    """이미지에서 실제로 사용 중인 RGB 집합."""
    packed = rgb_to_int(provinces_arr)  # (H, W) int32
    unique = np.unique(packed)
    return {int_to_rgb(int(n)) for n in unique}


def find_adjacent_colors(
    provinces_arr: np.ndarray,
    target_rgbs: set[tuple[int, int, int]],
) -> dict[tuple[int, int, int], set[tuple[int, int, int]]]:
    """target_rgbs 각각에 대해 4-방향 인접한 다른 RGB들을 반환.

    벡터화된 비교로 큰 BMP에서도 빠르게 동작.
    """
    packed = rgb_to_int(provinces_arr)  # (H, W) int32
    target_packed = {(int(r) << 16) | (int(g) << 8) | int(b) for r, g, b in target_rgbs}

    adjacency: dict[int, set[int]] = defaultdict(set)

    # 좌-우 인접 비교
    left = packed[:, :-1]
    right = packed[:, 1:]
    diff_mask = left != right
    if diff_mask.any():
        a = left[diff_mask]
        b = right[diff_mask]
        for av, bv in zip(a.tolist(), b.tolist()):
            if av in target_packed:
                adjacency[av].add(bv)
            if bv in target_packed:
                adjacency[bv].add(av)

    # 상-하 인접 비교
    top = packed[:-1, :]
    bottom = packed[1:, :]
    diff_mask = top != bottom
    if diff_mask.any():
        a = top[diff_mask]
        b = bottom[diff_mask]
        for av, bv in zip(a.tolist(), b.tolist()):
            if av in target_packed:
                adjacency[av].add(bv)
            if bv in target_packed:
                adjacency[bv].add(av)

    return {
        int_to_rgb(k): {int_to_rgb(v) for v in vs}
        for k, vs in adjacency.items()
    }


def find_dominant_terrain(
    provinces_arr: np.ndarray,
    terrain_arr: Optional[np.ndarray],
    target_rgb: tuple[int, int, int],
    terrain_categories: list[TerrainCategory],
) -> str:
    """target_rgb가 차지하는 픽셀들의 terrain.bmp 최빈 지형 이름.

    terrain_arr가 8bit indexed (H,W)인 경우에만 의미 있음.
    RGB 모드인 경우 색상-카테고리 매칭을 시도한다.
    """
    if terrain_arr is None:
        return "plains"

    r, g, b = target_rgb
    mask = (
        (provinces_arr[..., 0] == r)
        & (provinces_arr[..., 1] == g)
        & (provinces_arr[..., 2] == b)
    )
    if not mask.any():
        return "plains"

    if terrain_arr.ndim == 2:
        # 8bit palette index
        values = terrain_arr[mask]
        if values.size == 0:
            return "plains"
        idx, _ = Counter(values.tolist()).most_common(1)[0]
        # palette index → 카테고리 이름 매핑은 어렵다.
        # HOI4 바닐라에서 인덱스 순서가 00_terrain.txt 카테고리 순서와 일치하는 편이라
        # 안전하게 해당 인덱스를 카테고리 이름으로 매핑한다(없으면 plains).
        if 0 <= idx < len(terrain_categories):
            cat = terrain_categories[idx]
            if not cat.is_water:
                return cat.name
        return "plains"
    else:
        # RGB. 각 픽셀 RGB를 카테고리 색상과 매칭.
        pixels = terrain_arr[mask]  # (N, 3)
        # 가장 많은 RGB 찾기
        packed = rgb_to_int(pixels)
        most_common, _ = Counter(packed.tolist()).most_common(1)[0]
        most_rgb = int_to_rgb(int(most_common))
        # 가장 가까운 카테고리 찾기
        best = None
        best_dist = 1e18
        for cat in terrain_categories:
            if cat.color is None or cat.is_water:
                continue
            cr, cg, cb = cat.color
            d = (cr - most_rgb[0]) ** 2 + (cg - most_rgb[1]) ** 2 + (cb - most_rgb[2]) ** 2
            if d < best_dist:
                best_dist = d
                best = cat
        return best.name if best else "plains"


def infer_continent_from_neighbors(
    rgb: tuple[int, int, int],
    adjacency: dict[tuple[int, int, int], set[tuple[int, int, int]]],
    province_by_rgb: dict[tuple[int, int, int], Province],
) -> int:
    """인접 프로빈스들의 대륙 중 가장 흔한 값 (0 제외).

    바다/호수만 인접한 경우 0 반환.
    """
    neighbors = adjacency.get(rgb, set())
    counts: Counter[int] = Counter()
    for nrgb in neighbors:
        prov = province_by_rgb.get(nrgb)
        if prov is None or prov.continent == 0:
            continue
        if prov.type != "land":
            continue
        counts[prov.continent] += 1
    if not counts:
        return 0
    return counts.most_common(1)[0][0]


def has_sea_neighbor(
    rgb: tuple[int, int, int],
    adjacency: dict[tuple[int, int, int], set[tuple[int, int, int]]],
    province_by_rgb: dict[tuple[int, int, int], Province],
) -> bool:
    """sea 타입 프로빈스가 인접해 있으면 True (해안선 자동 판단)."""
    for nrgb in adjacency.get(rgb, set()):
        prov = province_by_rgb.get(nrgb)
        if prov and prov.type == "sea":
            return True
    return False
