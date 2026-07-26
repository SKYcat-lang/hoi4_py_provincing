"""map_saver: provinces.bmp/definition.csv/state/region 일괄 저장."""
from __future__ import annotations

import re
from collections import Counter

from PIL import Image

from .definitions import Province
from .province_analyzer import (
    find_adjacent_colors,
    find_used_colors,
    find_dominant_terrain,
    has_sea_neighbor,
    infer_continent_from_neighbors,
)


def analyze_for_save_v2(provinces_arr, terrain_arr, existing_provinces, terrain_categories):
    used = find_used_colors(provinces_arr)
    by_rgb = {p.rgb: p for p in existing_provinces}
    new_set = used - set(by_rgb.keys())
    new_set.discard((0, 0, 0))
    new_rgbs = sorted(new_set)
    removed = []
    for rgb, p in by_rgb.items():
        if rgb in used:
            continue
        if p.id == 0 or rgb == (0, 0, 0):
            continue
        removed.append(p)
    return new_rgbs, removed


analyze_for_save = analyze_for_save_v2


def build_new_provinces(provinces_arr, terrain_arr, new_rgbs, existing_provinces,
                       terrain_categories, province_type_overrides=None,
                       parent_rgb_resolver=None):
    """새 RGB들에 대한 Province 객체 리스트 생성.

    parent_rgb_resolver: 선택. callable(new_rgb_tuple) -> Province | None
      주어지면 land 프로빈스의 'continent'를 부모 → 인접 → europe(1) 순으로 결정.
      None이면 기존 인접 기반 동작(하위 호환).
    """
    overrides = province_type_overrides or {}
    by_rgb = {p.rgb: p for p in existing_provinces}
    adj = find_adjacent_colors(provinces_arr, set(new_rgbs))
    next_id = max((p.id for p in existing_provinces), default=0) + 1
    out = []
    for rgb in new_rgbs:
        ptype = overrides.get(rgb, "land")
        if ptype == "sea":
            terrain = "ocean"
        elif ptype == "lake":
            terrain = "lakes"
        else:
            terrain = find_dominant_terrain(provinces_arr, terrain_arr, rgb, terrain_categories)
        if ptype == "land":
            cont = 0
            # 1) 부모 상속 우선
            if parent_rgb_resolver is not None:
                parent = parent_rgb_resolver(rgb)
                if parent is not None and parent.continent:
                    cont = parent.continent
            # 2) 인접 기반 폴백
            if not cont:
                cont = infer_continent_from_neighbors(rgb, adj, by_rgb)
            # 3) 최종 폴백: europe (1)
            if not cont:
                cont = 1
            coastal = has_sea_neighbor(rgb, adj, by_rgb)
        else:
            cont = 0
            coastal = False
        out.append(Province(
            id=next_id, r=rgb[0], g=rgb[1], b=rgb[2],
            type=ptype, coastal=coastal, terrain=terrain, continent=cont,
        ))
        next_id += 1
    return out


def write_provinces_bmp(arr, path):
    image = Image.fromarray(arr, mode="RGB")
    try:
        image.save(path, format="BMP")
    finally:
        image.close()


def write_terrain_bmp(arr, path, palette):
    """Save an indexed terrain.bmp without changing its palette indices."""
    if arr is None or getattr(arr, "ndim", 0) != 2:
        raise ValueError("terrain.bmp must be an 8-bit indexed image")
    if not palette or len(palette) != 256:
        raise ValueError("terrain.bmp palette must contain 256 RGB entries")

    flat_palette: list[int] = []
    for color in palette:
        if len(color) != 3:
            raise ValueError("invalid terrain palette entry")
        flat_palette.extend(max(0, min(255, int(channel))) for channel in color)

    image = Image.fromarray(arr.astype("uint8", copy=False), mode="P")
    try:
        image.putpalette(flat_palette)
        image.save(path, format="BMP")
    finally:
        image.close()


def write_heightmap_bmp(arr, path):
    """Save an uncompressed 8-bit greyscale HOI4 heightmap."""
    if arr is None or getattr(arr, "ndim", 0) != 2:
        raise ValueError("heightmap.bmp must be an 8-bit greyscale image")
    image = Image.fromarray(arr.astype("uint8", copy=False), mode="L")
    try:
        image.save(path, format="BMP", compression="raw")
    finally:
        image.close()


def write_world_normal_bmp(arr, path):
    """Save an uncompressed 24-bit RGB HOI4 world normal map."""
    if arr is None or getattr(arr, "ndim", 0) != 3 or arr.shape[2] != 3:
        raise ValueError("world_normal.bmp must be a 24-bit RGB image")
    image = Image.fromarray(arr.astype("uint8", copy=False), mode="RGB")
    try:
        image.save(path, format="BMP", compression="raw")
    finally:
        image.close()


def write_rivers_bmp(arr, path, palette):
    """Save rivers.bmp while preserving raw indices and its 256-entry palette."""
    write_terrain_bmp(arr, path, palette)
    # Pillow writes biClrUsed/biClrImportant as 256. HOI4's stock rivers.bmp
    # leaves both fields at zero; matching that header avoids the harmless but
    # noisy "Palette in rivers.bmp is probably not correct" map error.
    with open(path, "r+b") as bitmap:
        bitmap.seek(46)
        bitmap.write(b"\x00\x00\x00\x00")
        bitmap.seek(50)
        bitmap.write(b"\x00\x00\x00\x00")


def write_supply_nodes(path, nodes):
    """Write canonical ``level province`` supply node records."""
    lines = [f"{int(node['level'])} {int(node['province'])}" for node in nodes]
    with open(path, "w", encoding="utf-8", newline="") as output:
        output.write("\r\n".join(lines))
        if lines:
            output.write("\r\n")


def write_railways(path, railways):
    """Write canonical ``level count province...`` railway records."""
    lines = []
    for railway in railways:
        provinces = [int(value) for value in railway["provinces"]]
        values = " ".join(str(value) for value in provinces)
        lines.append(f"{int(railway['level'])} {len(provinces)} {values}")
    with open(path, "w", encoding="utf-8", newline="") as output:
        output.write("\r\n".join(lines))
        if lines:
            output.write("\r\n")


def write_definition_csv(path, provinces, removed_ids):
    """definition.csv 저장.

    HOI4는 definition.csv의 행 순서가 ID 0,1,2,...,max 순으로 빈틈없이 이어져야
    한다(엔진이 ID = 행 인덱스로 취급). 따라서 삭제(병합 흡수)나 원본 누락 등으로
    중간 ID가 비어 있을 경우, 다음 ID를 당겨오면 모든 후속 ID가 어긋나 외부 파일
    참조가 모두 깨진다. 대신 그 슬롯을 placeholder 행으로 채워 ID 순서를 보존한다.

    Placeholder 형식: `id;0;0;0;land;false;unknown;0`
      - RGB (0,0,0)는 invalid slot으로 어떤 픽셀도 가리키지 않음
      - terrain "unknown" / continent 0 은 안전한 무의미 값
    """
    kept = {p.id: p for p in provinces if p.id not in removed_ids}
    if not kept:
        # 비어있는 경우 그대로 빈 파일 작성 (이론상 발생하지 않음).
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write("")
        return

    max_id = max(kept.keys())
    rows: list[str] = []
    # ID 0은 HOI4의 invalid slot. 존재하지 않으면 placeholder로 채워둔다.
    for i in range(0, max_id + 1):
        p = kept.get(i)
        if p is not None:
            rows.append(p.to_csv_row())
        else:
            rows.append(f"{i};0;0;0;land;false;unknown;0")

    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("\r\n".join(rows))
        f.write("\r\n")


def _replace_provinces_block(file_path, new_ids):
    with open(file_path, "r", encoding="utf-8-sig", errors="replace") as f:
        text = f.read()
    block = "provinces={\n\t\t" + " ".join(str(i) for i in new_ids) + " \n\t}"
    new_text, count = re.subn(
        r"provinces\s*=\s*\{[^}]*\}", block, text, count=1, flags=re.DOTALL)
    if count == 0 or new_text == text:
        return False
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(new_text)
    return True


def update_state_file(state, add_ids, remove_ids):
    if not (add_ids or (remove_ids & set(state.province_ids))):
        return False
    new_ids = [i for i in state.province_ids if i not in remove_ids]
    new_ids.extend(i for i in add_ids if i not in new_ids)
    new_ids = sorted(new_ids)
    if _replace_provinces_block(state.file_path, new_ids):
        state.province_ids = new_ids
        return True
    return False


def update_strategic_region_file(region, add_ids, remove_ids):
    if not (add_ids or (remove_ids & set(region.province_ids))):
        return False
    new_ids = [i for i in region.province_ids if i not in remove_ids]
    new_ids.extend(i for i in add_ids if i not in new_ids)
    new_ids = sorted(new_ids)
    if _replace_provinces_block(region.file_path, new_ids):
        region.province_ids = new_ids
        return True
    return False


def pick_strategic_region_for_province(rgb, new_province_id, adjacency,
                                       province_by_rgb, regions):
    pid_to_region = {}
    for r in regions:
        for pid in r.province_ids:
            pid_to_region[pid] = r
    counts = Counter()
    for nrgb in adjacency.get(rgb, set()):
        prov = province_by_rgb.get(nrgb)
        if prov is None:
            continue
        reg = pid_to_region.get(prov.id)
        if reg:
            counts[reg.id] += 1
    if not counts:
        return None
    rid, _ = counts.most_common(1)[0]
    for r in regions:
        if r.id == rid:
            return r
    return None


# buildings.txt 자동 추가 기능은 의도적으로 제거됨.
# 게임이 자동 관리하는 부분이 많고, placeholder(0;0;0;0;0;0) 좌표를 채워도
# 시각적 의미가 없어 안전상 손대지 않는 것이 낫다는 판단.
