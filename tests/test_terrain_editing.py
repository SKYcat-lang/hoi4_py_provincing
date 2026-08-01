from __future__ import annotations

import os
import tempfile
import unittest
import gc
from types import SimpleNamespace

import numpy as np
from PIL import Image

from core.map_loader import (
    load_heightmap_bmp,
    load_graphical_terrain_index_names,
    load_rivers_bmp,
    load_rivers_palette,
    load_supply_nodes,
    load_railways,
    load_terrain_bmp,
    load_terrain_palette,
)
from core.map_saver import (
    write_heightmap_bmp,
    write_world_normal_bmp,
    write_railways,
    write_rivers_bmp,
    write_supply_nodes,
    write_terrain_bmp,
)
from core.normal_map import generate_world_normal


class TerrainBitmapTests(unittest.TestCase):
    def test_indexed_palette_is_preserved_when_saved(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hoi4_terrain_") as folder:
            source = os.path.join(folder, "terrain.bmp")
            saved = os.path.join(folder, "terrain_saved.bmp")

            indices = np.array(
                [[0, 1, 1, 2], [0, 1, 2, 2], [3, 3, 2, 0]],
                dtype=np.uint8,
            )
            palette = [
                [(index * 17) % 256, (index * 29) % 256, (index * 43) % 256]
                for index in range(256)
            ]
            flat_palette = [channel for color in palette for channel in color]

            image = Image.fromarray(indices, mode="P")
            image.putpalette(flat_palette)
            image.save(source, format="BMP")

            loaded = load_terrain_bmp(source)
            loaded_palette = load_terrain_palette(source)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.ndim, 2)
            self.assertEqual(loaded_palette, palette)

            loaded[0, 0] = 3
            write_terrain_bmp(loaded, saved, loaded_palette)

            with Image.open(saved) as reopened:
                self.assertEqual(reopened.mode, "P")
                self.assertEqual(int(reopened.getpixel((0, 0))), 3)
            del reopened
            self.assertEqual(load_terrain_palette(saved), palette)
            with open(saved, "rb") as bitmap:
                bitmap.seek(28)
                self.assertEqual(int.from_bytes(bitmap.read(2), "little"), 8)
            gc.collect()


class TerrainApiTests(unittest.TestCase):
    def test_province_fill_does_not_recolour_disconnected_exclaves(self) -> None:
        from core.definitions import Province
        from main import Api

        api = Api()
        api.provinces_arr = np.array(
            [
                [[10, 0, 0], [20, 0, 0], [10, 0, 0]],
                [[10, 0, 0], [20, 0, 0], [10, 0, 0]],
            ],
            dtype=np.uint8,
        )
        api.provinces = [
            Province(1, 10, 0, 0),
            Province(2, 20, 0, 0),
        ]

        result = api.flood_fill(0, 0, [30, 0, 0], False, False)

        self.assertTrue(result["ok"])
        self.assertEqual(result["applied"], 2)
        self.assertTrue(np.all(api.provinces_arr[:, 0] == [30, 0, 0]))
        self.assertTrue(np.all(api.provinces_arr[:, 2] == [10, 0, 0]))
        self.assertTrue(np.all(api.provinces_arr[:, 1] == [20, 0, 0]))

    def test_stroke_fill_and_exact_undo_changes(self) -> None:
        from main import Api

        api = Api()
        api.terrain_arr = np.array(
            [[1, 1, 2], [1, 2, 2], [3, 3, 2]],
            dtype=np.uint8,
        )
        api.provinces_arr = np.array(
            [
                [[10, 0, 0], [10, 0, 0], [20, 0, 0]],
                [[10, 0, 0], [20, 0, 0], [20, 0, 0]],
                [[30, 0, 0], [30, 0, 0], [20, 0, 0]],
            ],
            dtype=np.uint8,
        )
        api.terrain_palette = [[i, i, i] for i in range(256)]

        stroke = api.apply_terrain_stroke([[0, 0], [0, 1]], 4)
        self.assertTrue(stroke["ok"])
        self.assertEqual(stroke["applied"], 2)
        self.assertTrue(api.terrain_dirty)

        fill = api.flood_fill_terrain(2, 0, 5)
        self.assertTrue(fill["ok"])
        self.assertEqual(fill["applied"], 4)
        self.assertTrue(all(change[2] == 2 for change in fill["changedPixels"]))

        undo = api.apply_terrain_changes(fill["changedPixels"])
        self.assertTrue(undo["ok"])
        self.assertEqual(undo["applied"], 4)
        self.assertEqual(int(api.terrain_arr[2, 2]), 2)

    def test_terrain_fill_is_connected_and_clipped_by_province(self) -> None:
        from main import Api

        api = Api()
        api.provinces_arr = np.array(
            [
                [[10, 0, 0], [10, 0, 0], [20, 0, 0]],
                [[10, 0, 0], [10, 0, 0], [20, 0, 0]],
            ],
            dtype=np.uint8,
        )
        api.terrain_arr = np.array([[1, 1, 1], [1, 2, 1]], dtype=np.uint8)
        api.terrain_palette = [[i, i, i] for i in range(256)]

        result = api.flood_fill_terrain(0, 0, 9)

        self.assertTrue(result["ok"])
        self.assertEqual(result["applied"], 3)
        np.testing.assert_array_equal(
            api.terrain_arr,
            np.array([[9, 9, 1], [9, 2, 1]], dtype=np.uint8),
        )


class SupportBitmapTests(unittest.TestCase):
    def test_graphical_terrain_names_follow_declared_palette_indices(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hoi4_terrain_names_") as folder:
            source = os.path.join(folder, "00_terrain.txt")
            with open(source, "w", encoding="utf-8") as output:
                output.write(
                    "terrain = {\n"
                    "  plains_rule = { type = plains color = { 2 7 9 } }\n"
                    "  forest_rule = { type = forest color = { 7 11 } }\n"
                    "}\n"
                )
            names = load_graphical_terrain_index_names(folder)
            self.assertEqual(names[2], "plains")
            self.assertEqual(names[7], "plains / forest")
            self.assertEqual(names[11], "forest")

    def test_heightmap_stays_8_bit_grayscale(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hoi4_heightmap_") as folder:
            saved = os.path.join(folder, "heightmap.bmp")
            values = np.array([[0, 95, 255], [12, 110, 200]], dtype=np.uint8)
            write_heightmap_bmp(values, saved)

            loaded = load_heightmap_bmp(saved)
            np.testing.assert_array_equal(loaded, values)
            with Image.open(saved) as reopened:
                self.assertEqual(reopened.mode, "L")
            with open(saved, "rb") as bitmap:
                bitmap.seek(28)
                self.assertEqual(int.from_bytes(bitmap.read(2), "little"), 8)

    def test_world_normal_generation_and_24_bit_save(self) -> None:
        flat = np.full((6, 10), 95, dtype=np.uint8)
        flat_normal = generate_world_normal(flat)
        np.testing.assert_array_equal(
            flat_normal,
            np.full((3, 5, 3), [128, 128, 255], dtype=np.uint8),
        )

        east_ramp = np.tile(
            np.arange(0, 100, 10, dtype=np.uint8), (6, 1)
        )
        east_normal = generate_world_normal(east_ramp)
        self.assertLess(int(east_normal[1, 2, 0]), 128)
        self.assertEqual(int(east_normal[1, 2, 1]), 128)

        south_ramp = np.tile(
            np.arange(0, 60, 10, dtype=np.uint8)[:, None], (1, 10)
        )
        south_normal = generate_world_normal(south_ramp)
        self.assertGreater(int(south_normal[1, 2, 1]), 128)

        with tempfile.TemporaryDirectory(prefix="hoi4_normal_") as folder:
            saved = os.path.join(folder, "world_normal.bmp")
            write_world_normal_bmp(flat_normal, saved)
            with Image.open(saved) as reopened:
                self.assertEqual(reopened.mode, "RGB")
            with open(saved, "rb") as bitmap:
                bitmap.seek(28)
                self.assertEqual(int.from_bytes(bitmap.read(2), "little"), 24)

    def test_rivers_palette_and_comment_indices_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hoi4_rivers_") as folder:
            saved = os.path.join(folder, "rivers.bmp")
            indices = np.array([[0, 3, 42, 255], [254, 1, 2, 11]], dtype=np.uint8)
            palette = [[i, (i * 3) % 256, (255 - i)] for i in range(256)]
            write_rivers_bmp(indices, saved, palette)

            np.testing.assert_array_equal(load_rivers_bmp(saved), indices)
            self.assertEqual(load_rivers_palette(saved), palette)
            with Image.open(saved) as reopened:
                self.assertEqual(reopened.mode, "P")
            with open(saved, "rb") as bitmap:
                bitmap.seek(46)
                self.assertEqual(int.from_bytes(bitmap.read(4), "little"), 0)
                self.assertEqual(int.from_bytes(bitmap.read(4), "little"), 0)

    def test_supply_text_formats_round_trip(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hoi4_supply_") as folder:
            nodes_path = os.path.join(folder, "supply_nodes.txt")
            rails_path = os.path.join(folder, "railways.txt")
            nodes = [{"level": 1, "province": 1234}]
            railways = [{"level": 4, "provinces": [693, 1444, 12, 11]}]
            write_supply_nodes(nodes_path, nodes)
            write_railways(rails_path, railways)
            self.assertEqual(load_supply_nodes(nodes_path), nodes)
            self.assertEqual(load_railways(rails_path), railways)


class SupportEditorApiTests(unittest.TestCase):
    def test_railway_delete_is_not_rolled_back_by_unrelated_validation(self) -> None:
        from main import Api

        api = Api()
        api.railways = [
            {"level": 1, "provinces": [10]},  # unrelated invalid short rail
            {"level": 3, "provinces": [20, 21, 22]},
        ]

        result = api.delete_supply_railway(1)

        self.assertTrue(result["ok"])
        self.assertEqual(result["deletedIndex"], 1)
        self.assertEqual(result["railwayCount"], 1)
        self.assertEqual(api.railways, [{"level": 1, "provinces": [10]}])
        self.assertTrue(api.supply_dirty)

        restored = api.insert_supply_railway(1, result["deletedRailway"])
        self.assertTrue(restored["ok"])
        self.assertEqual(
            api.railways,
            [
                {"level": 1, "provinces": [10]},
                {"level": 3, "provinces": [20, 21, 22]},
            ],
        )

        redone = api.delete_supply_railway(1)
        self.assertTrue(redone["ok"])
        self.assertEqual(api.railways, [{"level": 1, "provinces": [10]}])

    def test_state_and_strategic_region_assignments_can_be_unassigned(self) -> None:
        from core.definitions import Province, StateInfo, StrategicRegionInfo
        from main import Api

        api = Api()
        api.provinces_arr = np.array([[[10, 0, 0]]], dtype=np.uint8)
        api.provinces = [Province(7, 10, 0, 0, type="lake")]
        api.states = [StateInfo(3, "state.txt", "STATE_3", [7])]
        api.regions = [
            StrategicRegionInfo(5, "region.txt", "REGION_5", [7])
        ]
        api.assignments = {7: 3}
        api.region_assignments = {7: 5}

        lookup = api.get_province_id_at_pixel(0, 0)
        self.assertEqual(lookup["stateId"], 3)
        self.assertEqual(lookup["strategicRegionId"], 5)

        state_result = api.assign_province_to_state(7, None)
        region_result = api.assign_province_to_strategic_region(7, None)
        self.assertTrue(state_result["ok"])
        self.assertTrue(region_result["ok"])
        self.assertNotIn(7, api.assignments)
        self.assertNotIn(7, api.region_assignments)

    def test_area_file_changes_move_province_between_regions(self) -> None:
        from core.area_assignments import area_file_changes
        from core.definitions import StrategicRegionInfo
        from core.map_saver import update_strategic_region_file

        with tempfile.TemporaryDirectory(prefix="hoi4_region_assignment_") as folder:
            first_path = os.path.join(folder, "1-one.txt")
            second_path = os.path.join(folder, "2-two.txt")
            with open(first_path, "w", encoding="utf-8") as output:
                output.write("strategic_region={ id=1 provinces={ 10 11 } }")
            with open(second_path, "w", encoding="utf-8") as output:
                output.write("strategic_region={ id=2 provinces={ 20 } }")

            first = StrategicRegionInfo(1, first_path, "ONE", [10, 11])
            second = StrategicRegionInfo(2, second_path, "TWO", [20])
            assignments = {10: 2, 11: 1, 20: 2}

            first_add, first_remove = area_file_changes(first, assignments)
            second_add, second_remove = area_file_changes(second, assignments)
            self.assertTrue(
                update_strategic_region_file(first, first_add, first_remove)
            )
            self.assertTrue(
                update_strategic_region_file(second, second_add, second_remove)
            )
            self.assertEqual(first.province_ids, [11])
            self.assertEqual(second.province_ids, [10, 20])

    def test_support_lasso_moves_clear_source_and_keep_overlap(self) -> None:
        from core.definitions import Province
        from main import Api

        api = Api()
        api.terrain_arr = np.array([[1, 2, 3, 4]], dtype=np.uint8)
        api.terrain_palette = []
        terrain_result = api.move_terrain_selection([1, 2], 1, 0)

        self.assertTrue(terrain_result["ok"])
        self.assertEqual(api.terrain_arr.tolist(), [[1, 0, 2, 3]])
        self.assertEqual(terrain_result["selectedPixels"], [2, 3])
        self.assertTrue(api.terrain_dirty)

        api.heightmap_arr = np.array(
            [[10, 20], [30, 40], [50, 60]], dtype=np.uint8
        )
        height_result = api.move_heightmap_selection([0, 1], 0, 1)

        self.assertTrue(height_result["ok"])
        self.assertEqual(api.heightmap_arr.tolist(), [[0, 0], [10, 20], [50, 60]])
        self.assertEqual(height_result["selectedPixels"], [2, 3])
        self.assertTrue(api.heightmap_dirty)
        self.assertTrue(api.world_normal_stale)

        land = [10, 0, 0]
        sea = [0, 0, 10]
        api.provinces_arr = np.array([[land, land, sea, sea]], dtype=np.uint8)
        api.provinces = [
            Province(1, *land, type="land"),
            Province(2, *sea, type="sea"),
        ]
        api.rivers_arr = np.array([[0, 3, 4, 5]], dtype=np.uint8)
        api.rivers_palette = [[i, i, i] for i in range(256)]
        river_result = api.move_rivers_selection([0, 2], 1, 0)

        self.assertTrue(river_result["ok"])
        self.assertEqual(api.rivers_arr.tolist(), [[255, 0, 254, 4]])
        self.assertEqual(river_result["selectedPixels"], [1, 3])
        self.assertTrue(api.rivers_dirty)

    def test_heightmap_and_river_changes_are_exact(self) -> None:
        from main import Api

        api = Api()
        api.heightmap_arr = np.full((3, 4), 95, dtype=np.uint8)
        height_result = api.apply_heightmap_changes([[1, 1, 130], [2, 1, 70]])
        self.assertTrue(height_result["ok"])
        self.assertEqual(height_result["applied"], 2)
        self.assertEqual(int(api.heightmap_arr[1, 1]), 130)
        self.assertTrue(api.heightmap_dirty)

        api.rivers_arr = np.full((3, 4), 255, dtype=np.uint8)
        api.rivers_palette = [[i, i, i] for i in range(256)]
        river_result = api.apply_rivers_changes([[0, 1, 0], [1, 1, 3], [2, 1, 4]])
        self.assertTrue(river_result["ok"])
        self.assertEqual(river_result["applied"], 3)
        self.assertTrue(api.rivers_dirty)

    def test_height_protection_and_support_fills_use_province_masks(self) -> None:
        from core.definitions import Province
        from main import Api

        sea = [0, 0, 10]
        lake = [0, 10, 0]
        land = [10, 0, 0]
        api = Api()
        api.provinces_arr = np.array(
            [
                [sea, sea, land, land],
                [sea, lake, lake, land],
                [land, land, lake, land],
            ],
            dtype=np.uint8,
        )
        api.provinces = [
            Province(1, *sea, type="sea"),
            Province(2, *lake, type="lake"),
            Province(3, *land, type="land"),
        ]
        api.heightmap_arr = np.arange(12, dtype=np.uint8).reshape(3, 4)

        protected = api.apply_heightmap_changes(
            [[0, 0, 200], [1, 1, 201], [2, 0, 202]],
            True,
            True,
        )
        self.assertTrue(protected["ok"])
        self.assertEqual(protected["applied"], 1)
        self.assertEqual(int(api.heightmap_arr[0, 0]), 0)
        self.assertEqual(int(api.heightmap_arr[1, 1]), 5)
        self.assertEqual(int(api.heightmap_arr[0, 2]), 202)

        blocked = api.fill_heightmap_province(0, 0, 120, True, True)
        self.assertTrue(blocked["blockedByProtection"])
        self.assertEqual(blocked["applied"], 0)

        land_mask = np.all(api.provinces_arr == land, axis=2)
        api.heightmap_arr[:] = 0
        api.heightmap_arr[land_mask] = 50
        api.heightmap_arr[0, 1] = 50  # same value, but across the sea boundary
        api.heightmap_arr[1, 3] = 60
        filled_height = api.fill_heightmap_province(2, 0, 120, True, True)
        self.assertEqual(filled_height["applied"], 2)
        self.assertEqual(int(api.heightmap_arr[0, 2]), 120)
        self.assertEqual(int(api.heightmap_arr[0, 3]), 120)
        self.assertEqual(int(api.heightmap_arr[1, 3]), 60)
        self.assertEqual(int(api.heightmap_arr[2, 3]), 50)
        self.assertTrue(np.all(api.heightmap_arr[2, :2] == 50))
        self.assertEqual(int(api.heightmap_arr[0, 1]), 50)

        api.rivers_arr = np.full((3, 4), 255, dtype=np.uint8)
        api.rivers_arr[1, 3] = 3
        api.rivers_palette = [[i, i, i] for i in range(256)]
        filled_rivers = api.fill_rivers_province(2, 0, 4)
        self.assertEqual(filled_rivers["applied"], 2)
        self.assertTrue(np.all(api.rivers_arr[0, 2:4] == 4))
        self.assertEqual(int(api.rivers_arr[1, 3]), 3)
        self.assertEqual(int(api.rivers_arr[2, 3]), 255)
        self.assertTrue(np.all(api.rivers_arr[2, :2] == 255))
        self.assertTrue(np.all(api.rivers_arr[~land_mask] == 255))

    def test_heightmap_coast_smoothing_only_changes_directly_adjacent_land(self) -> None:
        from core.definitions import Province
        from main import Api

        sea = [0, 0, 10]
        coast_a = [10, 0, 0]
        coast_b = [20, 0, 0]
        inland_a = [30, 0, 0]
        inland_b = [40, 0, 0]
        lake = [0, 10, 0]
        api = Api()
        api.provinces_arr = np.array(
            [
                [sea, sea, coast_a, coast_a, inland_a, inland_a],
                [sea, sea, coast_a, coast_a, inland_a, inland_a],
                [sea, sea, coast_b, coast_b, inland_b, inland_b],
                [sea, lake, coast_b, coast_b, inland_b, inland_b],
            ],
            dtype=np.uint8,
        )
        api.provinces = [
            Province(1, *sea, type="sea"),
            Province(2, *coast_a, type="land"),
            Province(3, *coast_b, type="land"),
            Province(4, *inland_a, type="land"),
            Province(5, *inland_b, type="land"),
            Province(6, *lake, type="lake"),
        ]
        api.heightmap_arr = np.array(
            [
                [95, 95, 130, 130, 200, 200],
                [95, 95, 130, 130, 200, 200],
                [95, 95, 150, 150, 210, 210],
                [95, 80, 150, 150, 210, 210],
            ],
            dtype=np.uint8,
        )
        before = api.heightmap_arr.copy()

        result = api.smooth_heightmap_coast(0, 0, 3, 100)

        self.assertTrue(result["ok"])
        self.assertEqual(result["seaProvinceId"], 1)
        self.assertEqual(result["adjacentProvinceIds"], [2, 3])
        self.assertEqual(result["seaLevel"], 95)
        self.assertTrue(result["changedPixels"])
        self.assertTrue(np.all(api.heightmap_arr[:, 0] == before[:, 0]))
        self.assertEqual(int(api.heightmap_arr[3, 1]), 80)
        self.assertTrue(np.all(api.heightmap_arr[:3, 2] == 94))
        self.assertGreater(int(api.heightmap_arr[0, 3]), 94)
        self.assertLess(int(api.heightmap_arr[0, 3]), 130)
        self.assertTrue(np.array_equal(api.heightmap_arr[:, 4:], before[:, 4:]))
        self.assertTrue(all(len(change) == 4 for change in result["changedPixels"]))
        self.assertTrue(api.heightmap_dirty)
        self.assertTrue(api.world_normal_stale)

        api.heightmap_arr = before.copy()
        width_one = api.smooth_heightmap_coast(0, 0, 1, 100)
        self.assertTrue(width_one["ok"])
        self.assertEqual(width_one["width"], 1)
        self.assertEqual(int(api.heightmap_arr[0, 2]), 94)
        self.assertEqual(int(api.heightmap_arr[0, 3]), 130)

        api.heightmap_arr = before.copy()
        half_strength = api.smooth_heightmap_coast(0, 0, 1, 50)
        self.assertTrue(half_strength["ok"])
        self.assertEqual(half_strength["shoreHeight"], 94)
        self.assertEqual(int(api.heightmap_arr[0, 2]), 112)
        repeated = api.smooth_heightmap_coast(0, 0, 1, 50)
        self.assertTrue(repeated["ok"])
        self.assertEqual(int(api.heightmap_arr[0, 2]), 103)

        unchanged = api.heightmap_arr.copy()
        rejected = api.smooth_heightmap_coast(2, 0, 3, 100)
        self.assertFalse(rejected["ok"])
        self.assertTrue(np.array_equal(api.heightmap_arr, unchanged))

        lake_result = api.smooth_heightmap_coast(1, 3, 3, 100)
        self.assertTrue(lake_result["ok"])
        self.assertEqual(lake_result["waterProvinceId"], 6)
        self.assertEqual(lake_result["waterType"], "lake")
        self.assertEqual(lake_result["waterLevel"], 80)
        self.assertEqual(lake_result["adjacentProvinceIds"], [3])
        self.assertTrue(lake_result["changedPixels"])
        self.assertEqual(int(api.heightmap_arr[3, 1]), 80)

    def test_world_normal_action_writes_current_live_heightmap(self) -> None:
        from main import Api

        with tempfile.TemporaryDirectory(prefix="hoi4_normal_api_") as folder:
            path = os.path.join(folder, "world_normal.bmp")
            api = Api()
            api.heightmap_arr = np.full((4, 6), 95, dtype=np.uint8)
            api.heightmap_dirty = True
            api.world_normal_stale = True
            api.paths = SimpleNamespace(world_normal_bmp=path)

            result = api.generate_world_normal()
            self.assertTrue(result["ok"])
            self.assertTrue(result["heightmapDirty"])
            self.assertFalse(api.world_normal_stale)
            with Image.open(path) as generated:
                self.assertEqual(generated.mode, "RGB")
                self.assertEqual(generated.size, (3, 2))

    def test_river_topology_accepts_one_source_and_comment_indices(self) -> None:
        from main import Api

        api = Api()
        api.rivers_arr = np.full((4, 6), 255, dtype=np.uint8)
        api.rivers_arr[0, 0] = 42  # legal, ignored comment index
        api.rivers_arr[2, 1:4] = [0, 3, 6]
        api.rivers_palette = [[i, i, i] for i in range(256)]

        result = api.validate_river_topology()
        self.assertTrue(result["ok"])
        self.assertTrue(result["valid"])
        self.assertEqual(result["componentCount"], 1)
        self.assertEqual(result["sourceCount"], 1)

    def test_river_topology_rejects_missing_source_and_thick_block(self) -> None:
        from main import Api

        api = Api()
        api.rivers_arr = np.full((4, 5), 255, dtype=np.uint8)
        api.rivers_arr[1:3, 1:3] = 3
        api.rivers_palette = [[i, i, i] for i in range(256)]

        result = api.validate_river_topology()
        self.assertFalse(result["valid"])
        kinds = {issue["kind"] for issue in result["issues"]}
        self.assertIn("source_count", kinds)
        self.assertIn("thick_2x2", kinds)

    def test_river_topology_does_not_mislabel_split_rejoin_as_cycle(self) -> None:
        from main import Api

        api = Api()
        api.rivers_arr = np.full((5, 6), 255, dtype=np.uint8)
        api.rivers_arr[2, 0:3] = [0, 3, 2]
        api.rivers_arr[1, 2:5] = [3, 3, 3]
        api.rivers_arr[3, 2:5] = [3, 3, 3]
        api.rivers_arr[2, 4] = 1
        api.rivers_palette = [[i, i, i] for i in range(256)]

        result = api.validate_river_topology()

        self.assertTrue(result["ok"])
        self.assertTrue(result["valid"])
        self.assertEqual(result["issues"], [])

    def test_supply_network_requires_stateful_adjacent_land_provinces(self) -> None:
        from core.definitions import Province
        from main import Api

        api = Api()
        api.provinces_arr = np.array(
            [[[10, 0, 0], [20, 0, 0], [30, 0, 0]]], dtype=np.uint8
        )
        api.provinces = [
            Province(1, 10, 0, 0),
            Province(2, 20, 0, 0),
            Province(3, 30, 0, 0),
        ]
        api.assignments = {1: 10, 2: 10, 3: 10}

        valid = api.update_supply_network(
            [{"level": 1, "province": 1}],
            [{"level": 5, "provinces": [1, 2, 3]}],
        )
        self.assertTrue(valid["ok"])
        self.assertTrue(api.supply_dirty)

        warning = api.validate_supply_network(
            [{"level": 1, "province": 1}],
            [{"level": 1, "provinces": [1, 3]}],
        )
        self.assertTrue(warning["valid"])
        self.assertIn("disjointed_railway", {
            issue["kind"] for issue in warning["warnings"]
        })

    def test_supply_validation_accepts_explicit_adjacency_connections(self) -> None:
        from core.definitions import Province
        from main import Api

        api = Api()
        api.provinces_arr = np.array(
            [[[10, 0, 0], [20, 0, 0], [30, 0, 0]]], dtype=np.uint8
        )
        api.provinces = [
            Province(1, 10, 0, 0),
            Province(2, 20, 0, 0),
            Province(3, 30, 0, 0),
        ]
        api.assignments = {1: 10, 2: 10, 3: 10}
        railway = [{"level": 1, "provinces": [1, 3]}]

        with tempfile.TemporaryDirectory(prefix="hoi4_supply_adjacency_") as folder:
            api.paths = SimpleNamespace(map_dir=folder)
            adjacency_path = os.path.join(folder, "adjacencies.csv")
            with open(adjacency_path, "w", encoding="utf-8") as output:
                output.write("1;3;sea;2;-1;-1;-1;-1;;test\n")

            connected = api.validate_supply_network([], railway)
            self.assertTrue(connected["valid"])
            self.assertEqual(connected["warnings"], [])

            with open(adjacency_path, "w", encoding="utf-8") as output:
                output.write("1;3;impassable;-1;-1;-1;-1;-1;;test\n")

            impassable = api.validate_supply_network([], railway)
            self.assertTrue(impassable["valid"])
            self.assertIn("disjointed_railway", {
                issue["kind"] for issue in impassable["warnings"]
            })

    def test_supply_mutations_ignore_unrelated_legacy_errors(self) -> None:
        from core.definitions import Province
        from main import Api

        api = Api()
        api.provinces_arr = np.array(
            [[[10, 0, 0], [20, 0, 0], [30, 0, 0]]], dtype=np.uint8
        )
        api.provinces = [
            Province(1, 10, 0, 0),
            Province(2, 20, 0, 0),
            Province(3, 30, 0, 0),
        ]
        api.assignments = {1: 10, 2: 10, 3: 10}
        api.supply_nodes = [{"level": 9, "province": 999}]
        api.railways = [{"level": 9, "provinces": [999]}]

        added = api.add_supply_node(1)
        self.assertTrue(added["ok"])
        self.assertIn({"level": 1, "province": 1}, api.supply_nodes)

        created = api.upsert_supply_railway(
            None, {"level": 3, "provinces": [1, 2]}
        )
        self.assertTrue(created["ok"])
        self.assertEqual(created["index"], 1)
        self.assertEqual(api.railways[1]["provinces"], [1, 2])

        edited = api.upsert_supply_railway(
            1, {"level": 4, "provinces": [1, 3]}
        )
        self.assertTrue(edited["ok"])
        self.assertTrue(edited["warnings"])
        self.assertEqual(edited["warnings"][0]["index"], 1)

        restored = api.replace_supply_railway(1, created["railway"])
        self.assertTrue(restored["ok"])
        self.assertEqual(api.railways[1], created["railway"])

        deleted = api.delete_supply_node(1)
        self.assertTrue(deleted["ok"])
        inserted = api.insert_supply_node(deleted["deletedIndex"], deleted["node"])
        self.assertTrue(inserted["ok"])
        self.assertIn({"level": 1, "province": 1}, api.supply_nodes)


if __name__ == "__main__":
    unittest.main()
