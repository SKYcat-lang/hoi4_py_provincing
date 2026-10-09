"""Repeatable CPU placement benchmark; no game/mod files are modified."""
import argparse
import json
from pathlib import Path
import random
import sys
import tempfile
from time import perf_counter

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.building_editor import BuildingEditor, BuildingMap
from core.definitions import Province


def benchmark():
    # 512 land provinces, 64 states, 12 custom building types: 4,608 records.
    width, height, cell = 2048, 1024, 64
    identifiers = (np.arange(height)[:, None] // cell * (width // cell)
                   + np.arange(width)[None, :] // cell + 1)
    image = np.stack((identifiers % 256, identifiers // 256, np.zeros_like(identifiers)), axis=2).astype(np.uint8)
    provinces = [Province(identifier, identifier % 256, identifier // 256, 0)
                 for identifier in range(1, 513)]
    assignments = {province.id: (province.id - 1) // 8 + 1 for province in provinces}
    templates = {f'custom_{index}': dict(name=f'custom_{index}', scope='state', max=6,
                 coastal=False, centered=False, disabled=False, buildings=[]) for index in range(12)}
    started = perf_counter()
    geometry = BuildingMap(image, provinces, assignments, np.full((height, width), 110, np.uint8))
    initialization = perf_counter() - started
    with tempfile.TemporaryDirectory() as temporary:
        editor = BuildingEditor(str(Path(temporary) / 'buildings.txt'), geometry, templates, [])
        started = perf_counter()
        result = editor.generate(list(assignments), list(templates), set(), rng=random.Random(42))
        generation = perf_counter() - started
        started = perf_counter()
        repeated = editor.generate(list(assignments), list(templates), set(), True, random.Random(42))
        randomization = perf_counter() - started
    return dict(map_size=[width, height], provinces=512, states=64, types=12,
                records=len(result['records']), randomized=len(repeated['records']),
                initialization_seconds=initialization, generation_seconds=generation,
                randomization_seconds=randomization)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output')
    arguments = parser.parse_args()
    result = benchmark()
    serialized = json.dumps(result, indent=2)
    if arguments.output:
        Path(arguments.output).write_text(serialized + '\n', encoding='utf-8')
    print(serialized)
