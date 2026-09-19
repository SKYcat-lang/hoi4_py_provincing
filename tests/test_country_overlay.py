from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from PIL import Image

from core.country_colors import load_country_colors
from core.map_loader import load_state_files
from core.state_ownership import parse_state_owner
from main import Api


class CountryOverlayTests(unittest.TestCase):
    def test_owner_ignores_comments_strings_controller_and_dated_history(self) -> None:
        source = '''\ufeffstate = {
            name = "history = { owner = WRONG } # not a comment"
            history = {
                # owner = COMMENT
                1939.1.1 = { owner = LATER }
                owner = "AAA" controller = BBB
                effect = { owner = NESTED }
            }
        }'''
        self.assertEqual(parse_state_owner(source), "AAA")
        self.assertIsNone(parse_state_owner('state = { history = { 1939.1.1 = { owner = BBB } } }'))
        self.assertIsNone(parse_state_owner('state = { history = {} }'))

    def test_country_colors_read_map_rgb_and_hsv_but_not_ui(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "colors.txt")
            path.write_text('''\ufeff# AAA = { color = rgb { 9 9 9 } }
                AAA = { color_ui = rgb { 1 2 3 } color = rgb { 20 80 240 } }
                BBB = { color = HSV { 0.5 1 1 } color_ui = rgb { 4 5 6 } }
                CCC = { color = { 0 0 0 } }
                DDD = { color_ui = rgb { 20 30 40 } }
                BAD = { color = rgb { 300 0 0 } }
                NAN = { color = rgb { nan 0 0 } }
                SHORT = { color = rgb { 1 2 } }
            ''', encoding="utf-8")
            self.assertEqual(load_country_colors(str(path)), {
                "AAA": [20, 80, 240], "BBB": [0, 255, 255], "CCC": [0, 0, 0],
            })
            self.assertEqual(load_country_colors(str(Path(folder, "missing.txt"))), {})

    def test_map_load_and_source_save_propagate_ownership_without_reassigning_provinces(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            map_dir = root / "map"
            states_dir = root / "history" / "states"
            countries_dir = root / "common" / "countries"
            for directory in (map_dir, states_dir, countries_dir):
                directory.mkdir(parents=True)
            Image.new("RGB", (2, 2), (10, 20, 30)).save(map_dir / "provinces.bmp")
            (map_dir / "definition.csv").write_text(
                "0;0;0;0;land;false;unknown;0\n1;10;20;30;land;false;plains;1\n", encoding="utf-8",
            )
            state_path = states_dir / "7-Test.txt"
            state_path.write_text(
                'state = { id = 7 history = { owner = AAA } provinces = { 1 } }', encoding="utf-8",
            )
            (countries_dir / "colors.txt").write_text(
                'AAA = { color = rgb { 20 80 240 } }', encoding="utf-8",
            )
            self.assertEqual(load_state_files(str(states_dir))[0].owner, "AAA")
            api = Api()
            loaded = api.load_map_folder(str(root))
            self.assertTrue(loaded["ok"], loaded)
            self.assertEqual(loaded["countryColors"], {"AAA": [20, 80, 240]})
            self.assertEqual(loaded["states"][0]["owner"], "AAA")
            changed = api.update_state_source(7, 'state = { id = 7 history = { owner = BBB } provinces = { 2 } }')
            self.assertTrue(changed["ok"])
            self.assertEqual(changed["owner"], "BBB")
            self.assertEqual(api.states[0].owner, "BBB")
            self.assertEqual(api.assignments, {1: 7})
            self.assertIsNone(api.update_state_source(7, 'state = { id = 7 }')["owner"])


if __name__ == "__main__":
    unittest.main()
