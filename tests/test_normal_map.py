import os
import tempfile
import unittest

import numpy as np
from PIL import Image

from core.map_saver import write_world_normal_bmp
from core.normal_map import generate_world_normal


class WorldNormalMapTests(unittest.TestCase):
    def test_generation_uses_hoi4_half_resolution(self) -> None:
        heightmap = np.full((8, 12), 95, dtype=np.uint8)

        normal = generate_world_normal(heightmap)

        self.assertEqual(normal.shape, (4, 6, 3))
        np.testing.assert_array_equal(
            normal,
            np.full((4, 6, 3), [128, 128, 255], dtype=np.uint8),
        )

    def test_generation_preserves_slope_directions_after_resize(self) -> None:
        east_ramp = np.tile(
            np.arange(0, 80, 10, dtype=np.uint8), (8, 1)
        )
        east_normal = generate_world_normal(east_ramp)
        self.assertLess(int(east_normal[2, 2, 0]), 128)
        self.assertEqual(int(east_normal[2, 2, 1]), 128)

        south_ramp = np.tile(
            np.arange(0, 80, 10, dtype=np.uint8)[:, None], (1, 8)
        )
        south_normal = generate_world_normal(south_ramp)
        self.assertGreater(int(south_normal[2, 2, 1]), 128)

    def test_generation_rejects_dimensions_that_cannot_be_halved(self) -> None:
        with self.assertRaisesRegex(ValueError, "must both be even"):
            generate_world_normal(np.full((7, 8), 95, dtype=np.uint8))

    def test_saved_bitmap_is_half_resolution_24_bit_rgb(self) -> None:
        normal = generate_world_normal(np.full((8, 12), 95, dtype=np.uint8))
        with tempfile.TemporaryDirectory(prefix="hoi4_normal_") as folder:
            path = os.path.join(folder, "world_normal.bmp")
            write_world_normal_bmp(normal, path)

            with Image.open(path) as image:
                self.assertEqual(image.size, (6, 4))
                self.assertEqual(image.mode, "RGB")
            with open(path, "rb") as bitmap:
                bitmap.seek(28)
                self.assertEqual(int.from_bytes(bitmap.read(2), "little"), 24)


if __name__ == "__main__":
    unittest.main()
