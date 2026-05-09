"""핵심 로직 단위/통합 테스트.

샘플 BMP/CSV를 만들어서 색상 풀, 영역 분석, 저장 흐름을 검증한다.
실제 PyWebView를 띄우지 않고도 백엔드 로직만 점검할 수 있다.

실행: python -m tests.test_core
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

import numpy as np
from PIL import Image

# 모듈 경로 보정
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.color_pool import ColorPool
from core.definitions import Province, TerrainCategory
from core.map_loader import (
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
from core.province_analyzer import (
    find_adjacent_colors,
    find_dominant_terrain,
    find_used_colors,
    has_sea_neighbor,
    infer_continent_from_neighbors,
)


PASS = "✔"
FAIL = "✘"


class T:
    n = 0
    passed = 0
    failed: list[str] = []

    @classmethod
    def check(cls, name: str, cond: bool, detail: str = "") -> None:
        cls.n += 1
        if cond:
            cls.passed += 1
            print(f"  {PASS} {name}")
        else:
            cls.failed.append(name)
            print(f"  {FAIL} {name}  {detail}")


def test_color_pool() -> None:
    print("[1] ColorPool")
    pool = ColorPool([(1, 1, 1), (2, 2, 2)])
    T.check("기존 색은 used", pool.is_used((1, 1, 1)))
    T.check("0,0,0 예약됨", pool.is_used((0, 0, 0)))
    new = pool.pick_new()
    T.check("새 색은 사용 안 된 것", not (new in {(1,1,1),(2,2,2),(0,0,0)}))
    # 100개 발급 모두 unique
    issued = {new}
    for _ in range(99):
        c = pool.pick_new()
        T.check(f"중복 없음 {c}", c not in issued, str(issued))
        issued.add(c)


def test_definitions_roundtrip() -> None:
    print("[2] Province CSV 라운드트립")
    p = Province(id=42, r=200, g=100, b=50, type="land", coastal=True,
                 terrain="hills", continent=2)
    row = p.to_csv_row()
    T.check("포맷", row == "42;200;100;50;land;true;hills;2", row)
    p2 = Province.from_csv_row(row)
    T.check("역변환", p2 == p)


def test_analyzers() -> None:
    print("[3] province_analyzer 함수들")
    # 4×4 이미지: 좌상단 2×2 빨강, 우상단 2×2 파랑, 하단 2×4 초록
    arr = np.zeros((4, 4, 3), dtype=np.uint8)
    arr[0:2, 0:2] = [255, 0, 0]
    arr[0:2, 2:4] = [0, 0, 255]
    arr[2:4, 0:4] = [0, 255, 0]

    used = find_used_colors(arr)
    T.check("3개 색 발견", used == {(255,0,0),(0,0,255),(0,255,0)}, str(used))

    adj = find_adjacent_colors(arr, used)
    T.check("빨강-파랑 인접", (0,0,255) in adj.get((255,0,0), set()))
    T.check("빨강-초록 인접", (0,255,0) in adj.get((255,0,0), set()))
    T.check("파랑-초록 인접", (0,255,0) in adj.get((0,0,255), set()))

    # terrain 추론: terrain 8bit, 모든 픽셀 인덱스 1
    terrain = np.full((4, 4), 1, dtype=np.uint8)
    cats = [
        TerrainCategory(name="unknown", color=(255,0,0)),
        TerrainCategory(name="forest", color=(0,255,0)),
        TerrainCategory(name="hills", color=(248,255,153)),
    ]
    name = find_dominant_terrain(arr, terrain, (255,0,0), cats)
    T.check("8bit terrain → forest", name == "forest", name)

    # continent 추론
    province_by_rgb = {
        (0,0,255): Province(id=2, r=0, g=0, b=255, type="land",
                            coastal=False, terrain="plains", continent=3),
        (0,255,0): Province(id=3, r=0, g=255, b=0, type="land",
                            coastal=False, terrain="plains", continent=3),
    }
    cont = infer_continent_from_neighbors((255,0,0), adj, province_by_rgb)
    T.check("대륙 추론 = 3", cont == 3, str(cont))

    # 해안 판정
    province_by_rgb_sea = {
        (0,0,255): Province(id=2, r=0, g=0, b=255, type="sea",
                            coastal=False, terrain="ocean", continent=0),
        (0,255,0): Province(id=3, r=0, g=255, b=0, type="land",
                            coastal=False, terrain="plains", continent=3),
    }
    T.check("해안 판정 yes",
            has_sea_neighbor((255,0,0), adj, province_by_rgb_sea))


def test_save_flow() -> None:
    print("[4] 저장 흐름 (임시 모드 폴더)")
    tmp = tempfile.mkdtemp(prefix="hoi4test_")
    try:
        map_dir = os.path.join(tmp, "map")
        os.makedirs(map_dir)
        os.makedirs(os.path.join(map_dir, "strategicregions"))
        os.makedirs(os.path.join(tmp, "history", "states"))
        os.makedirs(os.path.join(tmp, "common", "terrain"))

        # 8×8 BMP: 4×4 4분할 (id1=빨강, id2=파랑, id3=초록), 새 색(노랑) 한 픽셀
        arr = np.zeros((8, 8, 3), dtype=np.uint8)
        arr[0:4, 0:4] = [255, 0, 0]    # id 1
        arr[0:4, 4:8] = [0, 0, 255]    # id 2 (sea)
        arr[4:8, 0:8] = [0, 255, 0]    # id 3 (land)
        # 새 프로빈스 1픽셀 (노랑)
        arr[2, 2] = [255, 255, 0]

        # provinces.bmp
        provinces_path = os.path.join(map_dir, "provinces.bmp")
        Image.fromarray(arr, "RGB").save(provinces_path, "BMP")

        # terrain.bmp 8bit (모두 1)
        terrain = np.full((8, 8), 1, dtype=np.uint8)
        Image.fromarray(terrain, "L").convert("P").save(
            os.path.join(map_dir, "terrain.bmp"), "BMP"
        )

        # definition.csv (3개 + id 0)
        with open(os.path.join(map_dir, "definition.csv"), "w",
                  encoding="utf-8") as f:
            f.write("0;0;0;0;land;false;unknown;0\r\n")
            f.write("1;255;0;0;land;false;plains;1\r\n")
            f.write("2;0;0;255;sea;true;ocean;0\r\n")
            f.write("3;0;255;0;land;false;plains;1\r\n")

        # continent.txt
        with open(os.path.join(map_dir, "continent.txt"), "w") as f:
            f.write("continents = {\n\teurope\n\tasia\n}\n")

        # default.map (없어도 무방하지만 형식 맞춰줌)
        with open(os.path.join(map_dir, "default.map"), "w") as f:
            f.write('definitions = "definition.csv"\n')

        # 00_terrain.txt
        with open(os.path.join(tmp, "common", "terrain", "00_terrain.txt"), "w") as f:
            f.write(
                "categories = {\n"
                "  unknown = { color = { 255 0 0 } }\n"
                "  forest  = { color = { 0 255 0 } }\n"
                "  ocean   = { color = { 0 0 255 } is_water = yes }\n"
                "}\n"
            )

        # state 1: id 1, 3
        state_path = os.path.join(tmp, "history", "states", "1-Test.txt")
        with open(state_path, "w") as f:
            f.write(
                'state={\n'
                '  id=1\n'
                '  name="STATE_1"\n'
                '  history={ owner = TST }\n'
                '  provinces={ 1 3 }\n'
                '}\n'
            )

        # strategic region: id 1, 3
        region_path = os.path.join(map_dir, "strategicregions", "1-R.txt")
        with open(region_path, "w") as f:
            f.write(
                'strategic_region={\n'
                '  id=1\n'
                '  name="STRATEGICREGION_1"\n'
                '  provinces={ 1 3 }\n'
                '}\n'
            )

        # ========== 로드 ==========
        paths = find_map_paths(map_dir)
        provinces_arr = load_provinces_bmp(paths.provinces_bmp)
        terrain_arr = load_terrain_bmp(paths.terrain_bmp)
        provinces = load_definition_csv(paths.definition_csv)
        cats = load_terrain_categories(paths.common_terrain_dir)
        states = load_state_files(paths.history_states_dir)
        regions = load_strategic_regions(paths.strategicregions_dir)

        T.check("프로빈스 4개 로드", len(provinces) == 4, str(len(provinces)))
        T.check("터레인 카테고리 ≥ 2", len(cats) >= 2, str(len(cats)))
        T.check("스테이트 1개", len(states) == 1)
        T.check("region 1개", len(regions) == 1)

        # ========== 분석 ==========
        new_rgbs, removed = analyze_for_save(
            provinces_arr, terrain_arr, provinces, cats
        )
        T.check("새 RGB 1개", new_rgbs == [(255, 255, 0)], str(new_rgbs))
        T.check("사라진 프로빈스 0", len(removed) == 0)

        new_provs = build_new_provinces(
            provinces_arr, terrain_arr, new_rgbs, provinces, cats
        )
        T.check("새 프로빈스 객체 1개", len(new_provs) == 1)
        new_p = new_provs[0]
        T.check("새 ID = 4", new_p.id == 4, str(new_p.id))
        T.check("type = land", new_p.type == "land", new_p.type)
        T.check("대륙 1 (인접 빨강 land)", new_p.continent == 1, str(new_p.continent))

        # ========== 저장 ==========
        write_provinces_bmp(provinces_arr, paths.provinces_bmp)
        all_provs = provinces + new_provs
        write_definition_csv(paths.definition_csv, all_provs, set())

        # state 업데이트
        update_state_file(states[0], add_ids=[new_p.id], remove_ids=set())
        with open(states[0].file_path, "r") as f:
            txt = f.read()
        T.check("state 파일에 4 추가됨", " 4 " in txt or txt.endswith("4 \n\t}\n}\n"), txt)

        # region 업데이트
        update_strategic_region_file(regions[0], add_ids=[new_p.id], remove_ids=set())
        with open(regions[0].file_path, "r") as f:
            txt = f.read()
        T.check("region 파일에 4 추가됨", " 4 " in txt, txt)

        # 다시 로드해서 새 프로빈스가 보이는지
        provinces2 = load_definition_csv(paths.definition_csv)
        T.check("재로드 후 5개", len(provinces2) == 5, str(len(provinces2)))
        T.check("재로드 후 새 색 포함",
                any(p.rgb == (255,255,0) for p in provinces2))

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_flood_fill() -> None:
    """Api.flood_fill 동작을 BMP만 갖고 검증.

    main.Api는 PyWebView 의존성이 무거우니 핵심 로직만 따로 베껴 테스트한다.
    """
    print("[5] Flood Fill (페인트통)")
    from collections import deque

    def flood_fill_mock(arr, x, y, new_color, protected):
        h, w = arr.shape[:2]
        target = tuple(int(c) for c in arr[y, x].tolist())
        if target == new_color:
            return []
        if target in protected:
            return None  # blocked
        mask = (arr[..., 0] == target[0]) & (arr[..., 1] == target[1]) & (arr[..., 2] == target[2])
        visited = np.zeros_like(mask, dtype=bool)
        q = deque()
        q.append((x, y))
        visited[y, x] = True
        changed = []
        while q:
            cx, cy = q.popleft()
            arr[cy, cx] = new_color
            changed.append([cx, cy, target[0], target[1], target[2]])
            for nx, ny in ((cx-1,cy),(cx+1,cy),(cx,cy-1),(cx,cy+1)):
                if 0 <= nx < w and 0 <= ny < h and mask[ny, nx] and not visited[ny, nx]:
                    visited[ny, nx] = True
                    q.append((nx, ny))
        return changed

    # 6×6, 두 영역의 같은 색이지만 연결되지 않음
    arr = np.zeros((6, 6, 3), dtype=np.uint8)
    arr[:, :] = [255, 0, 0]
    arr[1:3, 1:3] = [0, 255, 0]   # 좌상단 작은 초록 사각
    arr[3:5, 3:5] = [0, 255, 0]   # 우하단 작은 초록 사각 (연결 안 됨)

    # (1,1) 시작 → 좌상단 4픽셀만 변경되어야 함
    changes = flood_fill_mock(arr.copy(), 1, 1, (50, 50, 50), set())
    T.check("연결된 영역만 채움 (4픽셀)", len(changes) == 4, str(len(changes)))

    # 시작 픽셀이 보호 대상이면 None
    arr2 = arr.copy()
    blocked = flood_fill_mock(arr2, 1, 1, (50,50,50), {(0,255,0)})
    T.check("보호 대상이면 차단", blocked is None)

    # 같은 색 클릭 시 변경 없음
    same = flood_fill_mock(arr.copy(), 0, 0, (255,0,0), set())
    T.check("동일 색이면 빈 결과", same == [])

    # 이미 칠해진 후 재호출 → 새로 칠한 영역 다시 다른 색으로
    arr3 = arr.copy()
    flood_fill_mock(arr3, 1, 1, (100, 100, 100), set())
    again = flood_fill_mock(arr3, 1, 1, (200, 200, 200), set())
    T.check("재칠하기 4픽셀", len(again) == 4)


def test_xcrossing() -> None:
    """X-crossing(2×2 4색) 검출 검증."""
    print("[6] X-crossing 검사")
    from core.xcrossing import find_all_xcrossings, find_xcrossings_near

    # 2×2 정확히 4색이면 1건
    arr = np.array([
        [[255, 0, 0], [0, 255, 0]],
        [[0, 0, 255], [255, 255, 0]],
    ], dtype=np.uint8)
    coords = find_all_xcrossings(arr)
    T.check("정확히 4색 2x2 → 1건", coords == [(0, 0)], str(coords))

    # 같은 색 두 개 포함 → 0건
    arr2 = np.array([
        [[255, 0, 0], [0, 255, 0]],
        [[255, 0, 0], [255, 255, 0]],  # 좌상단과 좌하단이 같음
    ], dtype=np.uint8)
    T.check("3색 2x2 → 0건", find_all_xcrossings(arr2) == [])

    # 4×4에 X-crossing 2개 떨어져 있는 경우
    arr3 = np.zeros((4, 4, 3), dtype=np.uint8)
    arr3[:, :] = [10, 10, 10]
    # 좌상단 (0,0) 윈도우 = 4색
    arr3[0, 0] = [255, 0, 0]; arr3[0, 1] = [0, 255, 0]
    arr3[1, 0] = [0, 0, 255]; arr3[1, 1] = [255, 255, 0]
    # 우하단 (2,2) 윈도우 = 4색
    arr3[2, 2] = [100, 0, 0]; arr3[2, 3] = [0, 100, 0]
    arr3[3, 2] = [0, 0, 100]; arr3[3, 3] = [100, 100, 0]
    coords3 = find_all_xcrossings(arr3)
    T.check("두 X-crossing 분리 → 2건", set(coords3) == {(0, 0), (2, 2)}, str(coords3))

    # 국소 검사: 변경된 픽셀 주변만
    near = find_xcrossings_near(arr3, [[2, 2]])
    T.check("국소 검사 (2,2) 주변 → 1건", set(near) == {(2, 2)}, str(near))

    # 국소 검사: 무관한 좌표
    far = find_xcrossings_near(arr3, [[0, 3]])  # 윈도우 (0,2)는 4색 아님
    T.check("국소 검사 무관 좌표 → 0건", far == [], str(far))


def main() -> int:
    print("=" * 60)
    print("HOI4 Province Painter - 백엔드 테스트")
    print("=" * 60)
    test_color_pool()
    test_definitions_roundtrip()
    test_analyzers()
    test_save_flow()
    test_flood_fill()
    test_xcrossing()
    print("=" * 60)
    print(f"결과: {T.passed}/{T.n} 통과")
    if T.failed:
        print("실패 항목:")
        for name in T.failed:
            print(f"  - {name}")
        return 1
    print("모두 통과 ✔")
    return 0


if __name__ == "__main__":
    sys.exit(main())
