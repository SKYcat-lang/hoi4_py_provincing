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
    load_provinces_bmp,
    load_state_files,
    load_strategic_regions,
    load_terrain_bmp,
    load_terrain_categories,
)
from core.map_saver import (
    analyze_for_save,
    build_new_provinces,
    pick_strategic_region_for_province,
    update_state_file,
    update_strategic_region_file,
    write_definition_csv,
    write_provinces_bmp,
)
from core.province_analyzer import find_adjacent_colors
from core.xcrossing import find_all_xcrossings, find_xcrossings_near
from core.validators import (
    find_exclaves,
    find_one_pixel_provinces,
    fix_rivers_bmp,
    validate_rivers_bmp,
)
from core.split import split_region


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
        self.provinces: list[Province] = []
        self.terrain_categories: list[TerrainCategory] = []
        self.continents: list[str] = []
        self.states: list[StateInfo] = []
        self.regions: list[StrategicRegionInfo] = []
        self.color_pool: Optional[ColorPool] = None
        # province_id -> state_id 매핑 (사용자가 스테이트 맵에서 편집)
        self.assignments: dict[int, int] = {}
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
                self.provinces = load_definition_csv(paths.definition_csv)
                self.continents = load_continent_txt(paths.continent_txt)
                self.terrain_categories = load_terrain_categories(paths.common_terrain_dir)
                self.states = load_state_files(paths.history_states_dir)
                self.regions = load_strategic_regions(paths.strategicregions_dir)

                self.color_pool = ColorPool(p.rgb for p in self.provinces)

                # 초기 스테이트 할당: 기존 state 파일에서 가져옴
                self.assignments = {}
                for s in self.states:
                    for pid in s.province_ids:
                        self.assignments[pid] = s.id

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

                return {
                    "ok": True,
                    "mapDir": paths.map_dir,
                    "modRoot": paths.mod_root,
                    "width": width,
                    "height": height,
                    "imageDataUrl": encode_image_to_png_base64(self.provinces_arr),
                    "riversImageDataUrl": rivers_data_url,
                    "terrainImageDataUrl": terrain_data_url,
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
                        {"id": r.id, "name": r.name}
                        for r in self.regions
                    ],
                    "continents": self.continents,
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
        """rivers.bmp 팔레트 표준 준수 검사."""
        if self.paths is None:
            return {"ok": False, "error": "맵이 로드되지 않았습니다."}
        return validate_rivers_bmp(self.paths.rivers_bmp)

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
        }

    def assign_province_to_state(self, province_id: int, state_id: Optional[int]) -> dict:
        """프로빈스의 스테이트 소속 갱신. state_id=None이면 미할당으로."""
        if not isinstance(province_id, int):
            return {"ok": False, "error": "잘못된 province_id"}
        prev_state_id = self.assignments.get(province_id)
        if state_id is None:
            self.assignments.pop(province_id, None)
        else:
            if not any(s.id == state_id for s in self.states):
                return {"ok": False, "error": f"존재하지 않는 state_id: {state_id}"}
            self.assignments[province_id] = state_id
        return {
            "ok": True,
            "provinceId": province_id,
            "previousStateId": prev_state_id,
            "stateId": state_id,
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
        """페인트통: (x,y)에서 시작해 4방향으로 연결된 같은 색 영역을 새 색으로 칠한다.

        호수/바다 보호 토글이 켜져 있고 시작 픽셀이 보호 대상이면 아무것도 하지 않는다.

        반환: changedPixels = [[x,y,oldR,oldG,oldB], ...]
        프론트엔드에서 이 리스트로 캔버스 갱신과 Undo 스택 등록을 한다.
        """
        if self.provinces_arr is None:
            return {"ok": False, "error": "맵이 로드되지 않았습니다."}

        arr = self.provinces_arr
        height, width = arr.shape[:2]
        if not (0 <= x < width and 0 <= y < height):
            return {"ok": False, "error": "범위 밖"}

        target = tuple(int(c) for c in arr[y, x].tolist())
        new_color = (int(rgb[0]), int(rgb[1]), int(rgb[2]))
        if target == new_color:
            return {"ok": True, "changedPixels": [], "applied": 0, "skipped": 0}

        # 보호 토글: 시작 픽셀이 보호 대상이면 즉시 종료
        protected: set[tuple[int, int, int]] = set()
        if respect_lakes:
            protected.update(p.rgb for p in self.provinces if p.type == "lake")
        if respect_sea:
            protected.update(p.rgb for p in self.provinces if p.type == "sea")
        if target in protected:
            return {
                "ok": True, "changedPixels": [], "applied": 0, "skipped": 1,
                "blockedByProtection": True,
            }

        # NumPy 기반 빠른 flood fill: 같은 색 마스크 → connected components
        # SciPy 없이 BFS로 처리. 5632×2048에서도 보통 영역은 빠르다.
        mask_eq = (
            (arr[..., 0] == target[0])
            & (arr[..., 1] == target[1])
            & (arr[..., 2] == target[2])
        )

        visited = np.zeros_like(mask_eq, dtype=bool)
        changed_pixels: list[list[int]] = []

        # 반복형 BFS (재귀는 큰 영역에서 스택 오버플로 위험)
        from collections import deque
        q: deque[tuple[int, int]] = deque()
        q.append((x, y))
        visited[y, x] = True

        nr, ng, nb = new_color
        tr, tg, tb = target

        while q:
            cx, cy = q.popleft()
            # 픽셀 색 변경 + 변경 기록 (Undo용)
            arr[cy, cx, 0] = nr
            arr[cy, cx, 1] = ng
            arr[cy, cx, 2] = nb
            changed_pixels.append([cx, cy, tr, tg, tb])

            # 4방향
            if cx > 0 and mask_eq[cy, cx - 1] and not visited[cy, cx - 1]:
                visited[cy, cx - 1] = True
                q.append((cx - 1, cy))
            if cx + 1 < width and mask_eq[cy, cx + 1] and not visited[cy, cx + 1]:
                visited[cy, cx + 1] = True
                q.append((cx + 1, cy))
            if cy > 0 and mask_eq[cy - 1, cx] and not visited[cy - 1, cx]:
                visited[cy - 1, cx] = True
                q.append((cx, cy - 1))
            if cy + 1 < height and mask_eq[cy + 1, cx] and not visited[cy + 1, cx]:
                visited[cy + 1, cx] = True
                q.append((cx, cy + 1))

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

            # 1) provinces.bmp 저장
            write_provinces_bmp(self.provinces_arr, self.paths.provinces_bmp)

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
            assignments_by_state: dict[int, set[int]] = {}
            for pid, sid in self.assignments.items():
                if pid in removed_ids:
                    continue
                assignments_by_state.setdefault(sid, set()).add(pid)

            for state in self.states:
                desired = assignments_by_state.get(state.id, set())
                current = set(state.province_ids)
                add = sorted(desired - current)
                # 제거 = 현재 파일에 있지만 매핑에 더는 이 state로 없는 것
                # 또는 사라진 프로빈스
                remove = (current - desired) | (current & removed_ids)
                if add or remove:
                    changed = update_state_file(state, add, remove)
                    if changed:
                        modified_state_files.append(state.file_path)

            # 전략구역 자동 할당: 부모 기반 우선, 없으면 인접 기반 폴백
            # (가희 요청: 새 프로빈스는 기존 색상에서 분할되었을 것이므로 부모의 region을 상속)
            region_by_pid: dict[int, StrategicRegionInfo] = {}
            for r in self.regions:
                for pid in r.province_ids:
                    region_by_pid[pid] = r

            for p in new_provs:
                if p.type not in ("land", "sea", "lake"):
                    continue
                region = None

                # 1) 부모 기반 상속
                parent = _resolve_parent(p.rgb)
                if parent is not None:
                    region = region_by_pid.get(parent.id)

                # 2) 인접 기반 폴백
                if region is None:
                    region = pick_strategic_region_for_province(
                        p.rgb, p.id, adjacency, province_by_rgb, self.regions
                    )

                if region is None:
                    continue
                changed = update_strategic_region_file(region, [p.id], set())
                if changed and region.file_path not in modified_region_files:
                    modified_region_files.append(region.file_path)

            # 사라진 프로빈스는 모든 전략구역에서도 제거
            if removed_ids:
                for region in self.regions:
                    changed = update_strategic_region_file(region, [], removed_ids)
                    if changed and region.file_path not in modified_region_files:
                        modified_region_files.append(region.file_path)

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

            return {
                "ok": True,
                "provincesBmp": self.paths.provinces_bmp,
                "definitionCsv": self.paths.definition_csv,
                "newProvinceCount": len(new_provs),
                "removedProvinceCount": len(removed_ids),
                "modifiedStateFiles": modified_state_files,
                "modifiedRegionFiles": modified_region_files,
                "xcrossings": [[x, y] for x, y in xcoords],
                "xcrossingCount": len(xcoords),
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
