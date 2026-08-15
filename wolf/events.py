"""事件与可见性。

这是整个系统的隐私边界：智能体看到的一切都必须经过 :func:`visible_to`
过滤。心理活动（thinking / beliefs / notes）**永远不会**进入事件流，
它只写进档案（见 :mod:`wolf.archive`）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Visibility(str, Enum):
    PUBLIC = "public"  # 全场可见
    WOLF = "wolf"  # 仅狼队频道
    PRIVATE = "private"  # 仅指定座位（如预言家的验人结果）
    GOD = "god"  # 上帝视角，任何智能体都看不到（仅存档与复盘）


@dataclass
class Event:
    idx: int
    day: int
    phase: str  # setup | night | day
    type: str
    text: str
    visibility: Visibility = Visibility.PUBLIC
    actor: int | None = None
    targets: list[int] = field(default_factory=list)
    audience: frozenset[int] = frozenset()
    data: dict[str, Any] = field(default_factory=dict)

    def visible_to(self, seat: int) -> bool:
        if self.visibility is Visibility.PUBLIC:
            return True
        if self.visibility is Visibility.GOD:
            return False
        return seat in self.audience

    def to_dict(self) -> dict[str, Any]:
        return {
            "idx": self.idx,
            "day": self.day,
            "phase": self.phase,
            "type": self.type,
            "text": self.text,
            "visibility": self.visibility.value,
            "actor": self.actor,
            "targets": list(self.targets),
            "audience": sorted(self.audience),
            "data": self.data,
        }


class EventLog:
    def __init__(self) -> None:
        self.events: list[Event] = []
        self._sinks: list[Any] = []

    def subscribe(self, sink) -> None:
        self._sinks.append(sink)

    def add(
        self,
        *,
        day: int,
        phase: str,
        type: str,
        text: str,
        visibility: Visibility = Visibility.PUBLIC,
        actor: int | None = None,
        targets: list[int] | None = None,
        audience: frozenset[int] | set[int] | None = None,
        data: dict[str, Any] | None = None,
    ) -> Event:
        ev = Event(
            idx=len(self.events),
            day=day,
            phase=phase,
            type=type,
            text=text,
            visibility=visibility,
            actor=actor,
            targets=list(targets or []),
            audience=frozenset(audience or ()),
            data=dict(data or {}),
        )
        self.events.append(ev)
        for sink in self._sinks:
            sink(ev)
        return ev

    def visible(self, seat: int) -> list[Event]:
        return [e for e in self.events if e.visible_to(seat)]

    def public(self) -> list[Event]:
        return [e for e in self.events if e.visibility is Visibility.PUBLIC]
