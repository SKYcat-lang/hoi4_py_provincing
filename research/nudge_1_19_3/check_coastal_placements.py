"""Read-only comparison against installed vanilla placements; no game/mod writes.

Usage: python research/nudge_1_19_3/check_coastal_placements.py GAME_ROOT [sample_count]
Existing vanilla records may be hand-edited or generated against earlier maps.
"""
import json
import math
from pathlib import Path
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from core.building_editor import BuildingMap
from core.map_loader import load_definition_csv


def compare(root, sample_count):
    folder = root / 'map'
    provinces = load_definition_csv(str(folder / 'definition.csv'))
    with Image.open(folder / 'provinces.bmp') as bitmap:
        pixels = np.asarray(bitmap.convert('RGB'))
    geometry = BuildingMap(pixels, provinces, {}, None)
    records = [line.split(';') for line in (folder / 'buildings.txt').read_text().splitlines()
               if ';floating_harbor;' in line][:sample_count]
    results = []
    for fields in records:
        province_id = int(fields[6])
        x, z, rotation = geometry.coastal.floating_harbor(province_id, 7)
        error = math.hypot(x - float(fields[2]), z - float(fields[4]))
        rotation_error = abs((rotation - float(fields[5]) + math.pi) % math.tau - math.pi)
        results.append({'province': province_id, 'position_error': round(error, 3),
                        'rotation_error': round(rotation_error, 5)})
    return {'sample_count': len(results),
            'position_within_0_02_pixels': sum(row['position_error'] <= .02 for row in results),
            'rotation_within_0_01_radians': sum(row['rotation_error'] <= .01 for row in results),
            'all_generated_positions_in_adjacent_sea': all(
                geometry.province_at(*geometry.coastal.floating_harbor(row['province'], 7)[:2])
                in {edge[4] for edge in geometry.coast(row['province'])} for row in results),
            'results': results}


if __name__ == '__main__':
    print(json.dumps(compare(Path(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) > 2 else 100), indent=2))
