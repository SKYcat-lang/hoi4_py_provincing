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


def test_delete_module() -> None:
    """core/delete.py - 흡수 매핑 + 인접 흡수 동작."""
    print("[7] 프로빈스 삭제 (인접 흡수)")
    from core.delete import (
        absorb_province,
        compute_absorption_map,
        find_best_absorber_rgb,
    )

    # 6×6 BMP: 우상단 빨강이 있고, 그 외 영역은 파랑(상단)/초록(하단)
    arr = np.zeros((6, 6, 3), dtype=np.uint8)
    arr[:3, :] = [0, 0, 255]   # 상단 파랑
    arr[3:, :] = [0, 255, 0]   # 하단 초록
    arr[0:2, 3:5] = [255, 0, 0]  # 우상단 작은 빨강 사각 (4픽셀)

    provinces = [
        Province(id=1, r=255, g=0, b=0, type="land"),
        Province(id=2, r=0, g=0, b=255, type="land"),
        Province(id=3, r=0, g=255, b=0, type="land"),
    ]

    # 빨강은 파랑하고만 인접 → 흡수자 = 파랑
    absorber = find_best_absorber_rgb(arr.copy(), (255, 0, 0), set())
    T.check("최적 흡수자 = 파랑", absorber == (0, 0, 255), str(absorber))

    # 파랑이 보호되면 흡수 불가 (이 케이스에서는 다른 인접 없음)
    absorber_blocked = find_best_absorber_rgb(arr.copy(), (255, 0, 0), {(0, 0, 255)})
    T.check("보호된 인접만 있으면 None", absorber_blocked is None,
            str(absorber_blocked))

    # 실제 흡수
    arr_copy = arr.copy()
    changes = absorb_province(arr_copy, (255, 0, 0), (0, 0, 255))
    T.check("흡수 후 변경 픽셀 4개", len(changes) == 4, str(len(changes)))
    T.check("흡수 후 빨강 사라짐",
            not ((arr_copy[..., 0] == 255) & (arr_copy[..., 1] == 0)
                 & (arr_copy[..., 2] == 0)).any())

    # compute_absorption_map: disk_arr는 흡수 전, cur_arr는 흡수 후
    disk_arr = arr
    cur_arr = arr_copy
    mapping = compute_absorption_map(disk_arr, cur_arr, provinces)
    T.check("매핑: 1 → 2 (빨강 → 파랑)",
            mapping == {1: 2}, str(mapping))


def test_external_files_railways() -> None:
    """railways.txt 노드 교체 + 중복 dedupe + 길이 1 라인 제거."""
    print("[8] railways.txt 흡수 매핑")
    from core.external_files import update_railways_txt

    tmp = tempfile.mkdtemp(prefix="hoi4test_rail_")
    try:
        path = os.path.join(tmp, "railways.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("3 4 100 200 300 400\r\n")  # 200 → 흡수자 999 로 교체
            f.write("1 3 50 60 70\r\n")          # 변화 없음
            f.write("2 2 80 81\r\n")              # 80 → 81 흡수 시 길이 1 → 라인 제거
            f.write("\r\n")                       # 빈 줄
            f.write("4 5 11 12 13 14 15\r\n")     # 14 → 999 (999는 13 다음이 아니라 14 자리)
        absorption_map = {200: 999, 80: 81, 14: 999}
        r = update_railways_txt(path, absorption_map)
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        # 200 → 999 잘 들어갔는지
        T.check("200이 999로 교체됨", "999" in text and "200" not in text,
                text)
        # 길이 1 라인 제거됐는지 (원래 "2 2 80 81" 줄)
        T.check("길이1 라인 제거됨", "80" not in text, text)
        T.check("변화 없는 라인 보존", "1 3 50 60 70" in text, text)
        T.check("returned changed=True", r["changed"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_external_files_adjacencies() -> None:
    """adjacencies.csv 재매핑 + self-loop 제거 + 헤더 보존."""
    print("[9] adjacencies.csv 흡수 매핑")
    from core.external_files import update_adjacencies_csv

    tmp = tempfile.mkdtemp(prefix="hoi4test_adj_")
    try:
        path = os.path.join(tmp, "adjacencies.csv")
        with open(path, "w", encoding="utf-8") as f:
            f.write("From;To;Type;Through;start_x;start_y;stop_x;stop_y;adjacency_rule_name;Comment\r\n")
            f.write("100;200;sea;-1;-1;-1;-1;-1;;Test1\r\n")    # 100→999, 200→그대로 = 999;200 (정상)
            f.write("999;100;sea;-1;-1;-1;-1;-1;;Test2\r\n")    # 999→그대로, 100→999 = self-loop → 제거
            f.write("50;60;sea;-1;-1;-1;-1;-1;;Test3\r\n")      # 변화 없음
        absorption_map = {100: 999}
        r = update_adjacencies_csv(path, absorption_map)
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        T.check("헤더 보존", text.startswith("From;To;Type;Through"), text[:80])
        T.check("self-loop 행 제거됨 (Test2)", "Test2" not in text, text)
        T.check("변화 없는 행 보존 (Test3)", "50;60;sea" in text, text)
        T.check("From=100→999 재매핑된 행 존재", "999;200;sea" in text, text)
        T.check("returned changed=True", r["changed"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_external_files_supply_nodes() -> None:
    """supply_nodes.txt 재매핑 + dedupe."""
    print("[10] supply_nodes.txt 흡수 매핑")
    from core.external_files import update_supply_nodes_txt

    tmp = tempfile.mkdtemp(prefix="hoi4test_sn_")
    try:
        path = os.path.join(tmp, "supply_nodes.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("1 100\r\n")  # 100 → 999
            f.write("1 999\r\n")  # 이미 999가 있음 → 위 매핑 후 중복
            f.write("1 50\r\n")    # 변화 없음
        r = update_supply_nodes_txt(path, {100: 999})
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        # 100 제거되고 999는 한 줄만 남아야 함
        count_999 = text.count("999")
        T.check("999가 정확히 1번", count_999 == 1, f"count={count_999} text={text!r}")
        T.check("50 보존", "1 50" in text, text)
        T.check("100 사라짐", " 100" not in text and "1 100" not in text, text)
        T.check("returned changed=True", r["changed"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_external_files_simple_removes() -> None:
    """buildings/unitstacks/positions 단순 제거 검증."""
    print("[11] buildings/unitstacks/positions 단순 제거")
    from core.external_files import (
        update_buildings_txt,
        update_positions_like,
        update_unitstacks_txt,
    )

    tmp = tempfile.mkdtemp(prefix="hoi4test_simple_")
    try:
        # buildings.txt: 첫 컬럼이 prov_id, 마지막이 naval target
        bp = os.path.join(tmp, "buildings.txt")
        with open(bp, "w", encoding="utf-8") as f:
            f.write("100;arms_factory;2900.0;9.5;1350.0;0.0;0\r\n")    # 100 제거
            f.write("200;industrial_complex;2950.0;9.5;1360.0;0.0;0\r\n")  # 보존
            f.write("1;floating_harbor;2940.0;9.5;1356.0;1.5;100\r\n")  # 마지막=100 제거
        r = update_buildings_txt(bp, {100})
        with open(bp, "r", encoding="utf-8") as f:
            text = f.read()
        T.check("buildings: 100 줄 제거", "arms_factory" not in text, text)
        T.check("buildings: naval target 100 줄 제거", "floating_harbor" not in text, text)
        T.check("buildings: 200 보존", "industrial_complex" in text, text)

        # unitstacks.txt: 첫 컬럼 prov_id
        up = os.path.join(tmp, "unitstacks.txt")
        with open(up, "w", encoding="utf-8") as f:
            f.write("100;0;2900.0;9.5;1350.0;0.0;0.5\r\n")
            f.write("200;0;2950.0;9.5;1360.0;0.0;0.5\r\n")
        r = update_unitstacks_txt(up, {100})
        with open(up, "r", encoding="utf-8") as f:
            text = f.read()
        T.check("unitstacks: 100 줄 제거", "100;0;2900" not in text, text)
        T.check("unitstacks: 200 보존", "200;0;2950" in text, text)

        # positions.txt
        pp = os.path.join(tmp, "positions.txt")
        with open(pp, "w", encoding="utf-8") as f:
            f.write("100;0;2900.0;9.5;1350.0;0.0;0.5\r\n")
            f.write("200;0;2950.0;9.5;1360.0;0.0;0.5\r\n")
        r = update_positions_like(pp, {100})
        with open(pp, "r", encoding="utf-8") as f:
            text = f.read()
        T.check("positions: 100 줄 제거", "100;0;2900" not in text, text)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_external_files_units_decisions() -> None:
    """history/units location 재매핑 + decision set_province_name 재매핑."""
    print("[12] units location / decisions set_province_name 재매핑")
    from core.external_files import (
        update_set_province_name_in_file,
        update_unit_history_file,
    )

    tmp = tempfile.mkdtemp(prefix="hoi4test_ud_")
    try:
        up = os.path.join(tmp, "FRA_1936.txt")
        with open(up, "w", encoding="utf-8") as f:
            f.write("division={\n    name=\"Test\"\n    location = 100\n}\n")
            f.write("division={\n    name=\"Other\"\n    location = 50\n}\n")
        r = update_unit_history_file(up, {100: 999})
        with open(up, "r", encoding="utf-8") as f:
            text = f.read()
        T.check("location 100 → 999", "location = 999" in text, text)
        T.check("location 50 보존", "location = 50" in text, text)

        dp = os.path.join(tmp, "decision.txt")
        with open(dp, "w", encoding="utf-8") as f:
            f.write("set_province_name = {\n    id = 100\n    name = test\n}\n")
            f.write("set_province_name = {\n    id = 50\n    name = unchanged\n}\n")
        r = update_set_province_name_in_file(dp, {100: 999})
        with open(dp, "r", encoding="utf-8") as f:
            text = f.read()
        T.check("set_province_name id 100 → 999", "id = 999" in text, text)
        T.check("set_province_name id 50 보존", "id = 50" in text, text)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_external_files_state_blocks() -> None:
    """state 파일 victory_points 재매핑 + 합산."""
    print("[13] state 파일 victory_points / buildings 블록 재매핑")
    from core.external_files import update_state_file_blocks

    tmp = tempfile.mkdtemp(prefix="hoi4test_st_")
    try:
        sp = os.path.join(tmp, "1-Test.txt")
        with open(sp, "w", encoding="utf-8") as f:
            f.write(
                'state={\n'
                '    id=1\n'
                '    name="STATE_1"\n'
                '    history={\n'
                '        victory_points = { 100 5 }\n'
                '        victory_points = { 999 2 }\n'
                '        buildings = {\n'
                '            infrastructure = 4\n'
                '            100 = { naval_base = 3 }\n'
                '        }\n'
                '    }\n'
                '    provinces={ 100 999 }\n'
                '}\n'
            )
        r = update_state_file_blocks(sp, {100: 999})
        with open(sp, "r", encoding="utf-8") as f:
            text = f.read()
        # victory_points 안에서 100 → 999로 매핑되고, 기존 999=2가 있으면 합산은
        # 다른 블록과는 통합되지 않음(블록별로 처리). 100 → 999가 됐는지 확인.
        T.check("victory_points 100 사라지고 999가 됨",
                "100 5" not in text and "999" in text, text)
        # buildings 블록에서 100 = { ... } → 999 = { ... }
        T.check("buildings 100 블록이 999로 재매핑",
                "999 = {" in text and "100 = {" not in text, text)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_definition_csv_id_gap_filling() -> None:
    """병합/삭제로 생긴 ID 갭은 placeholder 행으로 채워져 ID 순서가 유지되어야 한다."""
    print("[14] definition.csv ID 갭 placeholder 채움")
    tmp = tempfile.mkdtemp(prefix="hoi4test_defgap_")
    try:
        path = os.path.join(tmp, "definition.csv")

        # 케이스 1: 1711, 1713 만 존재 (1712가 비었음 — 병합 흡수 후 가정)
        provs = [
            Province(id=0, r=0, g=0, b=0, type="land", coastal=False,
                     terrain="unknown", continent=0),
            Province(id=1711, r=8, g=34, b=248, type="sea", coastal=True,
                     terrain="ocean", continent=0),
            Province(id=1713, r=8, g=36, b=145, type="land", coastal=True,
                     terrain="forest", continent=4),
        ]
        write_definition_csv(path, provs, set())
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        lines = text.strip("\r\n").split("\r\n")

        T.check("총 행 수 = max_id + 1 = 1714",
                len(lines) == 1714, str(len(lines)))
        T.check("0번째 줄 = 원본 0행",
                lines[0] == "0;0;0;0;land;false;unknown;0", lines[0])
        T.check("1711행 정상 보존",
                lines[1711] == "1711;8;34;248;sea;true;ocean;0", lines[1711])
        T.check("1712행 = placeholder",
                lines[1712] == "1712;0;0;0;land;false;unknown;0", lines[1712])
        T.check("1713행 정상 보존",
                lines[1713] == "1713;8;36;145;land;true;forest;4", lines[1713])

        # 케이스 2: removed_ids로 중간 ID를 제거해도 placeholder가 채움
        provs2 = [
            Province(id=0, r=0, g=0, b=0, type="land", coastal=False,
                     terrain="unknown", continent=0),
            Province(id=1, r=10, g=10, b=10, type="land", coastal=False,
                     terrain="plains", continent=1),
            Province(id=2, r=20, g=20, b=20, type="land", coastal=False,
                     terrain="plains", continent=1),
            Province(id=3, r=30, g=30, b=30, type="land", coastal=False,
                     terrain="plains", continent=1),
        ]
        write_definition_csv(path, provs2, {2})
        with open(path, "r", encoding="utf-8") as f:
            text2 = f.read()
        lines2 = text2.strip("\r\n").split("\r\n")
        T.check("removed_ids 적용 후에도 max_id 기준 채움 (총 4행)",
                len(lines2) == 4, str(len(lines2)))
        T.check("removed_ids 위치(2)는 placeholder로 채워짐",
                lines2[2] == "2;0;0;0;land;false;unknown;0", lines2[2])
        T.check("ID 3은 그대로 ID 3에 보존 (순서 어긋남 없음)",
                lines2[3] == "3;30;30;30;land;false;plains;1", lines2[3])

        # 케이스 3: 갭이 없는 일반 경우는 기존 동작 그대로
        provs3 = [
            Province(id=0, r=0, g=0, b=0, type="land", coastal=False,
                     terrain="unknown", continent=0),
            Province(id=1, r=11, g=11, b=11, type="land", coastal=False,
                     terrain="plains", continent=1),
            Province(id=2, r=22, g=22, b=22, type="land", coastal=False,
                     terrain="plains", continent=1),
        ]
        write_definition_csv(path, provs3, set())
        with open(path, "r", encoding="utf-8") as f:
            text3 = f.read()
        lines3 = text3.strip("\r\n").split("\r\n")
        T.check("갭 없을 때는 placeholder 추가 안 됨",
                len(lines3) == 3 and "0;0;0;0" not in lines3[1]
                and "0;0;0;0" not in lines3[2], str(lines3))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_min_invasive_compaction_plan() -> None:
    """최소침습 병합: 맨 뒷번호를 빈자리로 끌어와 매핑 1개씩 생성."""
    print("[15] min-invasive compaction plan")
    from core.compact import build_min_invasive_plan

    tmp = tempfile.mkdtemp(prefix="hoi4test_compact_")
    try:
        csv_path = os.path.join(tmp, "definition.csv")
        # 시나리오: 0(invalid), 1, 2, [3 hole], 4, [5 hole], 6, 7
        # 기대: 7 → 3, 6 → 5. 최종 ID 공간: 0,1,2,3(was7),4,5(was6)
        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            f.write("\r\n".join([
                "0;0;0;0;land;false;unknown;0",
                "1;10;10;10;land;false;plains;1",
                "2;20;20;20;land;false;plains;1",
                "3;0;0;0;land;false;unknown;0",       # placeholder hole
                "4;40;40;40;land;false;plains;1",
                "5;0;0;0;land;false;unknown;0",       # placeholder hole
                "6;60;60;60;land;false;plains;1",
                "7;70;70;70;land;false;plains;1",
                "",
            ]))
        plan = build_min_invasive_plan(csv_path)

        T.check("id_map 크기 = 2 (mover 2개)",
                len(plan.id_map) == 2, str(plan.id_map))
        T.check("매핑 7→3",
                plan.id_map.get(7) == 3, str(plan.id_map))
        T.check("매핑 6→5",
                plan.id_map.get(6) == 5, str(plan.id_map))
        T.check("removed_ids = {3, 5, 6, 7}",
                set(plan.removed_ids) == {3, 5, 6, 7}, str(plan.removed_ids))

        ids = [p.id for p in plan.new_provinces]
        T.check("new_provinces ID = [0,1,2,3,4,5]",
                ids == [0, 1, 2, 3, 4, 5], str(ids))

        # 새 ID 3의 RGB가 원래 ID 7의 것이어야 함
        p3 = next(p for p in plan.new_provinces if p.id == 3)
        T.check("new ID 3은 옛 ID 7의 RGB(70,70,70)",
                (p3.r, p3.g, p3.b) == (70, 70, 70), str((p3.r, p3.g, p3.b)))
        # 새 ID 5의 RGB가 원래 ID 6의 것이어야 함
        p5 = next(p for p in plan.new_provinces if p.id == 5)
        T.check("new ID 5는 옛 ID 6의 RGB(60,60,60)",
                (p5.r, p5.g, p5.b) == (60, 60, 60), str((p5.r, p5.g, p5.b)))
        # ID 4는 그대로
        p4 = next(p for p in plan.new_provinces if p.id == 4)
        T.check("ID 4는 그대로 유지",
                (p4.r, p4.g, p4.b) == (40, 40, 40), str((p4.r, p4.g, p4.b)))

        # === 시나리오 2: hole이 mover보다 뒤에 있으면 이동 안 함 ===
        # 0, 1, 2, [3 hole]  (max=3, mover 후보 2뿐인데 2 <= 3 이므로 끌어올 게 없음)
        csv2 = os.path.join(tmp, "def2.csv")
        with open(csv2, "w", encoding="utf-8", newline="") as f:
            f.write("\r\n".join([
                "0;0;0;0;land;false;unknown;0",
                "1;10;10;10;land;false;plains;1",
                "2;20;20;20;land;false;plains;1",
                "3;0;0;0;land;false;unknown;0",
                "",
            ]))
        plan2 = build_min_invasive_plan(csv2)
        T.check("끝에 매달린 placeholder는 매핑 없이 잘려나감",
                len(plan2.id_map) == 0, str(plan2.id_map))
        T.check("new_provinces = [0,1,2] (placeholder 제거)",
                [p.id for p in plan2.new_provinces] == [0, 1, 2],
                str([p.id for p in plan2.new_provinces]))
        T.check("removed_ids = [3]",
                plan2.removed_ids == [3], str(plan2.removed_ids))

        # === 시나리오 3: 구멍이 mover보다 많으면 일부만 매핑되고 나머지는 잘려나감 ===
        # 0, 1, [2 hole], [3 hole], [4 hole], 5
        # mover 후보: [5], hole: [2,3,4]
        # 5→2 한 번만 매핑. 나머지 3,4는 mover로 채울 수 없으니 그냥 잘려나감.
        # 결과 new_provinces: 0,1,2(was 5)
        csv3 = os.path.join(tmp, "def3.csv")
        with open(csv3, "w", encoding="utf-8", newline="") as f:
            f.write("\r\n".join([
                "0;0;0;0;land;false;unknown;0",
                "1;10;10;10;land;false;plains;1",
                "2;0;0;0;land;false;unknown;0",
                "3;0;0;0;land;false;unknown;0",
                "4;0;0;0;land;false;unknown;0",
                "5;50;50;50;land;false;plains;1",
                "",
            ]))
        plan3 = build_min_invasive_plan(csv3)
        T.check("매핑 5→2만 생성",
                plan3.id_map == {5: 2}, str(plan3.id_map))
        T.check("new_provinces = [0,1,2]",
                [p.id for p in plan3.new_provinces] == [0, 1, 2],
                str([p.id for p in plan3.new_provinces]))
        p2 = next(p for p in plan3.new_provinces if p.id == 2)
        T.check("new ID 2는 옛 ID 5의 RGB(50,50,50)",
                (p2.r, p2.g, p2.b) == (50, 50, 50), str((p2.r, p2.g, p2.b)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


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
    test_delete_module()
    test_external_files_railways()
    test_external_files_adjacencies()
    test_external_files_supply_nodes()
    test_external_files_simple_removes()
    test_external_files_units_decisions()
    test_external_files_state_blocks()
    test_definition_csv_id_gap_filling()
    test_min_invasive_compaction_plan()
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
