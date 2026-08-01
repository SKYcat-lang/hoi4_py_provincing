"""HOI4 Province Painter - PyWebView 진입점.

JS에서는 `window.pywebview.api.<method>(...)`로 백엔드를 호출한다.
"""
from __future__ import annotations

import os
import sys
import threading
import traceback
from typing import Optional

import numpy as np
import webview

from core.color_pool import ColorPool
from core.definitions import (
    MapPaths,
    Province,
    StateInfo,
    StrategicRegionInfo,
    TerrainCategory,
)
from core.map_loader import (
    encode_image_to_png_base64,
    encode_rivers_layer_png_base64,
    encode_terrain_layer_png_base64,
    find_map_paths,
    load_continent_txt,
    load_definition_csv,
    load_heightmap_bmp,
    load_graphical_terrain_index_names,
    load_provinces_bmp,
    load_rivers_bmp,
    load_rivers_palette,
    load_supply_nodes,
    load_railways,
    load_resource_names,
    load_state_category_names,
    load_state_files,
    load_strategic_regions,
    load_terrain_bmp,
    load_terrain_categories,
    load_terrain_palette,
)
from core.map_saver import (
    analyze_for_save,
    build_new_provinces,
    update_state_file,
    update_strategic_region_file,
    write_definition_csv,
    write_heightmap_bmp,
    write_world_normal_bmp,
    write_provinces_bmp,
    write_rivers_bmp,
    write_supply_nodes,
    write_railways,
    write_terrain_bmp,
)
from core.normal_map import generate_world_normal as build_world_normal
from core.area_assignments import (
    area_file_changes,
    build_area_assignments,
    pick_neighbor_area_id,
    update_area_assignment,
)
from core.support_editors import (
    apply_terrain_changes as apply_terrain_buffer_changes,
    apply_terrain_stroke as apply_terrain_buffer_stroke,
    apply_heightmap_changes as apply_heightmap_buffer_changes,
    apply_river_changes as apply_river_buffer_changes,
    delete_supply_railway as delete_supply_railway_buffer,
    fill_scalar_layer_in_province,
    flood_fill_rgb_connected,
    flood_fill_terrain as flood_fill_terrain_buffer,
    insert_supply_railway as insert_supply_railway_buffer,
    move_scalar_selection,
    normalize_supply_network,
    protected_province_colors,
    province_color_at,
    smooth_heightmap_coast as smooth_heightmap_coast_buffer,
    validate_river_topology as validate_river_buffer,
    validate_supply_network as validate_supply_buffer,
)
from core.province_analyzer import find_adjacent_colors
from core.province_mover import move_province_group
from core.state_creator import create_state as create_state_files
from core.state_neighbors import rank_adjacent_states
from core.state_properties import (
    read_state_history_block,
    read_state_properties,
    read_state_source,
    update_state_properties,
    update_state_source as write_state_source,
)
from core.xcrossing import find_all_xcrossings, find_xcrossings_near
from core.validators import (
    find_exclaves,
    find_one_pixel_provinces,
    fix_rivers_bmp,
    validate_rivers_bmp,
)
from core.split import split_region
from core.delete import (
    absorb_province,
    compute_absorption_map,
    find_best_absorber_rgb,
)
from core.external_files import apply_absorption_to_all
from core.province_analyzer import find_used_colors
from core.id_search import (
    IdMatch,
    SearchConfig,
    find_placeholder_ids,
    search_ids_in_mod,
)
from core.compact import (
    apply_compaction,
    build_min_invasive_plan,
    rewrite_definition_csv,
)
from core.adjacencies import (
    Adjacency,
    add_adjacency as adj_add,
    delete_adjacency as adj_delete,
    load_adjacencies,
    load_adjacency_rule_names,
    sanitize_comment,
    save_adjacencies,
    update_adjacency as adj_update,
    validate_adjacency,
)


HERE = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(HERE, "web")
INDEX_HTML = os.path.join(WEB_DIR, "index.html")


def _state_color_from_id(sid: int) -> tuple[int, int, int]:
    """스테이트 ID 해시 기반의 결정적 고유색.

    같은 ID는 항상 같은 색. 어둡거나 너무 밝지 않은 톤 위주.
    """
    h = (sid * 2654435761) & 0xFFFFFFFF  # Knuth multiplicative hash
    r = ((h >> 16) & 0xFF)
    g = ((h >> 8) & 0xFF)
    b = (h & 0xFF)
    # 밝기 정규화: 너무 어두우면 띄움
    if r + g + b < 200:
        r = (r + 80) & 0xFF
        g = (g + 80) & 0xFF
        b = (b + 80) & 0xFF
    return (r, g, b)


class Api:
    """JS ↔ Python Bridge."""

    def __init__(self) -> None:
        self.paths: Optional[MapPaths] = None
        self.provinces_arr: Optional[np.ndarray] = None
        # disk_provinces_arr: 마지막 로드/저장 시점의 BMP 스냅샷.
        # 저장 시 (현재 vs 디스크) 비교로 부모 RGB를 정확히 재구성한다.
        self.disk_provinces_arr: Optional[np.ndarray] = None
        self.terrain_arr: Optional[np.ndarray] = None
        self.terrain_palette: Optional[list[list[int]]] = None
        self.terrain_dirty = False
        self.heightmap_arr: Optional[np.ndarray] = None
        self.heightmap_dirty = False
        self.world_normal_stale = False
        self.rivers_arr: Optional[np.ndarray] = None
        self.rivers_palette: Optional[list[list[int]]] = None
        self.rivers_dirty = False
        self.supply_nodes: list[dict] = []
        self.railways: list[dict] = []
        self.supply_dirty = False
        self.provinces: list[Province] = []
        self.terrain_categories: list[TerrainCategory] = []
        self.terrain_index_names: dict[int, str] = {}
        self.continents: list[str] = []
        self.states: list[StateInfo] = []
        self.state_category_names: list[str] = []
        self.resource_names: list[str] = []
        self.regions: list[StrategicRegionInfo] = []
        self.color_pool: Optional[ColorPool] = None
        # province_id -> state_id 매핑 (사용자가 스테이트 맵에서 편집)
        self.assignments: dict[int, int] = {}
        # province_id -> strategic_region_id 매핑
        self.region_assignments: dict[int, int] = {}
        # 새 RGB가 어느 RGB(들)을 잡아먹고 생겨났는지 카운트.
        # key=새 RGB tuple, value={옛 RGB tuple: 픽셀 수}
        # 저장 시 가장 많이 잡아먹은 옛 RGB의 부모 프로빈스로부터 region/state 상속.
        self.parent_pixel_counts: dict[tuple[int, int, int], dict[tuple[int, int, int], int]] = {}
        self._lock = threading.Lock()

    # -------- 파일 다이얼로그 ----------

    def pick_map_folder(self) -> dict:
        """폴더 선택 다이얼로그를 띄운다."""
        try:
            window = webview.windows[0] if webview.windows else None
            if window is None:
                return {"ok": False, "error": "윈도우가 아직 준비되지 않았습니다."}
            result = window.create_file_dialog(
                webview.FOLDER_DIALOG,
                directory=os.path.expanduser("~"),
            )
            if not result:
                return {"ok": False, "cancelled": True}
            folder = result[0] if isinstance(result, (list, tuple)) else result
            return self.load_map_folder(folder)
        except Exception as exc:
            return {"ok": False, "error": str(exc), "trace": traceback.format_exc()}

    def load_map_folder(self, folder: str) -> dict:
        """주어진 폴더에서 맵 파일들을 로드해 프론트엔드에 전송용 데이터 반환."""
        with self._lock:
            try:
                paths = find_map_paths(folder)
                self.paths = paths

                self.provinces_arr = load_provinces_bmp(paths.provinces_bmp)
                # 디스크 BMP 백업: 저장 시점에 "디스크 vs 현재" 비교로 부모 추적을
                # 정확히 재구성한다. 중간에 만들어지고 사라진 임시 RGB는 무시되며,
                # 디스크 RGB → 최종 RGB 직결 매핑이 자연스럽게 만들어진다.
                self.disk_provinces_arr = self.provinces_arr.copy()
                self.terrain_arr = load_terrain_bmp(paths.terrain_bmp)
                self.terrain_palette = load_terrain_palette(paths.terrain_bmp)
                self.terrain_dirty = False
                self.heightmap_arr = load_heightmap_bmp(paths.heightmap_bmp)
                self.heightmap_dirty = False
                self.world_normal_stale = not os.path.isfile(paths.world_normal_bmp)
                self.rivers_arr = load_rivers_bmp(paths.rivers_bmp)
                self.rivers_palette = load_rivers_palette(paths.rivers_bmp)
                self.rivers_dirty = False
                self.supply_nodes = load_supply_nodes(paths.supply_nodes_txt)
                self.railways = load_railways(paths.railways_txt)
                self.supply_dirty = False
                self.provinces = load_definition_csv(paths.definition_csv)
                self.continents = load_continent_txt(paths.continent_txt)
                self.terrain_categories = load_terrain_categories(paths.common_terrain_dir)
                self.terrain_index_names = load_graphical_terrain_index_names(
                    paths.common_terrain_dir
                )
                self.states = load_state_files(paths.history_states_dir)
                self.state_category_names = load_state_category_names(
                    os.path.join(paths.mod_root, "common", "state_category")
                )
                self.resource_names = load_resource_names(
                    os.path.join(paths.mod_root, "common", "resources")
                )
                self.regions = load_strategic_regions(paths.strategicregions_dir)

                self.color_pool = ColorPool(p.rgb for p in self.provinces)

                # 초기 스테이트 할당: 기존 state 파일에서 가져옴
                self.assignments = build_area_assignments(self.states)
                self.region_assignments = build_area_assignments(self.regions)

                # 부모 추적 카운터 초기화
                self.parent_pixel_counts = {}

                height, width = self.provinces_arr.shape[:2]
                lake_rgbs = [list(p.rgb) for p in self.provinces if p.type == "lake"]
                sea_rgbs = [list(p.rgb) for p in self.provinces if p.type == "sea"]

                # 오버레이 레이어용 PNG 데이터 URL.
                # rivers: 흰색(땅)/회색(바다) 알파 0 처리 → 강만 보임.
                # terrain: 팔레트 적용 RGB 변환 → 회색조가 아닌 진짜 색상.
                rivers_data_url = encode_rivers_layer_png_base64(paths.rivers_bmp)
                terrain_data_url = encode_terrain_layer_png_base64(paths.terrain_bmp)
                terrain_editable = (
                    self.terrain_arr is not None
                    and self.terrain_arr.ndim == 2
                    and self.terrain_palette is not None
                    and self.terrain_arr.shape[:2] == self.provinces_arr.shape[:2]
                )
                terrain_index_data_url = (
                    encode_image_to_png_base64(self.terrain_arr)
                    if terrain_editable else None
                )
                used_terrain_indices = (
                    set(int(v) for v in np.unique(self.terrain_arr).tolist())
                    if terrain_editable else set()
                )
                terrain_palette_entries = []
                if terrain_editable and self.terrain_palette is not None:
                    for index, color in enumerate(self.terrain_palette):
                        category_name = self.terrain_index_names.get(index)
                        terrain_palette_entries.append({
                            "index": index,
                            "rgb": color,
                            "name": category_name,
                            "used": index in used_terrain_indices,
                        })
                heightmap_editable = (
                    self.heightmap_arr is not None
                    and self.heightmap_arr.ndim == 2
                    and self.heightmap_arr.shape[:2] == self.provinces_arr.shape[:2]
                )
                rivers_editable = (
                    self.rivers_arr is not None
                    and self.rivers_arr.ndim == 2
                    and self.rivers_palette is not None
                    and self.rivers_arr.shape[:2] == self.provinces_arr.shape[:2]
                )

                return {
                    "ok": True,
                    "mapDir": paths.map_dir,
                    "modRoot": paths.mod_root,
                    "width": width,
                    "height": height,
                    "imageDataUrl": encode_image_to_png_base64(self.provinces_arr),
                    "riversImageDataUrl": rivers_data_url,
                    "terrainImageDataUrl": terrain_data_url,
                    "terrainIndexDataUrl": terrain_index_data_url,
                    "terrainEditable": terrain_editable,
                    "terrainPalette": terrain_palette_entries,
                    "heightmapEditable": heightmap_editable,
                    "heightmapImageDataUrl": (
                        encode_image_to_png_base64(self.heightmap_arr)
                        if heightmap_editable else None
                    ),
                    "worldNormalAvailable": os.path.isfile(paths.world_normal_bmp),
                    "worldNormalStale": self.world_normal_stale,
                    "riversEditable": rivers_editable,
                    "riversPalette": self.rivers_palette if rivers_editable else [],
                    "riversIndexDataUrl": (
                        encode_image_to_png_base64(self.rivers_arr)
                        if rivers_editable else None
                    ),
                    "supplyEditable": (
                        os.path.isfile(paths.supply_nodes_txt)
                        and os.path.isfile(paths.railways_txt)
                    ),
                    "supplyNodes": self.supply_nodes,
                    "railways": self.railways,
                    "provinceCount": len(self.provinces),
                    "lakeRgbs": lake_rgbs,
                    "seaRgbs": sea_rgbs,
                    "states": [
                        {
                            "id": s.id,
                            "name": s.name,
                            "fileName": os.path.basename(s.file_path),
                            "color": list(_state_color_from_id(s.id)),
                            "provinceCount": len(s.province_ids),
                        }
                        for s in self.states
                    ],
                    # province_id -> state_id 매핑 (프론트엔드가 스테이트 맵 그릴 때 사용)
                    "assignments": [
                        [pid, sid] for pid, sid in self.assignments.items()
                    ],
                    # 모든 프로빈스의 RGB → ID 매핑 (프론트엔드 픽업용)
                    "provinceRgbToId": [
                        [list(p.rgb), p.id] for p in self.provinces
                    ],
                    "regions": [
                        {
                            "id": r.id,
                            "name": r.name,
                            "color": list(_state_color_from_id(r.id)),
                            "provinceCount": len(r.province_ids),
                        }
                        for r in self.regions
                    ],
                    "regionAssignments": [
                        [pid, region_id]
                        for pid, region_id in self.region_assignments.items()
                    ],
                    "continents": self.continents,
                    "stateCategoryNames": self.state_category_names,
                    "resourceNames": self.resource_names,
                    "terrainCategoryNames": [c.name for c in self.terrain_categories if not c.is_water],
                }
            except Exception as exc:
                traceback.print_exc()
                return {"ok": False, "error": str(exc), "trace": traceback.format_exc()}

    # -------- 그리기 보조 ----------

    def pick_new_color(self) -> dict:
        """현재 사용되지 않은 RGB를 하나 발급."""
        if self.color_pool is None:
            return {"ok": False, "error": "맵을 먼저 로드해주세요."}
        rgb = self.color_pool.pick_new()
        return {"ok": True, "rgb": list(rgb)}

    def _record_parent_for_change(
        self, new_rgb: tuple[int, int, int], old_rgb: tuple[int, int, int]
    ) -> None:
        """부모 추적: new_rgb가 old_rgb 자리에 들어왔다는 사실을 카운트."""
        if new_rgb == old_rgb:
            return
        bucket = self.parent_pixel_counts.setdefault(new_rgb, {})
        bucket[old_rgb] = bucket.get(old_rgb, 0) + 1

    def _rebuild_parent_counts_from_disk(self) -> None:
        """디스크 백업 BMP와 현재 BMP를 비교해 parent_pixel_counts를 재구성.

        이 방식의 장점:
          - 중간에 만들어졌다 사라진 임시 RGB(예: 그렸다가 다시 분할로 갈아탄 색)는
            카운터에 안 잡힘 → 새 RGB의 진짜 부모(디스크에 저장된 RGB)가 정확히 추적됨.
          - 작업 순서/Undo/Redo와 무관하게 항상 일관된 결과.
          - BMP 덮어쓰기와 자연스럽게 호환.

        호출 시점: preview_save와 commit_save 진입 시점.
        """
        if self.disk_provinces_arr is None or self.provinces_arr is None:
            return
        if self.disk_provinces_arr.shape != self.provinces_arr.shape:
            return  # 비정상: 크기 불일치 (overlay 시 거부됐어야 함)

        cur = self.provinces_arr
        disk = self.disk_provinces_arr

        # 변경된 픽셀 마스크
        diff = (
            (cur[..., 0] != disk[..., 0])
            | (cur[..., 1] != disk[..., 1])
            | (cur[..., 2] != disk[..., 2])
        )
        if not diff.any():
            self.parent_pixel_counts = {}
            return

        ys, xs = np.where(diff)
        new_pixels = cur[ys, xs]
        old_pixels = disk[ys, xs]

        # (new_rgb, old_rgb) 쌍별 카운트 — NumPy 벡터 패킹으로 빠르게
        # 32-bit packed: r<<16 | g<<8 | b
        new_packed = (
            new_pixels[:, 0].astype(np.int64) << 16
            | new_pixels[:, 1].astype(np.int64) << 8
            | new_pixels[:, 2].astype(np.int64)
        )
        old_packed = (
            old_pixels[:, 0].astype(np.int64) << 16
            | old_pixels[:, 1].astype(np.int64) << 8
            | old_pixels[:, 2].astype(np.int64)
        )
        # 64-bit 키: new<<32 | old
        combined = (new_packed.astype(np.int64) << 32) | old_packed
        unique_keys, counts = np.unique(combined, return_counts=True)

        new_counts: dict[tuple[int, int, int], dict[tuple[int, int, int], int]] = {}
        for key, cnt in zip(unique_keys.tolist(), counts.tolist()):
            new_p = int((key >> 32) & 0xFFFFFFFF)
            old_p = int(key & 0xFFFFFFFF)
            new_rgb = ((new_p >> 16) & 0xFF, (new_p >> 8) & 0xFF, new_p & 0xFF)
            old_rgb = ((old_p >> 16) & 0xFF, (old_p >> 8) & 0xFF, old_p & 0xFF)
            new_counts.setdefault(new_rgb, {})[old_rgb] = int(cnt)

        self.parent_pixel_counts = new_counts

    def _pick_parent_province(
        self,
        new_rgb: tuple[int, int, int],
        existing_by_rgb: dict[tuple[int, int, int], Province],
    ) -> Optional[Province]:
        """new_rgb가 가장 많이 잡아먹은 옛 RGB의 (현존하는) 부모 프로빈스를 반환.

        후보 우선순위:
          1) 픽셀 수가 많은 옛 RGB 순
          2) (0, 0, 0) invalid 슬롯 옛 RGB는 제외
          3) 후보 RGB가 existing_by_rgb에 존재해야 함 (사라진 RGB는 패스)
        """
        bucket = self.parent_pixel_counts.get(new_rgb, {})
        if not bucket:
            return None
        ranked = sorted(
            ((rgb, cnt) for rgb, cnt in bucket.items() if rgb != (0, 0, 0)),
            key=lambda kv: kv[1], reverse=True,
        )
        for rgb, _ in ranked:
            parent = existing_by_rgb.get(rgb)
            if parent is not None:
                return parent
        return None

    def apply_stroke(self, pixels: list[list[int]], rgb: list[int],
                     respect_lakes: bool, respect_sea: bool,
                     old_pixel_rgbs: list[list[int]] | None = None,
                     track_parents: bool = True) -> dict:
        """프론트엔드에서 한 스트로크 동안 변경된 픽셀을 백엔드 ndarray에 반영.

        pixels: [[x, y], ...]
        rgb: [r, g, b]  (새 색)
        old_pixel_rgbs: [[oR, oG, oB], ...] - pixels와 같은 순서. 부모 추적용.
                       프론트에서 변경 직전 색을 알기에 보내준다.
                       None이면 백엔드 ndarray의 현재 값을 옛 색으로 사용한다(안전).
        """
        if self.provinces_arr is None:
            return {"ok": False, "error": "맵이 로드되지 않았습니다."}

        arr = self.provinces_arr
        height, width = arr.shape[:2]
        new_color = np.array(rgb, dtype=np.uint8)
        new_rgb_t = (int(rgb[0]), int(rgb[1]), int(rgb[2]))

        protected: set[tuple[int, int, int]] = set()
        if respect_lakes:
            protected.update(p.rgb for p in self.provinces if p.type == "lake")
        if respect_sea:
            protected.update(p.rgb for p in self.provinces if p.type == "sea")

        applied = 0
        skipped = 0
        for i, px in enumerate(pixels):
            x, y = int(px[0]), int(px[1])
            if x < 0 or y < 0 or x >= width or y >= height:
                continue
            current = tuple(int(c) for c in arr[y, x].tolist())
            if current in protected:
                skipped += 1
                continue
            # 부모 추적: 옛 색은 프론트가 보낸 값 우선, 없으면 백엔드 현재값 사용
            if old_pixel_rgbs is not None and i < len(old_pixel_rgbs):
                o = old_pixel_rgbs[i]
                old_t = (int(o[0]), int(o[1]), int(o[2]))
            else:
                old_t = current
            arr[y, x] = new_color
            if track_parents:
                self._record_parent_for_change(new_rgb_t, old_t)
            applied += 1

        if self.color_pool is not None:
            self.color_pool.add(new_rgb_t)

        return {"ok": True, "applied": applied, "skipped": skipped}

    # -------- terrain.bmp 편집 ----------

    def _terrain_edit_error(self) -> Optional[str]:
        if self.terrain_arr is None:
            return "terrain.bmp가 로드되지 않았습니다."
        if self.terrain_arr.ndim != 2 or self.terrain_palette is None:
            return "terrain.bmp가 8비트 인덱스 형식이 아니어서 안전하게 편집할 수 없습니다."
        return None

    def apply_terrain_stroke(
        self, pixels: list[list[int]], terrain_index: int
    ) -> dict:
        """Apply a brush stroke to the indexed terrain buffer."""
        error = self._terrain_edit_error()
        if error:
            return {"ok": False, "error": error}

        assert self.terrain_arr is not None
        try:
            applied = apply_terrain_buffer_stroke(
                self.terrain_arr, pixels, terrain_index
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if applied:
            self.terrain_dirty = True
        return {"ok": True, "applied": applied}

    def apply_terrain_changes(self, changes: list[list[int]]) -> dict:
        """Apply exact ``[x, y, palette_index]`` values for undo/redo."""
        error = self._terrain_edit_error()
        if error:
            return {"ok": False, "error": error}

        assert self.terrain_arr is not None
        applied = apply_terrain_buffer_changes(self.terrain_arr, changes)
        if applied:
            self.terrain_dirty = True
        return {"ok": True, "applied": applied}

    def move_terrain_selection(
        self, pixel_indices: list[int], dx: int, dy: int
    ) -> dict:
        """Move an indexed terrain selection and clear its source to index 0."""
        error = self._terrain_edit_error()
        if error:
            return {"ok": False, "error": error}
        assert self.terrain_arr is not None
        try:
            result = move_scalar_selection(
                self.terrain_arr, pixel_indices, dx, dy, 0
            )
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        if result["changedPixels"]:
            self.terrain_dirty = True
        return {"ok": True, "applied": len(result["changedPixels"]), **result}

    def flood_fill_terrain(self, x: int, y: int, terrain_index: int) -> dict:
        """Fill terrain.bmp inside the clicked province boundary."""
        error = self._terrain_edit_error()
        if error:
            return {"ok": False, "error": error}
        if self.provinces_arr is None:
            return {"ok": False, "error": "provinces.bmp가 로드되지 않았습니다."}

        assert self.terrain_arr is not None
        try:
            changed_pixels = flood_fill_terrain_buffer(
                self.provinces_arr, self.terrain_arr, x, y, terrain_index
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

        self.terrain_dirty = bool(changed_pixels) or self.terrain_dirty
        return {
            "ok": True,
            "changedPixels": changed_pixels,
            "applied": len(changed_pixels),
        }

    # -------- heightmap.bmp 편집 ----------

    def _heightmap_edit_error(self) -> Optional[str]:
        if self.heightmap_arr is None:
            return "heightmap.bmp가 없거나 8비트 그레이스케일 형식이 아닙니다."
        if self.heightmap_arr.ndim != 2:
            return "heightmap.bmp를 안전하게 편집할 수 없습니다."
        return None

    def apply_heightmap_changes(
        self,
        changes: list[list[int]],
        respect_lakes: bool = False,
        respect_sea: bool = False,
    ) -> dict:
        """Apply exact ``[x, y, greyscale_value]`` height values."""
        error = self._heightmap_edit_error()
        if error:
            return {"ok": False, "error": error}
        assert self.heightmap_arr is not None
        protected = protected_province_colors(
            self.provinces, respect_lakes, respect_sea
        )
        try:
            applied = apply_heightmap_buffer_changes(
                self.heightmap_arr,
                changes,
                self.provinces_arr,
                protected,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if applied:
            self.heightmap_dirty = True
            self.world_normal_stale = True
        return {"ok": True, "applied": applied}

    def move_heightmap_selection(
        self, pixel_indices: list[int], dx: int, dy: int
    ) -> dict:
        """Move a heightmap selection and clear its source to height 0."""
        error = self._heightmap_edit_error()
        if error:
            return {"ok": False, "error": error}
        assert self.heightmap_arr is not None
        try:
            result = move_scalar_selection(
                self.heightmap_arr, pixel_indices, dx, dy, 0
            )
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        if result["changedPixels"]:
            self.heightmap_dirty = True
            self.world_normal_stale = True
        return {"ok": True, "applied": len(result["changedPixels"]), **result}

    def fill_heightmap_province(
        self,
        x: int,
        y: int,
        value: int,
        respect_lakes: bool = False,
        respect_sea: bool = False,
    ) -> dict:
        """Fill heightmap.bmp inside one province, honoring water protection."""
        error = self._heightmap_edit_error()
        if error:
            return {"ok": False, "error": error}
        if self.provinces_arr is None:
            return {"ok": False, "error": "provinces.bmp가 로드되지 않았습니다."}
        assert self.heightmap_arr is not None
        protected = protected_province_colors(
            self.provinces, respect_lakes, respect_sea
        )
        try:
            if province_color_at(self.provinces_arr, x, y) in protected:
                return {
                    "ok": True,
                    "blockedByProtection": True,
                    "changedPixels": [],
                    "applied": 0,
                }
            changed_pixels = fill_scalar_layer_in_province(
                self.provinces_arr, self.heightmap_arr, x, y, value
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if changed_pixels:
            self.heightmap_dirty = True
            self.world_normal_stale = True
        return {
            "ok": True,
            "changedPixels": changed_pixels,
            "applied": len(changed_pixels),
        }

    def smooth_heightmap_coast(
        self,
        x: int,
        y: int,
        width: int = 2,
        strength: int = 50,
    ) -> dict:
        """Smooth land-side height values along the clicked sea or lake."""
        error = self._heightmap_edit_error()
        if error:
            return {"ok": False, "error": error}
        if self.provinces_arr is None:
            return {"ok": False, "error": "provinces.bmp가 로드되지 않았습니다."}
        assert self.heightmap_arr is not None
        try:
            result = smooth_heightmap_coast_buffer(
                self.provinces_arr,
                self.heightmap_arr,
                self.provinces,
                x,
                y,
                width,
                max(1, min(100, int(strength))) / 100.0,
            )
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        if result["changedPixels"]:
            self.heightmap_dirty = True
            self.world_normal_stale = True
        return {
            "ok": True,
            "applied": len(result["changedPixels"]),
            **result,
        }

    def generate_world_normal(self) -> dict:
        """Generate and immediately write world_normal.bmp from live height data."""
        error = self._heightmap_edit_error()
        if error:
            return {"ok": False, "error": error}
        if self.paths is None or self.heightmap_arr is None:
            return {"ok": False, "error": "맵 경로가 로드되지 않았습니다."}
        try:
            existed = os.path.isfile(self.paths.world_normal_bmp)
            normal = build_world_normal(self.heightmap_arr)
            write_world_normal_bmp(normal, self.paths.world_normal_bmp)
            self.world_normal_stale = False
            return {
                "ok": True,
                "path": self.paths.world_normal_bmp,
                "width": int(normal.shape[1]),
                "height": int(normal.shape[0]),
                "overwritten": existed,
                "heightmapDirty": self.heightmap_dirty,
            }
        except Exception as exc:
            traceback.print_exc()
            return {"ok": False, "error": str(exc), "trace": traceback.format_exc()}

    # -------- rivers.bmp 편집 ----------

    def _rivers_edit_error(self) -> Optional[str]:
        if self.rivers_arr is None or self.rivers_palette is None:
            return "rivers.bmp가 없거나 8비트 인덱스 형식이 아닙니다."
        if self.rivers_arr.ndim != 2:
            return "rivers.bmp를 안전하게 편집할 수 없습니다."
        return None

    def apply_rivers_changes(self, changes: list[list[int]]) -> dict:
        """Apply exact ``[x, y, palette_index]`` river-map values."""
        error = self._rivers_edit_error()
        if error:
            return {"ok": False, "error": error}
        assert self.rivers_arr is not None
        applied = apply_river_buffer_changes(self.rivers_arr, changes)
        if applied:
            self.rivers_dirty = True
        return {"ok": True, "applied": applied}

    def fill_rivers_province(self, x: int, y: int, river_index: int) -> dict:
        """Fill rivers.bmp inside the clicked province boundary."""
        error = self._rivers_edit_error()
        if error:
            return {"ok": False, "error": error}
        if self.provinces_arr is None:
            return {"ok": False, "error": "provinces.bmp가 로드되지 않았습니다."}
        assert self.rivers_arr is not None
        try:
            changed_pixels = fill_scalar_layer_in_province(
                self.provinces_arr,
                self.rivers_arr,
                x,
                y,
                river_index,
                allowed_values=set(range(12)) | {254, 255},
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if changed_pixels:
            self.rivers_dirty = True
        return {
            "ok": True,
            "changedPixels": changed_pixels,
            "applied": len(changed_pixels),
        }

    def move_rivers_selection(
        self, pixel_indices: list[int], dx: int, dy: int
    ) -> dict:
        """Move a rivers.bmp selection and restore land/water backgrounds."""
        error = self._rivers_edit_error()
        if error:
            return {"ok": False, "error": error}
        if self.provinces_arr is None:
            return {"ok": False, "error": "provinces.bmp가 로드되지 않았습니다."}
        assert self.rivers_arr is not None

        rgb = self.provinces_arr[..., :3]
        packed = (
            (rgb[..., 0].astype(np.uint32) << 16)
            | (rgb[..., 1].astype(np.uint32) << 8)
            | rgb[..., 2].astype(np.uint32)
        )
        water_keys = np.fromiter(
            (
                (province.r << 16) | (province.g << 8) | province.b
                for province in self.provinces
                if province.type in {"sea", "lake"}
            ),
            dtype=np.uint32,
        )
        background = np.full(self.rivers_arr.shape, 255, dtype=np.uint8)
        if water_keys.size:
            background[np.isin(packed, water_keys)] = 254
        try:
            result = move_scalar_selection(
                self.rivers_arr, pixel_indices, dx, dy, background
            )
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        if result["changedPixels"]:
            self.rivers_dirty = True
        return {"ok": True, "applied": len(result["changedPixels"]), **result}

    def validate_river_topology(self, max_issues: int = 200) -> dict:
        """Validate the structural rules used by HOI4's rivers.bmp."""
        error = self._rivers_edit_error()
        if error:
            return {"ok": False, "error": error}
        assert self.rivers_arr is not None
        return validate_river_buffer(self.rivers_arr, max_issues)

    # -------- supply_nodes.txt / railways.txt 편집 ----------

    def validate_supply_network(
        self, nodes: Optional[list[dict]] = None,
        railways: Optional[list[dict]] = None,
    ) -> dict:
        """Validate supply records against live land provinces and adjacency."""
        if self.provinces_arr is None:
            return {"ok": False, "error": "맵이 로드되지 않았습니다."}
        nodes = self.supply_nodes if nodes is None else nodes
        railways = self.railways if railways is None else railways
        explicit_pairs: set[frozenset[int]] = set()
        adjacency_path = self._adjacencies_csv_path()
        if adjacency_path is not None:
            try:
                explicit_pairs = {
                    frozenset((adjacency.from_id, adjacency.to_id))
                    for adjacency in load_adjacencies(adjacency_path).items
                    if adjacency.type != "impassable"
                }
            except (OSError, ValueError):
                # Pixel-border validation remains available even if the
                # optional adjacency file cannot be read.
                explicit_pairs = set()
        return validate_supply_buffer(
            self.provinces_arr,
            self.provinces,
            self.assignments,
            nodes,
            railways,
            explicit_pairs,
        )

    def update_supply_network(self, nodes: list[dict], railways: list[dict]) -> dict:
        """Replace the in-memory supply network after full validation."""
        try:
            normalized_nodes, normalized_railways = normalize_supply_network(
                nodes, railways
            )
        except (KeyError, TypeError, ValueError) as exc:
            return {"ok": False, "valid": False, "error": f"보급망 입력 형식 오류: {exc}"}
        validation = self.validate_supply_network(normalized_nodes, normalized_railways)
        if not validation.get("valid", False):
            return validation
        self.supply_nodes = normalized_nodes
        self.railways = normalized_railways
        self.supply_dirty = True
        return {"ok": True, "valid": True,
                "nodeCount": len(normalized_nodes),
                "railwayCount": len(normalized_railways),
                "warnings": validation.get("warnings", [])}

    def add_supply_node(self, province_id: int) -> dict:
        """Add one hub without revalidating unrelated legacy railways."""
        try:
            province_id = int(province_id)
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": f"잘못된 프로빈스 ID입니다: {exc}"}
        if any(node.get("province") == province_id for node in self.supply_nodes):
            return {"ok": False, "error": "이 프로빈스에는 이미 보급 허브가 있습니다."}
        candidate = {"level": 1, "province": province_id}
        validation = self.validate_supply_network([candidate], [])
        if not validation.get("valid", False):
            return validation
        self.supply_nodes.append(candidate)
        self.supply_dirty = True
        return {"ok": True, "node": candidate}

    def delete_supply_node(self, province_id: int) -> dict:
        """Delete exactly one hub without validating unrelated records."""
        try:
            province_id = int(province_id)
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": f"잘못된 프로빈스 ID입니다: {exc}"}
        index = next((
            index for index, node in enumerate(self.supply_nodes)
            if int(node.get("province", -1)) == province_id
        ), None)
        if index is None:
            return {"ok": False, "error": "이 프로빈스에는 삭제할 보급 허브가 없습니다."}
        removed = self.supply_nodes.pop(index)
        self.supply_dirty = True
        return {"ok": True, "deletedIndex": index, "node": removed}

    def insert_supply_node(self, node_index: int, node: dict) -> dict:
        """Restore one exact hub at its list position for undo."""
        try:
            index = int(node_index)
            restored = {
                "level": int(node["level"]),
                "province": int(node["province"]),
            }
        except (KeyError, TypeError, ValueError) as exc:
            return {"ok": False, "error": f"복원할 보급 허브 데이터가 잘못되었습니다: {exc}"}
        if not 0 <= index <= len(self.supply_nodes):
            return {"ok": False, "error": "복원할 보급 허브 위치가 범위를 벗어났습니다."}
        self.supply_nodes.insert(index, restored)
        self.supply_dirty = True
        return {"ok": True, "insertedIndex": index, "node": restored}

    def upsert_supply_railway(
        self, railway_index: Optional[int], railway: dict
    ) -> dict:
        """Create or replace one railway, isolating unrelated file defects."""
        try:
            _, normalized = normalize_supply_network([], [railway])
            candidate = normalized[0]
        except (KeyError, TypeError, ValueError) as exc:
            return {"ok": False, "valid": False,
                    "error": f"철도 입력 형식 오류: {exc}"}

        creating = railway_index is None
        if creating:
            index = len(self.railways)
        else:
            try:
                index = int(railway_index)
            except (TypeError, ValueError) as exc:
                return {"ok": False, "error": f"잘못된 철도 인덱스입니다: {exc}"}
            if not 0 <= index < len(self.railways):
                return {"ok": False, "error": "편집할 철도 인덱스가 범위를 벗어났습니다."}

        validation = self.validate_supply_network([], [candidate])
        if not validation.get("valid", False):
            return validation
        warnings = [
            {**warning, "index": index}
            for warning in validation.get("warnings", [])
        ]
        previous = None
        if creating:
            self.railways.append(candidate)
        else:
            previous = {
                "level": int(self.railways[index]["level"]),
                "provinces": [int(value) for value in self.railways[index]["provinces"]],
            }
            self.railways[index] = candidate
        self.supply_dirty = True
        return {
            "ok": True,
            "valid": True,
            "index": index,
            "created": creating,
            "railway": candidate,
            "previousRailway": previous,
            "warnings": warnings,
        }

    def replace_supply_railway(self, railway_index: int, railway: dict) -> dict:
        """Restore one exact railway in place for undo/redo."""
        try:
            index = int(railway_index)
            _, normalized = normalize_supply_network([], [railway])
        except (KeyError, TypeError, ValueError) as exc:
            return {"ok": False, "error": f"복원할 철도 데이터가 잘못되었습니다: {exc}"}
        if not 0 <= index < len(self.railways):
            return {"ok": False, "error": "복원할 철도 인덱스가 범위를 벗어났습니다."}
        self.railways[index] = normalized[0]
        self.supply_dirty = True
        return {"ok": True, "index": index, "railway": normalized[0]}

    def delete_supply_railway(self, railway_index: int) -> dict:
        """Delete one railway immediately; remaining records are untouched."""
        try:
            removed = delete_supply_railway_buffer(
                self.railways, railway_index
            )
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        self.supply_dirty = True
        return {
            "ok": True,
            "deletedIndex": int(railway_index),
            "deletedRailway": removed,
            "railwayCount": len(self.railways),
        }

    def insert_supply_railway(self, railway_index: int, railway: dict) -> dict:
        """Restore a deleted railway for undo without validating other rails."""
        try:
            restored = insert_supply_railway_buffer(
                self.railways, railway_index, railway
            )
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        self.supply_dirty = True
        return {
            "ok": True,
            "insertedIndex": int(railway_index),
            "railway": restored,
            "railwayCount": len(self.railways),
        }

    # -------- 외부 BMP 덮어쓰기 ----------

    def pick_overlay_bmp(self) -> dict:
        """파일 다이얼로그로 BMP 선택 → 현재 맵에 덮어쓴다.

        '새로 붓으로 그려버린 것'으로 간주하므로 변경된 픽셀을 모두
        부모 추적 카운터에 누적한다. Undo는 프론트엔드 측 변경 적용 시 등록.
        """
        if self.provinces_arr is None or self.paths is None:
            return {"ok": False, "error": "맵이 로드되지 않았습니다."}

        try:
            window = webview.windows[0] if webview.windows else None
            if window is None:
                return {"ok": False, "error": "윈도우가 준비되지 않았습니다."}
            file_types = ("BMP files (*.bmp)", "All files (*.*)")
            result = window.create_file_dialog(
                webview.OPEN_DIALOG,
                file_types=file_types,
                allow_multiple=False,
            )
            if not result:
                return {"ok": False, "cancelled": True}
            path = result[0] if isinstance(result, (list, tuple)) else result
            return self.apply_overlay_bmp(path)
        except Exception as exc:
            traceback.print_exc()
            return {"ok": False, "error": str(exc), "trace": traceback.format_exc()}

    def apply_overlay_bmp(self, path: str) -> dict:
        """주어진 경로의 BMP를 현재 provinces_arr에 덮어쓴다.

        반환:
          ok, width, height, changedCount, imageDataUrl,
          changes: [[x, y, oldR, oldG, oldB, newR, newG, newB], ...]
                   (프론트에서 Undo 등록 + 화면 갱신 + X-crossing 스캔에 사용)

        주의: changes 양이 매우 클 수 있으므로 압축적으로 인코딩한다.
        하지만 PyWebView 브리지는 JSON이라 한 번에 큰 배열도 처리 가능.
        """
        if self.provinces_arr is None:
            return {"ok": False, "error": "맵이 로드되지 않았습니다."}
        if not os.path.isfile(path):
            return {"ok": False, "error": f"파일이 존재하지 않습니다: {path}"}

        try:
            new_arr = load_provinces_bmp(path)
        except Exception as exc:
            return {"ok": False, "error": f"BMP 로드 실패: {exc}"}

        cur = self.provinces_arr
        if new_arr.shape != cur.shape:
            return {
                "ok": False,
                "error": (
                    f"크기가 다릅니다. 현재 {cur.shape[1]}×{cur.shape[0]}, "
                    f"불러온 BMP {new_arr.shape[1]}×{new_arr.shape[0]}. "
                    "동일 해상도의 BMP만 덮어쓸 수 있습니다."
                ),
            }

        # 픽셀별 차이 계산 (벡터화)
        diff_mask = (
            (cur[..., 0] != new_arr[..., 0])
            | (cur[..., 1] != new_arr[..., 1])
            | (cur[..., 2] != new_arr[..., 2])
        )
        if not diff_mask.any():
            return {
                "ok": True,
                "changedCount": 0,
                "changes": [],
                "width": cur.shape[1],
                "height": cur.shape[0],
                "message": "차이가 없습니다.",
            }

        ys, xs = np.where(diff_mask)
        # 옛 색 / 새 색 추출
        old_pixels = cur[ys, xs]
        new_pixels = new_arr[ys, xs]

        # changes 리스트 생성 (Python 측에서 큰 변환은 비싸므로 tolist() 한 번에)
        xs_list = xs.tolist()
        ys_list = ys.tolist()
        old_list = old_pixels.tolist()
        new_list = new_pixels.tolist()

        changes: list[list[int]] = []
        for i in range(len(xs_list)):
            x = int(xs_list[i]); y = int(ys_list[i])
            o = old_list[i]; n = new_list[i]
            changes.append([x, y,
                           int(o[0]), int(o[1]), int(o[2]),
                           int(n[0]), int(n[1]), int(n[2])])
            # 부모 추적
            self._record_parent_for_change(
                (int(n[0]), int(n[1]), int(n[2])),
                (int(o[0]), int(o[1]), int(o[2])),
            )

        # 백엔드 ndarray 갱신
        self.provinces_arr[:] = new_arr

        # 새로 등장한 RGB를 color_pool에 등록
        if self.color_pool is not None:
            unique_new = set(tuple(p) for p in new_list)
            for rgb in unique_new:
                self.color_pool.add(rgb)

        return {
            "ok": True,
            "width": cur.shape[1],
            "height": cur.shape[0],
            "changedCount": len(changes),
            "changes": changes,
            "imageDataUrl": encode_image_to_png_base64(self.provinces_arr),
        }

    # -------- X-crossing 검사 ----------

    def scan_xcrossings_near(self, pixels: list[list[int]]) -> dict:
        """변경된 픽셀 주변만 국소 스캔. 브러시 스트로크 직후 호출용."""
        if self.provinces_arr is None:
            return {"ok": False, "error": "맵이 로드되지 않았습니다."}
        coords = find_xcrossings_near(self.provinces_arr, pixels)
        return {
            "ok": True,
            "coords": [[x, y] for x, y in coords],
            "count": len(coords),
        }

    def scan_xcrossings_all(self, max_results: int = 5000) -> dict:
        """전체 BMP를 스캔. 저장 시점 또는 사용자 요청 시 호출."""
        if self.provinces_arr is None:
            return {"ok": False, "error": "맵이 로드되지 않았습니다."}
        coords = find_all_xcrossings(self.provinces_arr, max_results=max_results)
        return {
            "ok": True,
            "coords": [[x, y] for x, y in coords],
            "count": len(coords),
            "truncated": len(coords) >= max_results,
        }

    # -------- One-pixel province 검사 ----------

    def scan_one_pixel_provinces(self, max_results: int = 1000) -> dict:
        """connected component 단위로 1픽셀짜리 외톨이 모두 검출.

        같은 RGB의 큰 영역과 떨어진 1픽셀 외톨이도 잡는다.
        """
        if self.provinces_arr is None:
            return {"ok": False, "error": "맵이 로드되지 않았습니다."}
        results = find_one_pixel_provinces(self.provinces_arr, max_results=max_results)
        return {
            "ok": True,
            "coords": [[x, y, list(rgb)] for x, y, rgb in results],
            "count": len(results),
            "truncated": len(results) >= max_results,
        }

    # -------- Exclave (월경지) 검사 ----------

    def scan_exclaves(self, max_pixels: int = 2000) -> dict:
        """월경지 검출. 같은 RGB가 분리된 2+ 컴포넌트일 때 본체 외 모든 컴포넌트 반환.

        반환: { exclaves: [{rgb, size, pixels: [[x,y],...]}, ...], totalPixelMarkers, count }
        """
        if self.provinces_arr is None:
            return {"ok": False, "error": "맵이 로드되지 않았습니다."}
        exclaves = find_exclaves(self.provinces_arr, max_results=max_pixels)
        total = sum(len(e["pixels"]) for e in exclaves)
        return {
            "ok": True,
            "exclaves": exclaves,
            "count": len(exclaves),
            "totalPixelMarkers": total,
            "truncated": total >= max_pixels,
        }

    # -------- rivers.bmp 검증 + 교정 ----------

    def validate_rivers(self) -> dict:
        """rivers.bmp 팔레트/크기/전체 엔트리 종합 검증."""
        if self.paths is None:
            return {"ok": False, "error": "맵이 로드되지 않았습니다."}
        return validate_rivers_bmp(
            self.paths.rivers_bmp,
            provinces_path=self.paths.provinces_bmp,
        )

    def fix_rivers(self) -> dict:
        """rivers.bmp를 표준 팔레트로 자동 교정 (백업 포함)."""
        if self.paths is None:
            return {"ok": False, "error": "맵이 로드되지 않았습니다."}
        return fix_rivers_bmp(self.paths.rivers_bmp, backup=True)

    def pick_color_at(self, x: int, y: int) -> dict:
        """우클릭 스포이드: 해당 픽셀의 RGB와 (있으면) 프로빈스 정보 반환."""
        if self.provinces_arr is None:
            return {"ok": False, "error": "맵이 로드되지 않았습니다."}
        arr = self.provinces_arr
        height, width = arr.shape[:2]
        if not (0 <= x < width and 0 <= y < height):
            return {"ok": False, "error": "범위 밖"}
        rgb = tuple(int(c) for c in arr[y, x].tolist())
        prov = next((p for p in self.provinces if p.rgb == rgb), None)
        return {
            "ok": True,
            "rgb": list(rgb),
            "province": (
                None if prov is None
                else {
                    "id": prov.id,
                    "type": prov.type,
                    "terrain": prov.terrain,
                    "continent": prov.continent,
                    "coastal": prov.coastal,
                }
            ),
        }

    # -------- 스테이트 맵 ----------

    def get_province_id_at_pixel(self, x: int, y: int) -> dict:
        """픽셀 좌표 → province ID. 등록되지 않은 RGB면 id=null."""
        if self.provinces_arr is None:
            return {"ok": False, "error": "맵이 로드되지 않았습니다."}
        h, w = self.provinces_arr.shape[:2]
        if not (0 <= x < w and 0 <= y < h):
            return {"ok": False, "error": "범위 밖"}
        rgb = tuple(int(c) for c in self.provinces_arr[y, x].tolist())
        prov = next((p for p in self.provinces if p.rgb == rgb), None)
        return {
            "ok": True,
            "rgb": list(rgb),
            "provinceId": (prov.id if prov else None),
            "stateId": (self.assignments.get(prov.id) if prov else None),
            "strategicRegionId": (
                self.region_assignments.get(prov.id) if prov else None
            ),
        }

    def assign_province_to_state(self, province_id: int, state_id: Optional[int]) -> dict:
        """프로빈스의 스테이트 소속 갱신. state_id=None이면 미할당으로."""
        try:
            prev_state_id = update_area_assignment(
                self.assignments,
                province_id,
                state_id,
                (state.id for state in self.states),
                "스테이트",
            )
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        return {
            "ok": True,
            "provinceId": province_id,
            "previousStateId": prev_state_id,
            "stateId": state_id,
        }

    def get_state_properties(self, state_id: int) -> dict:
        """스테이트의 안전한 최상위 속성만 읽는다. history 블록은 해석하지 않는다."""
        try:
            sid = int(state_id)
            state = next((item for item in self.states if item.id == sid), None)
            if state is None:
                raise ValueError(f"존재하지 않는 스테이트 ID입니다: {sid}")
            props = read_state_properties(state.file_path)
            return {
                "ok": True,
                "stateId": sid,
                "manpower": props.manpower,
                "stateCategory": props.state_category,
                "resources": props.resources,
                "localSupplies": props.local_supplies,
                "stateCategoryNames": self.state_category_names,
                "resourceNames": self.resource_names,
            }
        except (OSError, TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def create_state(self, file_label: str, template_state_id: Optional[int] = None) -> dict:
        """입력값을 파일명에만 사용해 빈 스테이트 파일을 만들고 등록한다."""
        if self.paths is None:
            return {"ok": False, "error": "맵을 먼저 로드해주세요."}
        if not self._lock.acquire(blocking=False):
            return {"ok": False, "error": "다른 맵 작업이 진행 중입니다. 잠시 후 다시 시도해주세요."}
        try:
            clean_label = str(file_label or "").strip()
            category = "rural"
            if template_state_id is not None:
                try:
                    template_id = int(template_state_id)
                    template = next(
                        (item for item in self.states if item.id == template_id), None
                    )
                    if template is not None:
                        inherited = read_state_properties(template.file_path).state_category
                        if inherited:
                            category = inherited
                except (OSError, TypeError, ValueError):
                    pass

            new_id = max((state.id for state in self.states), default=0) + 1
            created = create_state_files(
                self.paths.history_states_dir,
                new_id,
                clean_label,
                state_category=category,
            )
            state_info = StateInfo(
                id=created.state_id,
                file_path=created.state_file,
                name=created.localisation_key,
                province_ids=[],
            )
            self.states.append(state_info)
            self.states.sort(key=lambda item: item.id)
            return {
                "ok": True,
                "state": {
                    "id": state_info.id,
                    "name": state_info.name,
                    "fileName": os.path.basename(state_info.file_path),
                    "color": list(_state_color_from_id(state_info.id)),
                    "provinceCount": 0,
                },
                "stateCategory": category,
                "stateFile": created.state_file,
            }
        except (OSError, TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        finally:
            self._lock.release()

    def update_state_properties(
        self,
        state_id: int,
        manpower: int,
        state_category: str,
        resources: dict | None,
        local_supplies: float,
    ) -> dict:
        """스테이트 최상위 속성을 저장하되 history 내용은 그대로 보존한다."""
        try:
            sid = int(state_id)
            state = next((item for item in self.states if item.id == sid), None)
            if state is None:
                raise ValueError(f"존재하지 않는 스테이트 ID입니다: {sid}")
            category = str(state_category or "").strip()
            props = update_state_properties(
                state.file_path,
                manpower=int(manpower),
                state_category=category,
                resources=resources or {},
                local_supplies=float(local_supplies),
            )
            return {
                "ok": True,
                "stateId": sid,
                "manpower": props.manpower,
                "stateCategory": props.state_category,
                "resources": props.resources,
                "localSupplies": props.local_supplies,
            }
        except (OSError, TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def get_state_history_editor(self, state_id: int) -> dict:
        """Return editable state source and three closest pixel-adjacent examples."""
        if self.provinces_arr is None:
            return {"ok": False, "error": "맵을 먼저 로드해주세요."}
        try:
            sid = int(state_id)
            target = next((item for item in self.states if item.id == sid), None)
            if target is None:
                raise ValueError(f"존재하지 않는 스테이트 ID입니다: {sid}")
            connected_province_pairs: list[tuple[int, int]] = []
            adjacency_path = self._adjacencies_csv_path()
            if adjacency_path is not None:
                try:
                    connected_province_pairs = [
                        (adjacency.from_id, adjacency.to_id)
                        for adjacency in load_adjacencies(adjacency_path).items
                        if adjacency.type != "impassable"
                    ]
                except OSError:
                    connected_province_pairs = []
            ranked = rank_adjacent_states(
                self.provinces_arr,
                self.provinces,
                self.assignments,
                sid,
                connected_province_pairs=connected_province_pairs,
                limit=3,
            )
            state_by_id = {item.id: item for item in self.states}
            neighbours = []
            for ranked_neighbour in ranked:
                neighbour = state_by_id.get(ranked_neighbour.state_id)
                if neighbour is None:
                    continue
                if ranked_neighbour.relation == "border":
                    relation_detail = f"공유 경계 {ranked_neighbour.shared_edges}px"
                elif ranked_neighbour.relation == "connection":
                    relation_detail = (
                        f"연결 인접 {ranked_neighbour.connection_count}개"
                    )
                else:
                    relation_detail = (
                        f"가까운 스테이트 · 중심 거리 "
                        f"{round(ranked_neighbour.distance or 0)}px"
                    )
                neighbours.append({
                    "id": neighbour.id,
                    "name": neighbour.name,
                    "fileName": os.path.basename(neighbour.file_path),
                    "relation": ranked_neighbour.relation,
                    "relationDetail": relation_detail,
                    "source": read_state_source(neighbour.file_path),
                    "history": read_state_history_block(neighbour.file_path),
                })
            return {
                "ok": True,
                "stateId": target.id,
                "stateName": target.name,
                "fileName": os.path.basename(target.file_path),
                "source": read_state_source(target.file_path),
                "neighbours": neighbours,
            }
        except (OSError, TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def update_state_source(self, state_id: int, source: str) -> dict:
        """Replace one complete state file without changing the loaded map model."""
        try:
            sid = int(state_id)
            target = next((item for item in self.states if item.id == sid), None)
            if target is None:
                raise ValueError(f"존재하지 않는 스테이트 ID입니다: {sid}")
            saved = write_state_source(target.file_path, source)
            return {"ok": True, "stateId": sid, "source": saved}
        except (OSError, TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def assign_province_to_strategic_region(
        self, province_id: int, region_id: Optional[int]
    ) -> dict:
        """프로빈스의 전략구역 소속을 갱신하거나 해제한다."""
        try:
            previous_region_id = update_area_assignment(
                self.region_assignments,
                province_id,
                region_id,
                (region.id for region in self.regions),
                "전략구역",
            )
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        return {
            "ok": True,
            "provinceId": province_id,
            "previousStrategicRegionId": previous_region_id,
            "strategicRegionId": region_id,
        }

    def list_assignments(self) -> dict:
        """현재 매핑 전체 반환. 프론트엔드 뷰 동기화용."""
        return {
            "ok": True,
            "assignments": [[pid, sid] for pid, sid in self.assignments.items()],
        }

    # -------- 페인트통 ----------

    def flood_fill(self, x: int, y: int, rgb: list[int],
                   respect_lakes: bool, respect_sea: bool) -> dict:
        """클릭 지점과 4방향으로 연결된 같은 RGB 영역만 새 색으로 칠한다.

        호수/바다 보호 토글이 켜져 있고 시작 픽셀이 보호 대상이면 아무것도 하지 않는다.

        반환: changedPixels = [[x,y,oldR,oldG,oldB], ...]
        프론트엔드에서 이 리스트로 캔버스 갱신과 Undo 스택 등록을 한다.
        """
        if self.provinces_arr is None:
            return {"ok": False, "error": "맵이 로드되지 않았습니다."}

        try:
            target = province_color_at(self.provinces_arr, x, y)
            new_color = (int(rgb[0]), int(rgb[1]), int(rgb[2]))
        except (IndexError, TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        if target == new_color:
            return {"ok": True, "changedPixels": [], "applied": 0, "skipped": 0}

        protected = protected_province_colors(
            self.provinces, respect_lakes, respect_sea
        )
        if target in protected:
            return {
                "ok": True, "changedPixels": [], "applied": 0, "skipped": 1,
                "blockedByProtection": True,
            }

        try:
            _, changed_pixels = flood_fill_rgb_connected(
                self.provinces_arr, x, y, new_color
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

        # 사용된 색으로 등록
        if self.color_pool is not None:
            self.color_pool.add(new_color)

        # 부모 추적: flood fill은 모두 같은 target → new_color로 갈아탄 것
        if changed_pixels:
            bucket = self.parent_pixel_counts.setdefault(new_color, {})
            bucket[target] = bucket.get(target, 0) + len(changed_pixels)

        return {
            "ok": True,
            "changedPixels": changed_pixels,
            "applied": len(changed_pixels),
            "skipped": 0,
        }

    # -------- 인접 흡수(삭제) ----------

    def delete_province_at(
        self, x: int, y: int,
        respect_lakes: bool = True, respect_sea: bool = True,
    ) -> dict:
        """(x,y)의 프로빈스를 가장 큰 인접 프로빈스의 RGB로 통째 덮어씀.

        BMP에서 해당 RGB가 완전히 사라지면 다음 저장 시 자동으로
        analyze_for_save_v2가 removed로 잡아내 외부 파일 정리까지 일관 처리.

        반환:
          ok, absorbedIntoRgb, absorbedIntoProvinceId, changedPixels
        """
        if self.provinces_arr is None:
            return {"ok": False, "error": "맵이 로드되지 않았습니다."}

        arr = self.provinces_arr
        h, w = arr.shape[:2]
        if not (0 <= x < w and 0 <= y < h):
            return {"ok": False, "error": "범위 밖 좌표"}

        target = tuple(int(c) for c in arr[y, x].tolist())
        if target == (0, 0, 0):
            return {"ok": False, "error": "(0,0,0) invalid 슬롯은 삭제 대상이 아닙니다."}

        # 보호 토글: 호수/바다 프로빈스는 그 자체를 삭제 불가
        target_prov = next((p for p in self.provinces if p.rgb == target), None)
        if target_prov is not None:
            if respect_lakes and target_prov.type == "lake":
                return {
                    "ok": False,
                    "error": f"호수 보호: 프로빈스 ID {target_prov.id} (호수)는 삭제할 수 없습니다.",
                    "blockedByProtection": True,
                    "protectionType": "lake",
                }
            if respect_sea and target_prov.type == "sea":
                return {
                    "ok": False,
                    "error": f"바다 보호: 프로빈스 ID {target_prov.id} (바다)는 삭제할 수 없습니다.",
                    "blockedByProtection": True,
                    "protectionType": "sea",
                }

        # 흡수자 후보에서 제외할 protected RGB 집합
        protected: set[tuple[int, int, int]] = set()
        if respect_lakes:
            protected.update(p.rgb for p in self.provinces if p.type == "lake")
        if respect_sea:
            protected.update(p.rgb for p in self.provinces if p.type == "sea")

        absorber = find_best_absorber_rgb(arr, target, protected)
        if absorber is None:
            return {
                "ok": False,
                "error": "흡수할 인접 프로빈스를 찾지 못했습니다. "
                         "(보호된 색만 인접해 있거나 단독 영역일 수 있음)",
            }

        changes = absorb_province(arr, target, absorber)
        if not changes:
            return {"ok": False, "error": "변경된 픽셀이 없습니다."}

        # 부모 추적: target → absorber로 잡아먹힌 것으로 카운트
        # (저장 시 _rebuild_parent_counts_from_disk가 다시 만들지만, 임시 캐시도 갱신)
        bucket = self.parent_pixel_counts.setdefault(absorber, {})
        bucket[target] = bucket.get(target, 0) + len(changes)

        absorber_prov = next((p for p in self.provinces if p.rgb == absorber), None)

        return {
            "ok": True,
            "absorbedIntoRgb": list(absorber),
            "absorbedIntoProvinceId": (absorber_prov.id if absorber_prov else None),
            "deletedRgb": list(target),
            "deletedProvinceId": (target_prov.id if target_prov else None),
            "changedPixels": changes,
            "pixelCount": len(changes),
        }

    # -------- 카운터 ----------

    def get_live_province_count(self) -> dict:
        """현재 BMP에 살아있는 unique RGB 수(= 다음 저장 후 프로빈스 수).

        (0,0,0) invalid 슬롯은 제외. provinces.length가 아니라 BMP 기준이라
        편집 중 새로 생성됐지만 미저장된 RGB도 함께 집계된다.
        """
        if self.provinces_arr is None:
            return {"ok": False, "error": "맵이 로드되지 않았습니다."}
        used = find_used_colors(self.provinces_arr)
        used.discard((0, 0, 0))
        count = len(used)
        return {
            "ok": True,
            "liveCount": count,
            "definitionCount": len(self.provinces),
        }

    def move_provinces(self, province_ids: list[int], dx: int, dy: int) -> dict:
        """Translate all pixels belonging to the selected province IDs."""
        if self.provinces_arr is None:
            return {"ok": False, "error": "맵이 로드되지 않았습니다."}

        requested_ids = {int(province_id) for province_id in province_ids}
        by_id = {province.id: province for province in self.provinces}
        missing = sorted(requested_ids - set(by_id))
        if missing:
            return {
                "ok": False,
                "error": f"definition.csv에 없는 프로빈스 ID입니다: {missing[0]}",
            }
        selected_rgbs = [by_id[province_id].rgb for province_id in requested_ids]
        try:
            result = move_province_group(
                self.provinces_arr, selected_rgbs, int(dx), int(dy)
            )
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

        return {
            "ok": True,
            "changedPixels": result["changes"],
            "selectedPixelCount": result["selectedPixelCount"],
            "bounds": result["bounds"],
            "dx": result["dx"],
            "dy": result["dy"],
        }

    # -------- 자동 분할 ----------

    def split_province_at(self, x: int, y: int, avg_pixels: int,
                          min_pixels: int | None = None,
                          noise_strength: float = 0.5,
                          respect_lakes: bool = True,
                          respect_sea: bool = False) -> dict:
        """주어진 픽셀이 속한 프로빈스(같은 RGB 영역)를 자동 분할.

        반환:
          ok, splitCount, mergedCount, totalPixels, seedCount, minPixels,
          changedPixels: [[x, y, oR, oG, oB, nR, nG, nB], ...]
            (프론트가 화면/Undo 등록에 사용)
        """
        if self.provinces_arr is None:
            return {"ok": False, "error": "맵이 로드되지 않았습니다."}
        arr = self.provinces_arr
        h, w = arr.shape[:2]
        if not (0 <= x < w and 0 <= y < h):
            return {"ok": False, "error": "범위 밖 좌표"}

        target = tuple(int(c) for c in arr[y, x].tolist())
        if target == (0, 0, 0):
            return {"ok": False, "error": "(0,0,0) invalid 슬롯은 분할 대상이 아닙니다."}

        # 보호 토글: 호수/바다 RGB는 분할하지 않음
        target_prov = next((p for p in self.provinces if p.rgb == target), None)
        if target_prov is not None:
            if respect_lakes and target_prov.type == "lake":
                return {
                    "ok": False,
                    "error": f"호수 보호: 프로빈스 ID {target_prov.id} (호수)는 분할할 수 없습니다. "
                             "툴바의 호수 보호 토글을 끄면 분할 가능합니다.",
                    "blockedByProtection": True,
                    "protectionType": "lake",
                }
            if respect_sea and target_prov.type == "sea":
                return {
                    "ok": False,
                    "error": f"바다 보호: 프로빈스 ID {target_prov.id} (바다)는 분할할 수 없습니다. "
                             "툴바의 바다 보호 토글을 끄면 분할 가능합니다.",
                    "blockedByProtection": True,
                    "protectionType": "sea",
                }

        try:
            ns = float(noise_strength)
        except (TypeError, ValueError):
            ns = 0.5
        result = split_region(
            arr, target, int(avg_pixels),
            min_pixels=min_pixels,
            noise_strength=ns,
        )
        if not result.get("ok"):
            return result

        labels: np.ndarray = result["labels"]
        K = int(result["label_count"])
        if K <= 1:
            return {
                "ok": True,
                "splitCount": 0,
                "mergedCount": int(result["mergedCount"] if "mergedCount" in result else result.get("merged_count", 0)),
                "totalPixels": 0,
                "changedPixels": [],
                "message": "더 이상 쪼갤 수 없는 크기입니다.",
            }

        # 각 라벨에 새 RGB 할당.
        # 라벨 0은 원본 RGB 유지, 1..K-1은 ColorPool에서 새 색 발급.
        # 이렇게 하면 변경 픽셀 수가 최소화되고, "원본도 부모로 인식"됨.
        if self.color_pool is None:
            return {"ok": False, "error": "ColorPool 미초기화."}

        label_to_rgb: dict[int, tuple[int, int, int]] = {0: target}
        for L in range(1, K):
            label_to_rgb[L] = self.color_pool.pick_new()

        # 변경 픽셀 추출: 라벨 0이 아닌 픽셀들 (라벨 0은 원본 RGB라 변화 없음)
        # NumPy로 빠르게: 마스크 = labels >= 1
        change_mask = labels >= 1
        ys, xs = np.where(change_mask)
        if len(xs) == 0:
            return {
                "ok": True,
                "splitCount": K,
                "mergedCount": int(result.get("merged_count", 0)),
                "totalPixels": int(result.get("areas", [0])[0]),
                "changedPixels": [],
                "message": "분할 결과가 모두 원본 라벨(0)에 합쳐졌습니다.",
            }

        # 라벨별로 RGB 적용 + changes 리스트 작성
        changes: list[list[int]] = []
        new_rgb_t_set: set[tuple[int, int, int]] = set()
        # 픽셀 단위 루프는 비싸지만 수만~수십만 정도라 OK.
        # 더 빠르게 하려면 라벨별 슬라이스로 처리 가능.
        labels_at = labels[ys, xs]
        old_pixels = arr[ys, xs].copy()  # 모두 target과 같음
        new_pixels = np.zeros((len(xs), 3), dtype=np.uint8)
        for L in range(1, K):
            mask_L = labels_at == L
            if not mask_L.any():
                continue
            rgb_L = label_to_rgb[L]
            new_pixels[mask_L] = rgb_L
            new_rgb_t_set.add(rgb_L)

        # 백엔드 ndarray 갱신
        arr[ys, xs] = new_pixels

        # 부모 추적: 모든 새 RGB는 target에서 갈라져 나옴.
        # 라벨별 픽셀 수만큼 (target → new_rgb)로 잡아먹힌 것으로 카운트.
        for L in range(1, K):
            mask_L = labels_at == L
            cnt = int(mask_L.sum())
            if cnt == 0:
                continue
            rgb_L = label_to_rgb[L]
            bucket = self.parent_pixel_counts.setdefault(rgb_L, {})
            bucket[target] = bucket.get(target, 0) + cnt

        # changes 리스트 작성 (Python 리스트 변환)
        xs_list = xs.tolist()
        ys_list = ys.tolist()
        old_list = old_pixels.tolist()
        new_list = new_pixels.tolist()
        for i in range(len(xs_list)):
            o = old_list[i]
            n = new_list[i]
            changes.append([
                int(xs_list[i]), int(ys_list[i]),
                int(o[0]), int(o[1]), int(o[2]),
                int(n[0]), int(n[1]), int(n[2]),
            ])

        return {
            "ok": True,
            "splitCount": K,
            "mergedCount": int(result.get("merged_count", 0)),
            "seedCount": int(result.get("seed_count", 0)),
            "minPixels": int(result.get("min_pixels", 0)),
            "totalPixels": int(sum(result.get("areas", []))),
            "changedPixels": changes,
        }

    # -------- 저장 ----------

    def preview_save(self, default_state_id: Optional[int] = None) -> dict:
        """저장 전 변화 요약: 새 프로빈스 후보 / 사라진 프로빈스 / 추론된 속성."""
        if self.provinces_arr is None or self.paths is None:
            return {"ok": False, "error": "맵이 로드되지 않았습니다."}

        # 부모 추적을 디스크 BMP 비교로 재구성 (중간 임시 RGB 노이즈 제거)
        self._rebuild_parent_counts_from_disk()

        new_rgbs, removed = analyze_for_save(
            self.provinces_arr, self.terrain_arr, self.provinces, self.terrain_categories
        )
        # 미리보기에서도 부모 상속 결과(특히 continent)를 정확히 반영
        _by_rgb_preview = {p.rgb: p for p in self.provinces}
        def _resolver_preview(new_rgb_tuple):
            return self._pick_parent_province(new_rgb_tuple, _by_rgb_preview)
        new_provs_preview = build_new_provinces(
            self.provinces_arr, self.terrain_arr, new_rgbs,
            self.provinces, self.terrain_categories,
            parent_rgb_resolver=_resolver_preview,
        )
        return {
            "ok": True,
            "terrainDirty": self.terrain_dirty,
            "heightmapDirty": self.heightmap_dirty,
            "riversDirty": self.rivers_dirty,
            "supplyDirty": self.supply_dirty,
            "newProvinces": [
                {
                    "id": p.id,
                    "rgb": list(p.rgb),
                    "type": p.type,
                    "terrain": p.terrain,
                    "continent": p.continent,
                    "coastal": p.coastal,
                }
                for p in new_provs_preview
            ],
            "removedProvinces": [
                {"id": p.id, "rgb": list(p.rgb), "type": p.type}
                for p in removed
            ],
            "states": [
                {"id": s.id, "name": s.name} for s in self.states
            ],
        }

    def commit_save(
        self,
        type_overrides: dict | None,  # {"r,g,b": "land"/"sea"/"lake"}
        state_assignments: dict | None,  # {"r,g,b": state_id_or_null}
    ) -> dict:
        """저장 커밋. provinces.bmp + definition.csv + state/region 일괄 갱신."""
        if self.provinces_arr is None or self.paths is None:
            return {"ok": False, "error": "맵이 로드되지 않았습니다."}

        type_overrides = type_overrides or {}
        state_assignments = state_assignments or {}

        try:
            # 부모 추적을 디스크 BMP 비교로 재구성.
            # 메모리에 누적된 카운터(중간 임시 RGB 포함)를 무시하고,
            # 디스크 RGB → 현재 RGB 직결 매핑만 반영해 정확한 부모를 식별한다.
            self._rebuild_parent_counts_from_disk()

            new_rgbs, removed = analyze_for_save(
                self.provinces_arr, self.terrain_arr, self.provinces, self.terrain_categories
            )
            override_map: dict[tuple[int, int, int], str] = {}
            for key, val in type_overrides.items():
                try:
                    parts = [int(x) for x in key.split(",")]
                    if len(parts) == 3 and val in ("land", "sea", "lake"):
                        override_map[(parts[0], parts[1], parts[2])] = val
                except Exception:
                    continue

            # 부모 리졸버: 새 프로빈스의 continent도 부모 상속으로 결정하기 위해 사용.
            # existing_by_rgb는 "새로 만들어진 것 제외한" 기존 프로빈스 매핑.
            _existing_by_rgb_for_build = {p.rgb: p for p in self.provinces}

            def _resolver_for_build(new_rgb_tuple):
                return self._pick_parent_province(new_rgb_tuple, _existing_by_rgb_for_build)

            new_provs = build_new_provinces(
                self.provinces_arr, self.terrain_arr, new_rgbs,
                self.provinces, self.terrain_categories,
                province_type_overrides=override_map,
                parent_rgb_resolver=_resolver_for_build,
            )

            removed_ids = {p.id for p in removed}

            if self.supply_dirty:
                supply_validation = self.validate_supply_network()
                if not supply_validation.get("valid", False):
                    raise ValueError(
                        f"보급망 규칙 위반 {len(supply_validation.get('issues', []))}건을 먼저 수정하세요."
                    )

            # 1) provinces.bmp / terrain.bmp 저장
            write_provinces_bmp(self.provinces_arr, self.paths.provinces_bmp)
            terrain_saved = False
            if self.terrain_dirty:
                if self.terrain_arr is None or self.terrain_palette is None:
                    raise ValueError("편집한 terrain.bmp의 인덱스 팔레트 정보가 없습니다.")
                write_terrain_bmp(
                    self.terrain_arr,
                    self.paths.terrain_bmp,
                    self.terrain_palette,
                )
                terrain_saved = True
            heightmap_saved = False
            if self.heightmap_dirty:
                if self.heightmap_arr is None:
                    raise ValueError("편집한 heightmap.bmp 데이터가 없습니다.")
                write_heightmap_bmp(self.heightmap_arr, self.paths.heightmap_bmp)
                heightmap_saved = True
            rivers_saved = False
            if self.rivers_dirty:
                if self.rivers_arr is None or self.rivers_palette is None:
                    raise ValueError("편집한 rivers.bmp의 인덱스 팔레트 정보가 없습니다.")
                write_rivers_bmp(
                    self.rivers_arr,
                    self.paths.rivers_bmp,
                    self.rivers_palette,
                )
                rivers_saved = True
            supply_saved = False
            if self.supply_dirty:
                write_supply_nodes(self.paths.supply_nodes_txt, self.supply_nodes)
                write_railways(self.paths.railways_txt, self.railways)
                supply_saved = True

            # 2) definition.csv: 새 항목 + 삭제 반영
            all_provs = [p for p in self.provinces if p.id not in removed_ids] + new_provs
            write_definition_csv(self.paths.definition_csv, all_provs, set())
            self.provinces = all_provs

            # 3) 상태 파일 업데이트
            modified_state_files: list[str] = []
            modified_region_files: list[str] = []

            # 인접 정보 (전략구역 자동 할당용)
            adjacency = find_adjacent_colors(
                self.provinces_arr, {p.rgb for p in new_provs}
            )
            province_by_rgb = {p.rgb: p for p in self.provinces}

            # === 부모 기반 상속 ===
            # 새 프로빈스마다 (가장 많이 잡아먹은 옛 RGB) → 그 부모 프로빈스 → state/region 상속
            # 부모를 못 찾으면 인접 기반으로 폴백.
            _new_ids = {nprov.id for nprov in new_provs}
            existing_by_rgb_for_inherit = {
                p.rgb: p for p in self.provinces if p.id not in _new_ids
            }

            def _resolve_parent(new_rgb_tuple):
                return self._pick_parent_province(new_rgb_tuple, existing_by_rgb_for_inherit)

            # 새 프로빈스에 대해 다이얼로그로 명시한 매핑이 있으면 self.assignments에 반영,
            # 없으면 부모 프로빈스의 state를 자동 상속
            for p in new_provs:
                key = f"{p.r},{p.g},{p.b}"
                state_id = state_assignments.get(key)
                if state_id is not None:
                    try:
                        sid = int(state_id)
                        if any(s.id == sid for s in self.states):
                            self.assignments[p.id] = sid
                            continue
                    except (TypeError, ValueError):
                        pass
                # 다이얼로그 지정 없음 → 사용자가 [스테이트 할당] 탭에서 미리 정한 것이 있을 수 있음
                if p.id in self.assignments:
                    continue
                # 그도 없음 → 부모 프로빈스의 state 상속
                parent = _resolve_parent(p.rgb)
                if parent is not None:
                    parent_state = self.assignments.get(parent.id)
                    if parent_state is not None:
                        self.assignments[p.id] = parent_state

            # self.assignments 전체를 기준으로 각 state 파일을 재구성
            # state별 현재 매핑 → 비교해서 (추가/제거) 산출
            for state in self.states:
                add, remove = area_file_changes(
                    state, self.assignments, removed_ids
                )
                if add or remove:
                    changed = update_state_file(state, add, remove)
                    if changed:
                        modified_state_files.append(state.file_path)

            # 새 프로빈스의 전략구역은 부모 기반 우선, 인접 기반 폴백으로 상속한다.
            # 기존 프로빈스는 편집기에서 만든 region_assignments를 그대로 보존한다.
            for p in new_provs:
                if p.type not in ("land", "sea", "lake"):
                    continue
                region_id = None
                parent = _resolve_parent(p.rgb)
                if parent is not None:
                    region_id = self.region_assignments.get(parent.id)

                if region_id is None:
                    region_id = pick_neighbor_area_id(
                        p.rgb,
                        adjacency,
                        province_by_rgb,
                        self.region_assignments,
                    )
                if region_id is not None:
                    self.region_assignments[p.id] = region_id

            # 수동 이동·할당 해제와 신규 상속 결과를 모든 전략구역 파일에 동기화한다.
            for region in self.regions:
                add, remove = area_file_changes(
                    region, self.region_assignments, removed_ids
                )
                if add or remove:
                    changed = update_strategic_region_file(region, add, remove)
                    if changed and region.file_path not in modified_region_files:
                        modified_region_files.append(region.file_path)

            # === 외부 파일 일괄 갱신 (인접 흡수 매핑 기반) ===
            # 사라진 RGB가 흡수된 자리를 기반으로 외부 파일들의 prov_id를 재매핑한다.
            # buildings / unitstacks / positions / weatherpositions: 단순 제거
            # supply_nodes / railways / adjacencies / state(victory_points,buildings) /
            # history/units(location) / decisions(set_province_name): 흡수자로 재매핑
            external_summary = {"modifiedFiles": [], "totalRemovedLines": 0,
                                "totalRemappedTokens": 0, "perFile": {}}
            if removed_ids:
                # disk_provinces_arr 는 이 함수 진입 시점의 디스크 스냅샷이고,
                # write_provinces_bmp() 직후 self.provinces_arr 가 새 디스크 상태이므로
                # 흡수 매핑은 (옛 디스크 vs 현재 메모리)로 계산해야 정확하다.
                # 단, 이 시점 self.disk_provinces_arr는 아직 옛 디스크라 OK.
                # provinces 인자는 새 ID가 부여된 all_provs(=self.provinces)로 전달해야
                # 흡수자 RGB → ID 환산이 신규 프로빈스에도 동작.
                absorption_map = compute_absorption_map(
                    self.disk_provinces_arr, self.provinces_arr, self.provinces
                )
                if absorption_map:
                    try:
                        external_summary = apply_absorption_to_all(
                            self.paths.map_dir, self.paths.mod_root, absorption_map
                        )
                    except Exception as exc:
                        traceback.print_exc()
                        external_summary = {
                            "error": str(exc),
                            "modifiedFiles": [],
                            "totalRemovedLines": 0,
                            "totalRemappedTokens": 0,
                            "perFile": {},
                        }

            # color pool 갱신
            self.color_pool = ColorPool(p.rgb for p in self.provinces)

            # 부모 카운터 리셋 (저장 완료된 프로빈스의 부모 기록은 더 이상 의미 없음)
            self.parent_pixel_counts = {}

            # 디스크 BMP 백업을 현재 상태로 갱신.
            # 다음 저장 사이클에서 (현재 vs 디스크) 비교의 기준점이 된다.
            self.disk_provinces_arr = self.provinces_arr.copy()

            # X-crossing 전체 스캔 (저장 시점)
            xcoords = find_all_xcrossings(self.provinces_arr, max_results=2000)

            # buildings.txt 자동 추가는 의도적으로 제거됨.
            # 게임이 buildings를 자동 관리하므로 손대지 않는 것이 안전함.

            # 라이브 프로빈스 카운트 (한도 카운터 갱신용)
            live_used = find_used_colors(self.provinces_arr)
            live_used.discard((0, 0, 0))
            live_count = len(live_used)
            if terrain_saved:
                self.terrain_dirty = False
            if heightmap_saved:
                self.heightmap_dirty = False
            if rivers_saved:
                self.rivers_dirty = False
            if supply_saved:
                self.supply_dirty = False

            return {
                "ok": True,
                "provincesBmp": self.paths.provinces_bmp,
                "terrainBmp": self.paths.terrain_bmp if terrain_saved else None,
                "terrainSaved": terrain_saved,
                "heightmapBmp": self.paths.heightmap_bmp if heightmap_saved else None,
                "heightmapSaved": heightmap_saved,
                "riversBmp": self.paths.rivers_bmp if rivers_saved else None,
                "riversSaved": rivers_saved,
                "supplyNodesTxt": self.paths.supply_nodes_txt if supply_saved else None,
                "railwaysTxt": self.paths.railways_txt if supply_saved else None,
                "supplySaved": supply_saved,
                "definitionCsv": self.paths.definition_csv,
                "newProvinceCount": len(new_provs),
                "removedProvinceCount": len(removed_ids),
                "modifiedStateFiles": modified_state_files,
                "modifiedRegionFiles": modified_region_files,
                "assignments": [
                    [pid, state_id]
                    for pid, state_id in self.assignments.items()
                    if pid not in removed_ids
                ],
                "regionAssignments": [
                    [pid, region_id]
                    for pid, region_id in self.region_assignments.items()
                    if pid not in removed_ids
                ],
                "modifiedExternalFiles": external_summary.get("modifiedFiles", []),
                "externalSummary": external_summary,
                "liveProvinceCount": live_count,
                "definitionProvinceCount": len(self.provinces),
                "xcrossings": [[x, y] for x, y in xcoords],
                "xcrossingCount": len(xcoords),
            }
        except Exception as exc:
            traceback.print_exc()
            return {"ok": False, "error": str(exc), "trace": traceback.format_exc()}

    # =====================================================================
    # 최소침습 ID 병합 (감독자 모드)
    # =====================================================================
    # 사용 시나리오:
    #   1) UI에서 "ID 갭 분석" 버튼 → scan_placeholder_ids() 호출
    #      → 어떤 placeholder가 있고, 최소침습 계획상 어떤 매핑이 될지 미리보기 반환
    #   2) UI에서 "검색" 버튼 → search_id_usages(ids) 호출
    #      → 매핑 대상 ID들이 모드 폴더 어디에 등장하는지 매치 리스트 반환
    #   3) 사용자가 매치별 Yes/No 선택
    #   4) UI에서 "실행(드라이런)" → apply_min_invasive_compaction(..., dry_run=True)
    #   5) 문제 없으면 "실행(실제)" → apply_min_invasive_compaction(..., dry_run=False)
    # 자동화 금지 원칙: 4번 매치 승인 단계는 반드시 사용자 손을 거쳐야 한다.

    def scan_placeholder_ids(self) -> dict:
        """definition.csv에서 placeholder 행을 찾고 최소침습 매핑 미리보기 반환."""
        if self.paths is None:
            return {"ok": False, "error": "맵이 로드되지 않았습니다."}
        try:
            placeholder_ids = find_placeholder_ids(self.paths.definition_csv)
            plan = build_min_invasive_plan(self.paths.definition_csv)
            return {
                "ok": True,
                "placeholderIds": placeholder_ids,
                "plan": plan.to_dict(),
                # UI 편의를 위해 매핑을 (old, new) 튜플 리스트로도 제공
                "movePairs": sorted(
                    [[old, new] for old, new in plan.id_map.items()],
                    key=lambda kv: kv[1],
                ),
            }
        except Exception as exc:
            traceback.print_exc()
            return {"ok": False, "error": str(exc), "trace": traceback.format_exc()}

    def search_id_usages(self, ids: list[int]) -> dict:
        """주어진 ID들이 모드 폴더 안에서 등장하는 모든 위치 반환.

        ids: 검색할 ID 정수 리스트. 보통 movePairs의 old_id들.
        """
        if self.paths is None:
            return {"ok": False, "error": "맵이 로드되지 않았습니다."}
        try:
            int_ids = [int(i) for i in ids if int(i) > 0]
            if not int_ids:
                return {"ok": True, "matches": []}
            matches = search_ids_in_mod(
                self.paths.mod_root, int_ids, SearchConfig(),
            )
            return {
                "ok": True,
                "matches": [m.to_dict() for m in matches],
                "matchCount": len(matches),
            }
        except Exception as exc:
            traceback.print_exc()
            return {"ok": False, "error": str(exc), "trace": traceback.format_exc()}

    def apply_min_invasive_compaction(
        self, approved_matches: list[dict], dry_run: bool = False,
    ) -> dict:
        """사용자가 승인한 매치만 새 ID로 치환하고 definition.csv 재작성.

        approved_matches: search_id_usages()가 반환한 dict 형태 매치들 중 사용자가
        Yes로 표시한 것들. 각 dict는 filePath/relPath/lineNo/lineText/matchedId/
        colStart/colEnd 필드를 가진다.

        dry_run=True: 실제 파일은 안 건드리고 영향 범위만 리포트.
        dry_run=False: definition.csv 재작성 + 외부 파일 치환 적용.
        """
        if self.paths is None:
            return {"ok": False, "error": "맵이 로드되지 않았습니다."}
        try:
            plan = build_min_invasive_plan(self.paths.definition_csv)

            # dict → IdMatch 객체 변환
            id_match_objs: list[IdMatch] = []
            for d in approved_matches or []:
                try:
                    id_match_objs.append(IdMatch(
                        file_path=str(d["filePath"]),
                        rel_path=str(d.get("relPath", "")),
                        line_no=int(d["lineNo"]),
                        line_text=str(d.get("lineText", "")),
                        matched_id=int(d["matchedId"]),
                        col_start=int(d.get("colStart", 0)),
                        col_end=int(d.get("colEnd", 0)),
                    ))
                except (KeyError, TypeError, ValueError):
                    continue

            apply_report = apply_compaction(plan, id_match_objs, dry_run=dry_run)

            if not dry_run:
                # definition.csv 재작성 (placeholder 제거 + mover 이동)
                rewrite_definition_csv(plan, self.paths.definition_csv)
                # 메모리상 self.provinces도 갱신
                self.provinces = list(plan.new_provinces)

            return {
                "ok": True,
                "dryRun": dry_run,
                "plan": plan.to_dict(),
                "report": apply_report.to_dict(),
            }
        except Exception as exc:
            traceback.print_exc()
            return {"ok": False, "error": str(exc), "trace": traceback.format_exc()}

    # =====================================================================
    # adjacencies.csv 편집 (인접 연결 모드)
    # =====================================================================

    def _adjacencies_csv_path(self) -> Optional[str]:
        if self.paths is None:
            return None
        return os.path.join(self.paths.map_dir, "adjacencies.csv")

    def list_adjacencies(self) -> dict:
        """현재 adjacencies.csv 의 모든 항목 반환 (index 포함)."""
        path = self._adjacencies_csv_path()
        if path is None:
            return {"ok": False, "error": "맵이 로드되지 않았습니다."}
        try:
            af = load_adjacencies(path)
            return {
                "ok": True,
                "items": [{"index": i, **adj.to_dict()} for i, adj in enumerate(af.items)],
                "count": len(af.items),
            }
        except Exception as exc:
            traceback.print_exc()
            return {"ok": False, "error": str(exc), "trace": traceback.format_exc()}

    def list_adjacency_rules(self) -> dict:
        """map/adjacency_rules.txt 에서 정의된 rule 이름 목록 반환.

        모더가 모드 폴더 안에 새 rule을 추가하면 자동으로 드롭다운에 반영된다.
        """
        if self.paths is None:
            return {"ok": False, "error": "맵이 로드되지 않았습니다."}
        try:
            rules_path = os.path.join(self.paths.map_dir, "adjacency_rules.txt")
            names = load_adjacency_rule_names(rules_path)
            return {"ok": True, "names": names, "path": rules_path}
        except Exception as exc:
            traceback.print_exc()
            return {"ok": False, "error": str(exc), "trace": traceback.format_exc()}

    def add_adjacency(
        self,
        from_id: int,
        to_id: int,
        type_: str = "",
        through: int = -1,
        rule_name: str = "",
        comment: str = "",
    ) -> dict:
        """인접 항목을 추가하고 adjacencies.csv를 즉시 저장.

        UI에서 시각 좌표(start_x/y/stop_x/stop_y)는 -1로 둔다(엔진이 자동 결정).
        """
        path = self._adjacencies_csv_path()
        if path is None:
            return {"ok": False, "error": "맵이 로드되지 않았습니다."}
        try:
            adj = Adjacency(
                from_id=int(from_id),
                to_id=int(to_id),
                type=(type_ or "").strip(),
                through=int(through) if through is not None else -1,
                rule_name=(rule_name or "").strip(),
                comment=sanitize_comment(comment or ""),
            )
            err = validate_adjacency(adj, allow_existing=False)
            if err:
                return {"ok": False, "error": err}
            af = load_adjacencies(path)
            err = adj_add(af, adj)
            if err:
                return {"ok": False, "error": err}
            save_adjacencies(af)
            return {
                "ok": True,
                "added": adj.to_dict(),
                "index": len(af.items) - 1,
                "count": len(af.items),
            }
        except Exception as exc:
            traceback.print_exc()
            return {"ok": False, "error": str(exc), "trace": traceback.format_exc()}

    def delete_adjacency(self, index: int) -> dict:
        """주어진 index의 인접 항목 삭제."""
        path = self._adjacencies_csv_path()
        if path is None:
            return {"ok": False, "error": "맵이 로드되지 않았습니다."}
        try:
            af = load_adjacencies(path)
            err = adj_delete(af, int(index))
            if err:
                return {"ok": False, "error": err}
            save_adjacencies(af)
            return {"ok": True, "count": len(af.items)}
        except Exception as exc:
            traceback.print_exc()
            return {"ok": False, "error": str(exc), "trace": traceback.format_exc()}

    def update_adjacency(
        self,
        index: int,
        from_id: int,
        to_id: int,
        type_: str = "",
        through: int = -1,
        rule_name: str = "",
        comment: str = "",
    ) -> dict:
        """기존 인접 항목을 새 값으로 교체."""
        path = self._adjacencies_csv_path()
        if path is None:
            return {"ok": False, "error": "맵이 로드되지 않았습니다."}
        try:
            new_adj = Adjacency(
                from_id=int(from_id),
                to_id=int(to_id),
                type=(type_ or "").strip(),
                through=int(through) if through is not None else -1,
                rule_name=(rule_name or "").strip(),
                comment=sanitize_comment(comment or ""),
            )
            err = validate_adjacency(new_adj, allow_existing=True)
            if err:
                return {"ok": False, "error": err}
            af = load_adjacencies(path)
            err = adj_update(af, int(index), new_adj)
            if err:
                return {"ok": False, "error": err}
            save_adjacencies(af)
            return {
                "ok": True,
                "updated": new_adj.to_dict(),
                "index": int(index),
                "count": len(af.items),
            }
        except Exception as exc:
            traceback.print_exc()
            return {"ok": False, "error": str(exc), "trace": traceback.format_exc()}

    def get_province_centroids(self) -> dict:
        """모든 프로빈스의 중심점(픽셀 평균 좌표) 매핑 반환.

        반환 형식: {provinceId: [cx, cy], ...}
        영구 인접 선의 양 끝점을 그리는 데 사용. 한 번 계산 후 프론트엔드에서 캐싱.
        """
        if self.provinces_arr is None or self.paths is None:
            return {"ok": False, "error": "맵이 로드되지 않았습니다."}
        try:
            arr = self.provinces_arr
            # RGB → 24bit packed
            packed = (
                arr[..., 0].astype(np.int64) << 16
                | arr[..., 1].astype(np.int64) << 8
                | arr[..., 2].astype(np.int64)
            )
            flat = packed.reshape(-1)
            h, w = arr.shape[:2]
            ys, xs = np.divmod(np.arange(flat.size, dtype=np.int64), w)

            # 각 RGB(packed)별 픽셀 합과 카운트
            unique_packed, inverse = np.unique(flat, return_inverse=True)
            counts = np.bincount(inverse)
            sum_x = np.bincount(inverse, weights=xs.astype(np.float64))
            sum_y = np.bincount(inverse, weights=ys.astype(np.float64))
            cx = sum_x / counts
            cy = sum_y / counts

            # packed RGB → 프로빈스 ID
            rgb_to_id: dict[tuple[int, int, int], int] = {
                p.rgb: p.id for p in self.provinces
            }
            out: dict[int, list[int]] = {}
            for i, pk in enumerate(unique_packed.tolist()):
                r = (pk >> 16) & 0xFF
                g = (pk >> 8) & 0xFF
                b = pk & 0xFF
                pid = rgb_to_id.get((r, g, b))
                if pid is None:
                    continue
                out[pid] = [int(round(cx[i])), int(round(cy[i]))]
            return {"ok": True, "centroids": out}
        except Exception as exc:
            traceback.print_exc()
            return {"ok": False, "error": str(exc), "trace": traceback.format_exc()}

    def get_province_rgb(self, province_id: int) -> dict:
        """프로빈스 ID로 RGB 조회 (캔버스 마스크 색칠용)."""
        if self.provinces is None:
            return {"ok": False, "error": "맵이 로드되지 않았습니다."}
        try:
            for p in self.provinces:
                if p.id == int(province_id):
                    return {"ok": True, "rgb": [p.r, p.g, p.b]}
            return {"ok": False, "error": f"ID {province_id} 없음"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def pick_through_province(self, from_id: int, to_id: int) -> dict:
        """From과 To 사이의 선분이 지나가는 물(sea/lake) 프로빈스 중 가장 길게 통과한 ID.

        알고리즘:
          1) 두 프로빈스의 centroid를 구해 그 사이 선분을 따라 균등 샘플링.
          2) 각 샘플 픽셀의 RGB → province type 매핑.
          3) From/To 제외, type in {sea, lake} 만 카운트해 1위 반환.
          4) 후보 없으면 through=-1 로 폴백.

        반환: {ok, through: int (또는 -1), via: 'sea'|'lake'|None, candidates: [...]}
        """
        if self.provinces_arr is None or self.provinces is None:
            return {"ok": False, "error": "맵이 로드되지 않았습니다."}
        try:
            arr = self.provinces_arr
            h, w = arr.shape[:2]
            by_id: dict[int, "Province"] = {p.id: p for p in self.provinces}
            by_rgb: dict[tuple[int, int, int], "Province"] = {p.rgb: p for p in self.provinces}

            f_id, t_id = int(from_id), int(to_id)
            if f_id not in by_id or t_id not in by_id:
                return {"ok": False, "error": "프로빈스 ID 없음"}
            if f_id == t_id:
                return {"ok": False, "error": "From과 To가 같습니다."}

            # 두 프로빈스의 centroid (간단 계산: 해당 RGB 픽셀의 평균 좌표)
            def centroid_of(p):
                mask = (arr[..., 0] == p.r) & (arr[..., 1] == p.g) & (arr[..., 2] == p.b)
                if not mask.any():
                    return None
                ys, xs = np.where(mask)
                return (float(xs.mean()), float(ys.mean()))

            c1 = centroid_of(by_id[f_id])
            c2 = centroid_of(by_id[t_id])
            if c1 is None or c2 is None:
                return {"ok": True, "through": -1, "via": None, "candidates": []}

            # 선분 따라 균등 샘플 — 거리에 비례한 점 개수(최소 64, 최대 2000)
            dx = c2[0] - c1[0]
            dy = c2[1] - c1[1]
            dist = float(np.hypot(dx, dy))
            n_samples = int(max(64, min(2000, round(dist * 2))))
            ts = np.linspace(0.0, 1.0, n_samples)
            xs = np.clip(np.round(c1[0] + dx * ts).astype(np.int64), 0, w - 1)
            ys = np.clip(np.round(c1[1] + dy * ts).astype(np.int64), 0, h - 1)
            samples = arr[ys, xs]   # (n_samples, 3)

            # 픽셀 RGB → 24bit packed → unique count
            packed = (samples[..., 0].astype(np.int64) << 16) | (samples[..., 1].astype(np.int64) << 8) | samples[..., 2].astype(np.int64)
            unique, counts = np.unique(packed, return_counts=True)

            # 빈도 내림차순으로 sea/lake 후보 추출 (From/To는 제외)
            order = np.argsort(-counts)
            candidates: list[dict] = []
            best_id = -1
            best_via = None
            for i in order.tolist():
                pk = int(unique[i])
                rgb = ((pk >> 16) & 0xFF, (pk >> 8) & 0xFF, pk & 0xFF)
                prov = by_rgb.get(rgb)
                if prov is None:
                    continue
                if prov.id == f_id or prov.id == t_id:
                    continue
                if prov.type not in ("sea", "lake"):
                    continue
                candidates.append({"id": prov.id, "type": prov.type, "pixels": int(counts[i])})
                if best_id == -1:
                    best_id = prov.id
                    best_via = prov.type
            return {
                "ok": True,
                "through": best_id,
                "via": best_via,
                "candidates": candidates[:10],
            }
        except Exception as exc:
            traceback.print_exc()
            return {"ok": False, "error": str(exc), "trace": traceback.format_exc()}


def main() -> None:
    api = Api()
    window = webview.create_window(
        title="HOI4 Province Painter",
        url=INDEX_HTML,
        js_api=api,
        width=1400,
        height=900,
        resizable=True,
        text_select=False,
    )
    # 환경변수 HOI4_PAINTER_DEBUG=1 이면 DevTools 활성화
    debug_mode = os.environ.get("HOI4_PAINTER_DEBUG", "").strip() in ("1", "true", "True", "yes")
    webview.start(debug=debug_mode)


if __name__ == "__main__":
    main()

# end of file
