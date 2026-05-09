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
    Image.fromarray(arr, mode="RGB").save(path, format="BMP")


def write_definition_csv(path, provinces, removed_ids):
    rows = [p.to_csv_row() for p in sorted(provinces, key=lambda p: p.id)
            if p.id not in removed_ids]
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
