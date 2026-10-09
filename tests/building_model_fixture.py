"""Small independently authored model for asset and browser integration tests."""
import struct

from PIL import Image


def write_model(root):
    folder = root / 'gfx/models/buildings'
    folder.mkdir(parents=True, exist_ok=True)
    data = bytearray(b'@@b@')

    def node(depth, name):
        data.extend(b'[' * depth + name.encode() + b'\0')

    def property(name, kind, values):
        data.extend(b'!' + bytes([len(name)]) + name.encode() + kind.encode() + struct.pack('<I', len(values)))
        for value in values:
            if kind == 's':
                encoded = value.encode() + b'\0'
                data.extend(struct.pack('<I', len(encoded)) + encoded)
            else:
                data.extend(struct.pack('<' + kind, value))

    property('pdxasset', 'i', [1, 0])
    node(1, 'object'); node(2, 'roof'); node(3, 'mesh')
    property('p', 'f', [-4, 2, -3, 4, 2, -3, 4, 2, 3, -4, 2, 3])
    property('n', 'f', [0, 1, 0] * 4)
    property('u0', 'f', [0, 0, 1, 0, 1, 1, 0, 1])
    property('tri', 'i', [0, 1, 2, 0, 2, 3])
    node(4, 'material'); property('shader', 's', ['PdxMeshAdvanced'])
    property('diff', 's', ['roof.dds'])
    (folder / 'roof.mesh').write_bytes(data)
    Image.new('RGBA', (8, 8), (220, 90, 45, 255)).save(folder / 'roof.dds')
    (folder / 'preview.gfx').write_text('''objectTypes = {
      pdxmesh = { name = "test_mesh" file = "gfx/models/buildings/roof.mesh" }
    }''')
    (folder / 'preview.asset').write_text(''.join(
        f'entity = {{ name = "{name}" pdxmesh = "test_mesh" scale = 1.5 }}\n'
        for name in ['building_arms_factory', 'building_bunker', 'building_naval_base_spawn', 'floating_harbor_entity']))
    return folder
