"""프로빈스 자동 분할 알고리즘 (4가지 성장 패턴).

각 시드는 다음 4가지 성장 패턴 중 하나를 부여받고, 그 패턴에 따라
인접 픽셀을 우선순위로 점유한다:
  - circle:  유클리드 거리(L2) 작은 픽셀부터 → 동그란 셀
  - square:  Chebyshev 거리(max(|dx|,|dy|)) 작은 픽셀부터 → 네모난 셀
  - star:    축 정렬 4방향 우선 → 별/십자형 셀
  - random:  랜덤 priority → 흐물흐물한 자연 형태

모든 시드는 글로벌 priority queue에서 동시에 경쟁 성장.
한 픽셀이 처음 점유한 시드의 라벨로 확정 → 월경지 없음(BFS 연결성),
4-라벨이 한 점에 모이는 X-crossing도 거의 없음.

분할 후, 너무 작은 조각은 공유 경계가 가장 큰 인접 조각에 병합.
"""
from __future__ import annotations

import heapq
import math
import random
from collections import deque, Counter
from typing import Optional

import numpy as np


# 성장 패턴 이름
PATTERN_CIRCLE = 0   # 원: 유클리드 거리 (등방)
PATTERN_ELLIPSE = 1  # 타원: 유클리드 + 강제 비등방
PATTERN_DIAMOND = 2  # 마름모: 맨해튼 거리
PATTERN_SQUARE = 3   # 사각형: Chebyshev 거리
PATTERN_NAMES = ("circle", "ellipse", "diamond", "square")


def _build_target_mask(arr: np.ndarray, rgb: tuple[int, int, int]) -> np.ndarray:
    return (
        (arr[..., 0] == rgb[0])
        & (arr[..., 1] == rgb[1])
        & (arr[..., 2] == rgb[2])
    )


def _lloyd_relax(
    mask: np.ndarray,
    seed_positions: list[tuple[int, int]],
    iterations: int = 4,
) -> list[tuple[int, int]]:
    """Lloyd 알고리즘으로 시드 위치를 평등화 (k-means style centroid 갱신)."""
    h, w = mask.shape
    ys_all, xs_all = np.where(mask)
    if len(seed_positions) == 0 or len(xs_all) == 0:
        return seed_positions

    seeds = [(float(x), float(y)) for x, y in seed_positions]

    for _ in range(iterations):
        sx_arr = np.array([s[0] for s in seeds], dtype=np.float32)
        sy_arr = np.array([s[1] for s in seeds], dtype=np.float32)

        chunk = 200_000
        n_total = len(xs_all)
        nearest = np.empty(n_total, dtype=np.int32)
        for start in range(0, n_total, chunk):
            end = min(n_total, start + chunk)
            xs_c = xs_all[start:end].astype(np.float32)
            ys_c = ys_all[start:end].astype(np.float32)
            dx = xs_c[:, None] - sx_arr[None, :]
            dy = ys_c[:, None] - sy_arr[None, :]
            d2 = dx * dx + dy * dy
            nearest[start:end] = np.argmin(d2, axis=1)

        new_seeds: list[tuple[float, float]] = []
        for si in range(len(seeds)):
            mask_si = nearest == si
            if not mask_si.any():
                new_seeds.append(seeds[si])
                continue
            cx = float(xs_all[mask_si].mean())
            cy = float(ys_all[mask_si].mean())
            ix, iy = int(round(cx)), int(round(cy))
            if not (0 <= ix < w and 0 <= iy < h and mask[iy, ix]):
                d = (xs_all - cx) ** 2 + (ys_all - cy) ** 2
                j = int(d.argmin())
                ix, iy = int(xs_all[j]), int(ys_all[j])
            new_seeds.append((float(ix), float(iy)))

        seeds = new_seeds

    return [(int(round(x)), int(round(y))) for x, y in seeds]


def _priority_for(
    pattern: int, sx: int, sy: int, x: int, y: int, rng: random.Random,
    cos_t: float = 1.0, sin_t: float = 0.0, axis_a: float = 1.0,
) -> float:
    """시드 (sx, sy)에서 픽셀 (x, y)에 도달할 때의 우선순위 (작을수록 먼저).

    이방성 변형: 시드의 (cos_t, sin_t, axis_a)에 따라 거리에 회전·축비가 적용된다.
    각 패턴의 base 거리 함수:
      - circle:  유클리드 √(rx²+ry²)        → 원형
      - ellipse: 유클리드(이방성이 강하게 들어옴) → 길쭉한 타원
      - diamond: 맨해튼 |rx|+|ry|             → 마름모
      - square:  Chebyshev max(|rx|,|ry|)     → 사각형
    """
    dx = x - sx
    dy = y - sy
    # 시드 회전축 좌표계로 변환
    rx = dx * cos_t + dy * sin_t
    ry = -dx * sin_t + dy * cos_t
    # 이방성 변형 (axis_a=1.0이면 등방)
    rx /= axis_a
    ry *= axis_a

    if pattern == PATTERN_CIRCLE:
        return math.hypot(rx, ry)
    if pattern == PATTERN_ELLIPSE:
        # 타원도 베이스는 유클리드. 차이는 호출자가 axis_a를 강하게 주는 데서 옴.
        return math.hypot(rx, ry)
    if pattern == PATTERN_DIAMOND:
        return float(abs(rx) + abs(ry))
    if pattern == PATTERN_SQUARE:
        return float(max(abs(rx), abs(ry)))
    return math.hypot(rx, ry)


def _grow_with_patterns(
    mask: np.ndarray,
    seeds: list[tuple],  # (x, y, pattern, cos_t, sin_t, axis_a)
    rng: random.Random,
    noise_strength: float = 0.5,  # 0~1, 성장 시 추가 점유 확률
) -> np.ndarray:
    """모든 시드를 글로벌 priority queue에서 경쟁 성장.

    한 픽셀은 처음 도달한 시드의 라벨로 확정 (BFS 연결성 보장).
    각 시드의 우선순위는 자기 패턴 함수로 결정.

    noise_strength: 0~1. 한 픽셀 점유 시 이 확률로 인접 한 픽셀을 '즉시' 추가 점유.
    추가 점유한 픽셀은 큐에 넣지 않고 바로 라벨이 부여된다.
    (heap의 우선순위가 큰 픽셀까지도 확률로 빨리 차지하게 되므로 모양이 들쭉날쭉해짐.)

    반환: (H, W) int32. -1 = 영역 외 또는 도달 못 한 픽셀.
    """
    h, w = mask.shape
    labels = np.full((h, w), -1, dtype=np.int32)
    noise_strength = max(0.0, min(1.0, float(noise_strength)))

    # heap: (priority, tiebreak, seed_idx, x, y)
    heap: list[tuple[float, float, int, int, int]] = []

    def _seed_pixel(li: int, sx: int, sy: int, pattern: int,
                    cos_t: float, sin_t: float, axis_a: float,
                    x: int, y: int) -> None:
        labels[y, x] = li
        for nx, ny in ((x-1, y), (x+1, y), (x, y-1), (x, y+1)):
            if 0 <= nx < w and 0 <= ny < h and mask[ny, nx] and labels[ny, nx] == -1:
                npri = _priority_for(pattern, sx, sy, nx, ny, rng,
                                     cos_t=cos_t, sin_t=sin_t, axis_a=axis_a)
                ntb = rng.random()
                heapq.heappush(heap, (npri, ntb, li, nx, ny))

    # 시드 초기 점유
    for li, sd in enumerate(seeds):
        sx, sy, pattern, cos_t, sin_t, axis_a = sd
        if 0 <= sx < w and 0 <= sy < h and mask[sy, sx] and labels[sy, sx] == -1:
            _seed_pixel(li, sx, sy, pattern, cos_t, sin_t, axis_a, sx, sy)

    seed_data = seeds

    while heap:
        pri, tb, li, x, y = heapq.heappop(heap)
        if labels[y, x] != -1:
            continue
        sx, sy, pattern, cos_t, sin_t, axis_a = seed_data[li]
        _seed_pixel(li, sx, sy, pattern, cos_t, sin_t, axis_a, x, y)

        if noise_strength > 0 and rng.random() < noise_strength:
            empties: list[tuple[int, int]] = []
            for nx, ny in ((x-1, y), (x+1, y), (x, y-1), (x, y+1)):
                if 0 <= nx < w and 0 <= ny < h and mask[ny, nx] and labels[ny, nx] == -1:
                    empties.append((nx, ny))
            if empties:
                ex, ey = rng.choice(empties)
                _seed_pixel(li, sx, sy, pattern, cos_t, sin_t, axis_a, ex, ey)

    return labels


def _smooth_boundaries(
    labels: np.ndarray,
    mask: np.ndarray,
    iterations: int = 3,
    rng: Optional[random.Random] = None,
) -> np.ndarray:
    """경계 픽셀의 라벨을 3×3 이웃 다수결로 갱신해 톱니를 곡선화.

    핵심: 픽셀 순서를 매번 셔플하고 변화를 즉시 반영 (Gauss-Seidel 스타일).
    이방성 majority가 곡선 경계를 만든다. iterations만큼 반복.

    경계 픽셀만 처리: 라벨이 모두 같은 픽셀은 건드리지 않아 큰 영역의 내부는 그대로.
    """
    if iterations <= 0:
        return labels
    if rng is None:
        rng = random.Random()

    h, w = labels.shape
    out = labels.copy()

    for _ in range(iterations):
        # 경계 픽셀 식별: 4-방향 이웃 중 하나라도 다른 라벨이면 경계
        # (벡터화)
        diff_l = np.zeros_like(mask, dtype=bool)
        diff_l[:, 1:] |= (out[:, 1:] != out[:, :-1]) & mask[:, 1:] & mask[:, :-1]
        diff_l[:, :-1] |= diff_l[:, 1:] | ((out[:, :-1] != out[:, 1:]) & mask[:, :-1] & mask[:, 1:])
        diff_l[1:, :] |= (out[1:, :] != out[:-1, :]) & mask[1:, :] & mask[:-1, :]
        diff_l[:-1, :] |= diff_l[1:, :] | ((out[:-1, :] != out[1:, :]) & mask[:-1, :] & mask[1:, :])

        ys_b, xs_b = np.where(diff_l & mask)
        if len(xs_b) == 0:
            break

        # 매번 셔플: 곡선화 효과
        order = list(range(len(xs_b)))
        rng.shuffle(order)

        for k in order:
            x = int(xs_b[k]); y = int(ys_b[k])
            cur = int(out[y, x])
            # 3×3 이웃 라벨 카운트 (자기 자신 제외, mask 안만)
            cnt: dict[int, int] = {}
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    nx = x + dx; ny = y + dy
                    if 0 <= nx < w and 0 <= ny < h and mask[ny, nx]:
                        L = int(out[ny, nx])
                        if L != -1:
                            cnt[L] = cnt.get(L, 0) + 1
            if not cnt:
                continue
            # 최다 라벨
            max_L = max(cnt.items(), key=lambda kv: (kv[1], -abs(kv[0] - cur)))[0]
            max_count = cnt[max_L]
            cur_count = cnt.get(cur, 0)
            # 다수가 자기와 다르면 변경. 동률은 유지(안정성).
            if max_count > cur_count and max_L != cur:
                out[y, x] = max_L

    return out


def _compact_labels(labels: np.ndarray, mask: np.ndarray) -> tuple[np.ndarray, int]:
    used = np.unique(labels[mask])
    if used.size == 0:
        return labels, 0
    label_map = {int(old): new for new, old in enumerate(used.tolist())}
    out = labels.copy()
    for old, new in label_map.items():
        if old != new:
            out[labels == old] = new
    out[~mask] = -1
    return out, int(used.size)


def _merge_small_pieces(
    labels: np.ndarray,
    mask: np.ndarray,
    K: int,
    min_pixels: int,
) -> tuple[np.ndarray, int, int]:
    """작은 조각을 공유 경계가 가장 큰 인접 조각에 병합."""
    if K <= 1:
        return labels, K, 0

    h, w = labels.shape
    areas = np.bincount(labels[mask], minlength=K).astype(np.int64)
    shared_edges: dict[int, Counter] = {}

    def add_edge(a: int, b: int) -> None:
        if a == -1 or b == -1 or a == b:
            return
        shared_edges.setdefault(a, Counter())[b] += 1
        shared_edges.setdefault(b, Counter())[a] += 1

    left = labels[:, :-1]; right = labels[:, 1:]
    diff = (left != right) & (left != -1) & (right != -1)
    if diff.any():
        ls = left[diff].tolist(); rs = right[diff].tolist()
        for a, b in zip(ls, rs):
            add_edge(int(a), int(b))

    top = labels[:-1, :]; bot = labels[1:, :]
    diff = (top != bot) & (top != -1) & (bot != -1)
    if diff.any():
        ts = top[diff].tolist(); bs = bot[diff].tolist()
        for a, b in zip(ts, bs):
            add_edge(int(a), int(b))

    parent = list(range(K))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(small: int, target: int) -> None:
        rs_ = find(small); rt_ = find(target)
        if rs_ == rt_:
            return
        parent[rs_] = rt_
        areas[rt_] += areas[rs_]; areas[rs_] = 0
        moved = shared_edges.pop(rs_, Counter())
        for nb, cnt in moved.items():
            nb_root = find(nb)
            if nb_root == rt_:
                continue
            shared_edges.setdefault(rt_, Counter())[nb_root] += cnt
            if nb_root in shared_edges:
                shared_edges[nb_root][rt_] = shared_edges[nb_root].get(rt_, 0) + cnt
                shared_edges[nb_root].pop(rs_, None)

    merged_count = 0
    order = sorted(range(K), key=lambda i: areas[i])
    for i in order:
        if find(i) != i:
            continue
        if areas[i] >= min_pixels:
            break
        cands = shared_edges.get(i, Counter())
        if not cands:
            continue
        my_root = find(i)
        best_target = None
        best_score = -1
        for nb, cnt in cands.items():
            nb_root = find(nb)
            if nb_root == my_root:
                continue
            if cnt > best_score:
                best_score = cnt
                best_target = nb_root
        if best_target is None:
            continue
        union(i, best_target)
        merged_count += 1

    final_labels = labels.copy()
    for old in range(K):
        new = find(old)
        if new != old:
            final_labels[labels == old] = new

    final_labels, final_K = _compact_labels(final_labels, mask)
    return final_labels, final_K, merged_count


def split_region(
    arr: np.ndarray,
    target_rgb: tuple[int, int, int],
    avg_pixels: int,
    *,
    min_pixels: Optional[int] = None,
    rng_seed: Optional[int] = None,
    lloyd_iters: int = 4,
    noise_strength: float = 0.5,
) -> dict:
    """대상 RGB 영역을 4가지 성장 패턴 혼합으로 분할.

    파라미터:
      avg_pixels: 새 조각의 목표 평균 픽셀 수
      min_pixels: 이보다 작으면 인접에 병합 (None이면 평균의 30%)
      lloyd_iters: 시드 평등화 반복 (3~5 권장)
      noise_strength: 0~1. 성장 노이즈 강도 (0=깔끔한 패턴, 1=흐물흐물)

    반환: { ok, labels, label_count, areas, seed_count, merged_count, min_pixels }
    """
    h, w = arr.shape[:2]
    mask = _build_target_mask(arr, target_rgb)
    area_total = int(mask.sum())
    if area_total == 0:
        return {"ok": False, "error": "대상 RGB 영역이 비어있습니다."}
    if avg_pixels <= 0:
        return {"ok": False, "error": "avg_pixels는 양수여야 합니다."}

    # rng_seed가 None이면 매 호출마다 다른 결과가 나오도록 시간 기반 난수 시드.
    if rng_seed is None:
        rng = random.Random()  # OS entropy 기반
    else:
        rng = random.Random(int(rng_seed))
    if min_pixels is None:
        min_pixels = max(1, avg_pixels * 30 // 100)

    seed_count = max(2, area_total // max(1, avg_pixels))
    seed_count = min(seed_count, 4096)

    ys_all, xs_all = np.where(mask)
    bbox_y0, bbox_y1 = int(ys_all.min()), int(ys_all.max())
    bbox_x0, bbox_x1 = int(xs_all.min()), int(xs_all.max())
    bbox_h = bbox_y1 - bbox_y0 + 1
    bbox_w = bbox_x1 - bbox_x0 + 1

    # ---- 시드 위치: jittered 격자 → Lloyd 1회 ----
    # 격자 셀 크기와 일치하는 평균 면적 보정
    cells_x = max(1, int(round(math.sqrt(seed_count * bbox_w / max(1, bbox_h)))))
    cells_y = max(1, int(math.ceil(seed_count / cells_x)))
    cell_w = bbox_w / cells_x
    cell_h = bbox_h / cells_y
    # 셀 크기의 ±35% 무작위 오프셋 → 격자 흔적 깨기
    jitter_amp_x = cell_w * 0.35
    jitter_amp_y = cell_h * 0.35

    grid_seeds: list[tuple[int, int]] = []
    for ix in range(cells_x):
        for iy in range(cells_y):
            if len(grid_seeds) >= seed_count:
                break
            cx = bbox_x0 + (ix + 0.5) * cell_w + (rng.random() * 2 - 1) * jitter_amp_x
            cy = bbox_y0 + (iy + 0.5) * cell_h + (rng.random() * 2 - 1) * jitter_amp_y
            ix2, iy2 = int(round(cx)), int(round(cy))
            if not (0 <= ix2 < w and 0 <= iy2 < h and mask[iy2, ix2]):
                d = (xs_all - cx) ** 2 + (ys_all - cy) ** 2
                j = int(d.argmin())
                ix2, iy2 = int(xs_all[j]), int(ys_all[j])
            grid_seeds.append((ix2, iy2))

    # Lloyd 1회만 (너무 많이 돌리면 다시 격자가 됨)
    if grid_seeds and lloyd_iters > 0:
        grid_seeds = _lloyd_relax(mask, grid_seeds, iterations=min(1, lloyd_iters))

    # 동일 좌표 시드 제거
    seen: set[tuple[int, int]] = set()
    pos_seeds: list[tuple[int, int]] = []
    for s in grid_seeds:
        if s not in seen:
            seen.add(s)
            pos_seeds.append(s)

    if len(pos_seeds) < 2:
        return {
            "ok": True,
            "labels": np.where(mask, 0, -1).astype(np.int32),
            "label_count": 1 if mask.any() else 0,
            "areas": [int(area_total)],
            "seed_count": len(pos_seeds),
            "merged_count": 0,
            "min_pixels": int(min_pixels),
            "message": "시드 부족으로 분할 안 됨",
        }

    # ---- 각 시드에 4가지 패턴 + 이방성 변형 무작위 부여 ----
    # circle / ellipse / diamond / square (random 제거)
    patterns = [PATTERN_CIRCLE, PATTERN_ELLIPSE, PATTERN_DIAMOND, PATTERN_SQUARE]
    seeds_with_pattern: list[tuple] = []
    for sx, sy in pos_seeds:
        pattern = rng.choice(patterns)
        # 회전각: 시드별로 매번 다름 (전 분할에서 결정성 회피용 rng 자체도 매 호출 다름)
        theta = rng.uniform(0.0, math.pi)
        cos_t = math.cos(theta)
        sin_t = math.sin(theta)

        # axis_a: 패턴마다 강도 다름.
        # - circle: 등방 강제 (axis_a = 1.0)
        # - ellipse: 강한 비등방 (1.4~2.5)
        # - diamond: 약한~중간 비등방 (0.85~1.3)
        # - square: 약한 비등방 (0.85~1.2)
        # noise_strength가 높을수록 비등방 폭이 넓어짐.
        ns_amp = max(0.0, min(1.0, float(noise_strength)))
        if pattern == PATTERN_CIRCLE:
            axis_a = 1.0
        elif pattern == PATTERN_ELLIPSE:
            # 1.4~2.5, noise 높을수록 더 길쭉
            lo = 1.3 + ns_amp * 0.2
            hi = 1.7 + ns_amp * 0.9
            axis_a = rng.uniform(lo, hi)
            # 50% 확률로 가로/세로 뒤집기 (단축이 장축이 되는 효과)
            if rng.random() < 0.5:
                axis_a = 1.0 / axis_a
        elif pattern == PATTERN_DIAMOND:
            # 0.85~1.3, 약한 비등방
            spread = 0.15 + ns_amp * 0.25
            axis_a = rng.uniform(1.0 - spread, 1.0 + spread * 1.4)
        else:  # SQUARE
            spread = 0.15 + ns_amp * 0.2
            axis_a = rng.uniform(1.0 - spread, 1.0 + spread)

        seeds_with_pattern.append((sx, sy, pattern, cos_t, sin_t, axis_a))

    # 패턴 분포 통계 (디버그용)
    pattern_counts = Counter(s[2] for s in seeds_with_pattern)

    # ---- 동시 priority 성장 ----
    labels = _grow_with_patterns(mask, seeds_with_pattern, rng,
                                 noise_strength=noise_strength)

    # 도달 못 한 픽셀 흡수 (이론상 발생 안 하지만 안전)
    unreached = mask & (labels == -1)
    if unreached.any():
        bfs2: deque[tuple[int, int]] = deque()
        ys_l, xs_l = np.where(mask & (labels >= 0))
        for k in range(len(xs_l)):
            bfs2.append((int(xs_l[k]), int(ys_l[k])))
        while bfs2:
            x, y = bfs2.popleft()
            L = int(labels[y, x])
            for nx, ny in ((x-1, y), (x+1, y), (x, y-1), (x, y+1)):
                if 0 <= nx < w and 0 <= ny < h and mask[ny, nx] and labels[ny, nx] == -1:
                    labels[ny, nx] = L
                    bfs2.append((nx, ny))

    labels, K = _compact_labels(labels, mask)

    # ---- 곡선 경계 평활화 ----
    # 톱니 격자 경계를 부드러운 곡선으로 만든다.
    # 반복 횟수는 평균 크기에 비례 (큰 조각일수록 더 다듬어도 안전)
    smooth_iters = 2 if avg_pixels < 100 else (3 if avg_pixels < 1000 else 4)
    labels = _smooth_boundaries(labels, mask, iterations=smooth_iters, rng=rng)
    labels, K = _compact_labels(labels, mask)

    # ---- 작은 조각 병합 ----
    final_labels, final_K, merged_count = _merge_small_pieces(
        labels, mask, K, int(min_pixels)
    )

    final_areas = (
        np.bincount(final_labels[mask], minlength=final_K).astype(np.int64).tolist()
        if final_K > 0 else []
    )

    return {
        "ok": True,
        "labels": final_labels,
        "label_count": final_K,
        "areas": final_areas,
        "seed_count": len(seeds_with_pattern),
        "merged_count": merged_count,
        "min_pixels": int(min_pixels),
        "pattern_counts": {PATTERN_NAMES[k]: v for k, v in pattern_counts.items()},
    }
