"""Read Clausewitz @@b@ geometry without Blender or game runtime dependencies."""
import struct

import numpy as np


def read_mesh(path):
    data = path.read_bytes()
    if data[:4] != b'@@b@':
        raise ValueError(f'{path.name}: 지원하지 않는 메시 형식')
    root = {'properties': {}, 'children': []}
    stack = [root]
    cursor = 4

    def take(size):
        nonlocal cursor
        if size < 0 or cursor + size > len(data):
            raise ValueError(f'{path.name}: 잘린 메시 데이터')
        start = cursor
        cursor += size
        return data[start:cursor]

    while cursor < len(data):
        marker = take(1)
        if marker == b'[':
            depth = 1
            while cursor < len(data) and data[cursor:cursor + 1] == b'[':
                cursor += 1
                depth += 1
            if depth > len(stack):
                raise ValueError(f'{path.name}: 잘못된 메시 계층')
            end = data.find(b'\0', cursor)
            if end < 0:
                raise ValueError(f'{path.name}: 메시 이름 누락')
            name = take(end - cursor + 1)[:-1].decode('utf-8')
            node = {'name': name, 'properties': {}, 'children': []}
            stack = stack[:depth]
            stack[-1]['children'].append(node)
            stack.append(node)
        elif marker == b'!':
            name = take(take(1)[0]).decode('utf-8')
            kind = take(1)
            count = struct.unpack('<I', take(4))[0]
            if kind in (b'i', b'f'):
                values = np.frombuffer(take(count * 4), dtype='<i4' if kind == b'i' else '<f4')
            elif kind == b's':
                if count > len(data) - cursor:
                    raise ValueError(f'{path.name}: 잘못된 문자열 개수')
                values = [take(struct.unpack('<I', take(4))[0]).rstrip(b'\0').decode('utf-8') for _ in range(count)]
            else:
                raise ValueError(f'{path.name}: 알 수 없는 속성 형식')
            stack[-1]['properties'][name] = values
        else:
            raise ValueError(f'{path.name}: 잘못된 메시 구분자')
    meshes = []
    for objects in root['children']:
        if objects['name'] != 'object':
            continue
        for shape in objects['children']:
            for mesh_index, node in enumerate(child for child in shape['children'] if child['name'] == 'mesh'):
                properties = node['properties']
                positions = properties.get('p', np.array([]))
                triangles = properties.get('tri', np.array([], dtype=int))
                if not len(positions) or len(positions) % 3 or len(triangles) % 3:
                    raise ValueError(f'{path.name}: 잘못된 정점/삼각형 개수')
                if not np.isfinite(positions).all() or (len(triangles) and (triangles.min() < 0 or triangles.max() >= len(positions) // 3)):
                    raise ValueError(f'{path.name}: 잘못된 메시 좌표/인덱스')
                normals = properties.get('n', np.zeros(len(positions), dtype=np.float32))
                uv = properties.get('u0', np.zeros(len(positions) // 3 * 2, dtype=np.float32))
                if len(normals) != len(positions) or len(uv) != len(positions) // 3 * 2:
                    raise ValueError(f'{path.name}: 정점 속성 크기 불일치')
                if not np.isfinite(normals).all() or not np.isfinite(uv).all():
                    raise ValueError(f'{path.name}: 잘못된 노멀/UV 좌표')
                material = next((child['properties'] for child in node['children'] if child['name'] == 'material'), {})
                meshes.append(dict(name=shape['name'], index=mesh_index, positions=positions.tolist(), normals=normals.tolist(),
                                   uv=uv.tolist(), indices=triangles.tolist(),
                                   material={key: value[0] for key, value in material.items() if len(value)},
                                   skinned=any(child['name'] == 'skin' for child in node['children'])))
    if not meshes:
        raise ValueError(f'{path.name}: 표시할 메시가 없습니다.')
    return meshes
