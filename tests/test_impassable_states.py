from pathlib import Path
import tempfile
import unittest

from core.definitions import StateInfo
from core.map_loader import load_state_files
from core.state_properties import parse_state_impassable, read_state_properties
from main import Api


STATE_SOURCE = '''state = {
    id = 12
    name = "STATE_12"
    manpower = 100
    state_category = rural
    # impassable = yes
    force_link_ownership_to = 13
    impassable_ignored_links = { 14 }
    history = {
        owner = AAA
        1939.1.1 = { impassable = yes }
    }
    provinces = { 1 2 }
}
'''


class ImpassableStateTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "12-Test.txt"
        self.path.write_text(STATE_SOURCE, encoding="utf-8")
        self.api = Api()
        self.api.states = [StateInfo(12, str(self.path), "STATE_12", [1, 2])]

    def save(self, *impassable):
        return self.api.update_state_properties(12, 100, "rural", {}, 0, *impassable)

    def test_missing_flag_comments_and_nested_history_do_not_block_state(self):
        self.assertFalse(parse_state_impassable(STATE_SOURCE))
        self.assertFalse(self.api.get_state_properties(12)["impassable"])
        self.assertFalse(load_state_files(self.folder.name)[0].impassable)

    def test_enable_disable_round_trip_preserves_history_and_other_flags(self):
        original_history = STATE_SOURCE.split("    history = ")[1].split("    provinces")[0]
        for blocked in (True, False, True):
            with self.subTest(blocked=blocked):
                result = self.save(blocked)
                self.assertTrue(result["ok"], result)
                self.assertEqual(result["impassable"], blocked)
                self.assertEqual(self.api.states[0].impassable, blocked)
                self.assertEqual(read_state_properties(str(self.path)).impassable, blocked)
                self.assertEqual(load_state_files(self.folder.name)[0].impassable, blocked)
                saved = self.path.read_text(encoding="utf-8")
                self.assertIn(original_history, saved)
                self.assertIn("force_link_ownership_to = 13", saved)
                self.assertIn("impassable_ignored_links = { 14 }", saved)
                self.assertEqual(saved.count("    impassable = "), 1)

    def test_legacy_api_call_preserves_existing_flag_and_inline_comment(self):
        self.path.write_text(STATE_SOURCE.replace(
            "    # impassable = yes", "    impassable = yes # keep comment"
        ), encoding="utf-8")
        self.assertTrue(self.save()["impassable"])
        self.assertIn("impassable = yes # keep comment", self.path.read_text())
        self.assertFalse(self.save(False)["impassable"])
        self.assertIn("impassable = no # keep comment", self.path.read_text())

    def test_non_boolean_flag_fails_without_writing(self):
        for invalid in ("no", "yes", 1, 0):
            self.assertFalse(self.save(invalid)["ok"])
            self.assertEqual(self.path.read_text(), STATE_SOURCE)

    def test_source_editor_refreshes_impassable_state(self):
        source = STATE_SOURCE.replace("    # impassable = yes", "    impassable = yes")
        result = self.api.update_state_source(12, source)
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["impassable"])
        self.assertTrue(self.api.states[0].impassable)
        result = self.api.update_state_source(12, STATE_SOURCE)
        self.assertFalse(result["impassable"])

    def test_inline_fields_can_be_read_and_updated_without_consuming_neighbors(self):
        self.path.write_text(
            'state = { id = 12 manpower = 100 state_category = rural '
            'impassable = yes history = { owner = AAA } provinces = { 1 2 } }',
            encoding="utf-8",
        )
        self.assertTrue(load_state_files(self.folder.name)[0].impassable)
        result = self.save(False)
        self.assertTrue(result["ok"], result)
        self.assertFalse(read_state_properties(str(self.path)).impassable)
        saved = self.path.read_text()
        self.assertIn("id = 12", saved)
        self.assertIn("history = { owner = AAA }", saved)
        self.assertIn("provinces = { 1 2 }", saved)
        self.assertEqual(load_state_files(self.folder.name)[0].province_ids, [1, 2])


if __name__ == "__main__":
    unittest.main()
