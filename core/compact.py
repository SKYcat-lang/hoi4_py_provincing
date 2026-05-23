"""ID 압축(compact) 실행 모듈.

`definition.csv`의 placeholder 행을 제거하고, 남은 프로빈스 ID들을 1부터 빈틈없이
재번호한 뒤, **사용자가 감독자 UI에서 승인한 매치들만** 새 ID로 치환한다.

## 설계 원칙

1. BMP는 건드리지 않는다 (사용자 명시 지시). RGB → ID 매핑은 definition.csv에 의해
   결정되므로, ID만 재번호해도 픽셀 데이터는 자동으로 새 ID를 가리킨다.
2. 외부 파일 치환은 매치 단위(file_path, line_no, col_start, col_end). 같은 라인에
   여러 ID가 있어도 충돌하지 않게 컬럼 범위까지 본다.
3. 같은 파일의 여러 매치는 **뒤에서부터(line desc, col desc)** 적용해 오프셋 안전성
   보장.
4. 자동 결정 금지. 어떤 매치를 치환할지는 호출자(=프론트엔드)가 결정해서 넘긴다.
5. 백업 파일(.bak)은 생성하지 않는다. 원본 보존이 필요하면 호출자(또는 사용자)가
   별도로 처리할 책임을 진다.

## 흐름

  build_compaction_plan(definition_csv) → CompactionPlan
      ├ id_map: {old_id: new_id}
      ├ removed_ids: placeholder로서 사라질 ID들 (= old_id의 키 중 매핑 안 된 것)
      └ new_provinces: 새 ID 순서로 정렬된 Province 리스트

  apply_compaction(plan, approved_matches, dry_run=False)
      ├ 각 파일별 매치 그룹화
      ├ 매치를 (line, col_start) 내림차순 정렬
      ├ 라인 내 col_start~col_end를 new_id로 치환
      ├ 변경된 라인만 메모리에서 교체 후 파일 덮어쓰기
      └ 변경 요약 리포트 반환

  rewrite_definition_csv(plan, csv_path)
      └ placeholder 제거 + 재번호된 행으로 다시 씀
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .definitions import Province
from .id_search import IdMatch


# ---------------------------------------------------------------------------
# 계획 (Plan)
# ---------------------------------------------------------------------------


@dataclass
class CompactionPlan:
    """압축 계획. UI에 표시할 메타정보를 한 곳에 모은다."""
    # placeholder 제외 후 남은 프로빈스들의 (옛 ID → 새 ID) 매핑.
    # 옛 ID에 placeholder ID는 포함되지 않는다(=사라질 ID라 매핑 불필요).
    id_map: dict[int, int] = field(default_factory=dict)
    # placeholder로 사라질 ID들. 외부 파일에 이 ID가 남아 있으면 새 ID로 치환할 수
    # 없으므로, "그냥 텍스트에서 지워야" 한다(또는 호출자가 무시).
    removed_ids: list[int] = field(default_factory=list)
    # 새 ID로 재할당된 Province 리스트 (id 오름차순).
    new_provinces: list[Province] = field(default_factory=list)

    @property
    def changed_id_map(self) -> dict[int, int]:
        """실제로 ID가 바뀌는 것들만 (old != new)."""
        return {o: n for o, n in self.id_map.items() if o != n}

    def to_dict(self) -> dict:
        return {
            "idMap": self.id_map,
            "changedIdMap": self.changed_id_map,
            "removedIds": self.removed_ids,
            "newProvinceCount": len(self.new_provinces),
            "maxNewId": (max((p.id for p in self.new_provinces), default=0)),
        }


def build_compaction_plan(definition_csv_path: str) -> CompactionPlan:
    """definition.csv를 읽어 압축 계획 산출.

    규칙:
      - 행 순서대로 읽되, placeholder(RGB 0,0,0 & terrain == "unknown" & id != 0)는
        제외하고 남은 프로빈스를 그대로의 상대 순서로 0번부터 재번호한다.
      - ID 0(invalid slot)은 그대로 0으로 유지 (HOI4 규약).
      - 매핑되지 않는 옛 ID(placeholder) 들은 removed_ids로 분리.
    """
    plan = CompactionPlan()

    # 1) 원본 행을 순서대로 읽기 (id별 dict로 모으되 원본 순서도 추적)
    survivors: list[Province] = []
    with open(definition_csv_path, "r", encoding="utf-8-sig", errors="replace") as f:
        for line in f:
            line = line.rstrip("\r\n")
            if not line:
                continue
            try:
                p = Province.from_csv_row(line)
            except (ValueError, IndexError):
                continue
            # placeholder 판정
            is_placeholder = (
                p.id != 0
                and p.r == 0 and p.g == 0 and p.b == 0
                and p.terrain == "unknown"
            )
            if is_placeholder:
                plan.removed_ids.append(p.id)
                continue
            survivors.append(p)

    # 2) 재번호: ID 오름차순으로 정렬 후 0번부터 (ID 0 invalid slot은 0 그대로)
    survivors.sort(key=lambda p: p.id)
    new_id = 0
    seen_zero = False
    for p in survivors:
        if p.id == 0 and not seen_zero:
            # invalid slot은 그대로 유지하고, 다음 카운터는 1부터.
            plan.id_map[0] = 0
            plan.new_provinces.append(Province(
                id=0, r=p.r, g=p.g, b=p.b, type=p.type,
                coastal=p.coastal, terrain=p.terrain, continent=p.continent,
            ))
            seen_zero = True
            new_id = 1
            continue
        if not seen_zero and new_id == 0:
            # ID 0 행이 아예 없는 경우 — invalid slot 자동 삽입 안 함(원본 그대로).
            # HOI4는 ID 0 행을 요구하지만, 여기서는 데이터를 임의 생성하지 않는다.
            new_id = 1
        plan.id_map[p.id] = new_id
        plan.new_provinces.append(Province(
            id=new_id, r=p.r, g=p.g, b=p.b, type=p.type,
            coastal=p.coastal, terrain=p.terrain, continent=p.continent,
        ))
        new_id += 1

    return plan


# ---------------------------------------------------------------------------
# definition.csv 재작성
# ---------------------------------------------------------------------------


def rewrite_definition_csv(plan: CompactionPlan, csv_path: str) -> None:
    """압축된 프로빈스 리스트로 definition.csv를 다시 쓴다.

    placeholder는 자연스럽게 사라지고, 모든 ID가 0..N 순서로 빈틈 없음.
    백업은 생성하지 않는다.
    """
    rows = [p.to_csv_row() for p in plan.new_provinces]
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        f.write("\r\n".join(rows))
        f.write("\r\n")


# ---------------------------------------------------------------------------
# 외부 파일 치환
# ---------------------------------------------------------------------------


@dataclass
class ApplyReport:
    """apply_compaction 결과 리포트."""
    modified_files: list[str] = field(default_factory=list)
    skipped_files: list[str] = field(default_factory=list)
    # 매치 중 plan.id_map에 없어서 치환 불가능했던 것들 (= placeholder ID 잔존 등).
    # 보통 사용자에게 "이 위치들은 수동으로 검토 바람"으로 보여줘야 한다.
    unmapped_matches: list[dict] = field(default_factory=list)
    total_replacements: int = 0
    dry_run: bool = False

    def to_dict(self) -> dict:
        return {
            "modifiedFiles": self.modified_files,
            "skippedFiles": self.skipped_files,
            "unmappedMatches": self.unmapped_matches,
            "totalReplacements": self.total_replacements,
            "dryRun": self.dry_run,
        }


def apply_compaction(
    plan: CompactionPlan,
    approved_matches: Iterable[IdMatch],
    *,
    dry_run: bool = False,
) -> ApplyReport:
    """승인된 매치만 새 ID로 치환.

    같은 파일의 매치들은 (line_no desc, col_start desc) 순으로 적용해 오프셋 안전.
    백업은 생성하지 않는다.
    """
    report = ApplyReport(dry_run=dry_run)
    by_file: dict[str, list[IdMatch]] = {}
    for m in approved_matches:
        by_file.setdefault(m.file_path, []).append(m)

    for file_path, matches in by_file.items():
        # 파일 내 매치를 뒤에서부터 적용
        matches.sort(key=lambda m: (m.line_no, m.col_start), reverse=True)

        # 매치 중 매핑 불가능한 것(=placeholder ID여서 새 ID가 없음)을 분리
        usable: list[IdMatch] = []
        for m in matches:
            if m.matched_id not in plan.id_map:
                report.unmapped_matches.append(m.to_dict())
                continue
            usable.append(m)

        if not usable:
            report.skipped_files.append(file_path)
            continue

        try:
            with open(file_path, "r", encoding="utf-8-sig", errors="replace") as f:
                lines = f.readlines()
        except OSError:
            report.skipped_files.append(file_path)
            continue

        for m in usable:
            idx = m.line_no - 1
            if not (0 <= idx < len(lines)):
                continue
            line = lines[idx].rstrip("\r\n")
            # 매치 위치가 현재 라인 내용과 정합한지 한번 더 검증.
            # (파일이 사이에 외부에서 바뀌었을 위험 방지)
            seg = line[m.col_start:m.col_end]
            if seg != str(m.matched_id):
                # 정합성 깨짐 → 이 매치는 건너뛰고 unmapped로 기록.
                report.unmapped_matches.append({**m.to_dict(), "reason": "stale_match"})
                continue
            new_id = plan.id_map[m.matched_id]
            new_line = line[:m.col_start] + str(new_id) + line[m.col_end:]
            # 줄바꿈 보존
            orig = lines[idx]
            if orig.endswith("\r\n"):
                lines[idx] = new_line + "\r\n"
            elif orig.endswith("\n"):
                lines[idx] = new_line + "\n"
            else:
                lines[idx] = new_line
            report.total_replacements += 1

        if dry_run:
            report.modified_files.append(file_path)
            continue

        try:
            with open(file_path, "w", encoding="utf-8", newline="") as f:
                f.writelines(lines)
            report.modified_files.append(file_path)
        except OSError:
            report.skipped_files.append(file_path)

    return report


# ---------------------------------------------------------------------------
# 최소침습(min-invasive) 병합: "맨 뒷번호 → 빈자리"
# ---------------------------------------------------------------------------


def build_min_invasive_plan(definition_csv_path: str) -> CompactionPlan:
    """전체 재번호 대신, 구멍 하나당 max ID 하나만 끌어다 채우는 최소침습 계획.

    동작:
      1) definition.csv를 읽어 placeholder 행(=구멍)을 식별.
      2) 구멍을 ID 오름차순으로, "정상 프로빈스" ID를 내림차순으로 페어링.
      3) 한 페어당 매핑 1개씩 생성: `{mover_id: hole_id}`.
      4) mover_id가 hole_id보다 작거나 같으면 멈춤(=더 이상 끌어올 게 없음).
      5) 남은 구멍은 끝에서 잘라내면 되므로 매핑 없이 사라짐.

    이 방식의 핵심 장점:
      - 외부 파일 영향 = (이동된 mover 수)개의 ID만. 전체 재번호 대비 침습 폭이 극히 작음.
      - 사용자 감독자 UI에서 보여줄 매치 수가 한 자릿수~수십 개로 떨어지는 게 보통.

    반환되는 CompactionPlan은 기존 compact 흐름과 호환:
      - id_map: {mover_id: hole_id}만 들어있음(=움직인 것들만). 그대로 두는 ID는 포함 X.
      - removed_ids: definition.csv 상 사라진 ID들 (placeholder 였던 hole + mover의 옛 자리)
      - new_provinces: 압축 후 ID 0..N 순서의 Province 리스트 (placeholder 없음)
    """
    plan = CompactionPlan()

    # 1) 원본 행 읽기
    raw_by_id: dict[int, Province] = {}
    placeholder_ids: list[int] = []
    with open(definition_csv_path, "r", encoding="utf-8-sig", errors="replace") as f:
        for line in f:
            line = line.rstrip("\r\n")
            if not line:
                continue
            try:
                p = Province.from_csv_row(line)
            except (ValueError, IndexError):
                continue
            is_placeholder = (
                p.id != 0
                and p.r == 0 and p.g == 0 and p.b == 0
                and p.terrain == "unknown"
            )
            if is_placeholder:
                placeholder_ids.append(p.id)
            else:
                raw_by_id[p.id] = p

    if not raw_by_id:
        return plan

    max_id = max(raw_by_id.keys())
    # 2) hole(asc) ↔ mover(desc) 페어링
    holes = sorted(placeholder_ids)
    # mover 후보: 정상 프로빈스 ID 내림차순. ID 0(invalid)은 이동 대상 아님.
    movers = sorted((pid for pid in raw_by_id.keys() if pid != 0), reverse=True)

    move_map: dict[int, int] = {}   # mover_id -> hole_id
    hole_idx = 0
    mover_idx = 0
    while hole_idx < len(holes) and mover_idx < len(movers):
        hole = holes[hole_idx]
        mover = movers[mover_idx]
        # mover가 hole보다 뒤에 있어야 의미 있는 이동(앞에서 뒤로 끌어옴이 아니라
        # 뒤에서 앞으로 끌어옴).
        if mover <= hole:
            break
        move_map[mover] = hole
        hole_idx += 1
        mover_idx += 1

    # 3) 새 ID 공간 구성
    # 살아남은 ID들의 새 위치: 기존 ID 그대로 + move_map 적용
    # 즉 final_by_new_id[new_id] = Province(new_id, ...)
    final: dict[int, Province] = {}
    # ID 0 invalid slot 보존
    if 0 in raw_by_id:
        p0 = raw_by_id[0]
        final[0] = Province(
            id=0, r=p0.r, g=p0.g, b=p0.b, type=p0.type,
            coastal=p0.coastal, terrain=p0.terrain, continent=p0.continent,
        )

    # 이동한 mover들은 hole 위치로
    for mover_id, hole_id in move_map.items():
        src = raw_by_id[mover_id]
        final[hole_id] = Province(
            id=hole_id, r=src.r, g=src.g, b=src.b, type=src.type,
            coastal=src.coastal, terrain=src.terrain, continent=src.continent,
        )

    # 이동하지 않은 정상 프로빈스들은 ID 유지. 단, mover로 빠진 자리에는 두지 않음.
    moved_out = set(move_map.keys())
    for pid, prov in raw_by_id.items():
        if pid == 0:
            continue
        if pid in moved_out:
            continue
        # 이 자리에 이미 이동된 게 들어왔으면(=pid가 hole이었는데 채워진 경우) 덮어쓰지 않음
        if pid in final:
            continue
        final[pid] = Province(
            id=pid, r=prov.r, g=prov.g, b=prov.b, type=prov.type,
            coastal=prov.coastal, terrain=prov.terrain, continent=prov.continent,
        )

    # 4) 최종 결과
    plan.id_map = dict(move_map)
    # removed_ids = 디스크에서 사라지는 모든 ID = (모든 placeholder hole) ∪ (mover의 옛 자리)
    plan.removed_ids = sorted(set(placeholder_ids) | moved_out)
    plan.new_provinces = [final[k] for k in sorted(final.keys())]

    return plan
