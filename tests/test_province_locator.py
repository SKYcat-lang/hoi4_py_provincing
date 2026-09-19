import unittest

import numpy as np

from core.definitions import Province
from core.province_locator import locate_province
from main import Api


class ProvinceLocatorTests(unittest.TestCase):
    def setUp(self):
        self.pixels = np.zeros((7, 9, 3), dtype=np.uint8)
        self.provinces = [Province(0, 0, 0, 0), Province(5, 10, 20, 30), Province(6, 0, 0, 0)]

    def test_single_pixel_and_current_unsaved_geometry(self):
        self.pixels[2, 8] = [10, 20, 30]
        result = locate_province(self.pixels, self.provinces, '005')
        self.assertEqual((result['x'], result['y']), (8, 2))
        self.assertEqual(result['bounds'], [8, 2, 8, 2])
        self.assertEqual(result['pixelCount'], 1)
        self.pixels[2, 8] = 0
        self.pixels[6, 1] = [10, 20, 30]
        result = locate_province(self.pixels, self.provinces, 5)
        self.assertEqual((result['x'], result['y']), (1, 6))

    def test_disconnected_and_concave_provinces_focus_on_actual_pixel(self):
        for positions in ([(0, 0), (6, 8)], [(1, 1), (1, 2), (1, 3), (2, 1), (3, 1)]):
            self.pixels[:] = 0
            for row, column in positions:
                self.pixels[row, column] = [10, 20, 30]
            result = locate_province(self.pixels, self.provinces, 5)
            self.assertIn((result['y'], result['x']), positions)
            self.assertEqual(result['pixelCount'], len(positions))

    def test_invalid_unknown_reserved_and_deleted_provinces(self):
        for identifier in (0, -1, 1.5, True, None, '', '1e2', '１２', '9' * 100, 6, 999):
            with self.subTest(identifier=identifier):
                self.assertFalse(locate_province(self.pixels, self.provinces, identifier)['ok'])
        self.assertIn('픽셀이 없습니다', locate_province(self.pixels, self.provinces, 5)['error'])

    def test_bridge_uses_current_map_and_reports_unloaded(self):
        api = Api()
        self.assertFalse(api.locate_province(5)['ok'])
        api.provinces = self.provinces
        api.provinces_arr = self.pixels
        self.pixels[4, 3] = [10, 20, 30]
        result = api.locate_province(5)
        self.assertTrue(result['ok'])
        self.assertEqual((result['x'], result['y']), (3, 4))
