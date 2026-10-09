"""Resolve building preview entities and lazily decode their static mesh/materials."""
import base64
from copy import deepcopy
from io import BytesIO
import math
from pathlib import PurePosixPath

from PIL import Image

from .pdx_assets import AssetBlock, AssetFiles, parse_asset
from .pdx_mesh import read_mesh


class BuildingModels:
    def __init__(self, mod_root, game_root=''):
        self.files = AssetFiles(mod_root, game_root)
        self.entities = {}
        self.meshes = {}
        self.warnings = []
        self._models = {}
        self._geometry = {}
        self._textures = {}
        self._texture_names = None
        for suffix in ('.asset', '.gfx'):
            for path in self.files.files('gfx', suffix):
                try:
                    root = parse_asset(path.read_text(encoding='utf-8-sig'))
                    for entity in root.blocks('entity'):
                        if entity.value('name'):
                            self.entities[entity.value('name')] = entity
                    for container in [root, *root.blocks('objectTypes')]:
                        for mesh in container.blocks('pdxmesh'):
                            if mesh.value('name'):
                                self.meshes[mesh.value('name')] = mesh
                except (OSError, ValueError) as error:
                    self.warnings.append(f'{path.name}: {error}')

    def entity_names(self, name, mesh_count=1):
        if name == 'floating_harbor':
            return ['floating_harbor_entity']
        return ([f'building_{name}_{index + 1}' for index in range(mesh_count)] if mesh_count > 1
                else [f'building_{name}'])

    def _entity(self, name, visited=None):
        visited = set() if visited is None else visited
        if name in visited:
            raise ValueError(f'{name}: entity copy_from 순환 참조')
        visited.add(name)
        entity = self.entities.get(name)
        if entity is None:
            raise ValueError(f'{name}: 모델 엔티티가 없습니다.')
        parent = entity.value('copy_from')
        return AssetBlock([*(self._entity(parent, visited) if parent else []), *entity])

    def _texture(self, name, mesh_file):
        candidates = [str(PurePosixPath(mesh_file).parent / name), name]
        for candidate in candidates:
            try:
                path = self.files.resolve(candidate)
                break
            except ValueError:
                path = None
        if path is None:
            # Vanilla also shares textures by basename across model folders
            # (e.g. rocket_site.mesh uses a texture under units/planes).
            if self._texture_names is None:
                self._texture_names = {}
                for texture_path in self.files.files('gfx', '.dds'):
                    self._texture_names.setdefault(texture_path.name.lower(), []).append(texture_path)
            matches = self._texture_names.get(PurePosixPath(name).name.lower(), [])
            mod_matches = [candidate for candidate in matches if candidate.is_relative_to(self.files.mod)]
            matches = mod_matches or matches
            if len(matches) != 1:
                reason = '동일 이름의 경로가 여러 개입니다' if matches else '파일이 없습니다'
                raise ValueError(f'텍스처 {name}: {reason}.')
            path = matches[0]
        if path not in self._textures:
            with Image.open(path) as texture:
                texture = texture.convert('RGBA')
                texture.thumbnail((1024, 1024))
                output = BytesIO()
                texture.save(output, format='PNG')
            self._textures[path] = 'data:image/png;base64,' + base64.b64encode(output.getvalue()).decode('ascii')
        return self._textures[path]

    def load(self, entity_name):
        if entity_name in self._models:
            return self._models[entity_name]
        try:
            entity = self._entity(entity_name)
            mesh_name = entity.value('pdxmesh')
            definition = self.meshes.get(mesh_name)
            if definition is None:
                raise ValueError(f'{entity_name}: pdxmesh {mesh_name} 정의가 없습니다.')
            mesh_file = definition.value('file')
            if not isinstance(mesh_file, str):
                raise ValueError(f'{mesh_name}: 메시 파일 경로가 없습니다.')
            path = self.files.resolve(mesh_file)
            if path not in self._geometry:
                self._geometry[path] = read_mesh(path)
            meshes = deepcopy(self._geometry[path])
            warnings = []
            for mesh in meshes:
                for settings in definition.blocks('meshsettings'):
                    if settings.value('name') == mesh['name'] and int(settings.value('index', 0)) == mesh['index']:
                        diffuse = settings.value('texture_diffuse')
                        if diffuse:
                            mesh['material']['diff'] = diffuse
                        mesh['material']['shader'] = settings.value('shader', mesh['material'].get('shader'))
                diffuse = mesh['material'].get('diff')
                try:
                    mesh['texture'] = self._texture(diffuse, mesh_file) if diffuse else None
                except (OSError, ValueError) as error:
                    mesh['texture'] = None
                    warnings.append(str(error))
            entity_scale = float(entity.value('scale', 1))
            mesh_scale = float(definition.value('scale', 1))
            scale = entity_scale * mesh_scale
            if not all(math.isfinite(value) and value > 0 for value in (entity_scale, mesh_scale, scale)):
                raise ValueError(f'{entity_name}: 잘못된 모델 크기')
            result = dict(ok=True, entity=entity_name, scale=scale, entityScale=entity_scale,
                          meshScale=mesh_scale, meshes=meshes, warnings=warnings,
                          staticPose=True)
        except (OSError, ValueError, TypeError, UnicodeError) as error:
            result = dict(ok=False, error=str(error), entity=entity_name)
        self._models[entity_name] = result
        return result
