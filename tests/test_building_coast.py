import math
from pathlib import Path
import random
import tempfile
import unittest

import numpy as np

from core.building_definitions import load_building_templates
from core.building_editor import BuildingMap
from core.definitions import Province


class CoastalPlacementTests(unittest.TestCase):
    def geometry(self, identifiers):
        provinces = [Province(1, 100, 0, 0), Province(2, 0, 100, 0, type='sea'),
                     Province(3, 0, 0, 100, type='sea')]
        palette = np.array([(0, 0, 0)] + [province.rgb for province in provinces], np.uint8)
        return BuildingMap(palette[identifiers], provinces, {1: 10}, np.full(identifiers.shape, 100, np.uint8))

    def test_floating_harbor_is_seven_pixels_offshore_and_deterministic(self):
        identifiers = np.full((40, 60), 3)
        identifiers[:, 10:30] = 1
        geometry = self.geometry(identifiers)
        anchor_x, anchor_z = geometry.coastal.anchor(1, 3)
        template = {'name': 'floating_harbor', 'coastal_distance': 7}
        first = geometry.candidate(1, template, random.Random(1))
        second = geometry.candidate(1, template, random.Random(2))
        self.assertAlmostEqual(math.hypot(first['x'] - anchor_x, first['z'] - anchor_z), 7, places=2)
        self.assertEqual(geometry.province_at(first['x'], first['z']), 3)
        self.assertEqual(first['target'], 1)
        self.assertEqual((first['x'], first['z'], first['rotation']), (second['x'], second['z'], second['rotation']))

    def test_slanted_coast_has_continuous_port_angle(self):
        rows, columns = np.indices((40, 60))
        identifiers = np.where(columns < 10 + rows // 2, 1, 3)
        geometry = self.geometry(identifiers)
        rotation = geometry.coastal.port_rotation(1, 3)
        cardinal_difference = abs(rotation / (math.pi / 2) - round(rotation / (math.pi / 2)))
        self.assertGreater(cardinal_difference, .05)
        point = geometry.candidate(1, dict(name='naval_base_spawn', coastal=True, buildings=[]), random.Random(4))
        self.assertAlmostEqual(point['rotation'], rotation, places=5)

    def test_narrow_sea_falls_back_to_its_center_without_entering_another_sea(self):
        identifiers = np.full((40, 60), 2)
        identifiers[:, :20] = 1
        identifiers[:, 20:23] = 3
        geometry = self.geometry(identifiers)
        point = geometry.coastal.floating_harbor(1, 7)
        self.assertEqual(point[:2], geometry.coastal.center(3))
        self.assertEqual(geometry.province_at(*point[:2]), 3)

    def test_largest_connected_shore_is_used_instead_of_averaging_disjoint_shores(self):
        identifiers = np.full((40, 60), 2)
        identifiers[:, :20] = 1
        identifiers[4:18, 20:] = 3
        identifiers[32:35, 20:] = 3
        geometry = self.geometry(identifiers)
        _, anchor_z = geometry.coastal.anchor(1, 3)
        self.assertGreaterEqual(anchor_z, 22)

    def test_one_pixel_island_and_world_edge_fallback_remain_in_the_selected_sea(self):
        identifiers = np.full((20, 30), 3)
        identifiers[10, 0] = 1
        geometry = self.geometry(identifiers)
        point = geometry.coastal.floating_harbor(1, 7)
        self.assertEqual(geometry.province_at(*point[:2]), 3)
        self.assertTrue(math.isfinite(point[2]))

    def test_mod_distance_override_and_unsupported_expression(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'common/buildings').mkdir(parents=True)
            (root / 'common/defines').mkdir()
            (root / 'common/buildings/00.txt').write_text('buildings = { bunker = { show_on_map = 1 } }')
            definition = root / 'common/defines/99_distance.lua'
            definition.write_text('NDefines.NGraphics.DEFAULT_NUDGE_FLOATING_HARBOR_DIST = 12.5 -- custom')
            templates, _ = load_building_templates(str(root))
            self.assertEqual(templates['floating_harbor']['coastal_distance'], 12.5)
            definition.write_text('NDefines.NGraphics.DEFAULT_NUDGE_FLOATING_HARBOR_DIST = 7 * 2')
            templates, warnings = load_building_templates(str(root))
            self.assertTrue(templates['floating_harbor']['disabled'])
            self.assertTrue(warnings)


if __name__ == '__main__':
    unittest.main()
