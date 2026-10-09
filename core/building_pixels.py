"""Lazy province bounds: scan map runs once, then search only local rectangles."""
import numpy as np


class ProvincePixelIndex:
    def __init__(self, identifiers):
        self.identifiers = identifiers
        self.bounds = None

    def _index_bounds(self):
        height, width = self.identifiers.shape
        starts = np.ones((height, width), dtype=bool)
        starts[:, 1:] = self.identifiers[:, 1:] != self.identifiers[:, :-1]
        offsets = np.flatnonzero(starts)
        lengths = np.diff(np.append(offsets, height * width))
        rows, columns = np.divmod(offsets, width)
        province_ids = self.identifiers.ravel()[offsets]
        distinct, groups = np.unique(province_ids, return_inverse=True)
        minimum_rows = np.full(len(distinct), height)
        minimum_columns = np.full(len(distinct), width)
        maximum_rows = np.zeros(len(distinct), dtype=int)
        maximum_columns = np.zeros(len(distinct), dtype=int)
        np.minimum.at(minimum_rows, groups, rows)
        np.minimum.at(minimum_columns, groups, columns)
        np.maximum.at(maximum_rows, groups, rows + 1)
        np.maximum.at(maximum_columns, groups, columns + lengths)
        self.bounds = dict(zip(distinct.tolist(), zip(minimum_rows, maximum_rows,
                                                     minimum_columns, maximum_columns)))

    def pixels(self, province_id):
        if self.bounds is None:
            self._index_bounds()
        bounds = self.bounds.get(province_id)
        if bounds is None:
            return np.empty(0, dtype=np.intp), np.empty(0, dtype=np.intp)
        top, bottom, left, right = bounds
        rows, columns = np.nonzero(self.identifiers[top:bottom, left:right] == province_id)
        return rows + top, columns + left
