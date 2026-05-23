"""사라진 프로빈스 ID를 흡수자 ID로 재매핑하거나 단순 제거하는 외부 파일 갱신기.

설계 메모
---------
core/delete.py가 {removed_id: absorber_id} 사전을 만들면,
이 모듈이 그 사전을 받아 다음 파일들을 일관된 방식으로 갱신한다.

  map/buildings.txt          : 단순 제거 (사용자 결정: nudge 재생성)
  map/unitstacks.txt         : 단순 제거 (동일 사유)
  map/positions.txt          : 단순 제거
  map/weatherpositions.txt   : 단순 제거
  map/supply_nodes.txt       : 흡수자로 재매핑 + dedupe
  map/railways.txt           : 노드 교체 + 연속 중복 dedupe + n 갱신
  map/adjacencies.csv        : From/To/Through 재매핑 + self-loop 행 제거
  history/states/*.txt       : provinces={} 외에 victory_points / buildings 블록 갱신
  history/units/*.txt        : location = N 재매핑
  common/decisions/*.txt     : set_province_name = { id = N ... } 재매핑

각 함수는 (path, absorption_map) 형태로 호출하고
{ "changed": bool, "removedLines": int, "remappedTokens": int } 정보를 반환.
파일이 없으면 조용히 패스한다.
"""
from __future__ import annotations

import os
import re
from typing import Iterable, Optional


# ---------------------------------------------------------------------------
# 공통 헬퍼
# ---------------------------------------------------------------------------


def _read_text(path: str) -> tuple[str, str]:
    """파일을 읽고 (text, encoding_used) 반환.

    BOM 유무를 정확히 추적해야 _write_text가 원본대로 재기록한다.
    파이썬의 utf-8-sig는 BOM이 없는 파일도 그냥 읽어버려 BOM 유무를 잃기 때문에,
    raw 바이트로 BOM 마커를 직접 확인한다.
    """
    with open(path, "rb") as f:
        raw = f.read()
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", errors="replace"), "utf-8-sig"
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace"), "cp1252"


def _write_text(path: str, text: str, encoding: str) -> None:
    """원래 인코딩을 보존해 다시 쓴다.

    BOM 유무는 _read_text가 추적한 encoding 값으로 결정된다.
    bytes 모드로 직접 쓰는 이유: text 모드의 'utf-8-sig'는 매번 BOM을 새로 붙이므로,
    BOM이 원래 없던 파일을 utf-8-sig로 잘못 분류하면 오염이 발생할 수 있어서.
    """
    if encoding == "utf-8-sig":
        with open(path, "wb") as f:
            f.write(b"\xef\xbb\xbf")
            f.write(text.encode("utf-8"))
    elif encoding == "utf-8":
        with open(path, "wb") as f:
            f.write(text.encode("utf-8"))
    else:
        with open(path, "wb") as f:
            f.write(text.encode(encoding, errors="replace"))


def _detect_newline(text: str) -> str:
    """원본의 주된 줄바꿈 스타일을 추정 ('\r\n' 또는 '\n')."""
    if "\r\n" in text:
        return "\r\n"
    return "\n"


def _split_lines_preserve(text: str) -> tuple[list[str], str]:
    """줄바꿈 스타일을 보존하며 줄 단위로 split.

    반환: (lines_without_terminator, terminator)
    """
    nl = _detect_newline(text)
    # 양쪽 표기 모두를 한 번에 자르기 위해 정규식 사용
    raw_lines = re.split(r"\r\n|\n", text)
    return raw_lines, nl


# ---------------------------------------------------------------------------
# map/buildings.txt — "단순 제거"
# ---------------------------------------------------------------------------


def update_buildings_txt(path: str, removed_ids: set[int]) -> dict:
    """첫 컬럼(= 건물이 속한 prov_id) 또는 마지막 컬럼(= naval_base_spawn 등이
    가리키는 prov_id)이 removed_ids에 포함된 행을 모두 제거.

    형식: `prov_id;type;X;height;Y;rot;naval_target_prov_id_or_0`
    """
    if not os.path.isfile(path) or not removed_ids:
        return {"changed": False, "removedLines": 0, "remappedTokens": 0}

    text, enc = _read_text(path)
    lines, nl = _split_lines_preserve(text)

    out: list[str] = []
    removed = 0
    for line in lines:
        stripped = line.strip()
        if not stripped:
            out.append(line)
            continue
        parts = stripped.split(";")
        try:
            first = int(parts[0])
        except (ValueError, IndexError):
            out.append(line)
            continue
        if first in removed_ids:
            removed += 1
            continue
        # 마지막 컬럼: naval_base_spawn / floating_harbor의 target prov
        if len(parts) >= 7:
            try:
                last = int(parts[6])
            except ValueError:
                last = 0
            if last and last in removed_ids:
                removed += 1
                continue
        out.append(line)

    if removed == 0:
        return {"changed": False, "removedLines": 0, "remappedTokens": 0}

    _write_text(path, nl.join(out), enc)
    return {"changed": True, "removedLines": removed, "remappedTokens": 0}


# ---------------------------------------------------------------------------
# map/unitstacks.txt — "단순 제거"
# ---------------------------------------------------------------------------


def update_unitstacks_txt(path: str, removed_ids: set[int]) -> dict:
    """첫 컬럼(prov_id)이 removed_ids에 포함된 행 모두 제거.

    형식: `prov_id;stack_idx;X;height;Y;rot;scale`
    """
    if not os.path.isfile(path) or not removed_ids:
        return {"changed": False, "removedLines": 0, "remappedTokens": 0}

    text, enc = _read_text(path)
    lines, nl = _split_lines_preserve(text)

    out: list[str] = []
    removed = 0
    for line in lines:
        stripped = line.strip()
        if not stripped:
            out.append(line)
            continue
        parts = stripped.split(";")
        try:
            first = int(parts[0])
        except (ValueError, IndexError):
            out.append(line)
            continue
        if first in removed_ids:
            removed += 1
            continue
        out.append(line)

    if removed == 0:
        return {"changed": False, "removedLines": 0, "remappedTokens": 0}

    _write_text(path, nl.join(out), enc)
    return {"changed": True, "removedLines": removed, "remappedTokens": 0}


# ---------------------------------------------------------------------------
# map/positions.txt, weatherpositions.txt — "단순 제거" (첫 컬럼 prov_id)
# ---------------------------------------------------------------------------


def update_positions_like(path: str, removed_ids: set[int]) -> dict:
    """positions.txt / weatherpositions.txt 공통 처리.

    형식: `prov_id;...` 또는 `prov_id;X;...` (둘 다 첫 컬럼이 prov_id).
    """
    if not os.path.isfile(path) or not removed_ids:
        return {"changed": False, "removedLines": 0, "remappedTokens": 0}

    text, enc = _read_text(path)
    lines, nl = _split_lines_preserve(text)

    out: list[str] = []
    removed = 0
    for line in lines:
        stripped = line.strip()
        if not stripped:
            out.append(line)
            continue
        parts = stripped.split(";")
        try:
            first = int(parts[0])
        except (ValueError, IndexError):
            out.append(line)
            continue
        if first in removed_ids:
            removed += 1
            continue
        out.append(line)

    if removed == 0:
        return {"changed": False, "removedLines": 0, "remappedTokens": 0}

    _write_text(path, nl.join(out), enc)
    return {"changed": True, "removedLines": removed, "remappedTokens": 0}


# ---------------------------------------------------------------------------
# map/supply_nodes.txt — "흡수자로 재매핑 + dedupe"
# ---------------------------------------------------------------------------


def update_supply_nodes_txt(path: str, absorption_map: dict[int, int]) -> dict:
    """공급 노드의 prov_id를 흡수자 ID로 재매핑.

    형식: `level prov_id` (공백 구분, 한 줄에 하나).
    같은 prov_id가 여러 번 등장하지 않게 dedupe.
    흡수 매핑에 없는 removed_id (= 안전망 없음)는 단순 제거.
    """
    if not os.path.isfile(path):
        return {"changed": False, "removedLines": 0, "remappedTokens": 0}

    removed_ids = set(absorption_map.keys())
    if not removed_ids:
        return {"changed": False, "removedLines": 0, "remappedTokens": 0}

    text, enc = _read_text(path)
    lines, nl = _split_lines_preserve(text)

    seen_prov_ids: set[int] = set()
    out: list[str] = []
    removed_lines = 0
    remapped = 0

    for line in lines:
        stripped = line.strip()
        if not stripped:
            out.append(line)
            continue
        parts = stripped.split()
        if len(parts) < 2:
            out.append(line)
            continue
        try:
            level = int(parts[0])
            prov_id = int(parts[1])
        except ValueError:
            out.append(line)
            continue

        if prov_id in removed_ids:
            new_id = absorption_map.get(prov_id)
            if new_id is None:
                removed_lines += 1
                continue
            if new_id in seen_prov_ids:
                # 흡수자가 이미 supply_node를 가지고 있음 → 중복 제거
                removed_lines += 1
                continue
            seen_prov_ids.add(new_id)
            remapped += 1
            # 공백 보존 어려우므로 단순 포맷
            trailing_space = " " if line.endswith(" ") else ""
            out.append(f"{level} {new_id}{trailing_space}")
        else:
            if prov_id in seen_prov_ids:
                # 이전에 다른 줄이 흡수자로 이미 들어왔다 → 중복
                removed_lines += 1
                continue
            seen_prov_ids.add(prov_id)
            out.append(line)

    if removed_lines == 0 and remapped == 0:
        return {"changed": False, "removedLines": 0, "remappedTokens": 0}

    _write_text(path, nl.join(out), enc)
    return {"changed": True, "removedLines": removed_lines, "remappedTokens": remapped}


# ---------------------------------------------------------------------------
# map/railways.txt — "노드 교체 + 연속 중복 dedupe + n 갱신"
# ---------------------------------------------------------------------------


def update_railways_txt(path: str, absorption_map: dict[int, int]) -> dict:
    """철도 경로 노드 시퀀스에서 사라진 prov_id를 흡수자로 교체.

    형식: `level n p1 p2 ... pn` (한 줄에 하나의 철도).
    교체 후 연속 중복 노드는 dedupe(같은 프로빈스를 두 번 지나는 건 의미 없음).
    노드 수 n도 새 길이로 갱신. 길이 < 2 가 되면 라인 통째 제거.

    흡수 매핑에 없는 removed_id는 라인에서 그 노드만 빼고 이어붙임.
    """
    if not os.path.isfile(path):
        return {"changed": False, "removedLines": 0, "remappedTokens": 0}

    text, enc = _read_text(path)
    lines, nl = _split_lines_preserve(text)

    out: list[str] = []
    removed_lines = 0
    remapped = 0
    line_changed = False
    removed_ids = set(absorption_map.keys())

    for line in lines:
        stripped = line.strip()
        if not stripped:
            out.append(line)
            continue
        parts = stripped.split()
        if len(parts) < 3:
            out.append(line)
            continue
        try:
            level = int(parts[0])
            # parts[1] = 노드 수, 신뢰 안 함 — 실제 토큰으로 다시 셈
            nodes = [int(t) for t in parts[2:]]
        except ValueError:
            out.append(line)
            continue

        new_nodes: list[int] = []
        any_change = False
        for nid in nodes:
            if nid in removed_ids:
                replacement = absorption_map.get(nid)
                if replacement is None:
                    # 흡수자 없음 → 그냥 노드 스킵
                    any_change = True
                    continue
                # 직전 노드와 같으면 dedupe
                if new_nodes and new_nodes[-1] == replacement:
                    any_change = True
                    continue
                new_nodes.append(replacement)
                remapped += 1
                any_change = True
            else:
                if new_nodes and new_nodes[-1] == nid:
                    # 이전 교체 결과로 동일 노드 생성됐을 가능성
                    any_change = True
                    continue
                new_nodes.append(nid)

        if not any_change:
            out.append(line)
            continue

        line_changed = True
        if len(new_nodes) < 2:
            # 철도 노드가 1개 이하면 더는 의미 없음 → 라인 제거
            removed_lines += 1
            continue

        # 원래 라인 끝 공백 보존 시도
        trailing_space = " " if line.endswith(" ") else ""
        rebuilt = f"{level} {len(new_nodes)} " + " ".join(str(n) for n in new_nodes)
        out.append(rebuilt + trailing_space)

    if not line_changed:
        return {"changed": False, "removedLines": 0, "remappedTokens": 0}

    _write_text(path, nl.join(out), enc)
    return {"changed": True, "removedLines": removed_lines, "remappedTokens": remapped}


# ---------------------------------------------------------------------------
# map/adjacencies.csv — "From/To/Through 재매핑 + self-loop 제거"
# ---------------------------------------------------------------------------


def update_adjacencies_csv(path: str, absorption_map: dict[int, int]) -> dict:
    """`From;To;Type;Through;start_x;start_y;stop_x;stop_y;adjacency_rule_name;Comment`

    From/To 재매핑. From == To 가 되면 self-loop → 행 삭제.
    Through 컬럼도 -1이 아니면 재매핑(통과 프로빈스도 살아있어야 의미 있음).
    Through가 -1이 아니고 사라진 ID인데 흡수자도 없으면 → 행 삭제(통과 불가).
    """
    if not os.path.isfile(path):
        return {"changed": False, "removedLines": 0, "remappedTokens": 0}

    removed_ids = set(absorption_map.keys())
    if not removed_ids:
        return {"changed": False, "removedLines": 0, "remappedTokens": 0}

    text, enc = _read_text(path)
    lines, nl = _split_lines_preserve(text)

    out: list[str] = []
    removed_lines = 0
    remapped = 0
    line_changed = False

    for i, line in enumerate(lines):
        # 헤더(첫 줄, From으로 시작)와 빈 줄은 그대로
        if i == 0 and line.startswith("From;"):
            out.append(line)
            continue
        if not line.strip():
            out.append(line)
            continue
        parts = line.split(";")
        if len(parts) < 4:
            out.append(line)
            continue

        try:
            f_id = int(parts[0])
            t_id = int(parts[1])
            through = int(parts[3]) if parts[3].strip() not in ("", "-1") else -1
        except ValueError:
            out.append(line)
            continue

        any_change = False

        if f_id in removed_ids:
            new_f = absorption_map.get(f_id)
            if new_f is None:
                # 흡수자 없음 → self-loop와 같은 처리: 행 삭제
                removed_lines += 1
                line_changed = True
                continue
            f_id = new_f
            remapped += 1
            any_change = True

        if t_id in removed_ids:
            new_t = absorption_map.get(t_id)
            if new_t is None:
                removed_lines += 1
                line_changed = True
                continue
            t_id = new_t
            remapped += 1
            any_change = True

        if through != -1 and through in removed_ids:
            new_th = absorption_map.get(through)
            if new_th is None:
                # 통과 노드가 사라지고 흡수자도 없음 → 행 삭제
                removed_lines += 1
                line_changed = True
                continue
            through = new_th
            remapped += 1
            any_change = True

        if f_id == t_id:
            # self-loop → 의미 없음
            removed_lines += 1
            line_changed = True
            continue

        if any_change:
            parts[0] = str(f_id)
            parts[1] = str(t_id)
            if parts[3].strip() not in ("", "-1"):
                parts[3] = str(through)
            out.append(";".join(parts))
            line_changed = True
        else:
            out.append(line)

    if not line_changed:
        return {"changed": False, "removedLines": 0, "remappedTokens": 0}

    _write_text(path, nl.join(out), enc)
    return {"changed": True, "removedLines": removed_lines, "remappedTokens": remapped}


# ---------------------------------------------------------------------------
# history/states/*.txt — provinces 외 victory_points / buildings 블록 갱신
# ---------------------------------------------------------------------------


def _find_block_range(text: str, start: int) -> Optional[tuple[int, int]]:
    """text[start]가 '{'를 가리킨다고 가정하고, 매칭 '}'의 위치까지 (start_of_inner, end_excl)을 반환.

    내부 중첩 블록 처리. 못 찾으면 None.
    """
    if start >= len(text) or text[start] != "{":
        return None
    depth = 1
    j = start + 1
    inner_start = j
    while j < len(text) and depth > 0:
        ch = text[j]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return inner_start, j  # inner_start ~ j-1 가 내부, j가 닫는 '}'
        j += 1
    return None


def _remap_victory_points_block(inner: str, absorption_map: dict[int, int],
                                removed_ids: set[int]) -> tuple[str, int, int]:
    """`victory_points = { id N id N ... }` 의 내부(중괄호 안 텍스트) 재작성.

    같은 prov_id가 여러 번 나오면 N 합산. 흡수자 없는 removed_id는 항목 제거.
    반환: (new_inner, remapped_count, removed_pairs)
    """
    # 토큰 파싱: 정수 토큰만 추출 (주석은 통상 # 이후 줄 끝 — 라인 단위로 다뤄야 정확)
    # 단순화 위해 줄 단위로 파싱하되, 정수 페어를 추출.
    # 라인 안 # 주석은 제거.
    cleaned_lines: list[str] = []
    for raw in inner.splitlines():
        # 주석 제거
        no_cmt = re.sub(r"#.*", "", raw)
        cleaned_lines.append(no_cmt)
    body = "\n".join(cleaned_lines)
    nums = re.findall(r"-?\d+", body)
    if len(nums) < 2:
        return inner, 0, 0

    pairs: list[tuple[int, int]] = []
    for i in range(0, len(nums) - 1, 2):
        try:
            pid = int(nums[i])
            val = int(nums[i + 1])
        except ValueError:
            continue
        pairs.append((pid, val))

    out_map: dict[int, int] = {}
    remapped = 0
    removed_pairs = 0
    for pid, val in pairs:
        if pid in removed_ids:
            new_id = absorption_map.get(pid)
            if new_id is None:
                removed_pairs += 1
                continue
            remapped += 1
            out_map[new_id] = out_map.get(new_id, 0) + val
        else:
            out_map[pid] = out_map.get(pid, 0) + val

    if remapped == 0 and removed_pairs == 0:
        return inner, 0, 0

    # 새 내부 텍스트 — 원래 들여쓰기를 추정해서 적용
    # 간단히: 한 줄에 하나씩 들여쓰기 1탭으로
    indent_match = re.search(r"\n([ \t]+)", inner)
    indent = indent_match.group(1) if indent_match else "\t\t\t"
    body_lines = [f"{indent}{pid} {val}" for pid, val in out_map.items()]
    new_inner = "\n" + "\n".join(body_lines) + "\n" + indent[:-1] if indent else ""
    if not new_inner:
        new_inner = " " + " ".join(f"{pid} {val}" for pid, val in out_map.items()) + " "
    return new_inner, remapped, removed_pairs


def _remap_buildings_block(inner: str, absorption_map: dict[int, int],
                           removed_ids: set[int]) -> tuple[str, int, int]:
    """`buildings = { ... infrastructure = N ... 3838 = { naval_base = 3 } ... }` 처리.

    숫자로 보이는 키(prov_id)만 재매핑. 같은 prov_id 블록이 여러 번 나오면
    내부 키별로 값 합산.
    """
    # 토큰 단위 스캐닝 (간단하게 줄 단위 / 블록 단위 처리)
    # `(\d+)\s*=\s*\{ ... \}` 패턴을 모두 찾는다.
    out_text = inner
    remapped = 0
    removed_count = 0

    # 단순화: 정규식으로 prov_id = { ... } 형태만 검출 (중첩 블록은 한 단계만 고려)
    # 더 안전하게 하려면 _find_block_range를 써야 한다. 여기서는 한 단계 깊이만 처리.
    pattern = re.compile(r"(\b\d+\b)\s*=\s*\{([^{}]*)\}")

    # 동일 prov_id 블록 합치는 작업이 필요 — 두 패스로 진행
    # 패스 1: 매칭된 prov_id 블록들의 (start, end, inner_content) 모음
    matches: list[tuple[int, int, int, str]] = []
    for m in pattern.finditer(inner):
        try:
            pid = int(m.group(1))
        except ValueError:
            continue
        matches.append((m.start(), m.end(), pid, m.group(2)))

    if not matches:
        return inner, 0, 0

    # 결과 prov_id 블록 모음. 중복 prov_id는 내부 키 합산.
    aggregated: dict[int, dict[str, int]] = {}
    # 내부 키 = 줄 단위 "key = value" 파싱(정수 값만 합산, 문자열/블록 값은 마지막 것 유지)
    aggregated_other: dict[int, list[str]] = {}

    def parse_block(inner_text: str) -> tuple[dict[str, int], list[str]]:
        kv_int: dict[str, int] = {}
        others: list[str] = []
        for raw_line in inner_text.splitlines():
            no_cmt = re.sub(r"#.*", "", raw_line).strip()
            if not no_cmt:
                continue
            mkv = re.match(r"([A-Za-z_][\w]*)\s*=\s*(-?\d+)\s*$", no_cmt)
            if mkv:
                k = mkv.group(1)
                v = int(mkv.group(2))
                kv_int[k] = kv_int.get(k, 0) + v
            else:
                others.append(no_cmt)
        return kv_int, others

    # 패스 2: 변경 필요 여부 판단
    any_change = False
    for (s, e, pid, content) in matches:
        if pid in removed_ids:
            new_id = absorption_map.get(pid)
            if new_id is None:
                removed_count += 1
                any_change = True
                continue
            kv_int, others = parse_block(content)
            cur = aggregated.setdefault(new_id, {})
            for k, v in kv_int.items():
                cur[k] = cur.get(k, 0) + v
            aggregated_other.setdefault(new_id, []).extend(others)
            remapped += 1
            any_change = True
        else:
            kv_int, others = parse_block(content)
            cur = aggregated.setdefault(pid, {})
            for k, v in kv_int.items():
                cur[k] = cur.get(k, 0) + v
            aggregated_other.setdefault(pid, []).extend(others)

    if not any_change:
        return inner, 0, 0

    # 결과 빌드: 매칭된 영역들을 모두 제거하고, 합쳐진 블록으로 다시 삽입
    # 매칭 영역 외 텍스트(공백/주석/일반 key=value)는 유지.

    # 매칭 영역을 빈 문자열로 치환 (역순으로)
    pieces: list[str] = []
    last_end = 0
    for (s, e, pid, content) in matches:
        pieces.append(inner[last_end:s])
        last_end = e
    pieces.append(inner[last_end:])
    leftover = "".join(pieces)

    # 합쳐진 블록을 leftover 뒤에 붙이기
    # 들여쓰기 추정
    indent_match = re.search(r"\n([ \t]+)\S", inner)
    indent = indent_match.group(1) if indent_match else "\t\t\t"
    rebuild_lines: list[str] = []
    for pid in sorted(aggregated.keys()):
        rebuild_lines.append(f"{indent}{pid} = {{")
        kv = aggregated[pid]
        others = aggregated_other.get(pid, [])
        for k, v in kv.items():
            rebuild_lines.append(f"{indent}\t{k} = {v}")
        for o in others:
            rebuild_lines.append(f"{indent}\t{o}")
        rebuild_lines.append(f"{indent}}}")

    new_text = leftover.rstrip() + "\n" + "\n".join(rebuild_lines) + "\n"
    return new_text, remapped, removed_count


def update_state_file_blocks(path: str, absorption_map: dict[int, int]) -> dict:
    """state 파일의 `victory_points = { ... }`와 `buildings = { ... }` 블록 갱신.

    `provinces = { ... }` 블록은 map_saver.update_state_file이 별도로 처리하므로
    여기서는 건드리지 않는다.
    """
    if not os.path.isfile(path):
        return {"changed": False, "removedLines": 0, "remappedTokens": 0}

    removed_ids = set(absorption_map.keys())
    if not removed_ids:
        return {"changed": False, "removedLines": 0, "remappedTokens": 0}

    text, enc = _read_text(path)
    original = text
    remapped_total = 0
    removed_total = 0

    # victory_points / buildings 블록 갱신.
    # 한 파일에 같은 키워드 블록이 여러 번 등장할 수 있으므로,
    # 매 반복 시 search_start를 진전시켜 모든 등장을 처리한다.
    new_text = text
    for keyword, handler in (
        ("victory_points", _remap_victory_points_block),
        ("buildings", _remap_buildings_block),
    ):
        pat = re.compile(r"\b" + re.escape(keyword) + r"\s*=\s*\{")
        search_start = 0
        while True:
            m = pat.search(new_text, search_start)
            if not m:
                break
            brace_pos = m.end() - 1  # '{' 위치
            rng = _find_block_range(new_text, brace_pos)
            if rng is None:
                break
            inner_start, end_brace = rng
            inner = new_text[inner_start:end_brace]
            new_inner, remapped, removed_pairs = handler(inner, absorption_map, removed_ids)
            if remapped == 0 and removed_pairs == 0:
                # 이 블록은 변화 없음 — 위치만 진전시켜 다음 동일 키워드 블록을 찾는다.
                search_start = end_brace + 1
                continue
            remapped_total += remapped
            removed_total += removed_pairs
            new_text = new_text[:inner_start] + new_inner + new_text[end_brace:]
            # 새 inner 길이만큼 위치 보정 후 다음 블록 찾기
            search_start = inner_start + len(new_inner) + 1

    if new_text == original:
        return {"changed": False, "removedLines": 0, "remappedTokens": 0}

    _write_text(path, new_text, enc)
    return {"changed": True, "removedLines": removed_total, "remappedTokens": remapped_total}


# ---------------------------------------------------------------------------
# history/units/*.txt — `location = N`
# ---------------------------------------------------------------------------


def update_unit_history_file(path: str, absorption_map: dict[int, int]) -> dict:
    """history/units/*.txt 의 `location = N` 토큰 재매핑.

    흡수자가 없으면(=removed_id 그대로) 토큰을 0으로 바꿔도 게임이 에러를 내므로,
    안전하게 매핑 가능한 경우만 재매핑하고, 매핑이 없으면 그대로 둔다.
    (스폰 위치가 사라진 유닛은 게임이 인접지로 자동 이동시키는 경우가 많음)
    """
    if not os.path.isfile(path):
        return {"changed": False, "removedLines": 0, "remappedTokens": 0}

    removed_ids = set(absorption_map.keys())
    if not removed_ids:
        return {"changed": False, "removedLines": 0, "remappedTokens": 0}

    text, enc = _read_text(path)
    remapped = 0

    def repl(m: re.Match) -> str:
        nonlocal remapped
        try:
            pid = int(m.group(1))
        except ValueError:
            return m.group(0)
        if pid not in removed_ids:
            return m.group(0)
        new_id = absorption_map.get(pid)
        if new_id is None:
            return m.group(0)
        remapped += 1
        return m.group(0).replace(m.group(1), str(new_id), 1)

    new_text = re.sub(r"\blocation\s*=\s*(\d+)", repl, text)
    if remapped == 0:
        return {"changed": False, "removedLines": 0, "remappedTokens": 0}

    _write_text(path, new_text, enc)
    return {"changed": True, "removedLines": 0, "remappedTokens": remapped}


# ---------------------------------------------------------------------------
# common/decisions/*.txt — `set_province_name = { id = N name = ... }`
# ---------------------------------------------------------------------------


def update_set_province_name_in_file(path: str, absorption_map: dict[int, int]) -> dict:
    """`set_province_name = { id = N name = ... }` 블록의 id를 재매핑.

    스크립트 의도 보존: id만 흡수자로 바꾼다.
    매핑이 없는 ID는 그대로 둔다(게임이 무시할 가능성).
    """
    if not os.path.isfile(path):
        return {"changed": False, "removedLines": 0, "remappedTokens": 0}

    removed_ids = set(absorption_map.keys())
    if not removed_ids:
        return {"changed": False, "removedLines": 0, "remappedTokens": 0}

    text, enc = _read_text(path)

    pat = re.compile(
        r"(set_province_name\s*=\s*\{[^}]*?\bid\s*=\s*)(\d+)",
        re.DOTALL,
    )
    remapped = 0

    def repl(m: re.Match) -> str:
        nonlocal remapped
        try:
            pid = int(m.group(2))
        except ValueError:
            return m.group(0)
        if pid not in removed_ids:
            return m.group(0)
        new_id = absorption_map.get(pid)
        if new_id is None:
            return m.group(0)
        remapped += 1
        return m.group(1) + str(new_id)

    new_text = pat.sub(repl, text)
    if remapped == 0:
        return {"changed": False, "removedLines": 0, "remappedTokens": 0}

    _write_text(path, new_text, enc)
    return {"changed": True, "removedLines": 0, "remappedTokens": remapped}


# ---------------------------------------------------------------------------
# 디렉터리 단위 일괄 처리
# ---------------------------------------------------------------------------


def apply_to_dir(
    dir_path: str,
    pattern_suffix: str,
    handler,
    *args,
) -> dict:
    """dir_path 아래 *.{pattern_suffix} 파일 모두에 handler를 적용해 합계 리턴."""
    out = {"changed": False, "removedLines": 0, "remappedTokens": 0, "modifiedFiles": []}
    if not os.path.isdir(dir_path):
        return out
    for fname in sorted(os.listdir(dir_path)):
        if not fname.endswith(pattern_suffix):
            continue
        full = os.path.join(dir_path, fname)
        r = handler(full, *args)
        if r.get("changed"):
            out["changed"] = True
            out["removedLines"] += r.get("removedLines", 0)
            out["remappedTokens"] += r.get("remappedTokens", 0)
            out["modifiedFiles"].append(full)
    return out


def apply_absorption_to_all(
    map_dir: str,
    mod_root: str,
    absorption_map: dict[int, int],
    *,
    process_buildings: bool = True,
    process_unitstacks: bool = True,
    process_positions: bool = True,
    process_weather: bool = True,
    process_supply_nodes: bool = True,
    process_railways: bool = True,
    process_adjacencies: bool = True,
    process_states: bool = True,
    process_units: bool = True,
    process_decisions: bool = True,
) -> dict:
    """흡수 매핑을 받아 모든 외부 파일을 일괄 갱신.

    map_dir   : provinces.bmp가 있는 폴더 (보통 mod/map)
    mod_root  : 그 부모 폴더 (history/, common/ 등 접근용)
    absorption_map: {removed_id: absorber_id}

    반환: 파일별 변경 통계 + 종합 modifiedFiles 리스트.
    """
    summary: dict = {
        "modifiedFiles": [],
        "totalRemovedLines": 0,
        "totalRemappedTokens": 0,
        "perFile": {},
    }
    removed_ids = set(absorption_map.keys())
    if not absorption_map:
        return summary

    def record(name: str, r: dict, path: str | None = None) -> None:
        summary["perFile"][name] = r
        if r.get("changed"):
            summary["totalRemovedLines"] += r.get("removedLines", 0)
            summary["totalRemappedTokens"] += r.get("remappedTokens", 0)
            if path and path not in summary["modifiedFiles"]:
                summary["modifiedFiles"].append(path)
            if "modifiedFiles" in r:
                for f in r["modifiedFiles"]:
                    if f not in summary["modifiedFiles"]:
                        summary["modifiedFiles"].append(f)

    if process_buildings:
        path = os.path.join(map_dir, "buildings.txt")
        record("buildings.txt", update_buildings_txt(path, removed_ids), path)

    if process_unitstacks:
        path = os.path.join(map_dir, "unitstacks.txt")
        record("unitstacks.txt", update_unitstacks_txt(path, removed_ids), path)

    if process_positions:
        path = os.path.join(map_dir, "positions.txt")
        record("positions.txt", update_positions_like(path, removed_ids), path)

    if process_weather:
        path = os.path.join(map_dir, "weatherpositions.txt")
        record("weatherpositions.txt", update_positions_like(path, removed_ids), path)

    if process_supply_nodes:
        path = os.path.join(map_dir, "supply_nodes.txt")
        record("supply_nodes.txt", update_supply_nodes_txt(path, absorption_map), path)

    if process_railways:
        path = os.path.join(map_dir, "railways.txt")
        record("railways.txt", update_railways_txt(path, absorption_map), path)

    if process_adjacencies:
        path = os.path.join(map_dir, "adjacencies.csv")
        record("adjacencies.csv", update_adjacencies_csv(path, absorption_map), path)

    if process_states:
        states_dir = os.path.join(mod_root, "history", "states")
        r = apply_to_dir(states_dir, ".txt", update_state_file_blocks, absorption_map)
        record("history/states/*.txt", r)

    if process_units:
        units_dir = os.path.join(mod_root, "history", "units")
        r = apply_to_dir(units_dir, ".txt", update_unit_history_file, absorption_map)
        record("history/units/*.txt", r)

    if process_decisions:
        decisions_dir = os.path.join(mod_root, "common", "decisions")
        r = apply_to_dir(decisions_dir, ".txt", update_set_province_name_in_file, absorption_map)
        record("common/decisions/*.txt", r)

    return summary
