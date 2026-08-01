from __future__ import annotations

import os
import tempfile
import unittest

from core.definitions import MapPaths, StateInfo
from core.area_assignments import area_file_changes
from core.map_loader import load_resource_names, load_state_category_names
from core.map_saver import update_state_file
from core.state_creator import create_state
from core.state_properties import read_state_properties, update_state_properties
from main import Api


STATE_TEXT = """state = {
\tid = 12
\tname = \"STATE_12\"
\tmanpower = 120000 # keep population comment
\tstate_category = rural
\tresources = {
\t\tsteel = 3
\t\toil = 2
\t}
\thistory = {
\t\towner = AAA
\t\t1939.1.1 = {
\t\t\tadd_core_of = BBB
\t\t}
\t}
\tprovinces = { 1 2 3 }
\tlocal_supplies = 1.5
}
"""


class StatePropertiesTests(unittest.TestCase):
    def test_update_preserves_history_block_and_edits_top_level_values(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "12-Test.txt")
            with open(path, "w", encoding="utf-8") as output:
                output.write(STATE_TEXT)
            history = """history = {
\t\towner = AAA
\t\t1939.1.1 = {
\t\t\tadd_core_of = BBB
\t\t}
\t}"""

            result = update_state_properties(
                path,
                manpower=345678,
                state_category="large_city",
                resources={"steel": 7, "chromium": 4, "oil": 0},
                local_supplies=8.25,
            )

            self.assertEqual(result.manpower, 345678)
            with open(path, "r", encoding="utf-8-sig") as source:
                changed = source.read()
            self.assertIn(history, changed)
            self.assertIn("manpower = 345678 # keep population comment", changed)
            self.assertIn("state_category = large_city", changed)
            self.assertIn("chromium = 4", changed)
            self.assertNotIn("oil =", changed)
            self.assertIn("local_supplies = 8.25", changed)
            self.assertEqual(read_state_properties(path), result)
            with open(path, "rb") as source:
                self.assertFalse(source.read().startswith(b"\xef\xbb\xbf"))

    def test_editing_an_existing_bom_state_removes_the_bom(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "12-Bom.txt")
            with open(path, "w", encoding="utf-8-sig") as output:
                output.write(STATE_TEXT)
            with open(path, "rb") as source:
                self.assertTrue(source.read().startswith(b"\xef\xbb\xbf"))

            update_state_properties(
                path,
                manpower=120001,
                state_category="rural",
                resources={"steel": 3},
                local_supplies=1.5,
            )

            with open(path, "rb") as source:
                self.assertFalse(source.read().startswith(b"\xef\xbb\xbf"))

    def test_missing_safe_fields_are_inserted_before_history(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "13-Test.txt")
            with open(path, "w", encoding="utf-8") as output:
                output.write("state = {\n\tid = 13\n\thistory = { owner = AAA }\n\tprovinces = { 4 }\n}\n")

            update_state_properties(
                path,
                manpower=10,
                state_category="pastoral",
                resources={},
                local_supplies=0,
            )

            with open(path, "r", encoding="utf-8-sig") as source:
                changed = source.read()
            self.assertLess(changed.index("manpower"), changed.index("history"))
            self.assertIn("history = { owner = AAA }", changed)

    def test_api_reads_and_writes_only_safe_state_properties(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "12-Test.txt")
            with open(path, "w", encoding="utf-8") as output:
                output.write(STATE_TEXT)
            api = Api()
            api.states = [StateInfo(12, path, "STATE_12", [1, 2, 3])]
            api.state_category_names = ["rural", "large_city"]

            before = api.get_state_properties(12)
            changed = api.update_state_properties(
                12, 250000, "large_city", {"steel": 9}, 3.5
            )

            self.assertTrue(before["ok"])
            self.assertEqual(before["resources"], {"steel": 3, "oil": 2})
            self.assertTrue(changed["ok"])
            self.assertEqual(read_state_properties(path).manpower, 250000)
            with open(path, "r", encoding="utf-8-sig") as source:
                self.assertIn("add_core_of = BBB", source.read())

    def test_state_categories_are_loaded_from_common_definition(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "00_state_categories.txt")
            with open(path, "w", encoding="utf-8") as output:
                output.write(
                    "state_categories = {\n"
                    "  rural = { local_building_slots = 2 }\n"
                    "  large_city = { local_building_slots = 6 }\n"
                    "}\n"
                )
            self.assertEqual(
                load_state_category_names(folder), ["large_city", "rural"]
            )

    def test_resources_and_direct_categories_are_loaded_from_common(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            category_dir = os.path.join(folder, "state_category")
            resource_dir = os.path.join(folder, "resources")
            os.makedirs(category_dir)
            os.makedirs(resource_dir)
            with open(
                os.path.join(category_dir, "custom.txt"), "w", encoding="utf-8"
            ) as output:
                output.write("arcology = { local_building_slots = 12 }\n")
            with open(
                os.path.join(resource_dir, "00_resources.txt"), "w", encoding="utf-8"
            ) as output:
                output.write(
                    "resources = {\n"
                    "  oil = { icon_frame = 1 }\n"
                    "  steel = { icon_frame = 2 }\n"
                    "  energy = { icon_frame = 7 }\n"
                    "}\n"
                )

            self.assertEqual(load_state_category_names(category_dir), ["arcology"])
            self.assertEqual(
                load_resource_names(resource_dir), ["energy", "oil", "steel"]
            )


class StateCreatorTests(unittest.TestCase):
    def test_file_label_only_changes_state_filename_and_never_writes_yml(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            states_dir = os.path.join(folder, "history", "states")

            result = create_state(
                states_dir, 123, 'New "State"', state_category="town"
            )

            self.assertTrue(os.path.isfile(result.state_file))
            self.assertEqual(os.path.basename(result.state_file), "123-New _State_.txt")
            with open(result.state_file, "rb") as source:
                self.assertFalse(source.read().startswith(b"\xef\xbb\xbf"))
            properties = read_state_properties(result.state_file)
            self.assertEqual(properties.state_category, "town")
            with open(result.state_file, "r", encoding="utf-8-sig") as source:
                state_text = source.read()
            self.assertIn('name="STATE_123"', state_text)
            self.assertIn("provinces={\n\t}", state_text.replace("\r\n", "\n"))
            self.assertFalse(os.path.exists(os.path.join(folder, "localisation")))

    def test_api_allocates_next_state_id_and_registers_created_state(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            states_dir = os.path.join(folder, "history", "states")
            os.makedirs(states_dir)
            template_path = os.path.join(states_dir, "4-Template.txt")
            with open(template_path, "w", encoding="utf-8") as output:
                output.write(STATE_TEXT.replace("id = 12", "id = 4"))
            api = Api()
            api.paths = MapPaths(
                map_dir=os.path.join(folder, "map"),
                provinces_bmp="", definition_csv="", terrain_bmp="",
                heightmap_bmp="", world_normal_bmp="", rivers_bmp="",
                supply_nodes_txt="", railways_txt="", continent_txt="",
                default_map="", strategicregions_dir="", buildings_txt="",
                mod_root=folder, history_states_dir=states_dir,
                common_terrain_dir="",
            )
            api.states = [StateInfo(4, template_path, "STATE_4", [1])]

            result = api.create_state("테스트 스테이트", 4)

            self.assertTrue(result["ok"])
            self.assertEqual(result["state"]["id"], 5)
            self.assertEqual(result["state"]["name"], "STATE_5")
            self.assertEqual(api.states[-1].id, 5)
            self.assertEqual(
                read_state_properties(api.states[-1].file_path).state_category,
                "rural",
            )

            new_state = api.states[-1]
            assigned = api.assign_province_to_state(77, new_state.id)
            add, remove = area_file_changes(new_state, api.assignments, set())
            self.assertTrue(assigned["ok"])
            self.assertEqual(add, [77])
            self.assertEqual(remove, set())
            self.assertTrue(update_state_file(new_state, add, remove))
            with open(new_state.file_path, "r", encoding="utf-8-sig") as source:
                self.assertRegex(source.read(), r"provinces\s*=\s*\{[^}]*77")


if __name__ == "__main__":
    unittest.main()
