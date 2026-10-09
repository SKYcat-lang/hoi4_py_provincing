from pathlib import Path
import tempfile
import unittest

import numpy as np

from core.building_definitions import load_building_templates
from core.building_models import BuildingModels
from core.building_pixels import ProvincePixelIndex
from core.pdx_assets import AssetFiles, parse_asset
from core.pdx_mesh import read_mesh
from tests.building_model_fixture import write_model


class BuildingModelsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.folder = write_model(self.root)

    def test_repeated_entities_and_vectors_are_preserved(self):
        block = parse_asset('entity={name="a" position={1 2 3}} entity={name="b"} # ignored }')
        self.assertEqual([entity.value('name') for entity in block.blocks('entity')], ['a', 'b'])
        self.assertEqual([value for _, value in block.blocks('entity')[0].value('position')], ['1', '2', '3'])

    def test_geometry_texture_scale_and_mesh_reuse(self):
        models = BuildingModels(self.root)
        factory = models.load('building_arms_factory')
        self.assertTrue(factory['ok'], factory)
        self.assertEqual(factory['scale'], 1.5)
        self.assertEqual(factory['meshes'][0]['indices'], [0, 1, 2, 0, 2, 3])
        self.assertTrue(factory['meshes'][0]['texture'].startswith('data:image/png;base64,'))
        self.assertTrue(models.load('building_bunker')['ok'])
        self.assertEqual(len(models._geometry), 1)
        self.assertEqual(len(models._textures), 1)
        self.assertIs(factory, models.load('building_arms_factory'))

    def test_entity_inheritance_and_cycles(self):
        (self.folder / 'z.asset').write_text('''entity={name="child" copy_from="building_arms_factory" scale=2}
            entity={name="cycle" copy_from="cycle"}''')
        models = BuildingModels(self.root)
        self.assertEqual(models.load('child')['scale'], 2)
        self.assertFalse(models.load('cycle')['ok'])
        self.assertFalse(models.load('missing')['ok'])

    def test_mod_path_override_and_replace_path(self):
        mod = self.root / 'mod'
        mod.mkdir()
        (mod / 'descriptor.mod').write_text('# replace_path="gfx"\nreplace_path="gfx/models/buildings"')
        files = AssetFiles(mod, self.root)
        self.assertEqual(files.files('gfx', '.asset'), [])
        with self.assertRaises(ValueError):
            files.resolve('gfx/models/buildings/roof.mesh')
        write_model(mod)
        self.assertEqual(files.resolve('gfx/models/buildings/roof.mesh'), mod / 'gfx/models/buildings/roof.mesh')
        with self.assertRaises(ValueError):
            files.resolve('../outside.mesh')

    def test_diffuse_override(self):
        (self.folder / 'preview.gfx').write_text('''objectTypes={pdxmesh={name="test_mesh"
            file="gfx/models/buildings/roof.mesh" meshsettings={name="roof" index=0 texture_diffuse="missing.dds"}}}''')
        result = BuildingModels(self.root).load('building_arms_factory')
        self.assertTrue(result['ok'])
        self.assertIsNone(result['meshes'][0]['texture'])
        self.assertTrue(result['warnings'])

    def test_mesh_scale_multiplies_entity_scale(self):
        path = self.folder / 'preview.gfx'
        path.write_text(path.read_text().replace('name = "test_mesh"', 'name = "test_mesh" scale = 0.1'))
        result = BuildingModels(self.root).load('building_arms_factory')
        self.assertTrue(result['ok'], result)
        self.assertAlmostEqual(result['scale'], .15)
        self.assertEqual(result['entityScale'], 1.5)
        self.assertEqual(result['meshScale'], .1)

    def test_shared_texture_in_another_model_folder(self):
        shared = self.root / 'gfx/models/units'
        shared.mkdir()
        (self.folder / 'roof.dds').rename(shared / 'roof.dds')
        result = BuildingModels(self.root).load('building_arms_factory')
        self.assertTrue(result['ok'], result)
        self.assertTrue(result['meshes'][0]['texture'])
        self.assertEqual(result['warnings'], [])

    def test_truncated_mesh_rejected_without_breaking_other_models(self):
        mesh = self.folder / 'roof.mesh'
        mesh.write_bytes(mesh.read_bytes()[:-1])
        with self.assertRaises(ValueError):
            read_mesh(mesh)
        self.assertFalse(BuildingModels(self.root).load('building_arms_factory')['ok'])

    def test_variables_repeated_roots_custom_scope_and_shared_name(self):
        folder = self.root / 'common/buildings'
        folder.mkdir(parents=True)
        (folder / 'custom.txt').write_text('''@count=2
          buildings={ custom={show_on_map=@count level_cap={state_max=5}} }
          buildings={ other={spawn_point=shared} }
          spawn_points={ shared={type=state max=1 centered=yes} invalid={type=ocean max=1} }
        ''')
        templates, warnings = load_building_templates(str(self.root))
        self.assertEqual(templates['custom']['max'], 2)
        self.assertEqual(templates['shared']['buildings'], ['other'])
        self.assertTrue(templates['shared']['centered'])
        self.assertTrue(templates['invalid']['disabled'])
        self.assertTrue(warnings)

    def test_building_and_explicit_spawn_collision_is_reported(self):
        folder = self.root / 'common/buildings'
        folder.mkdir(parents=True)
        (folder / 'custom.txt').write_text('buildings={same={show_on_map=1}} spawn_points={same={type=state max=1}}')
        templates, warnings = load_building_templates(str(self.root))
        self.assertTrue(templates['same']['disabled'])
        self.assertTrue(any('중복' in warning for warning in warnings))

    def test_province_bounds_preserve_disconnected_and_sparse_pixels(self):
        random = np.random.default_rng(123)
        identifiers = random.choice([0, 1, 3, 999999], size=(17, 29))
        index = ProvincePixelIndex(identifiers)
        for province_id in [0, 1, 3, 999999, 5]:
            actual = index.pixels(province_id)
            expected = np.nonzero(identifiers == province_id)
            np.testing.assert_array_equal(actual[0], expected[0])
            np.testing.assert_array_equal(actual[1], expected[1])


if __name__ == '__main__':
    unittest.main()
