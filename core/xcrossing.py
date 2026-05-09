"""HOI4 path-finding을 위한 X-crossing(2×2 4색) 검사.

X-crossing이란 (x, y) 좌상단으로 한 2×2 윈도우의 네 픽셀이
모두 서로 다른 색인 경우. HOI4는 유닛 이동 알고리즘에서 이 패턴을
허용하지 않으며, 발견 시 'Map invalid X crossing' 에러를 낸다.
"""
from __future__ import annotations

import numpy as np


def find_all_xcrossings(arr: np.ndarray, max_results: int = 5000) -> list[tuple[int, int]]:
    """이미지 전체를 스캔해서 X-crossing 좌표 리스트를 반환.

    좌표는 (x, y)로 2×2 윈도우의 좌상단. 즉 검사 대상 픽셀은
    (x, y), (x+1, y), (x, y+1), (x+1, y+1).

    벡터화된 비교로 5632×2048에서도 100~200ms 수준.
    너무 많이 발견되면 (모드 작업 초기엔 의미 없는 잡음일 수 있어) 상한 적용.
    """
    if arr.ndim != 3 or arr.shape[2] != 3:
        return []
    h, w = arr.shape[:2]
    if h < 2 or w < 2:
        return []

    # RGB → packed int32 (3비교 대신 1비교)
    packed = (
        arr[..., 0].astype(np.int32) << 16
        | arr[..., 1].astype(np.int32) << 8
        | arr[..., 2].astype(np.int32)
    )

    # 4개 corner 슬라이스 (모두 (h-1, w-1) 형태)
    tl = packed[:-1, :-1]   # (x, y)
    tr = packed[:-1, 1:]    # (x+1, y)
    bl = packed[1:, :-1]    # (x, y+1)
    br = packed[1:, 1:]     # (x+1, y+1)

    # 4색이 서로 다르려면 6개의 쌍이 모두 다름
    diff = (
        (tl != tr) & (tl != bl) & (tl != br)
        & (tr != bl) & (tr != br)
        & (bl != br)
    )

    ys, xs = np.where(diff)
    if len(xs) == 0:
        return []

    if len(xs) > max_results:
        xs = xs[:max_results]
        ys = ys[:max_results]

    return [(int(x), int(y)) for x, y in zip(xs, ys)]


def find_xcrossings_near(
    arr: np.ndarray,
    pixels: list[list[int]] | list[tuple[int, int]],
) -> list[tuple[int, int]]:
    """변경된 픽셀들 주변만 국소 검사.

    각 변경 픽셀 (px, py)마다 윈도우 좌상단 후보 (x, y)는
    px-1..px, py-1..py 범위 (즉 4가지). 중복 제거 후 일괄 검사.
    """
    if arr.ndim != 3 or arr.shape[2] != 3:
        return []
    h, w = arr.shape[:2]
    if h < 2 or w < 2:
        return []

    # 후보 좌상단 좌표 모집 (set으로 dedup)
    candidates: set[tuple[int, int]] = set()
    for p in pixels:
        px, py = int(p[0]), int(p[1])
        for dx in (-1, 0):
            for dy in (-1, 0):
                x = px + dx
                y = py + dy
                if 0 <= x < w - 1 and 0 <= y < h - 1:
                    candidates.add((x, y))

    if not candidates:
        return []

    # 각 후보 윈도우 검사
    results: list[tuple[int, int]] = []
    for x, y in candidates:
        c00 = arr[y, x]
        c10 = arr[y, x + 1]
        c01 = arr[y + 1, x]
        c11 = arr[y + 1, x + 1]
        # tuple 비교는 빠르지만 ndarray는 != 가 element-wise라 any/all 사용
        a = (int(c00[0]), int(c00[1]), int(c00[2]))
        b = (int(c10[0]), int(c10[1]), int(c10[2]))
        c = (int(c01[0]), int(c01[1]), int(c01[2]))
        d = (int(c11[0]), int(c11[1]), int(c11[2]))
        if a != b and a != c and a != d and b != c and b != d and c != d:
            results.append((x, y))

    # 좌표 정렬 (디버깅 가독성)
    results.sort()
    return results
