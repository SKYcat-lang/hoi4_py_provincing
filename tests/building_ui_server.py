"""Local browser test bridge backed by the real Api and disposable map files.

Run with ``python tests/building_ui_server.py``; never uses the user's mod files.
"""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from main import Api
from tests.building_model_fixture import write_model


def serve():
    with tempfile.TemporaryDirectory(prefix='hoi4-building-ui-') as temporary:
        mod = Path(temporary)
        for folder in ('map', 'history/states', 'common/buildings'):
            (mod / folder).mkdir(parents=True)
        (mod / 'descriptor.mod').write_text('replace_path="common/buildings"\nreplace_path="gfx"')
        write_model(mod)
        colors = [(80, 130, 95), (130, 145, 80), (40, 80, 150)]
        image = np.zeros((128, 256, 3), np.uint8)
        image[:, :100] = colors[0]
        image[:, 100:180] = colors[1]
        image[:, 180:] = colors[2]
        Image.fromarray(image).save(mod / 'map/provinces.bmp')
        Image.fromarray(np.full((128, 256), 110, np.uint8)).save(mod / 'map/heightmap.bmp')
        (mod / 'map/definition.csv').write_text(''.join(
            f'{index};{r};{g};{b};{"sea" if index == 3 else "land"};true;plains;1\n'
            for index, (r, g, b) in enumerate(colors, 1)))
        (mod / 'history/states/10-test.txt').write_text('state = { id = 10 name = "TEST" provinces = { 1 2 } history = { owner = AAA } }')
        (mod / 'common/buildings/00.txt').write_text('''buildings = {
          arms_factory = { show_on_map = 3 level_cap = { state_max = 20 } }
          bunker = { show_on_map = 1 level_cap = { province_max = 10 } }
          naval_base = { show_on_map = 1 spawn_point = naval_base_spawn }
        }
        spawn_points = { naval_base_spawn = { type = province max = 1 only_costal = yes } }''')
        (mod / 'map/buildings.txt').write_text('# test placement\n10;arms_factory;140.50;11;64.50;0;0\n')
        api = Api()

        class Handler(SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=str(ROOT / 'web'), **kwargs)

            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                method = self.path.removeprefix('/api/')
                if method == 'pick_map_folder':
                    result = api.load_map_folder(str(mod / 'map'))
                elif method == 'saved_building_text':
                    result = (mod / 'map/buildings.txt').read_text()
                elif method.startswith('_') or not callable(getattr(api, method, None)):
                    result = {'ok': False, 'error': 'Unknown test method'}
                else:
                    result = getattr(api, method)(*request)
                raw = json.dumps(result).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 8768), Handler)
        print('Building UI fixture ready at http://127.0.0.1:8768', flush=True)
        server.serve_forever()


if __name__ == '__main__':
    serve()
