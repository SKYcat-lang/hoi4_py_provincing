"""Ordered Paradox asset blocks and the effective game/mod asset filesystem."""
from pathlib import Path
import re


class AssetBlock(list):
    def value(self, name, default=None):
        return next((value for key, value in reversed(self) if key == name), default)

    def blocks(self, name):
        return [value for key, value in self if key == name and isinstance(value, AssetBlock)]


def parse_asset(text):
    tokens = re.findall(r'"(?:\\.|[^"\\])*"|#[^\n]*|[{}=]|[^\s{}=#]+', text)
    tokens = [token for token in tokens if not token.startswith('#')]
    cursor = 0

    def scalar(token):
        return token[1:-1].replace('\\"', '"').replace('\\\\', '\\') if token.startswith('"') else token

    def read(nested=False):
        nonlocal cursor
        entries = AssetBlock()
        while cursor < len(tokens):
            token = tokens[cursor]
            cursor += 1
            if token == '}':
                if not nested:
                    raise ValueError('예상하지 않은 닫는 중괄호')
                return entries
            if cursor < len(tokens) and tokens[cursor] == '=':
                cursor += 1
                if cursor == len(tokens):
                    raise ValueError('설정 값 누락')
                value = tokens[cursor]
                cursor += 1
                entries.append((scalar(token), read(True) if value == '{' else scalar(value)))
            else:
                entries.append((None, read(True) if token == '{' else scalar(token)))
        if nested:
            raise ValueError('닫는 중괄호 누락')
        return entries

    return read()


class AssetFiles:
    def __init__(self, mod_root, game_root=''):
        self.mod = Path(mod_root).resolve()
        self.game = Path(game_root).resolve() if game_root else None
        descriptor = self.mod / 'descriptor.mod'
        self.replaced = []
        if descriptor.is_file():
            for key, value in parse_asset(descriptor.read_text(encoding='utf-8-sig')):
                if key == 'replace_path' and isinstance(value, str):
                    self.replaced.append(value.replace('\\', '/').strip('/').lower())

    def _replaced(self, relative):
        relative = relative.lower()
        return any(relative == prefix or relative.startswith(prefix + '/') for prefix in self.replaced)

    def files(self, directory, suffix):
        selected = {}
        for root in filter(None, (self.game, self.mod)):
            for path in sorted((root / directory).rglob('*' + suffix)):
                relative = path.relative_to(root).as_posix().lower()
                if root == self.game and self._replaced(relative):
                    continue
                selected[relative] = path
        return [selected[key] for key in sorted(selected)]

    def resolve(self, relative):
        relative = str(relative).replace('\\', '/')
        for root in filter(None, (self.mod, self.game)):
            if root == self.game and self._replaced(relative.lower()):
                continue
            path = (root / relative).resolve()
            if path.is_relative_to(root) and path.is_file():
                return path
        raise ValueError(f'모델 파일을 찾을 수 없습니다: {relative}')
