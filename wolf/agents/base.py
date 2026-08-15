"""智能体接口。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from ..roles import Board, Role


@dataclass
class ActionRequest:
    kind: str
    day: int
    phase: str
    observation: str  # 已按可见性过滤的时间线
    alive: list[int]
    dead: list[tuple[int, int]]  # (座位, 出局天数)
    options: list[int] = field(default_factory=list)  # 合法目标座位
    extra: str = ""  # 额外提示（如「今晚被刀的是 3 号」）
    allow_zero: bool = False  # 是否允许 0（弃票/不用药/不开枪）


@dataclass
class Decision:
    thinking: str = ""
    notes: str = ""
    beliefs: list[dict] = field(default_factory=list)
    scheme: dict | None = None
    data: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)


class Agent(Protocol):
    seat: int
    role: Role
    model_key: str

    def on_game_start(
        self, *, seat: int, role: Role, board: Board, teammates: list[int], all_seats: list[int]
    ) -> None: ...

    def act(self, req: ActionRequest) -> Decision: ...


def coerce_seat(value: Any, options: list[int], *, allow_zero: bool, fallback: int | None) -> tuple[int, bool]:
    """把模型给出的座位号收敛到合法值。返回 (座位, 是否被强制修正)。"""
    try:
        seat = int(value)
    except (TypeError, ValueError):
        seat = -1
    if seat == 0 and allow_zero:
        return 0, False
    if seat in options:
        return seat, False
    if fallback is not None:
        return fallback, True
    return (0 if allow_zero else (options[0] if options else 0)), True
