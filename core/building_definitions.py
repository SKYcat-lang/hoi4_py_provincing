"""Resolve graphical building spawn points from the active definition files."""
from __future__ import annotations

import os
import math
from pathlib import Path
import re

from .pdx_assets import AssetBlock, AssetFiles, parse_asset


def _floating_harbor_distance(mod_root, game_root):
    distance = 7.0
    files = {}
    for root in filter(None, (game_root, mod_root)):
        for path in (Path(root) / 'common/defines').glob('*.lua'):
            files[path.name] = path
    for name in sorted(files):
        text = files[name].read_text(encoding='utf-8-sig')
        text = re.sub(r'--\[\[.*?\]\]', '', text, flags=re.S)
        text = re.sub(r'--[^\n]*', '', text)
        for match in re.finditer(r'\bDEFAULT_NUDGE_FLOATING_HARBOR_DIST\s*=\s*([^,;\n}]+)', text):
            try:
                distance = float(match[1].strip())
            except ValueError:
                raise ValueError(f'{name}: 플로팅 하버 거리의 Lua 수식은 해석할 수 없습니다.') from None
            if not math.isfinite(distance) or distance < 0:
                raise ValueError(f'{name}: 플로팅 하버 거리는 0 이상의 유한한 값이어야 합니다.')
    return distance


def _blocks(text: str) -> dict:
    def dictionary(block):
        result = {}
        for key, value in block:
            if key is None:
                continue
            if isinstance(value, AssetBlock):
                result.setdefault(key, {}).update(dictionary(value))
            else:
                result[key] = value
        return result
    return dictionary(parse_asset(text))


def discover_game_root() -> str:
    """Use Steam's library manifest, without a machine-specific installation path."""
    roots = [Path(os.environ.get('PROGRAMFILES(X86)', 'C:/Program Files (x86)')) / 'Steam']
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam') as key:
            roots.insert(0, Path(winreg.QueryValueEx(key, 'SteamPath')[0]))
    except (ImportError, OSError):
        pass
    libraries = list(roots)
    for root in roots:
        manifest = root / 'steamapps/libraryfolders.vdf'
        if manifest.is_file():
            libraries.extend(Path(value.replace('\\\\', '\\')) for value in
                             re.findall(r'"path"\s+"([^"]+)"', manifest.read_text(encoding='utf-8')))
    for library in libraries:
        candidate = library / 'steamapps/common/Hearts of Iron IV'
        if (candidate / 'common/buildings').is_dir():
            return str(candidate)
    return ''


def load_building_templates(mod_root: str, game_root: str = '') -> tuple[dict, list[str]]:
    files = AssetFiles(mod_root, game_root).files('common/buildings', '.txt')
    buildings, explicit, warnings = {}, {}, []
    for path in files:
        try:
            data = _blocks(path.read_text(encoding='utf-8-sig'))
            variables = {key: value for key, value in data.items() if key.startswith('@')}

            def resolve(value, visited=()):
                if isinstance(value, dict):
                    return {key: resolve(item, visited) for key, item in value.items()}
                if isinstance(value, str) and value.startswith('@'):
                    if value in visited or value not in variables:
                        raise ValueError(f'해석할 수 없는 변수 {value}')
                    return resolve(variables[value], (*visited, value))
                return value

            buildings.update(resolve(data.get('buildings', {})))
            explicit.update(resolve(data.get('spawn_points', {})))
        except (OSError, ValueError) as error:
            warnings.append(f'{path.name}: {error}')
    templates = {}

    def template(name, data, scope, count):
        try:
            maximum = int(count)
            mesh_count = int(data.get('show_on_map_meshes', 1))
            if maximum < 0 or mesh_count < 1 or scope not in ('state', 'province'):
                raise ValueError()
            invalid = False
        except (TypeError, ValueError):
            maximum, mesh_count, invalid = 0, 1, True
            warnings.append(f'{name}: 배치 범위·개수·메시 개수 정의가 올바르지 않습니다.')
        return {'name': name, 'scope': scope, 'max': maximum, 'mesh_count': mesh_count,
                'coastal': data.get('only_costal') == 'yes',
                'centered': data.get('centered') == 'yes',
                'disabled': invalid or data.get('disable_auto_nudging') == 'yes',
                'port': data.get('is_port') == 'yes', 'buildings': []}

    for name, data in explicit.items():
        if isinstance(data, dict):
            templates[name] = template(name, data, data.get('type', 'state'), data.get('max', 1))
    for name, data in buildings.items():
        if not isinstance(data, dict):
            continue
        reference = data.get('spawn_point')
        if reference:
            if reference in templates:
                templates[reference]['buildings'].append(name)
                templates[reference]['port'] |= data.get('is_port') == 'yes'
            else:
                warnings.append(f'{name}: 배치점 {reference} 정의가 없습니다.')
            continue
        if name in explicit:
            templates[name]['disabled'] = True
            warnings.append(f'{name}: 건물과 명시적 배치점의 이름이 중복됩니다.')
            continue
        try:
            count = int(data.get('show_on_map', 0))
            limits = data.get('level_cap', {})
            scope = 'province' if int(limits.get('province_max', 0)) > 0 else 'state'
        except (TypeError, ValueError, AttributeError):
            warnings.append(f'{name}: 표시 개수 또는 레벨 제한을 해석할 수 없습니다.')
            continue
        if count <= 0:
            continue
        templates[name] = template(name, data, scope, count)
        templates[name]['buildings'].append(name)
    if buildings:
        templates['floating_harbor'] = template('floating_harbor', {'only_costal': 'yes'}, 'province', 1)
        try:
            templates['floating_harbor']['coastal_distance'] = _floating_harbor_distance(mod_root, game_root)
        except ValueError as error:
            templates['floating_harbor']['disabled'] = True
            warnings.append(str(error))
    if not files:
        warnings.append('건물 정의를 찾지 못했습니다. 게임 폴더를 지정하면 누락 생성을 사용할 수 있습니다.')
    for entry in templates.values():
        if entry['scope'] == 'province' and entry['max'] > 1:
            warnings.append(f"{entry['name']}: 넛지의 총량 계산에 따라 프로빈스별 1개를 채웁니다. 기존 추가 배치는 유지합니다.")
    return templates, warnings
