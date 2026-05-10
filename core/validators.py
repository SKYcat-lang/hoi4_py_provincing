"""HOI4 맵 검증 도구.

세 가지 대표 버그 검사:
  1. X-crossing: 2x2 윈도우에 4가지 다른 라벨이 만나는 점 (xcrossing.py에 별도 구현됨)
  2. One-pixel province: provinces.bmp에서 같은 RGB가 단 1픽셀만 있는 경우
  3. rivers.bmp 팔레트 검증 + 자동 교정
"""
from __future__ import annotations

import io
import os
from collections import Counter
from typing import Optional

import numpy as np
from PIL import Image


# ---------- 연결 컴포넌트 분석 (One-pixel + Exclave) ----------

def _find_connected_components(arr: np.ndarray) -> dict:
    """provinces.bmp의 모든 픽셀에 대해 (RGB, component_id, size) 정보를 계산.

    같은 RGB여도 4-방향으로 연결되지 않으면 다른 컴포넌트.
    한 번의 BFS 통과로 one-pixel 검사와 exclave 검사를 모두 처리할 수 있도록
    풍부한 정보를 반환한다.

    반환: {
      "labels": (H, W) int32 — 각 픽셀의 컴포넌트 ID (0부터)
      "components": [
          {"id": cid, "rgb": (r,g,b), "size": N, "anyXY": (x,y)},
          ...
      ]
    }
    """
    from collections import deque

    if arr.ndim != 3 or arr.shape[2] != 3:
        return {"labels": None, "components": []}

    h, w = arr.shape[:2]
    labels = np.full((h, w), -1, dtype=np.int32)
    components: list[dict] = []
    next_id = 0

    # 픽셀 단위 BFS — 큰 맵에선 비싸지만 5632×2048도 NumPy 없이 1~3초.
    # 효율 위해 RGB 비교는 packed int 기반.
    packed = (
        arr[..., 0].astype(np.int32) << 16
        | arr[..., 1].astype(np.int32) << 8
        | arr[..., 2].astype(np.int32)
    )

    for y0 in range(h):
        for x0 in range(w):
            if labels[y0, x0] != -1:
                continue
            target_packed = int(packed[y0, x0])
            cid = next_id
            next_id += 1
            q: deque[tuple[int, int]] = deque()
            q.append((x0, y0))
            labels[y0, x0] = cid
            size = 0
            sample_x, sample_y = x0, y0
            while q:
                x, y = q.popleft()
                size += 1
                for nx, ny in ((x-1, y), (x+1, y), (x, y-1), (x, y+1)):
                    if 0 <= nx < w and 0 <= ny < h:
                        if labels[ny, nx] == -1 and int(packed[ny, nx]) == target_packed:
                            labels[ny, nx] = cid
                            q.append((nx, ny))
            rgb = (
                (target_packed >> 16) & 0xFF,
                (target_packed >> 8) & 0xFF,
                target_packed & 0xFF,
            )
            components.append({
                "id": cid,
                "rgb": rgb,
                "size": size,
                "anyXY": (sample_x, sample_y),
            })

    return {"labels": labels, "components": components}


def find_one_pixel_provinces(
    arr: np.ndarray,
    max_results: int = 1000,
) -> list[tuple[int, int, tuple[int, int, int]]]:
    """크기 1짜리 connected component를 모두 찾는다.

    이전 버전은 '같은 RGB의 전체 픽셀 수가 1'만 잡았는데,
    같은 RGB에 큰 영역이 따로 있고 떨어진 외톨이 1픽셀이 있는 경우를 놓쳤다.
    개선: connected component 단위로 size==1을 검출 → 떨어진 외톨이 모두 잡힘.

    반환: [(x, y, (r, g, b)), ...]
    """
    info = _find_connected_components(arr)
    components = info["components"]
    results: list[tuple[int, int, tuple[int, int, int]]] = []
    for c in components:
        if c["size"] != 1:
            continue
        x, y = c["anyXY"]
        results.append((int(x), int(y), tuple(int(v) for v in c["rgb"])))
        if len(results) >= max_results:
            break
    return results


def find_exclaves(
    arr: np.ndarray,
    max_results: int = 2000,
) -> list[dict]:
    """월경지(exclave) 검출.

    같은 RGB가 분리된 2개 이상의 connected component로 존재하면,
    그 RGB의 가장 큰 컴포넌트만 본체로 두고 나머지를 모두 월경지로 본다.
    (size==1인 경우는 one-pixel 검사가 별도로 처리하므로 여기선 size>=2만 포함하는게
    아니라, 동일 RGB의 두 컴포넌트가 1픽셀짜리라도 함께 보고. 클라이언트가 필터링)

    반환: [
      {
        "rgb": (r,g,b),
        "size": N,
        "pixels": [[x, y], ...],   # 컴포넌트의 모든 픽셀 (max_results 까지 잘림)
      },
      ...
    ]
    """
    info = _find_connected_components(arr)
    labels: np.ndarray = info["labels"]
    components: list[dict] = info["components"]
    if labels is None:
        return []

    # RGB별로 컴포넌트 그룹핑
    by_rgb: dict[tuple[int, int, int], list[dict]] = {}
    for c in components:
        rgb = tuple(int(v) for v in c["rgb"])
        # (0,0,0)은 invalid 슬롯 → 보통 영역 외 검정. 무시
        if rgb == (0, 0, 0):
            continue
        by_rgb.setdefault(rgb, []).append(c)

    results: list[dict] = []
    total_pixels = 0
    for rgb, comps in by_rgb.items():
        if len(comps) < 2:
            continue
        # 가장 큰 컴포넌트는 본체 → 나머지를 exclave로 마킹
        comps_sorted = sorted(comps, key=lambda c: c["size"], reverse=True)
        for ex in comps_sorted[1:]:
            cid = ex["id"]
            # 이 컴포넌트의 모든 픽셀 좌표 추출
            ys_e, xs_e = np.where(labels == cid)
            pixels = []
            for k in range(len(xs_e)):
                pixels.append([int(xs_e[k]), int(ys_e[k])])
                total_pixels += 1
                if total_pixels >= max_results:
                    break
            results.append({
                "rgb": list(rgb),
                "size": int(ex["size"]),
                "pixels": pixels,
            })
            if total_pixels >= max_results:
                return results

    return results


# ---------- rivers.bmp 팔레트 검증 ----------

# HOI4 표준 rivers.bmp 팔레트 (인덱스 → RGB)
# 위키 https://hoi4.paradoxwikis.com/Map_modding 기준
RIVERS_STANDARD_PALETTE: dict[int, tuple[int, int, int]] = {
    0: (0, 255, 0),       # river source (녹색)
    1: (255, 0, 0),       # flow-in (빨강)
    2: (255, 252, 0),     # flow-out (노랑)
    3: (0, 225, 255),     # 가장 좁은 강
    4: (0, 200, 255),
    5: (0, 150, 255),
    6: (0, 100, 255),
    7: (0, 0, 255),       # 중간 강
    8: (0, 0, 225),
    9: (0, 0, 200),
    10: (0, 0, 150),
    11: (0, 0, 100),      # 가장 굵은 강
    254: (122, 122, 122), # sea (바다, 회색)
    255: (255, 255, 255), # land (땅, 흰색)
}


def validate_rivers_bmp(path: str, provinces_path: str | None = None) -> dict:
    """rivers.bmp 팔레트 + 메타데이터 종합 검증.

    HOI4가 'Palette in rivers.bmp is probably not correct' 에러를 내는 조건들을
    빠짐없이 검사한다 (위키 https://hoi4.paradoxwikis.com/Map_modding 기준):

      1. 파일 존재
      2. 8-bit indexed (P-mode)
      3. provinces.bmp와 크기 일치 (provinces_path 주어진 경우)
      4. 사용된 인덱스가 모두 표준 팔레트(0~11, 254, 255) 내에 있는지
      5. 사용된 인덱스의 RGB가 표준값과 일치하는지
      6. **표준 인덱스 14개의 RGB가 팔레트에 정확히 정의되어 있는지**
         (사용 안 했어도 팔레트 엔트리 자체가 표준이어야 함 — HOI4가 이걸 가장
          엄격히 체크해서 'palette probably not correct' 에러를 띄움)

    반환: 위 모든 검사 결과 + paletteMatches(전체 OK 여부).
    """
    if not os.path.isfile(path):
        return {"ok": False, "error": f"파일이 없습니다: {path}"}

    try:
        img = Image.open(path)
    except Exception as exc:
        return {"ok": False, "error": f"BMP 로드 실패: {exc}"}

    is_paletted = img.mode == "P"
    used_indices: list[int] = []
    palette_entries: list[dict] = []
    invalid_indices: list[int] = []
    standard_palette_check: list[dict] = []
    standard_palette_complete = True
    palette_matches = True

    # provinces.bmp와 크기 비교
    size_match = None
    provinces_size = None
    if provinces_path and os.path.isfile(provinces_path):
        try:
            with Image.open(provinces_path) as pimg:
                provinces_size = (pimg.size[0], pimg.size[1])
                size_match = (pimg.size == img.size)
                if not size_match:
                    palette_matches = False
        except Exception:
            size_match = None

    if is_paletted:
        arr = np.array(img, dtype=np.uint8)
        unique = np.unique(arr).tolist()
        used_indices = [int(i) for i in unique]
        palette = img.getpalette() or []
        palette_size_bytes = len(palette)

        # 5: 사용된 인덱스의 RGB 일치 검사
        for idx in used_indices:
            current_rgb = (
                int(palette[idx * 3]) if idx * 3 < palette_size_bytes else 0,
                int(palette[idx * 3 + 1]) if idx * 3 + 1 < palette_size_bytes else 0,
                int(palette[idx * 3 + 2]) if idx * 3 + 2 < palette_size_bytes else 0,
            )
            expected = RIVERS_STANDARD_PALETTE.get(idx)
            is_standard = expected is not None and current_rgb == expected
            if expected is None:
                invalid_indices.append(idx)
                palette_matches = False
            elif current_rgb != expected:
                palette_matches = False

            palette_entries.append({
                "index": idx,
                "currentRgb": list(current_rgb),
                "expectedRgb": list(expected) if expected else None,
                "isStandard": is_standard,
            })

        # 6: 표준 인덱스 전체(14개)의 RGB가 팔레트에 정확히 있는지 검사
        # 사용 안 한 인덱스라도 게임이 팔레트 엔트리를 검사하므로 매우 중요.
        for idx, expected in RIVERS_STANDARD_PALETTE.items():
            if idx * 3 + 2 < palette_size_bytes:
                current_rgb = (
                    int(palette[idx * 3]),
                    int(palette[idx * 3 + 1]),
                    int(palette[idx * 3 + 2]),
                )
            else:
                current_rgb = None
            ok_entry = (current_rgb == expected)
            if not ok_entry:
                standard_palette_complete = False
                palette_matches = False
            standard_palette_check.append({
                "index": idx,
                "expectedRgb": list(expected),
                "currentRgb": list(current_rgb) if current_rgb else None,
                "ok": ok_entry,
            })
    else:
        palette_matches = False

    return {
        "ok": True,
        "isPalettedBmp": is_paletted,
        "mode": img.mode,
        "size": [img.size[0], img.size[1]],
        "provincesSize": list(provinces_size) if provinces_size else None,
        "sizeMatch": size_match,
        "usedIndices": used_indices,
        "paletteEntries": palette_entries,
        "invalidIndices": invalid_indices,
        "standardPaletteCheck": standard_palette_check,
        "standardPaletteComplete": standard_palette_complete,
        "paletteMatches": palette_matches,
    }


def fix_rivers_bmp(
    path: str,
    backup: bool = True,
) -> dict:
    """rivers.bmp의 팔레트를 HOI4 표준으로 교정.

    동작:
      1. 현재 BMP를 RGB로 디코딩
      2. 각 픽셀의 RGB를 가장 가까운 표준 팔레트 인덱스로 매핑
      3. 표준 팔레트로 8-bit indexed BMP를 새로 만들어 저장
      4. backup=True면 원본을 .bak로 백업

    반환: { ok, backupPath, replacedPixels, paletteSize, error }
    """
    if not os.path.isfile(path):
        return {"ok": False, "error": f"파일이 없습니다: {path}"}

    try:
        original = Image.open(path)
        rgb = np.array(original.convert("RGB"), dtype=np.uint8)
    except Exception as exc:
        return {"ok": False, "error": f"BMP 로드 실패: {exc}"}

    h, w = rgb.shape[:2]

    # 표준 팔레트 색상 배열
    standard_indices = sorted(RIVERS_STANDARD_PALETTE.keys())
    palette_rgbs = np.array(
        [RIVERS_STANDARD_PALETTE[i] for i in standard_indices],
        dtype=np.int32,
    )

    # 픽셀별로 가장 가까운 표준 팔레트 인덱스 (L2 거리)
    # 청크 처리로 메모리 절약
    flat_rgb = rgb.reshape(-1, 3).astype(np.int32)
    n_pixels = flat_rgb.shape[0]
    out_indices = np.zeros(n_pixels, dtype=np.uint8)

    chunk = 200_000
    replaced = 0
    for start in range(0, n_pixels, chunk):
        end = min(n_pixels, start + chunk)
        block = flat_rgb[start:end]
        # 거리² (chunk × n_palette)
        dx = block[:, 0:1] - palette_rgbs[None, :, 0]
        dy = block[:, 1:2] - palette_rgbs[None, :, 1]
        dz = block[:, 2:3] - palette_rgbs[None, :, 2]
        d2 = dx * dx + dy * dy + dz * dz
        nearest_local = np.argmin(d2, axis=1)
        # local index → 실제 standard_indices 값
        out_indices[start:end] = np.array(
            [standard_indices[i] for i in nearest_local.tolist()],
            dtype=np.uint8,
        )
        # 픽셀이 표준 팔레트와 정확히 일치하지 않았는지 카운트
        chosen_rgbs = palette_rgbs[nearest_local]
        not_exact = np.any(chosen_rgbs != block, axis=1)
        replaced += int(not_exact.sum())

    out_arr = out_indices.reshape(h, w)

    # 백업
    backup_path = None
    if backup:
        backup_path = path + ".bak"
        try:
            with open(path, "rb") as src, open(backup_path, "wb") as dst:
                dst.write(src.read())
        except Exception:
            backup_path = None

    # 새 8-bit indexed BMP 작성
    # 256 엔트리 팔레트 구성 (사용 안 된 인덱스도 0으로 채움)
    flat_palette = [0] * (256 * 3)
    for idx, (r, g, b) in RIVERS_STANDARD_PALETTE.items():
        flat_palette[idx * 3] = r
        flat_palette[idx * 3 + 1] = g
        flat_palette[idx * 3 + 2] = b

    new_img = Image.fromarray(out_arr, mode="P")
    new_img.putpalette(flat_palette)
    try:
        new_img.save(path, format="BMP")
    except Exception as exc:
        return {"ok": False, "error": f"저장 실패: {exc}", "backupPath": backup_path}

    return {
        "ok": True,
        "backupPath": backup_path,
        "replacedPixels": int(replaced),
        "paletteSize": len(RIVERS_STANDARD_PALETTE),
    }
