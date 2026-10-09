"""PyWebView bridge for the building editor; changes stay staged until Save."""
from __future__ import annotations

from pathlib import Path
import re

import webview

from .building_definitions import discover_game_root, load_building_templates
from .building_editor import BuildingEditor, BuildingMap
from .building_models import BuildingModels


class BuildingApi:
    def _require_building_map(self):
        if self.paths is None or self.provinces_arr is None:
            raise ValueError('먼저 맵을 불러오세요.')

    def load_buildings(self, game_root: str = '') -> dict:
        with self._lock:
            try:
                self._require_building_map()
                if game_root:
                    if not (Path(game_root) / 'common/buildings').is_dir():
                        raise ValueError('common/buildings가 있는 게임 루트를 선택하세요.')
                    self._building_game_root = game_root
                root = self._building_game_root or discover_game_root()
                templates, warnings = load_building_templates(self.paths.mod_root, root)
                models = BuildingModels(self.paths.mod_root, root)
                for template in templates.values():
                    template['entities'] = models.entity_names(template['name'], template['mesh_count'])
                    template['model_available'] = any(name in models.entities for name in template['entities'])
                    if not template['model_available']:
                        template['disabled'] = True
                        warnings.append(f"{template['name']}: 모델 엔티티가 없어 자동 생성을 제외합니다.")
                warnings.extend(models.warnings)
                geometry = BuildingMap(self.provinces_arr, self.provinces, self.assignments, self.heightmap_arr)
                if self._building_editor is None:
                    descriptor = Path(self.paths.mod_root) / 'descriptor.mod'
                    replaces_map = descriptor.is_file() and bool(re.search(
                        r'replace_path\s*=\s*"map/?"', descriptor.read_text(encoding='utf-8-sig')))
                    inherited = Path(root) / 'map/buildings.txt' if root and not replaces_map else None
                    source = str(inherited) if inherited and inherited.is_file() else None
                    self._building_editor = BuildingEditor(self.paths.buildings_txt, geometry, templates, warnings, source)
                else:
                    self._building_editor.document.check_source()
                    self._building_editor.geometry = geometry
                    self._building_editor.templates = templates
                    self._building_editor.warnings = warnings + self._building_editor.document.warnings
                self._building_editor.models = models
                return {**self._building_editor.payload(), 'gameRoot': root}
            except Exception as error:
                return {'ok': False, 'error': str(error)}

    def get_building_model(self, name: str, variant: int = 0) -> dict:
        with self._lock:
            editor = self._building_editor
            if editor is None or not hasattr(editor, 'models'):
                return {'ok': False, 'error': '건물 편집 모드를 먼저 여세요.'}
            template = editor.template_for(name)
            if template is None:
                return {'ok': False, 'error': f'{name}: 배치점 정의가 없습니다.'}
            entities = template['entities']
            if not isinstance(variant, int) or not 0 <= variant < len(entities):
                return {'ok': False, 'error': '잘못된 모델 변형 번호'}
            return editor.models.load(entities[variant])

    def pick_building_game_root(self) -> dict:
        try:
            result = webview.windows[0].create_file_dialog(webview.FOLDER_DIALOG)
            if not result:
                return {'ok': False, 'cancelled': True}
            return self.load_buildings(result[0])
        except Exception as error:
            return {'ok': False, 'error': str(error)}

    def reload_buildings(self) -> dict:
        with self._lock:
            self._building_editor = None
        return self.load_buildings()

    def update_building(self, identifier: str, values: dict, automatic_height: bool = True) -> dict:
        with self._lock:
            try:
                if self._building_editor is None:
                    raise ValueError('건물 편집 모드를 먼저 여세요.')
                return self._building_editor.update(identifier, values, automatic_height)
            except Exception as error:
                return {'ok': False, 'error': str(error)}

    def generate_buildings(self, provinces: list, names: list, locked: list, randomize: bool = False) -> dict:
        with self._lock:
            try:
                if self._building_editor is None:
                    raise ValueError('건물 편집 모드를 먼저 여세요.')
                return self._building_editor.generate(provinces, names, set(locked), randomize)
            except Exception as error:
                return {'ok': False, 'error': str(error)}

    def restore_buildings(self, replacements: dict) -> dict:
        with self._lock:
            try:
                if self._building_editor is None:
                    raise ValueError('건물 편집 모드를 먼저 여세요.')
                return self._building_editor.apply(replacements)
            except Exception as error:
                return {'ok': False, 'error': str(error)}

    def save_buildings(self) -> dict:
        with self._lock:
            try:
                if self._building_editor is None:
                    return {'ok': True, 'dirty': False}
                self._require_building_map()
                self._building_editor.geometry = BuildingMap(
                    self.provinces_arr, self.provinces, self.assignments, self.heightmap_arr,
                )
                if self.heightmap_dirty:
                    raise ValueError('높이맵 변경이 있습니다. 상단 저장으로 지도와 건물 배치를 함께 저장하세요.')
                return self._building_editor.save()
            except Exception as error:
                return {'ok': False, 'error': str(error)}

    def _save_buildings_with_map(self):
        if self._building_editor is None or not self._building_editor.document.dirty:
            return False
        self._building_editor.geometry = BuildingMap(
            self.provinces_arr, self.provinces, self.assignments, self.heightmap_arr,
        )
        self._building_editor.save()
        return True
