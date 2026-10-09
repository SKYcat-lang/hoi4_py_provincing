"""Lossless staged editing of map/buildings.txt, with conflict-safe atomic writes."""
from __future__ import annotations

from copy import deepcopy
import math
import os
from pathlib import Path
import tempfile


class BuildingDocument:
    def __init__(self, path: str, source_path: str | None = None):
        self.path = Path(path)
        self.destination_source = self.path.read_bytes() if self.path.exists() else b''
        self.source_path = self.path if self.path.exists() or not source_path else Path(source_path)
        self.source = self.source_path.read_bytes() if self.source_path.exists() else b''
        self.encoding = 'utf-8-sig' if self.source.startswith(b'\xef\xbb\xbf') else 'utf-8'
        try:
            text = self.source.decode(self.encoding)
        except UnicodeDecodeError:
            self.encoding = 'cp1252'
            text = self.source.decode(self.encoding)
        self.newline = '\r\n' if '\r\n' in text else '\n'
        self.lines = []
        self.records = {}
        self.warnings = []
        for index, line in enumerate(text.splitlines(keepends=True)):
            identifier = None
            body = line.split('#', 1)[0].strip()
            if body:
                try:
                    fields = body.split(';')
                    if len(fields) != 7:
                        raise ValueError('7개 필드가 필요합니다.')
                    identifier = f'line-{index}'
                    record = dict(zip(('x', 'y', 'z', 'rotation'), map(float, fields[2:6])))
                    if not all(math.isfinite(value) for value in record.values()):
                        raise ValueError('유한한 좌표가 필요합니다.')
                    record.update(id=identifier, state=int(fields[0]), type=fields[1].strip(), target=int(fields[6]))
                    self.records[identifier] = record
                except ValueError:
                    identifier = None
                    self.warnings.append(f'buildings.txt {index + 1}행: 해석할 수 없어 원문을 보존합니다.')
            self.lines.append((identifier, line))
        self.baseline = deepcopy(self.records)

    @property
    def dirty(self) -> bool:
        return self.records != self.baseline

    def check_source(self):
        current = self.path.read_bytes() if self.path.exists() else b''
        inherited_changed = (self.source_path != self.path and
                             self.source_path.read_bytes() != self.source)
        if current != self.destination_source or inherited_changed:
            raise ValueError('buildings.txt가 외부에서 변경되었습니다. 건물 편집을 다시 불러오세요.')

    def save(self):
        self.check_source()
        if not self.dirty:
            return
        lines, seen = [], set()

        def formatted(record, original=''):
            comment = (' #' + original.split('#', 1)[1].rstrip('\r\n')) if '#' in original else ''
            return (f"{record['state']};{record['type']};{record['x']:.2f};{record['y']:.2f};"
                    f"{record['z']:.2f};{record['rotation']:.6f};{record['target']}{comment}{self.newline}")

        for identifier, original in self.lines:
            if identifier is None:
                lines.append((None, original))
            elif identifier in self.records:
                seen.add(identifier)
                record = self.records[identifier]
                lines.append((identifier, original if record == self.baseline.get(identifier)
                              else formatted(record, original)))
        for identifier, record in self.records.items():
            if identifier not in seen:
                if lines and not lines[-1][1].endswith(('\n', '\r')):
                    previous_id, previous_line = lines[-1]
                    lines[-1] = previous_id, previous_line + self.newline
                lines.append((identifier, formatted(record)))
        raw = ''.join(line for _, line in lines).encode(self.encoding)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(dir=self.path.parent, suffix='.tmp')
        try:
            with os.fdopen(descriptor, 'wb') as output:
                output.write(raw)
            self.check_source()
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        self.source, self.lines, self.baseline = raw, lines, deepcopy(self.records)
        self.destination_source, self.source_path = raw, self.path
