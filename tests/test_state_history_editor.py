from __future__ import annotations

import os
import tempfile
import unittest

import numpy as np

from core.definitions import Province, StateInfo
from core.state_neighbors import rank_adjacent_states
from core.state_properties import (
    read_state_history_block,
    read_state_source,
    update_state_history_block,
    update_state_source,
)
from main import Api


class StateHistoryEditorTests(unittest.TestCase):
    def test_full_source_editor_writes_every_field_without_applying_guards(self) -> None:
        original = """state = {
\tid = 7
\tprovinces = { 1 }
}
"""
        edited = """state = {
\tid = 99
\tname = \"STATE_99\"
\tprovinces = { 2 3 4 }
\thistory = { owner = BBB }
}
"""
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "7-Test.txt")
            with open(path, "w", encoding="utf-8") as output:
                output.write(original)

            saved = update_state_source(path, edited)

            self.assertEqual(saved, edited)
            self.assertEqual(read_state_source(path), edited)

    def test_full_source_editor_only_removes_utf8_bom(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "7-Test.txt")
            update_state_source(path, "\ufeffnot even valid state syntax")

            with open(path, "rb") as source:
                raw = source.read()
            self.assertEqual(raw, b"not even valid state syntax")
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))

    def test_api_source_save_does_not_reload_the_loaded_map_model(self) -> None:
        original = "state = { id = 7 provinces = { 1 } }"
        edited = "state = { id = 99 provinces = { 2 3 } }"
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "7-Test.txt")
            with open(path, "w", encoding="utf-8") as output:
                output.write(original)
            api = Api()
            api.states = [StateInfo(7, path, "STATE_7", [1])]
            api.assignments = {1: 7}

            result = api.update_state_source(7, edited)

            self.assertTrue(result["ok"])
            self.assertEqual(read_state_source(path), edited)
            self.assertEqual(api.states[0].id, 7)
            self.assertEqual(api.states[0].province_ids, [1])
            self.assertEqual(api.assignments, {1: 7})

    def test_history_replacement_preserves_other_state_code_and_removes_bom(self) -> None:
        original = """state = {
\tid = 7
\tmanpower = 100
\thistory = {
\t\towner = AAA
\t}
\tprovinces = { 1 }
}
"""
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "7-Test.txt")
            with open(path, "w", encoding="utf-8-sig") as output:
                output.write(original)

            saved = update_state_history_block(
                path,
                "history = {\n\t\towner = BBB\n\t\tadd_core_of = BBB\n\t}",
            )

            self.assertEqual(read_state_history_block(path), saved)
            with open(path, "r", encoding="utf-8") as source:
                changed = source.read()
            self.assertIn("manpower = 100", changed)
            self.assertIn("provinces = { 1 }", changed)
            self.assertIn("owner = BBB", changed)
            self.assertNotIn("owner = AAA", changed)
            with open(path, "rb") as source:
                self.assertFalse(source.read().startswith(b"\xef\xbb\xbf"))

    def test_history_editor_rejects_non_history_state_code(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "7-Test.txt")
            with open(path, "w", encoding="utf-8") as output:
                output.write("state = { id = 7 history = { } provinces = { 1 } }")
            with self.assertRaisesRegex(ValueError, "history"):
                update_state_history_block(path, "state = { history = { } }")

    def test_neighbours_are_ranked_by_shared_pixel_boundary(self) -> None:
        red = (255, 0, 0)
        green = (0, 255, 0)
        blue = (0, 0, 255)
        yellow = (255, 255, 0)
        arr = np.zeros((5, 5, 3), dtype=np.uint8)
        arr[:, :] = blue
        arr[1:4, 1:4] = red
        arr[0, 1:4] = green
        arr[1:4, 4] = yellow
        provinces = [
            Province(1, *red),
            Province(2, *green),
            Province(3, *blue),
            Province(4, *yellow),
        ]
        assignments = {1: 10, 2: 20, 3: 30, 4: 40}

        ranked = rank_adjacent_states(arr, provinces, assignments, 10, limit=3)

        self.assertEqual([item.state_id for item in ranked], [30, 20, 40])
        self.assertTrue(all(item.relation == "border" for item in ranked))
        self.assertGreater(ranked[0].shared_edges, ranked[1].shared_edges)

    def test_connected_provinces_are_ranked_before_distance_fallback(self) -> None:
        red = (255, 0, 0)
        green = (0, 255, 0)
        blue = (0, 0, 255)
        arr = np.zeros((9, 9, 3), dtype=np.uint8)
        arr[4, 4] = red
        arr[6, 5] = green
        arr[8, 8] = blue
        provinces = [
            Province(1, *red),
            Province(2, *green),
            Province(3, *blue),
        ]
        assignments = {1: 10, 2: 20, 3: 30}

        ranked = rank_adjacent_states(
            arr,
            provinces,
            assignments,
            10,
            connected_province_pairs=[(1, 3)],
            limit=3,
        )

        self.assertEqual([item.state_id for item in ranked], [30, 20])
        self.assertEqual(ranked[0].relation, "connection")
        self.assertEqual(ranked[0].connection_count, 1)
        self.assertEqual(ranked[1].relation, "nearby")

    def test_nearest_states_are_returned_when_no_state_is_adjacent(self) -> None:
        red = (255, 0, 0)
        green = (0, 255, 0)
        blue = (0, 0, 255)
        arr = np.zeros((11, 11, 3), dtype=np.uint8)
        arr[5, 5] = red
        arr[5, 7] = green
        arr[0, 0] = blue
        provinces = [
            Province(1, *red),
            Province(2, *green),
            Province(3, *blue),
        ]
        assignments = {1: 10, 2: 20, 3: 30}

        ranked = rank_adjacent_states(
            arr, provinces, assignments, 10, limit=3
        )

        self.assertEqual([item.state_id for item in ranked], [20, 30])
        self.assertTrue(all(item.relation == "nearby" for item in ranked))
        self.assertLess(ranked[0].distance, ranked[1].distance)


if __name__ == "__main__":
    unittest.main()
