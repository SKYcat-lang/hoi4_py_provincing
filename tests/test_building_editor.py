from copy import deepcopy
from pathlib import Path
import random
import tempfile
import unittest

import numpy as np

from core.building_definitions import load_building_templates
from core.building_document import BuildingDocument
from core.building_editor import BuildingEditor, BuildingMap
from core.definitions import Province
from main import Api


def template(name='factory', scope='state', count=3, coastal=False):
    return dict(name=name, scope=scope, max=count, coastal=coastal,
                centered=False, disabled=False, buildings=[])


class BuildingEditorTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'buildings.txt'
        self.provinces = [Province(1, 100, 0, 0), Province(2, 0, 100, 0),
                          Province(3, 0, 0, 100, type='sea'), Province(4, 100, 100, 0, type='lake')]
        self.image = np.zeros((5, 8, 3), dtype=np.uint8)
        self.image[:, :2] = self.provinces[0].rgb
        self.image[:, 2:4] = self.provinces[1].rgb
        self.image[:, 4:7] = self.provinces[2].rgb
        self.image[:, 7:] = self.provinces[3].rgb
        self.geometry = BuildingMap(self.image, self.provinces, {1: 10, 2: 10}, np.full((5, 8), 110, np.uint8))

    def editor(self, *templates):
        return BuildingEditor(str(self.path), self.geometry, {entry['name']: entry for entry in templates}, [])

    def test_untouched_bytes_comments_bom_and_undo_after_save(self):
        source = b'\xef\xbb\xbf# header\r\n10;factory;1.50;11;2.50;0;0 # keep\r\nopaque malformed\r\n'
        self.path.write_bytes(source)
        editor = self.editor(template())
        result = editor.update('line-1', {'x': 1.6}, True)
        editor.save()
        output = self.path.read_bytes()
        self.assertTrue(output.startswith(b'\xef\xbb\xbf# header\r\n'))
        self.assertTrue(output.endswith(b'# keep\r\nopaque malformed'))
        self.assertIn(b'1.60;11.00;2.50', output)
        editor.apply(result['before'])
        editor.save()
        self.assertEqual(editor.document.records['line-1']['x'], 1.5)

    def test_unedited_file_is_exactly_preserved(self):
        source = b'# comment\n10;factory;1.50;11;2.50;0;0'
        self.path.write_bytes(source)
        self.editor().save()
        self.assertEqual(self.path.read_bytes(), source)

    def test_inherited_file_is_copied_losslessly_only_when_edited(self):
        original = self.path.parent / 'vanilla.txt'
        source = '# vanilla\n10;factory;1.50;11;2.50;0;0\n'
        original.write_text(source)
        document = BuildingDocument(str(self.path), str(original))
        self.assertEqual(len(document.records), 1)
        document.save()
        self.assertFalse(self.path.exists())
        document.records['line-1']['x'] = 1.6
        document.save()
        self.assertIn('1.60', self.path.read_text())
        self.assertEqual(original.read_text(), source)
        document.records['line-1']['x'] = 1.7
        document.save()
        self.assertIn('1.70', self.path.read_text())

    def test_legacy_building_names_count_toward_shared_point(self):
        self.path.write_text('10;old_factory;1.50;11;2.50;0;0\n')
        shared = template('factory_spawn', count=1)
        shared['buildings'] = ['old_factory', 'new_factory']
        editor = self.editor(shared)
        result = editor.generate([2], ['factory_spawn'], set())
        self.assertEqual(result['records'], [])

    def test_external_file_conflict_preserves_other_writer(self):
        editor = self.editor(template('bunker', 'province', 1))
        editor.generate([1], ['bunker'], set())
        self.path.write_text('# another writer\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '외부'):
            editor.save()
        self.assertEqual(self.path.read_text(), '# another writer\n')

    def test_provincial_fill_is_idempotent_and_undoable(self):
        editor = self.editor(template('bunker', 'province', 1))
        first = editor.generate([1, 2], ['bunker'], set(), rng=random.Random(5))
        self.assertEqual(len(first['records']), 2)
        self.assertEqual(len(editor.generate([1, 2], ['bunker'], set())['records']), 0)
        editor.apply(first['before'])
        self.assertEqual(editor.document.records, {})
        editor.apply(first['after'])
        self.assertEqual(len(editor.document.records), 2)

    def test_state_deficit_counts_unselected_provinces(self):
        self.path.write_text('10;factory;1.50;11;2.50;0;0\n')
        editor = self.editor(template())
        result = editor.generate([2], ['factory'], set())
        self.assertEqual(len(result['records']), 2)
        self.assertTrue(all(row['province'] == 2 for row in result['records']))
        self.assertEqual(editor.document.records['line-0']['x'], 1.5)

    def test_ports_have_opposite_target_semantics_and_lakes_do_not_count(self):
        editor = self.editor(template('naval_base_spawn', 'province', 1, True),
                             template('floating_harbor', 'province', 1, True))
        result = editor.generate([1, 2], list(editor.templates), set())
        self.assertEqual(len(result['records']), 2)  # province 1 only borders a lake, not sea
        port = next(row for row in result['records'] if row['type'] == 'naval_base_spawn')
        floating = next(row for row in result['records'] if row['type'] == 'floating_harbor')
        self.assertEqual(port['target'], 3)
        self.assertEqual(self.geometry.province_at(port['x'], port['z']), 2)
        self.assertEqual(floating['target'], 2)
        self.assertEqual(self.geometry.province_at(floating['x'], floating['z']), 3)

    def test_invalid_edit_and_invalid_target_leave_document_unchanged(self):
        editor = self.editor(template('naval_base_spawn', 'province', 1, True))
        row = editor.generate([2], list(editor.templates), set())['records'][0]
        baseline = deepcopy(editor.document.records)
        for changes in ({'x': 8}, {'x': float('nan')}, {'target': 4}, {'target': 3.5}, {'x': 1.5}):
            with self.assertRaises(ValueError):
                editor.update(row['id'], changes, True)
            self.assertEqual(editor.document.records, baseline)

    def test_serialized_rounding_is_validated(self):
        self.path.write_text('10;factory;1.50;11;2.50;0;0\n')
        editor = self.editor(template())
        with self.assertRaises(ValueError):
            editor.update('line-0', {'x': 1.999}, True)  # rounds into province 2

    def test_rotation_subdegree_edits_accumulate(self):
        self.path.write_text('10;factory;1.50;11;2.50;0;0\n')
        editor = self.editor(template())
        for _ in range(3):
            row = editor.document.records['line-0']
            editor.update(row['id'], {'rotation': row['rotation'] + np.pi / 1800}, True)
        self.assertAlmostEqual(editor.document.records['line-0']['rotation'], np.pi / 600, places=5)

    def test_locked_and_unselected_positions_survive_randomize(self):
        editor = self.editor(template('bunker', 'province', 1))
        first = editor.generate([1, 2], ['bunker'], set())['records']
        locked = first[0]['id']
        baseline = deepcopy(editor.document.records)
        self.assertEqual(editor.generate([1], ['bunker'], {locked}, True)['records'], [])
        self.assertEqual(editor.document.records, baseline)

    def test_thin_island_and_horizontal_seam(self):
        self.image[:] = self.provinces[2].rgb
        self.image[2, 0] = self.provinces[0].rgb
        geometry = BuildingMap(self.image, self.provinces, {1: 10}, np.full((5, 8), 100, np.uint8))
        row = geometry.candidate(1, template('bunker', 'province', 1), random.Random(1))
        self.assertEqual(geometry.province_at(row['x'], row['z']), 1)
        self.assertTrue(any(edge[2] == 7 for edge in geometry.coast(1)))
        self.assertEqual(geometry.province_at(-.1, 1), 0)
        self.assertEqual(geometry.province_at(8, 1), 0)

    def test_height_sampling_bottom_origin_and_interpolation(self):
        self.geometry.heightmap = np.arange(40, dtype=np.uint8).reshape(5, 8) + 100
        self.assertEqual(self.geometry.terrain_height(.5, .5), 13.2)
        self.assertAlmostEqual(self.geometry.terrain_height(1, 1), 12.85)

    def test_combined_map_save_writes_pending_building_edits(self):
        api = Api()
        api.provinces_arr = self.image
        api.provinces = self.provinces
        api.assignments = {1: 10, 2: 10}
        api.heightmap_arr = self.geometry.heightmap
        api._building_editor = self.editor(template('bunker', 'province', 1))
        api._building_editor.generate([1], ['bunker'], set())
        self.assertTrue(api._save_buildings_with_map())
        self.assertFalse(api._building_editor.document.dirty)
        self.assertIn(';bunker;', self.path.read_text())

    def test_unsaved_geometry_and_assignments_do_not_block_mode_entry(self):
        api = Api()
        api.paths = object()
        api.provinces_arr = self.image.copy()
        api.disk_provinces_arr = self.image.copy()
        api.provinces_arr[0, 0] = self.provinces[1].rgb
        api.assignments = {1: 20, 2: 10}
        api._require_building_map()

    def test_changed_map_membership_is_revalidated_before_writing(self):
        api = Api()
        api.provinces_arr = self.image
        api.provinces = self.provinces
        api.assignments = {1: 20, 2: 10}
        api.heightmap_arr = self.geometry.heightmap
        api._building_editor = self.editor(template('bunker', 'province', 1))
        api._building_editor.generate([1], ['bunker'], set())
        with self.assertRaisesRegex(ValueError, '스테이트'):
            api._save_buildings_with_map()
        self.assertFalse(self.path.exists())

    def test_definition_file_override_shared_spawns_and_unsupported_max(self):
        root = Path(self.directory.name)
        vanilla, mod = root / 'game', root / 'mod'
        for folder in (vanilla, mod):
            (folder / 'common/buildings').mkdir(parents=True)
        (vanilla / 'common/buildings/00.txt').write_text('buildings = { obsolete = { show_on_map = 8 } }')
        (mod / 'common/buildings/00.txt').write_text('''
        buildings = {
          naval_base = { province_max = 99 show_on_map = 1 show_on_map_meshes = 3 spawn_point = shared }
          naval_facility = { spawn_point = shared }
          bunker = { show_on_map = 1 level_cap = { province_max = 10 } }
        }
        spawn_points = {
          shared = { type = province max = 1 only_costal = yes }
          unusual = { type = province max = 2 }
          landmark = { type = province max = 1 disable_auto_nudging = yes }
        }''')
        templates, warnings = load_building_templates(str(mod), str(vanilla))
        self.assertNotIn('obsolete', templates)
        self.assertNotIn('naval_base', templates)
        self.assertEqual(templates['shared']['buildings'], ['naval_base', 'naval_facility'])
        self.assertEqual(templates['bunker']['max'], 1)
        self.assertFalse(templates['unusual']['disabled'])
        self.assertTrue(warnings)
        self.assertTrue(templates['landmark']['disabled'])


if __name__ == '__main__':
    unittest.main()
