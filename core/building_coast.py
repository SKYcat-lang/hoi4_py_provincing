"""Coastal anchors and directions recovered from HOI4 1.19.3's nudger.

See research/nudge_1_19_3/coastal_placement_followup.json for evidence and limits.
"""
import math

import numpy as np


class CoastalPlacement:
    def __init__(self, geometry):
        self.geometry = geometry
        self._centers = {}
        self._anchors = {}

    def center(self, province_id):
        if province_id not in self._centers:
            rows, columns = self.geometry.pixels(province_id)
            self._centers[province_id] = (int(columns.mean()), int((self.geometry.height - 1 - rows).mean()))
        return self._centers[province_id]

    def adjacent_sea(self, province_id, point):
        # The engine chooses among actual sea neighbors by their nearest pixel.
        candidates = sorted({edge[4] for edge in self.geometry.coast(province_id)})
        if not candidates:
            raise ValueError(f'프로빈스 {province_id}: 연결할 바다가 없습니다.')

        def distance(sea_id):
            rows, columns = self.geometry.pixels(sea_id)
            return np.min((columns - point[0]) ** 2 + (self.geometry.height - 1 - rows - point[1]) ** 2)

        return min(candidates, key=distance)

    def anchor(self, province_id, sea_id):
        key = (province_id, sea_id)
        if key in self._anchors:
            return self._anchors[key]
        # Adjacency records use the lower-ID side's unique border pixels,
        # then the largest eight-connected segment, not a random edge.
        points = set()
        for land_x, land_row, sea_x, sea_row, neighbor in self.geometry.coast(province_id):
            if neighbor != sea_id:
                continue
            x, row = (land_x, land_row) if province_id < sea_id else (sea_x, sea_row)
            points.add((x, self.geometry.height - 1 - row))
        largest = []
        while points:
            start = min(points, key=lambda point: (point[1], point[0]))
            points.remove(start)
            segment, pending = [], [start]
            while pending:
                x, z = pending.pop()
                segment.append((x, z))
                for delta_x in (-1, 0, 1):
                    for delta_z in (-1, 0, 1):
                        neighbor = ((x + delta_x) % self.geometry.width, z + delta_z)
                        if neighbor in points:
                            points.remove(neighbor)
                            pending.append(neighbor)
            if len(segment) > len(largest):
                largest = segment
        if not largest:
            raise ValueError(f'프로빈스 {province_id}: 선택한 바다와 공유하는 해안이 없습니다.')
        reference_x = largest[0][0]
        coordinates = [(reference_x + self._horizontal_delta(x, reference_x), z) for x, z in largest]
        # CFixedPoint divides each contribution before summing, then rounds.
        def average(axis):
            total = sum(math.trunc(point[axis] * 100000 / len(coordinates)) for point in coordinates)
            return math.trunc((total + (50000 if total >= 0 else -50000)) / 100000)
        anchor = (average(0) % self.geometry.width, average(1))
        self._anchors[key] = anchor
        return anchor

    def _horizontal_delta(self, destination, source):
        width = self.geometry.width
        return (destination - source + width / 2) % width - width / 2

    def port_rotation(self, province_id, sea_id):
        center_x, center_z = self.center(province_id)
        anchor_x, anchor_z = self.anchor(province_id, sea_id)
        return math.atan2(center_z - anchor_z, self._horizontal_delta(center_x, anchor_x)) - math.pi / 2

    def floating_harbor(self, province_id, distance):
        center_x, center_z = self.center(province_id)
        sea_id = self.adjacent_sea(province_id, (center_x, center_z))
        anchor_x, anchor_z = self.anchor(province_id, sea_id)
        delta_x = self._horizontal_delta(anchor_x, center_x)
        delta_z = anchor_z - center_z
        length = math.hypot(delta_x, delta_z)
        x, z = self.center(sea_id)
        if length:
            candidate_x = round(anchor_x + delta_x / length * distance, 2)
            candidate_z = round(anchor_z + delta_z / length * distance, 2)
            if self.geometry.province_at(candidate_x, candidate_z) == sea_id:
                x, z = candidate_x, candidate_z
        # Vanilla falls back to the sea's center. Concave seas can have an
        # exterior centroid: keep the fallback inside that same sea in our editor.
        if self.geometry.province_at(x, z) != sea_id:
            rows, columns = self.geometry.pixels(sea_id)
            nearest = np.argmin((columns - x) ** 2 + (self.geometry.height - 1 - rows - z) ** 2)
            x, z = int(columns[nearest]), int(self.geometry.height - 1 - rows[nearest])
        rotation = math.atan2(z - anchor_z, self._horizontal_delta(x, anchor_x)) - math.pi / 2
        return x, z, rotation
