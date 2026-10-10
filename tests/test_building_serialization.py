"""Building files must end at the final record, as vanilla files do."""
from pathlib import Path
import tempfile
import unittest

from core.building_document import BuildingDocument
from core.external_files import update_buildings_txt


class BuildingSerializationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'buildings.txt'

    def test_edit_append_and_delete_keep_eof_without_line_separator(self):
        for newline in (b'\n', b'\r\n'):
            for ending in (b'', newline, newline * 2):
                with self.subTest(newline=newline, ending=ending):
                    first = b'10;factory;1;11;2;0;0'
                    second = b'20;factory;3;11;4;0;0'
                    self.path.write_bytes(first + newline + second + ending)
                    document = BuildingDocument(str(self.path))
                    document.records['line-1']['x'] = 5
                    document.save()
                    self.assertEqual(self.path.read_bytes(), first + newline +
                                     b'20;factory;5.00;11.00;4.00;0.000000;0')
                    document.records['new'] = dict(document.records['line-1'], id='new', state=30)
                    document.save()
                    reloaded = BuildingDocument(str(self.path))
                    self.assertEqual(len(reloaded.records), 3)
                    self.assertEqual(reloaded.warnings, [])
                    self.assertFalse(self.path.read_bytes().endswith((b'\r', b'\n')))
                    del document.records['new']
                    del document.records['line-1']
                    document.save()
                    self.assertEqual(self.path.read_bytes(), first)
                    del document.records['line-0']
                    document.save()
                    self.assertEqual(self.path.read_bytes(), b'')

    def test_new_document_has_no_trailing_newline(self):
        document = BuildingDocument(str(self.path))
        document.records['new'] = dict(id='new', state=10, type='factory',
                                       x=1, y=11, z=2, rotation=0, target=0)
        document.save()
        self.assertEqual(self.path.read_bytes(), b'10;factory;1.00;11.00;2.00;0.000000;0')

    def test_port_removal_does_not_leave_trailing_separator(self):
        for newline in (b'\n', b'\r\n'):
            for ending in (b'', newline, newline * 2):
                with self.subTest(newline=newline, ending=ending):
                    kept = b'10;factory;1;11;2;0;0'
                    removed = b'10;naval_base_spawn;3;11;4;0;100'
                    self.path.write_bytes(kept + newline + removed + ending)
                    result = update_buildings_txt(str(self.path), {100})
                    self.assertEqual(result['removedLines'], 1)
                    self.assertEqual(self.path.read_bytes(), kept)
                    self.path.write_bytes(removed + ending)
                    update_buildings_txt(str(self.path), {100})
                    self.assertEqual(self.path.read_bytes(), b'')
