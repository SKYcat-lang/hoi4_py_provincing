"""맵 폴더에서 핵심 파일들을 로드한다.

PyWebView/JS는 BMP를 직접 다룰 수 없으므로,
provinces.bmp는 NumPy 배열로 메모리에 보관하고
프론트엔드에는 PNG로 인코딩된 base64만 전달한다.
"""
from __future__ import annotations

import base64
import io
import os
import re
from typing import Optional

import numpy as np
from PIL import Image

from .definitions import (
    MapPaths,
    Province,
    StateInfo,
    StrategicRegionInfo,
    TerrainCategory,
)


def find_map_paths(map_dir: str) -> MapPaths:
    """주어진 폴더(또는 그 부모)에서 HOI4 맵 파일 세트를 찾는다."""
    map_dir = os.path.abspath(map_dir)

    # 사용자가 mod 루트를 줬을 수도, map/ 폴더를 줬을 수도 있다.
    if not os.path.isfile(os.path.join(map_dir, "definition.csv")):
        candidate = os.path.join(map_dir, "map")
        if os.path.isfile(os.path.join(candidate, "definition.csv")):
            map_dir = candidate

    if not os.path.isfile(os.path.join(map_dir, "definition.csv")):
        raise FileNotFoundError(
            f"definition.csv를 찾을 수 없습니다: {map_dir}"
        )

    mod_root = os.path.dirname(map_dir)

    return MapPaths(
        map_dir=map_dir,
        provinces_bmp=os.path.join(map_dir, "provinces.bmp"),
        definition_csv=os.path.join(map_dir, "definition.csv"),
        terrain_bmp=os.path.join(map_dir, "terrain.bmp"),
        rivers_bmp=os.path.join(map_dir, "rivers.bmp"),
        continent_txt=os.path.join(map_dir, "continent.txt"),
        default_map=os.path.join(map_dir, "default.map"),
        strategicregions_dir=os.path.join(map_dir, "strategicregions"),
        buildings_txt=os.path.join(map_dir, "buildings.txt"),
        mod_root=mod_root,
        history_states_dir=os.path.join(mod_root, "history", "states"),
        common_terrain_dir=os.path.join(mod_root, "common", "terrain"),
    )


def load_provinces_bmp(path: str) -> np.ndarray:
    """provinces.bmp를 (H, W, 3) uint8 ndarray로 로드.

    HOI4 BMP는 BGR 24bit이지만 Pillow가 RGB로 변환해주므로 그대로 사용.
    """
    img = Image.open(path)
    if img.mode != "RGB":
        img = img.convert("RGB")
    arr = np.array(img, dtype=np.uint8)
    return arr  # shape (H, W, 3)


def load_terrain_bmp(path: str) -> Optional[np.ndarray]:
    """terrain.bmp를 (H, W) uint8 또는 (H, W, 3) ndarray로 로드.

    terrain.bmp는 8bit indexed이므로 'P' 모드로 두면 인덱스 값을 직접 얻을 수 있다.
    """
    if not os.path.isfile(path):
        return None
    img = Image.open(path)
    # 8bit palette 그대로 두고 인덱스 배열만 읽는다
    if img.mode == "P":
        arr = np.array(img, dtype=np.uint8)  # (H, W)
        return arr
    # fallback: RGB로 변환
    arr = np.array(img.convert("RGB"), dtype=np.uint8)
    return arr


def encode_image_to_png_base64(arr: np.ndarray) -> str:
    """ndarray를 PNG로 인코딩해 base64 데이터 URL 문자열로 반환."""
    if arr.ndim == 2:
        img = Image.fromarray(arr, mode="L")
    elif arr.shape[2] == 3:
        img = Image.fromarray(arr, mode="RGB")
    elif arr.shape[2] == 4:
        img = Image.fromarray(arr, mode="RGBA")
    else:
        raise ValueError(f"Unsupported array shape: {arr.shape}")

    buf = io.BytesIO()
    img.save(buf, format="PNG", compress_level=1)  # 빠른 압축
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def encode_terrain_layer_png_base64(path: str) -> str | None:
    """terrain.bmp를 팔레트 적용한 RGB PNG로 인코딩.

    P-mode 그대로 PNG로 저장하면 RGB가 아니라 인덱스 그레이스케일로 보일 수 있어,
    명시적으로 RGB로 변환해 색이 정확히 살아나도록 한다.
    """
    if not os.path.isfile(path):
        return None
    try:
        img = Image.open(path)
        if img.mode != "RGB":
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="PNG", compress_level=1)
        return f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode('ascii')}"
    except Exception:
        return None


def encode_rivers_layer_png_base64(path: str) -> str | None:
    """rivers.bmp를 RGBA PNG로 인코딩하면서 흰색(땅)/회색(바다)을 완전 투명 처리.

    HOI4 rivers.bmp 팔레트:
      - (255, 255, 255) 흰색 = 땅 (강 없음)
      - (122, 122, 122) 회색 = 바다 (강 없음)
      - 그 외 = 강 (각종 파랑, 강 발원지/하구 마커 등)

    프론트엔드에서 알파 블렌딩으로 자연스럽게 강만 보이도록 한다.
    """
    if not os.path.isfile(path):
        return None
    try:
        img = Image.open(path).convert("RGB")
        rgb = np.array(img, dtype=np.uint8)
        h, w = rgb.shape[:2]

        # 알파 채널: 흰색/회색은 0(투명), 나머지는 255(불투명)
        is_land = (
            (rgb[..., 0] == 255) & (rgb[..., 1] == 255) & (rgb[..., 2] == 255)
        )
        is_sea = (
            (rgb[..., 0] == 122) & (rgb[..., 1] == 122) & (rgb[..., 2] == 122)
        )
        alpha = np.where(is_land | is_sea, 0, 255).astype(np.uint8)

        rgba = np.empty((h, w, 4), dtype=np.uint8)
        rgba[..., 0:3] = rgb
        rgba[..., 3] = alpha

        out_img = Image.fromarray(rgba, mode="RGBA")
        buf = io.BytesIO()
        out_img.save(buf, format="PNG", compress_level=1)
        return f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode('ascii')}"
    except Exception:
        return None


def load_definition_csv(path: str) -> list[Province]:
    """definition.csv를 Province 리스트로 로드."""
    provinces: list[Province] = []
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                continue
            try:
                provinces.append(Province.from_csv_row(line))
            except ValueError:
                # 헤더나 깨진 행은 건너뜀
                continue
    return provinces


def load_continent_txt(path: str) -> list[str]:
    """continent.txt에서 대륙 이름 리스트를 읽는다 (인덱스 1부터)."""
    if not os.path.isfile(path):
        return ["europe", "north_america", "south_america",
                "australia", "africa", "asia", "middle_east"]
    text = _read_text(path)
    # continents = { ... } 블록 내부의 토큰만 추출
    match = re.search(r"continents\s*=\s*\{([^}]*)\}", text)
    if not match:
        return []
    body = match.group(1)
    # 주석 제거
    body = re.sub(r"#.*", "", body)
    return [w for w in re.split(r"\s+", body.strip()) if w]


def load_terrain_categories(common_terrain_dir: str) -> list[TerrainCategory]:
    """common/terrain/*.txt에서 지형 카테고리 정의를 읽는다.

    완전한 PDX script 파서는 아니고, 우리에게 필요한 (name, color, is_water)만 뽑는다.
    """
    if not os.path.isdir(common_terrain_dir):
        return []

    categories: list[TerrainCategory] = []
    seen: set[str] = set()

    for fname in sorted(os.listdir(common_terrain_dir)):
        if not fname.endswith(".txt"):
            continue
        text = _read_text(os.path.join(common_terrain_dir, fname))
        # 주석 제거
        text = re.sub(r"#.*", "", text)
        # 'categories = { ... }' 블록 내부만 본다
        cat_match = re.search(r"categories\s*=\s*\{(.*)\}\s*$", text, re.DOTALL)
        if not cat_match:
            continue
        body = cat_match.group(1)

        # 각 카테고리 = "name = { ... }"
        # 중첩 블록을 다루려면 한 줄씩 스캔해야 한다.
        for cat_name, cat_body in _iter_top_level_blocks(body):
            if cat_name in seen:
                continue
            seen.add(cat_name)
            color = _extract_color(cat_body)
            is_water = bool(re.search(r"is_water\s*=\s*yes", cat_body))
            naval = bool(re.search(r"naval_terrain\s*=\s*yes", cat_body))
            categories.append(TerrainCategory(
                name=cat_name, color=color, is_water=is_water, naval_terrain=naval
            ))

    return categories


def load_state_files(states_dir: str) -> list[StateInfo]:
    """history/states/*.txt 파일들을 가볍게 파싱."""
    if not os.path.isdir(states_dir):
        return []

    states: list[StateInfo] = []
    for fname in sorted(os.listdir(states_dir)):
        if not fname.endswith(".txt"):
            continue
        path = os.path.join(states_dir, fname)
        text = _read_text(path)
        # 주석 제거
        clean = re.sub(r"#.*", "", text)
        id_match = re.search(r"\bid\s*=\s*(\d+)", clean)
        name_match = re.search(r'\bname\s*=\s*"([^"]+)"', clean)
        prov_match = re.search(
            r"provinces\s*=\s*\{([^}]*)\}", clean, re.DOTALL
        )
        if not id_match:
            continue
        province_ids: list[int] = []
        if prov_match:
            province_ids = [int(t) for t in re.findall(r"\d+", prov_match.group(1))]
        states.append(StateInfo(
            id=int(id_match.group(1)),
            file_path=path,
            name=name_match.group(1) if name_match else f"STATE_{id_match.group(1)}",
            province_ids=province_ids,
        ))
    return states


def load_strategic_regions(regions_dir: str) -> list[StrategicRegionInfo]:
    """strategicregions/*.txt 가벼운 파싱."""
    if not os.path.isdir(regions_dir):
        return []

    regions: list[StrategicRegionInfo] = []
    for fname in sorted(os.listdir(regions_dir)):
        if not fname.endswith(".txt"):
            continue
        path = os.path.join(regions_dir, fname)
        text = _read_text(path)
        clean = re.sub(r"#.*", "", text)
        id_match = re.search(r"\bid\s*=\s*(\d+)", clean)
        name_match = re.search(r'\bname\s*=\s*"([^"]+)"', clean)
        prov_match = re.search(r"provinces\s*=\s*\{([^}]*)\}", clean, re.DOTALL)
        if not id_match:
            continue
        province_ids: list[int] = []
        if prov_match:
            province_ids = [int(t) for t in re.findall(r"\d+", prov_match.group(1))]
        regions.append(StrategicRegionInfo(
            id=int(id_match.group(1)),
            file_path=path,
            name=name_match.group(1) if name_match else f"STRATEGICREGION_{id_match.group(1)}",
            province_ids=province_ids,
        ))
    return regions


# ---------- 내부 헬퍼 ----------


def _read_text(path: str) -> str:
    """HOI4 파일은 보통 UTF-8 또는 ANSI(cp1252). 둘 다 시도."""
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return f.read()
    except UnicodeDecodeError:
        with open(path, "r", encoding="cp1252", errors="replace") as f:
            return f.read()


def _extract_color(body: str) -> Optional[tuple[int, int, int]]:
    """'color = { 255 0 0 }' 패턴에서 RGB 튜플 추출."""
    m = re.search(r"color\s*=\s*\{\s*(\d+)\s+(\d+)\s+(\d+)", body)
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def _iter_top_level_blocks(text: str):
    """'name = { ... }' 패턴의 최상위 블록들을 (name, body) 튜플로 yield.

    중괄호 깊이를 추적해 중첩된 블록도 올바르게 파싱한다.
    """
    i = 0
    n = len(text)
    while i < n:
        # 이름 = { 패턴 찾기
        m = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\{", text[i:])
        if not m:
            return
        name = m.group(1)
        start = i + m.end()  # '{' 다음 위치
        depth = 1
        j = start
        while j < n and depth > 0:
            ch = text[j]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            j += 1
        if depth != 0:
            return  # 깨진 파일
        body = text[start:j - 1]
        yield name, body
        i = j
