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
    def test_stroke_fill_and_exact_undo_changes(self) -> None:
        from main import Api

        api = Api()
        api.terrain_arr = np.array(
            [[1, 1, 2], [1, 2, 2], [3, 3, 2]],
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


class SupportBitmapTests(unittest.TestCase):
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
        flat = np.full((3, 5), 95, dtype=np.uint8)
        flat_normal = generate_world_normal(flat)
        np.testing.assert_array_equal(
            flat_normal,
            np.full((3, 5, 3), [128, 128, 255], dtype=np.uint8),
        )

        east_ramp = np.tile(np.array([0, 10, 20, 30, 40], dtype=np.uint8), (3, 1))
        east_normal = generate_world_normal(east_ramp)
        self.assertLess(int(east_normal[1, 2, 0]), 128)
        self.assertEqual(int(east_normal[1, 2, 1]), 128)

        south_ramp = np.tile(
            np.array([[0], [10], [20]], dtype=np.uint8), (1, 5)
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

    def test_world_normal_action_writes_current_live_heightmap(self) -> None:
        from main import Api

        with tempfile.TemporaryDirectory(prefix="hoi4_normal_api_") as folder:
            path = os.path.join(folder, "world_normal.bmp")
            api = Api()
            api.heightmap_arr = np.full((2, 3), 95, dtype=np.uint8)
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

        invalid = api.validate_supply_network(
            [{"level": 1, "province": 1}],
            [{"level": 1, "provinces": [1, 3]}],
        )
        self.assertFalse(invalid["valid"])
        self.assertIn("disjointed_railway", {
            issue["kind"] for issue in invalid["issues"]
        })


if __name__ == "__main__":
    unittest.main()
