"""ID 전역 검색기 (감독자 도구용).

`definition.csv`의 placeholder(`id;0;0;0;land;false;unknown;0`)를 제거하고 ID를 압축
(compact)하려면, 사라질 ID들이 모드 폴더 안의 어떤 파일/어떤 라인에 등장하는지를
정확히 알아야 한다. 자동 치환은 위험하므로(특히 LOC 키와 victory_points 좌표,
buildings.txt 좌표 등 컨텍스트별로 다른 의미), 이 모듈은 "검색 + 매치 리스트 반환"만
담당하고, 실제 치환 적용은 사용자가 매치별로 Yes/No를 결정한 뒤 별도 모듈이
수행한다.

## 정책 요약 (사용자 가이드라인 반영)

1. 검색 범위 = mod_root **하위만**. 바닐라는 절대 건드리지 않음.
2. 폴더 화이트리스트로 좁히고, 블랙리스트로 잡음 폴더(gfx, sound, dlc 등) 차단.
3. 확장자 화이트리스트로 텍스트류만 허용 (BMP/DDS/이미지/사운드 제외).
4. 매칭은 **단어 경계(`\b`)** 기준. 그래야 `1234`가 `12340` 안에서 잘못 잡히지 않고,
   `VICTORY_POINTS_1234`(`_`도 단어 문자) 같은 LOC 키는 정확히 매치된다.
5. 결과는 (파일, 라인 번호, 라인 원문, 매치된 ID)로 반환. UI에서 그대로 표시.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Iterable, Optional


# ---------------------------------------------------------------------------
# 폴더/확장자 정책
# ---------------------------------------------------------------------------

# mod_root 바로 아래 단계에서 "검색 대상"으로 삼을 폴더 이름들.
# 이 외 폴더는 기본적으로 무시한다.
DEFAULT_FOLDER_WHITELIST: tuple[str, ...] = (
    "map",
    "history",
    "common",
    "events",
    "localisation",
    "localization",  # 미국식 철자(드물지만 일부 모드/언어팩에서 사용)
)

# 어떤 단계에서든 만나면 무조건 잘라내는 폴더들.
# (mod_root 직하 외에 하위 어디에 있어도 차단)
DEFAULT_FOLDER_BLACKLIST: frozenset[str] = frozenset({
    "gfx", "interface", "sound", "music", "tutorial", "tweakergui_assets",
    "pdx_browser", "cef", "dlc", "integrated_dlc", "assets", "browser",
    "country_metadata", "crash_reporter", "dlc_metadata", "documentation",
    "portraits", "previewer_assets", "pdx_online_assets", "soundtrack",
    "wiki", "script", "tests", "tools", ".git", ".svn", "__pycache__",
    "node_modules", ".venv", "venv",
})

# 검색을 허용할 텍스트 확장자.
DEFAULT_EXT_WHITELIST: frozenset[str] = frozenset({
    ".txt", ".csv", ".yml", ".yaml", ".json", ".gfx", ".gui",
    ".map", ".info", ".lua", ".asset",
})


# ---------------------------------------------------------------------------
# 데이터 클래스
# ---------------------------------------------------------------------------


@dataclass
class IdMatch:
    """단일 ID 매치 정보. UI에 그대로 직렬화되어 표시된다."""
    file_path: str      # 절대 경로
    rel_path: str       # mod_root 기준 상대 경로 (표시용)
    line_no: int        # 1-based
    line_text: str      # 원문 라인 (개행 제거)
    matched_id: int     # 어떤 ID가 잡혔는가
    # 같은 라인에 여러 ID가 있을 수도 있으니, 라인 안에서의 컬럼 범위도 같이.
    col_start: int = 0
    col_end: int = 0

    def to_dict(self) -> dict:
        return {
            "filePath": self.file_path,
            "relPath": self.rel_path,
            "lineNo": self.line_no,
            "lineText": self.line_text,
            "matchedId": self.matched_id,
            "colStart": self.col_start,
            "colEnd": self.col_end,
        }


@dataclass
class SearchConfig:
    """검색 정책 오버라이드. 기본값은 위 상수 그대로."""
    folder_whitelist: tuple[str, ...] = DEFAULT_FOLDER_WHITELIST
    folder_blacklist: frozenset[str] = DEFAULT_FOLDER_BLACKLIST
    ext_whitelist: frozenset[str] = DEFAULT_EXT_WHITELIST
    # 너무 큰 파일은 스킵(바이너리 텍스트 파일에 우연히 .txt 확장자가 붙은 경우 보호).
    max_file_bytes: int = 16 * 1024 * 1024  # 16MB


# ---------------------------------------------------------------------------
# 파일 후보 수집
# ---------------------------------------------------------------------------


def iter_target_files(mod_root: str, cfg: Optional[SearchConfig] = None) -> Iterable[str]:
    """검색 대상 파일들을 yield. mod_root 절대 경로 기준 절대 경로 반환.

    화이트리스트 폴더는 mod_root 직하에서만 본다(예: `<mod_root>/map`).
    그 안에서 어디든 블랙리스트 폴더 이름을 만나면 가지치기.
    """
    cfg = cfg or SearchConfig()
    mod_root = os.path.abspath(mod_root)
    if not os.path.isdir(mod_root):
        return

    for top in cfg.folder_whitelist:
        top_dir = os.path.join(mod_root, top)
        if not os.path.isdir(top_dir):
            continue
        for dirpath, dirnames, filenames in os.walk(top_dir):
            # 하위에서 블랙리스트 폴더 가지치기
            dirnames[:] = [d for d in dirnames if d not in cfg.folder_blacklist]
            for name in filenames:
                ext = os.path.splitext(name)[1].lower()
                if ext not in cfg.ext_whitelist:
                    continue
                full = os.path.join(dirpath, name)
                try:
                    if os.path.getsize(full) > cfg.max_file_bytes:
                        continue
                except OSError:
                    continue
                yield full


# ---------------------------------------------------------------------------
# 핵심 검색
# ---------------------------------------------------------------------------


def _build_pattern(ids: Iterable[int]) -> re.Pattern[str]:
    """주어진 ID 집합을 한 번에 매치하는 정규식.

    `\\b` 단어 경계로 부분 매치 차단. `_`는 단어 문자라 `VICTORY_POINTS_123`도
    `123` 부분이 매치된다(이게 정상 동작).
    """
    sorted_ids = sorted({int(i) for i in ids}, reverse=True)
    # 긴 숫자가 먼저 오도록 정렬(정규식 alternation 안전성).
    alt = "|".join(str(i) for i in sorted_ids)
    return re.compile(rf"\b(?:{alt})\b")


def search_ids_in_file(
    file_path: str,
    pattern: re.Pattern[str],
    id_set: set[int],
    rel_base: Optional[str] = None,
) -> list[IdMatch]:
    """단일 파일에서 ID 매치 수집. 파일 IO 실패는 빈 리스트로 폴백."""
    matches: list[IdMatch] = []
    try:
        with open(file_path, "r", encoding="utf-8-sig", errors="replace") as f:
            for line_no, line in enumerate(f, start=1):
                line_stripped = line.rstrip("\r\n")
                for m in pattern.finditer(line_stripped):
                    try:
                        mid = int(m.group(0))
                    except ValueError:
                        continue
                    if mid not in id_set:
                        continue
                    rel = os.path.relpath(file_path, rel_base) if rel_base else file_path
                    matches.append(IdMatch(
                        file_path=file_path,
                        rel_path=rel.replace("\\", "/"),
                        line_no=line_no,
                        line_text=line_stripped,
                        matched_id=mid,
                        col_start=m.start(),
                        col_end=m.end(),
                    ))
    except OSError:
        return []
    return matches


def search_ids_in_mod(
    mod_root: str,
    ids: Iterable[int],
    cfg: Optional[SearchConfig] = None,
) -> list[IdMatch]:
    """mod_root 하위 전체에서 주어진 ID들을 그랩.

    반환 순서: 파일 상대경로 사전순 → 라인 번호 오름차순 → 컬럼 오름차순.
    UI에서 그대로 표시 가능.
    """
    cfg = cfg or SearchConfig()
    id_set = {int(i) for i in ids}
    if not id_set:
        return []
    pattern = _build_pattern(id_set)
    mod_root_abs = os.path.abspath(mod_root)
    all_matches: list[IdMatch] = []
    for fp in iter_target_files(mod_root_abs, cfg):
        all_matches.extend(search_ids_in_file(fp, pattern, id_set, rel_base=mod_root_abs))
    all_matches.sort(key=lambda m: (m.rel_path, m.line_no, m.col_start))
    return all_matches


# ---------------------------------------------------------------------------
# placeholder ID 추출 (definition.csv 기준)
# ---------------------------------------------------------------------------


def find_placeholder_ids(definition_csv_path: str) -> list[int]:
    """definition.csv를 훑어 placeholder(`id;0;0;0;land;false;unknown;0` 류) ID 목록.

    엄격하게 RGB가 (0,0,0)이고 terrain == "unknown"인 행만 placeholder로 본다.
    (HOI4의 invalid slot인 ID 0은 placeholder처럼 보이지만 의미가 다르므로 제외.)
    """
    out: list[int] = []
    try:
        with open(definition_csv_path, "r", encoding="utf-8-sig", errors="replace") as f:
            for line in f:
                parts = line.strip().split(";")
                if len(parts) < 8:
                    continue
                try:
                    pid = int(parts[0])
                except ValueError:
                    continue
                if pid == 0:
                    continue  # invalid slot은 항상 0;0;0;..., placeholder가 아님
                r, g, b = parts[1], parts[2], parts[3]
                terrain = parts[6]
                if r == "0" and g == "0" and b == "0" and terrain == "unknown":
                    out.append(pid)
    except OSError:
        return []
    return out
