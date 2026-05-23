"""map/adjacencies.csv 편집 모듈.

HOI4 인접(adjacency) 정의 파일을 안전하게 추가/삭제한다.

파일 포맷
---------
헤더 라인은 *없다*(첫 줄부터 데이터). 단, 파일 끝에 다음 두 줄이 관례적으로 붙는다:

    -1;-1;;-1;-1;-1;-1;-1;-1
    #Start province ID;End province ID;Adjacency type;Through province ID;...

첫 줄은 **sentinel** 행으로 파서를 종료시키는 역할. 두 번째 줄은 사람용 참조 주석.

데이터 컬럼:
    From;To;Type;Through;start_x;start_y;stop_x;stop_y;adjacency_rule_name;Comment

- From/To       : int 프로빈스 ID
- Type          : "sea" | "impassable" | "river" | ""(빈값=strait/canal 기본)
- Through       : int 또는 -1 (보통 sea province)
- start/stop x,y: 화살표 좌표. 미사용이면 -1
- rule_name     : UPPER_SNAKE_CASE([A-Z][A-Z0-9_]*) 또는 빈값
- Comment       : 자유 텍스트 (단 ';'은 금지 — CSV 구분자)

추가/삭제 정책
--------------
- 파일이 없으면 빈 파일을 만들고 sentinel + 헤더 주석을 자동 생성.
- 새 항목은 **sentinel 행 바로 위**에 삽입. (기존 행 순서는 유지)
- 삭제는 (line_index) 또는 (from, to, type, through) 키로.
- 본 모듈은 BOM 없는 UTF-8로 쓴다(바닐라가 ASCII만 쓰는 것 존중).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Optional


# 허용되는 type 값. 빈 문자열은 "기본"(strait/canal 등)을 의미.
ALLOWED_TYPES: frozenset[str] = frozenset({"", "sea", "impassable", "river"})

# rule_name 정규식: 비어있거나, 대문자로 시작 + [A-Z0-9_]만 허용.
RULE_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")

# sentinel 라인 정확한 형태(끝에 trailing space 있어도 무시하고 매칭).
SENTINEL_PREFIX = "-1;-1;"

# 헤더 주석 (필요시 새로 만들 때 사용).
HEADER_COMMENT = (
    "#Start province ID;End province ID;Adjacency type;Through province ID;"
    "Starting X position;Starting Y position;Ending X position;Ending Y position;"
    "Adjacency rule;Comment"
)
SENTINEL_LINE = "-1;-1;;-1;-1;-1;-1;-1;-1 "  # 바닐라처럼 끝에 공백 한 칸


@dataclass
class Adjacency:
    """단일 인접 정의. CSV 한 행에 대응."""
    from_id: int
    to_id: int
    type: str = ""           # ALLOWED_TYPES 중 하나
    through: int = -1
    start_x: int = -1
    start_y: int = -1
    stop_x: int = -1
    stop_y: int = -1
    rule_name: str = ""
    comment: str = ""

    def to_csv_row(self) -> str:
        return ";".join([
            str(self.from_id), str(self.to_id), self.type,
            str(self.through),
            str(self.start_x), str(self.start_y),
            str(self.stop_x), str(self.stop_y),
            self.rule_name, self.comment,
        ])

    def to_dict(self) -> dict:
        return {
            "fromId": self.from_id, "toId": self.to_id,
            "type": self.type, "through": self.through,
            "startX": self.start_x, "startY": self.start_y,
            "stopX": self.stop_x, "stopY": self.stop_y,
            "ruleName": self.rule_name, "comment": self.comment,
        }

    @classmethod
    def from_csv_row(cls, row: str) -> Optional["Adjacency"]:
        """CSV 한 줄 파싱. sentinel/주석/빈 줄이면 None."""
        s = row.rstrip("\r\n")
        if not s.strip():
            return None
        if s.lstrip().startswith("#"):
            return None
        if s.startswith(SENTINEL_PREFIX):
            return None
        parts = s.split(";")
        # 최소 8컬럼은 있어야(rule_name/comment 비어도 OK)
        while len(parts) < 10:
            parts.append("")
        try:
            return cls(
                from_id=int(parts[0]),
                to_id=int(parts[1]),
                type=parts[2].strip(),
                through=int(parts[3]) if parts[3].strip() not in ("", "-") else -1,
                start_x=int(parts[4]) if parts[4].strip() not in ("", "-") else -1,
                start_y=int(parts[5]) if parts[5].strip() not in ("", "-") else -1,
                stop_x=int(parts[6]) if parts[6].strip() not in ("", "-") else -1,
                stop_y=int(parts[7]) if parts[7].strip() not in ("", "-") else -1,
                rule_name=parts[8].strip(),
                comment=parts[9].strip(),
            )
        except ValueError:
            return None


# ---------------------------------------------------------------------------
# 검증
# ---------------------------------------------------------------------------


def validate_rule_name(name: str) -> Optional[str]:
    """rule_name이 유효하면 None, 위반 사유 문자열 반환."""
    if name == "":
        return None
    if " " in name:
        return "공백을 포함할 수 없습니다."
    if not RULE_NAME_RE.match(name):
        return "대문자/숫자/언더스코어만 허용됩니다. (예: KIEL_CANAL)"
    return None


def sanitize_comment(comment: str) -> str:
    """comment에서 ';'만 제거(다른 문자는 그대로). 양끝 공백 trim."""
    return comment.replace(";", "").strip()


def validate_type(t: str) -> Optional[str]:
    if t not in ALLOWED_TYPES:
        return f"허용된 type: {', '.join(sorted(x or '(빈값)' for x in ALLOWED_TYPES))}"
    return None


def validate_adjacency(adj: Adjacency, *, allow_existing: bool = True) -> Optional[str]:
    """단일 Adjacency가 의미적으로 유효한지. 형식 + 의미 검증.

    allow_existing이 False일 때만 self-loop를 명시적으로 거부한다(추가 시).
    """
    if adj.from_id == adj.to_id:
        return "From과 To는 달라야 합니다."
    if adj.from_id < 0 or adj.to_id < 0:
        return "From/To는 0 이상의 정수여야 합니다."
    err = validate_type(adj.type)
    if err:
        return err
    err = validate_rule_name(adj.rule_name)
    if err:
        return f"rule_name: {err}"
    if ";" in adj.comment:
        return "comment에는 ';' (세미콜론) 을 사용할 수 없습니다."
    return None


# ---------------------------------------------------------------------------
# 파일 IO
# ---------------------------------------------------------------------------


@dataclass
class AdjacenciesFile:
    """파싱된 adjacencies.csv 표현. 원본 순서 보존을 위해 줄 단위로 분류."""
    path: str
    # 데이터 행만. 사용자 편집 대상.
    items: list[Adjacency] = field(default_factory=list)
    # sentinel/주석/빈 줄을 그대로 보존(파일 끝 부분).
    # 새로 쓸 때는 items + tail_lines 순서로 직렬화.
    tail_lines: list[str] = field(default_factory=list)


def load_adjacencies(path: str) -> AdjacenciesFile:
    """파일을 읽어 데이터 행과 꼬리(sentinel/주석)를 분리.

    파일이 없으면 빈 결과를 반환한다(items=[], tail에 기본 sentinel+헤더).
    """
    af = AdjacenciesFile(path=path)
    if not os.path.exists(path):
        af.tail_lines = [SENTINEL_LINE, HEADER_COMMENT]
        return af

    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        raw_lines = [ln.rstrip("\r\n") for ln in f.readlines()]

    # 첫 sentinel 행 위치 탐색. 그 위는 items, 그 아래(자기 포함)는 tail.
    sentinel_idx = None
    for i, ln in enumerate(raw_lines):
        if ln.startswith(SENTINEL_PREFIX):
            sentinel_idx = i
            break

    data_lines = raw_lines if sentinel_idx is None else raw_lines[:sentinel_idx]
    tail = [] if sentinel_idx is None else raw_lines[sentinel_idx:]

    for ln in data_lines:
        parsed = Adjacency.from_csv_row(ln)
        if parsed is not None:
            af.items.append(parsed)

    # sentinel이 없던 파일이면 기본을 추가 (저장 시 사용)
    if not tail:
        tail = [SENTINEL_LINE, HEADER_COMMENT]
    af.tail_lines = tail
    return af


def save_adjacencies(af: AdjacenciesFile) -> None:
    """현재 items + tail_lines를 파일에 기록(덮어쓰기). 백업 없음."""
    rows = [adj.to_csv_row() for adj in af.items]
    rows.extend(af.tail_lines)
    # 디렉토리 보장
    os.makedirs(os.path.dirname(af.path) or ".", exist_ok=True)
    with open(af.path, "w", encoding="utf-8", newline="") as f:
        f.write("\r\n".join(rows))
        if rows:
            f.write("\r\n")


# ---------------------------------------------------------------------------
# 편집 작업
# ---------------------------------------------------------------------------


def add_adjacency(af: AdjacenciesFile, adj: Adjacency) -> Optional[str]:
    """sentinel 행 바로 위에 새 항목을 추가. 검증 실패 시 사유 반환."""
    err = validate_adjacency(adj, allow_existing=False)
    if err:
        return err
    # 같은 (From,To,Type,Through) 중복은 막아준다. 양방향 동일하므로 (B,A)도 검사.
    for existing in af.items:
        if _same_pair(existing, adj):
            return "이미 존재하는 인접 정의입니다 (양방향 동일 처리)."
    af.items.append(adj)
    return None


def delete_adjacency(af: AdjacenciesFile, index: int) -> Optional[str]:
    """0-based index의 항목 삭제. 범위 밖이면 사유 반환."""
    if not (0 <= index < len(af.items)):
        return f"인덱스가 범위를 벗어납니다 (0..{len(af.items)-1})."
    del af.items[index]
    return None


def update_adjacency(af: AdjacenciesFile, index: int, new_adj: Adjacency) -> Optional[str]:
    """index 위치의 항목을 new_adj로 교체. 검증 + 중복(자기 자신 제외) 검사."""
    if not (0 <= index < len(af.items)):
        return f"인덱스가 범위를 벗어납니다 (0..{len(af.items)-1})."
    err = validate_adjacency(new_adj, allow_existing=True)
    if err:
        return err
    # 자기 자신을 제외한 항목들에 대해 양방향 중복 검사
    for i, existing in enumerate(af.items):
        if i == index:
            continue
        if _same_pair(existing, new_adj):
            return "다른 항목과 동일한 (From,To,Type,Through)이 이미 존재합니다."
    af.items[index] = new_adj
    return None


def _same_pair(a: Adjacency, b: Adjacency) -> bool:
    """양방향 동일 검사. type/through까지 같아야 진짜 중복."""
    if a.type != b.type:
        return False
    if a.through != b.through:
        return False
    pair_a = {a.from_id, a.to_id}
    pair_b = {b.from_id, b.to_id}
    return pair_a == pair_b


# ---------------------------------------------------------------------------
# adjacency_rules.txt 파서
# ---------------------------------------------------------------------------

# 블록 안의 name 추출용. 큰따옴표 옵션, 공백 허용.
_RULE_NAME_RE = re.compile(r'name\s*=\s*"?([A-Z][A-Z0-9_]*)"?', re.IGNORECASE)


def load_adjacency_rule_names(rules_txt_path: str) -> list[str]:
    """map/adjacency_rules.txt에서 정의된 모든 rule name 추출.

    바닐라/모드의 해당 파일을 가벼운 정규식으로 훑어 `name = "..."` 모두 수집.
    파일이 없으면 빈 리스트. 결과는 원본 순서 유지(중복 제거).
    """
    if not os.path.exists(rules_txt_path):
        return []
    try:
        with open(rules_txt_path, "r", encoding="utf-8-sig", errors="replace") as f:
            text = f.read()
    except OSError:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for m in _RULE_NAME_RE.finditer(text):
        name = m.group(1)
        if name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out
