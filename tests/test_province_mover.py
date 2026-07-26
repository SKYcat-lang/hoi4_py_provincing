from __future__ import annotations

import unittest

import numpy as np

from core.definitions import Province
from core.province_mover import move_province_group


class ProvinceMoverTests(unittest.TestCase):
    def test_group_move_clears_source_and_overwrites_destination(self) -> None:
        black = [0, 0, 0]
        a = [10, 20, 30]
        b = [40, 50, 60]
        c = [70, 80, 90]
        arr = np.array([[a, b, c, c, black]], dtype=np.uint8)

        result = move_province_group(arr, [tuple(a), tuple(b)], 2, 0)

        np.testing.assert_array_equal(
            arr,
            np.array([[black, black, a, b, black]], dtype=np.uint8),
        )
        self.assertEqual(result["selectedPixelCount"], 2)
        self.assertEqual(result["bounds"], [0, 0, 1, 0])
        self.assertTrue(any(change[:2] == [2, 0] and change[2:5] == c
                            for change in result["changes"]))

    def test_overlapping_move_uses_original_selected_pixels(self) -> None:
        a = [10, 0, 0]
        b = [20, 0, 0]
        arr = np.array([[a, b, a, b]], dtype=np.uint8)

        move_province_group(arr, [tuple(a), tuple(b)], 0, 0)
        np.testing.assert_array_equal(arr, np.array([[a, b, a, b]], dtype=np.uint8))

        arr = np.array([[a, b, [99, 0, 0], [99, 0, 0]]], dtype=np.uint8)
        move_province_group(arr, [tuple(a), tuple(b)], 1, 0)
        np.testing.assert_array_equal(
            arr,
            np.array([[[0, 0, 0], a, b, [99, 0, 0]]], dtype=np.uint8),
        )

    def test_move_outside_bitmap_is_rejected_without_mutation(self) -> None:
        arr = np.array([[[10, 20, 30], [1, 2, 3]]], dtype=np.uint8)
        before = arr.copy()

        with self.assertRaisesRegex(ValueError, "바깥"):
            move_province_group(arr, [(10, 20, 30)], -1, 0)

        np.testing.assert_array_equal(arr, before)

    def test_api_moves_selected_definition_ids_as_one_group(self) -> None:
        from main import Api

        api = Api()
        api.provinces_arr = np.array(
            [[[10, 0, 0], [20, 0, 0], [30, 0, 0], [30, 0, 0]]],
            dtype=np.uint8,
        )
        api.provinces = [
            Province(1, 10, 0, 0),
            Province(2, 20, 0, 0),
            Province(3, 30, 0, 0),
        ]

        result = api.move_provinces([1, 2], 2, 0)

        self.assertTrue(result["ok"])
        self.assertEqual(result["selectedPixelCount"], 2)
        np.testing.assert_array_equal(
            api.provinces_arr,
            np.array(
                [[[0, 0, 0], [0, 0, 0], [10, 0, 0], [20, 0, 0]]],
                dtype=np.uint8,
            ),
        )


if __name__ == "__main__":
    unittest.main()
