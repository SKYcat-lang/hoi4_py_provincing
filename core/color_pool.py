"""사용 중인 RGB를 추적하고, 충돌하지 않는 새 색상을 생성한다."""
from __future__ import annotations

import random
from typing import Iterable


# RGB(0,0,0)은 HOI4에서 ID 0(invalid) 용도로 예약되어 있다.
RESERVED_COLORS: set[tuple[int, int, int]] = {(0, 0, 0)}


class ColorPool:
    def __init__(self, used: Iterable[tuple[int, int, int]] = ()) -> None:
        self._used: set[tuple[int, int, int]] = set(used) | RESERVED_COLORS
        # 결정성을 위해 시드는 사용 중인 색상 수에 의존하게 한다.
        self._rng = random.Random(0xC0FFEE ^ len(self._used))

    def add(self, rgb: tuple[int, int, int]) -> None:
        self._used.add(rgb)

    def is_used(self, rgb: tuple[int, int, int]) -> bool:
        return rgb in self._used

    def pick_new(self) -> tuple[int, int, int]:
        """사용되지 않은 RGB를 반환. 가능한 한 시각적으로 분간되도록 시도."""
        # 먼저 무작위 시도 (대부분 빠르게 성공)
        for _ in range(2048):
            rgb = (
                self._rng.randint(1, 255),
                self._rng.randint(0, 255),
                self._rng.randint(0, 255),
            )
            if rgb not in self._used:
                self._used.add(rgb)
                return rgb

        # fallback: 전수 탐색
        for r in range(1, 256):
            for g in range(256):
                for b in range(256):
                    rgb = (r, g, b)
                    if rgb not in self._used:
                        self._used.add(rgb)
                        return rgb

        raise RuntimeError("사용 가능한 RGB 색상이 모두 소진되었습니다.")

    def __len__(self) -> int:
        return len(self._used)
