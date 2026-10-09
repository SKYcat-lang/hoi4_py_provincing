"""Building placement geometry and transactional generation, independent of the UI."""
from __future__ import annotations

from copy import deepcopy
from collections import defaultdict
import math
import random
import uuid

import numpy as np

from .building_document import BuildingDocument
from .building_coast import CoastalPlacement
from .building_pixels import ProvincePixelIndex


class BuildingMap:
    def __init__(self, image, provinces, assignments, heightmap):
        self.height, self.width = image.shape[:2]
        self.provinces = {province.id: province for province in provinces}
        self.assignments = dict(assignments)
        self.heightmap = heightmap
        self.identifiers = np.zeros((self.height, self.width), dtype=np.int32)
        colors = sorted(((p.r << 16 | p.g << 8 | p.b), p.id) for p in provinces)
        palette = np.array([color for color, _ in colors] + [1 << 24], dtype=np.uint32)
        province_ids = np.array([identifier for _, identifier in colors] + [0], dtype=np.int32)
        for start in range(0, self.height, 128):
            pixels = image[start:start + 128].astype(np.uint32)
            packed = pixels[..., 0] << 16 | pixels[..., 1] << 8 | pixels[..., 2]
            indices = np.searchsorted(palette, packed)
            self.identifiers[start:start + 128] = np.where(palette[indices] == packed, province_ids[indices], 0)
        self._pixel_cache = {}
        self._coast_cache = {}
        self._interior_cache = {}
        self._center_cache = {}
        self._pixel_index = ProvincePixelIndex(self.identifiers)
        self._sea_ids = np.array([p.id for p in provinces if p.type == 'sea'], dtype=np.int32)
        self._state_provinces = defaultdict(list)
        for province_id, state_id in self.assignments.items():
            if province_id in self.provinces and self.provinces[province_id].type == 'land':
                self._state_provinces[state_id].append(province_id)
        self.coastal = CoastalPlacement(self)

    def province_at(self, x, z):
        if not (math.isfinite(x) and math.isfinite(z) and 0 <= x < self.width and 0 <= z < self.height):
            return 0
        return int(self.identifiers[self.height - 1 - int(z), int(x)])

    def pixels(self, province_id):
        if province_id not in self._pixel_cache:
            self._pixel_cache[province_id] = self._pixel_index.pixels(province_id)
        return self._pixel_cache[province_id]

    def interior_choices(self, province_id):
        if province_id not in self._interior_cache:
            rows, columns = self.pixels(province_id)
            interior = np.ones(len(rows), dtype=bool)
            # Nudge prefers eight surrounding pixels belonging to the same state.
            members = self._state_provinces.get(self.assignments.get(province_id), [province_id])
            for delta_x, delta_y in ((-1, -1), (0, -1), (1, -1), (-1, 0),
                                     (1, 0), (-1, 1), (0, 1), (1, 1)):
                neighbor_rows, neighbor_columns = rows + delta_y, columns + delta_x
                valid = ((neighbor_rows >= 0) & (neighbor_rows < self.height)
                         & (neighbor_columns >= 0) & (neighbor_columns < self.width))
                indices = np.flatnonzero(valid)
                valid[indices] &= np.isin(self.identifiers[neighbor_rows[indices], neighbor_columns[indices]], members)
                interior &= valid
            choices = np.flatnonzero(interior)
            self._interior_cache[province_id] = choices if len(choices) else np.arange(len(rows))
        return self._interior_cache[province_id]

    def coast(self, province_id):
        """Actual shared edges, including the horizontal seam; lakes are excluded."""
        if province_id in self._coast_cache:
            return self._coast_cache[province_id]
        rows, columns = self.pixels(province_id)
        candidates = []
        for delta_x, delta_y in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            neighbor_y = rows + delta_y
            neighbor_x = (columns + delta_x) % self.width
            valid = (neighbor_y >= 0) & (neighbor_y < self.height)
            indices = np.flatnonzero(valid)
            targets = self.identifiers[neighbor_y[indices], neighbor_x[indices]]
            sea_neighbors = np.isin(targets, self._sea_ids)
            for index, target in zip(indices[sea_neighbors], targets[sea_neighbors]):
                candidates.append((int(columns[index]), int(rows[index]),
                                   int(neighbor_x[index]), int(neighbor_y[index]), int(target)))
        self._coast_cache[province_id] = candidates
        return candidates

    def terrain_height(self, x, z):
        if self.heightmap is None or self.heightmap.shape != self.identifiers.shape:
            raise ValueError('같은 크기의 heightmap.bmp가 있어야 지형 높이를 계산할 수 있습니다.')
        sample_x, sample_z = max(0., min(self.width - 1., x - .5)), max(0., min(self.height - 1., z - .5))
        left, bottom = int(sample_x), int(sample_z)
        right, top = min(left + 1, self.width - 1), min(bottom + 1, self.height - 1)
        horizontal, vertical = sample_x - left, sample_z - bottom
        values = self.heightmap
        low = float(values[self.height - 1 - bottom, left]) * (1 - horizontal) + float(values[self.height - 1 - bottom, right]) * horizontal
        high = float(values[self.height - 1 - top, left]) * (1 - horizontal) + float(values[self.height - 1 - top, right]) * horizontal
        return round(max(9.5, (low * (1 - vertical) + high * vertical) * .1), 2)

    def owner_province(self, record):
        return record['target'] if record['type'] == 'floating_harbor' else self.province_at(record['x'], record['z'])

    def validate(self, record, template=None):
        for field in ('x', 'y', 'z', 'rotation'):
            if not math.isfinite(record[field]):
                raise ValueError('좌표와 회전에는 유한한 숫자를 입력하세요.')
        point_id = self.province_at(record['x'], record['z'])
        owner_id = self.owner_province(record)
        owner = self.provinces.get(owner_id)
        if not owner or owner.type != 'land':
            raise ValueError('배치 대상은 육지 프로빈스여야 합니다.')
        if self.assignments.get(owner_id) != record['state']:
            raise ValueError('건물이 지정된 스테이트 밖에 있습니다.')
        if record['type'] == 'floating_harbor':
            if point_id not in {edge[4] for edge in self.coast(owner_id)}:
                raise ValueError('부유식 항구는 대상 육지와 접한 바다에 배치하세요.')
        elif record['type'] in ('naval_base', 'naval_base_spawn') or (template and (template.get('port') or 'naval_base' in template['buildings'])):
            if record['target'] not in {edge[4] for edge in self.coast(owner_id)}:
                raise ValueError('항구의 연결 대상은 해당 육지와 접한 바다여야 합니다.')
        elif template and template['coastal'] and not self.coast(owner_id):
            raise ValueError('이 건물은 바다와 접한 프로빈스에만 배치할 수 있습니다.')
        elif record['target'] != 0:
            raise ValueError('일반 건물의 연결 프로빈스는 0이어야 합니다.')

    def candidate(self, province_id, template, rng):
        target = 0
        if template['name'] == 'floating_harbor':
            x, z, angle = self.coastal.floating_harbor(province_id, template.get('coastal_distance', 7.0))
            return {'id': 'new-' + uuid.uuid4().hex, 'state': self.assignments[province_id],
                    'type': template['name'], 'x': x, 'z': z, 'y': self.terrain_height(x, z),
                    'rotation': round(angle, 6), 'target': province_id}
        if template['coastal']:
            edges = self.coast(province_id)
            if not edges:
                raise ValueError(f'프로빈스 {province_id}: 연결할 바다가 없습니다.')
            x, row, sea_x, sea_row, sea_id = rng.choice(edges)
            angle = self.coastal.port_rotation(province_id, sea_id)
            if template.get('port') or template['name'] in ('naval_base', 'naval_base_spawn') or 'naval_base' in template['buildings']:
                target = sea_id
        else:
            rows, columns = self.pixels(province_id)
            if not len(rows):
                raise ValueError(f'프로빈스 {province_id}: 지도 픽셀이 없습니다.')
            if template['centered']:
                if province_id not in self._center_cache:
                    center_x, center_z = self.coastal.center(province_id)
                    distances = (columns - center_x) ** 2 + (self.height - 1 - rows - center_z) ** 2
                    self._center_cache[province_id] = int(np.argmin(distances))
                selected = self._center_cache[province_id]
            else:
                selected = rng.choice(self.interior_choices(province_id))
            x, row = int(columns[selected]), int(rows[selected])
            angle = rng.random() * math.tau
        x, z = x + .5, self.height - row - .5
        return {'id': 'new-' + uuid.uuid4().hex, 'state': self.assignments[province_id],
                'type': template['name'], 'x': x, 'z': z,
                'y': 9.5 if template['name'] == 'floating_harbor' else self.terrain_height(x, z),
                'rotation': round(angle, 6), 'target': target}


class BuildingEditor:
    def __init__(self, path, geometry, templates, warnings, source_path=None):
        self.document = BuildingDocument(path, source_path)
        self.geometry = geometry
        self.templates = templates
        self.warnings = warnings + self.document.warnings

    def payload(self):
        return {'ok': True, 'records': [self.describe(row) for row in self.document.records.values()],
                'templates': list(self.templates.values()), 'warnings': self.warnings,
                'dirty': self.document.dirty}

    def describe(self, row):
        return {**row, 'province': self.geometry.owner_province(row)}

    def template_for(self, name):
        return self.templates.get(name) or next((entry for entry in self.templates.values()
                                                if name in entry['buildings']), None)

    def update(self, identifier, values, automatic_height):
        before = self.document.records[identifier]
        after = dict(before)
        for field in ('x', 'y', 'z', 'rotation'):
            if field in values:
                after[field] = round(float(values[field]), 6 if field == 'rotation' else 2)
        if 'target' in values:
            numeric = float(values['target'])
            if not math.isfinite(numeric) or not numeric.is_integer():
                raise ValueError('연결 프로빈스 ID는 정수여야 합니다.')
            after['target'] = int(numeric)
        if automatic_height:
            after['y'] = 9.5 if after['type'] == 'floating_harbor' else self.geometry.terrain_height(after['x'], after['z'])
        self.geometry.validate(after, self.template_for(after['type']))
        if self.geometry.owner_province(after) != self.geometry.owner_province(before):
            raise ValueError('좌표 조정은 현재 프로빈스 안에서만 가능합니다.')
        return self.apply({identifier: after})

    def apply(self, replacements):
        before = {identifier: deepcopy(self.document.records.get(identifier)) for identifier in replacements}
        for identifier, row in replacements.items():
            if row is None:
                self.document.records.pop(identifier, None)
            else:
                self.document.records[identifier] = deepcopy(row)
        return {'ok': True, 'before': before, 'after': deepcopy(replacements),
                'records': [self.describe(row) for row in replacements.values() if row is not None],
                'removed': [identifier for identifier, row in replacements.items() if row is None],
                'dirty': self.document.dirty}

    def generate(self, province_ids, names, locked_ids, randomize=False, rng=None):
        rng = rng or random.Random()
        selected = sorted(set(int(value) for value in province_ids))
        if not selected:
            raise ValueError('먼저 지도에서 프로빈스를 선택하세요.')
        if any(pid not in self.geometry.provinces or self.geometry.provinces[pid].type != 'land'
               for pid in selected):
            raise ValueError('육지 프로빈스에만 건물을 생성할 수 있습니다.')
        unassigned = [pid for pid in selected if pid not in self.geometry.assignments]
        if unassigned:
            raise ValueError(f'프로빈스 {unassigned}: 건물의 소속 주를 기록하려면 먼저 주를 할당하세요.')
        replacements, warnings = {}, []
        rows_by_type = defaultdict(list)
        for row in self.document.records.values():
            rows_by_type[row['type']].append(row)
        coastal_counts = {}
        for name in dict.fromkeys(names):
            template = self.templates.get(name)
            if not template or template['disabled'] or template['max'] <= 0:
                warnings.append(f'{name}: 자동 배치 제외 대상입니다.')
                continue
            eligible = [pid for pid in selected if not template['coastal'] or self.geometry.coast(pid)]
            if not eligible:
                warnings.append(f'{name}: 선택 영역에 적합한 위치가 없습니다.')
                continue
            matching = [row for alias in dict.fromkeys([name, *template['buildings']]) for row in rows_by_type[alias]]
            if randomize:
                for row in matching:
                    owner = self.geometry.owner_province(row)
                    if owner in eligible and row['id'] not in locked_ids:
                        candidate = self.geometry.candidate(owner, template, rng)
                        candidate['id'] = row['id']
                        replacements[row['id']] = candidate
                continue
            if template['scope'] == 'province':
                occupied = {self.geometry.owner_province(row) for row in matching}
                targets = [pid for pid in eligible if pid not in occupied]
            else:
                targets = []
                eligible_by_state = defaultdict(list)
                existing_by_state = defaultdict(int)
                for pid in eligible:
                    eligible_by_state[self.geometry.assignments[pid]].append(pid)
                for row in matching:
                    existing_by_state[row['state']] += 1
                for state_id, choices in sorted(eligible_by_state.items()):
                    expected = template['max']
                    if template['coastal']:
                        if state_id not in coastal_counts:
                            coastal_counts[state_id] = sum(bool(self.geometry.coast(pid))
                                for pid in self.geometry._state_provinces[state_id])
                        expected = min(expected, coastal_counts[state_id])
                    existing = existing_by_state[state_id]
                    targets.extend(rng.choice(choices) for _ in range(max(0, expected - existing)))
            for pid in targets:
                candidate = self.geometry.candidate(pid, template, rng)
                replacements[candidate['id']] = candidate
        for row in replacements.values():
            self.geometry.validate(row, self.templates.get(row['type']))
        result = self.apply(replacements)
        result['warnings'] = warnings
        return result

    def save(self):
        for identifier, row in self.document.records.items():
            if row != self.document.baseline.get(identifier):
                self.geometry.validate(row, self.template_for(row['type']))
        self.document.save()
        return {'ok': True, 'dirty': False, 'path': str(self.document.path)}
