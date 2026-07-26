"""데이터 클래스 정의.

HOI4 맵 파일에서 다루는 핵심 엔티티들을 표현한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

ProvinceType = Literal["land", "sea", "lake", "unknown"]


@dataclass
class Province:
    """definition.csv의 한 행에 대응."""
    id: int
    r: int
    g: int
    b: int
    type: ProvinceType = "land"
    coastal: bool = False
    terrain: str = "plains"
    continent: int = 0  # 0 = 없음(바다/호수), 1~7 = continent.txt 인덱스

    @property
    def rgb(self) -> tuple[int, int, int]:
        return (self.r, self.g, self.b)

    def to_csv_row(self) -> str:
        coastal_str = "true" if self.coastal else "false"
        return f"{self.id};{self.r};{self.g};{self.b};{self.type};{coastal_str};{self.terrain};{self.continent}"

    @classmethod
    def from_csv_row(cls, row: str) -> "Province":
        parts = row.strip().split(";")
        if len(parts) < 8:
            raise ValueError(f"Invalid province row: {row!r}")
        return cls(
            id=int(parts[0]),
            r=int(parts[1]),
            g=int(parts[2]),
            b=int(parts[3]),
            type=parts[4],  # type: ignore[arg-type]
            coastal=parts[5].lower() == "true",
            terrain=parts[6],
            continent=int(parts[7]),
        )


@dataclass
class TerrainCategory:
    """common/terrain/00_terrain.txt에서 정의된 지형 카테고리."""
    name: str
    color: Optional[tuple[int, int, int]] = None  # terrain.bmp 매핑 색상
    is_water: bool = False
    naval_terrain: bool = False


@dataclass
class StateInfo:
    """history/states/*.txt 파일 요약."""
    id: int
    file_path: str
    name: str
    province_ids: list[int] = field(default_factory=list)


@dataclass
class StrategicRegionInfo:
    """map/strategicregions/*.txt 요약."""
    id: int
    file_path: str
    name: str
    province_ids: list[int] = field(default_factory=list)


@dataclass
class MapPaths:
    """로드된 맵 폴더의 핵심 경로들."""
    map_dir: str
    provinces_bmp: str
    definition_csv: str
    terrain_bmp: str
    heightmap_bmp: str
    world_normal_bmp: str
    rivers_bmp: str   # map/rivers.bmp (오버레이 레이어용)
    supply_nodes_txt: str
    railways_txt: str
    continent_txt: str
    default_map: str
    strategicregions_dir: str
    buildings_txt: str  # map/buildings.txt
    # 모드 루트 추정 (map의 부모 폴더)
    mod_root: str
    history_states_dir: str  # 있을 수도 없을 수도 있음
    common_terrain_dir: str  # 있을 수도 없을 수도 있음
