"""프로빈스 삭제(인접 흡수) 로직.

설계 메모
---------
프로빈스 "삭제" = 그 프로빈스의 모든 픽셀을 인접 프로빈스의 RGB로 덮어쓰기.
BMP에서 해당 RGB가 완전히 사라지면, 기존 저장 파이프라인의
analyze_for_save_v2가 이를 자동으로 'removed'로 감지해
definition.csv / state / strategicregions의 provinces={} 블록에서 제거한다.

흡수 매핑 (absorption map)
---------------------------
저장 시점에 (disk_arr vs cur_arr)을 비교해
{사라진_옛_RGB: 흡수한_현재_RGB} 사전을 만든다.
이 사전을 ID로 환산해 {removed_id: absorber_id}로 만들면
외부 파일들(buildings/positions/unitstacks/railways/...)을
일괄적으로 흡수자 ID로 재매핑하거나 제거할 수 있다.

이 모듈은 BMP 픽셀 조작과 ID 매핑 추출만 담당하고,
실제 외부 파일 재작성은 core/external_files.py가 한다.
"""
from __future__ import annotations

from collections import Counter
from typing import Iterable, Optional

import numpy as np

from .definitions import Province


RgbTuple = tuple[int, int, int]


# ---------------------------------------------------------------------------
# 1) Absorption map 구축
# ---------------------------------------------------------------------------


def compute_absorption_map(
    disk_arr: np.ndarray,
    cur_arr: np.ndarray,
    provinces: Iterable[Province],
) -> dict[int, int]:
    """디스크 BMP vs 현재 BMP를 비교해 {removed_id: absorber_id} 사전 생성.

    동작:
      1. 디스크에는 있었지만 현재에는 0픽셀인 RGB = 사라진 RGB(흡수당함).
      2. 사라진 RGB가 차지했던 픽셀 자리에서 현재 가장 많이 차지하고 있는
         RGB = 흡수자 RGB.
      3. RGB → Province.id 사전을 통해 ID 매핑으로 변환한다.

    반환값에 흡수자 ID가 들어가려면, 흡수자 RGB가 provinces(=현재 definition)
    에 존재해야 한다. 새로 생성된 신규 RGB(아직 ID 미배정)는 흡수자가 될 수 없다.
    실패하면 그 항목은 결과에서 빠진다(=외부 파일 처리 시 단순 제거 폴백).
    """
    if disk_arr is None or cur_arr is None:
        return {}
    if disk_arr.shape != cur_arr.shape:
        return {}

    by_rgb = {p.rgb: p.id for p in provinces}

    # 1) 현재 BMP에서 살아 있는 RGB 집합
    cur_packed = (
        cur_arr[..., 0].astype(np.int32) << 16
        | cur_arr[..., 1].astype(np.int32) << 8
        | cur_arr[..., 2].astype(np.int32)
    )
    cur_alive_packed = set(int(v) for v in np.unique(cur_packed).tolist())

    # 2) 디스크 BMP의 모든 RGB 집합 (변경된 픽셀만 보면 사라진 RGB 검출 가능)
    diff_mask = (
        (disk_arr[..., 0] != cur_arr[..., 0])
        | (disk_arr[..., 1] != cur_arr[..., 1])
        | (disk_arr[..., 2] != cur_arr[..., 2])
    )
    if not diff_mask.any():
        return {}

    disk_changed = disk_arr[diff_mask]
    cur_changed = cur_arr[diff_mask]

    disk_packed = (
        disk_changed[..., 0].astype(np.int64) << 16
        | disk_changed[..., 1].astype(np.int64) << 8
        | disk_changed[..., 2].astype(np.int64)
    )
    cur_packed_changed = (
        cur_changed[..., 0].astype(np.int64) << 16
        | cur_changed[..., 1].astype(np.int64) << 8
        | cur_changed[..., 2].astype(np.int64)
    )

    # 3) (disk_packed, cur_packed) 쌍별 카운트
    combined = (disk_packed << 32) | cur_packed_changed
    unique_keys, counts = np.unique(combined, return_counts=True)

    # disk_rgb별로 "어디로 갔는가" 누적
    disk_to_cur: dict[int, Counter] = {}
    for key, cnt in zip(unique_keys.tolist(), counts.tolist()):
        disk_p = int((key >> 32) & 0xFFFFFFFF)
        cur_p = int(key & 0xFFFFFFFF)
        disk_to_cur.setdefault(disk_p, Counter())[cur_p] += int(cnt)

    result: dict[int, int] = {}

    for disk_p, cur_counts in disk_to_cur.items():
        # 사라졌는가? = 현재 BMP에 단 1픽셀도 없음
        if disk_p in cur_alive_packed:
            continue
        # 흡수자 후보 중 픽셀 수 가장 많은 것
        # invalid 슬롯(0,0,0)은 흡수자로 부적합
        ranked = sorted(
            ((cp, c) for cp, c in cur_counts.items() if cp != 0),
            key=lambda kv: kv[1], reverse=True,
        )
        if not ranked:
            continue

        disk_rgb = (
            (disk_p >> 16) & 0xFF,
            (disk_p >> 8) & 0xFF,
            disk_p & 0xFF,
        )
        removed_id = by_rgb.get(disk_rgb)
        if removed_id is None:
            continue

        for cur_p, _cnt in ranked:
            cur_rgb = (
                (cur_p >> 16) & 0xFF,
                (cur_p >> 8) & 0xFF,
                cur_p & 0xFF,
            )
            absorber_id = by_rgb.get(cur_rgb)
            if absorber_id is None:
                # 신규 RGB(아직 ID 미배정) — 흡수자 후보 아님, 다음 순위로
                continue
            result[removed_id] = absorber_id
            break

    return result


# ---------------------------------------------------------------------------
# 2) 단일 프로빈스 인접 흡수 (BMP 픽셀 조작)
# ---------------------------------------------------------------------------


def find_best_absorber_rgb(
    arr: np.ndarray,
    target_rgb: RgbTuple,
    protected_rgbs: set[RgbTuple],
) -> Optional[RgbTuple]:
    """target_rgb 영역의 4방향 인접 RGB 중 공유 경계가 가장 긴 비-보호 RGB.

    호출자가 호수/바다 등 흡수자로 삼고 싶지 않은 RGB를 protected_rgbs로 넘긴다.
    target_rgb 자신은 결과에서 자동 제외된다.
    """
    if arr.ndim != 3 or arr.shape[2] != 3:
        return None
    h, w = arr.shape[:2]
    tr, tg, tb = target_rgb
    target_mask = (
        (arr[..., 0] == tr) & (arr[..., 1] == tg) & (arr[..., 2] == tb)
    )
    if not target_mask.any():
        return None

    packed = (
        arr[..., 0].astype(np.int32) << 16
        | arr[..., 1].astype(np.int32) << 8
        | arr[..., 2].astype(np.int32)
    )
    target_packed = (tr << 16) | (tg << 8) | tb

    # 4방향으로 target과 다른 RGB가 맞닿은 픽셀 → 그 RGB를 카운트
    counts: Counter[int] = Counter()

    # 좌측 이웃
    left = packed[:, :-1]
    right = packed[:, 1:]
    mask_lr = (left == target_packed) & (right != target_packed)
    if mask_lr.any():
        for v in right[mask_lr].tolist():
            counts[int(v)] += 1
    mask_rl = (right == target_packed) & (left != target_packed)
    if mask_rl.any():
        for v in left[mask_rl].tolist():
            counts[int(v)] += 1

    # 상하 이웃
    top = packed[:-1, :]
    bot = packed[1:, :]
    mask_tb = (top == target_packed) & (bot != target_packed)
    if mask_tb.any():
        for v in bot[mask_tb].tolist():
            counts[int(v)] += 1
    mask_bt = (bot == target_packed) & (top != target_packed)
    if mask_bt.any():
        for v in top[mask_bt].tolist():
            counts[int(v)] += 1

    if not counts:
        return None

    protected_packed = {
        (int(r) << 16) | (int(g) << 8) | int(b)
        for r, g, b in protected_rgbs
    }
    # invalid slot (0,0,0) 자동 보호
    protected_packed.add(0)

    ranked = sorted(
        ((p, c) for p, c in counts.items() if p not in protected_packed),
        key=lambda kv: kv[1], reverse=True,
    )
    if not ranked:
        return None

    best = ranked[0][0]
    return ((best >> 16) & 0xFF, (best >> 8) & 0xFF, best & 0xFF)


def absorb_province(
    arr: np.ndarray,
    target_rgb: RgbTuple,
    absorber_rgb: RgbTuple,
) -> list[list[int]]:
    """target_rgb 픽셀을 모두 absorber_rgb로 덮어쓴다.

    반환: changed_pixels = [[x, y, oR, oG, oB, nR, nG, nB], ...]
    프론트엔드 Undo/캔버스 갱신에 사용.
    """
    tr, tg, tb = target_rgb
    ar, ag, ab = absorber_rgb
    mask = (
        (arr[..., 0] == tr) & (arr[..., 1] == tg) & (arr[..., 2] == tb)
    )
    if not mask.any():
        return []

    ys, xs = np.where(mask)
    # 픽셀 갱신
    arr[ys, xs, 0] = ar
    arr[ys, xs, 1] = ag
    arr[ys, xs, 2] = ab

    xs_list = xs.tolist()
    ys_list = ys.tolist()
    n = len(xs_list)
    out: list[list[int]] = []
    for i in range(n):
        out.append([int(xs_list[i]), int(ys_list[i]),
                    int(tr), int(tg), int(tb),
                    int(ar), int(ag), int(ab)])
    return out
