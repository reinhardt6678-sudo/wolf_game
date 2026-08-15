"""角色、阵营与板子定义。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Camp(str, Enum):
    GOOD = "好人"
    WOLF = "狼人"


class Role(str, Enum):
    VILLAGER = "平民"
    SEER = "预言家"
    WITCH = "女巫"
    HUNTER = "猎人"
    GUARD = "守卫"
    WEREWOLF = "狼人"


GOD_ROLES = frozenset({Role.SEER, Role.WITCH, Role.HUNTER, Role.GUARD})
WOLF_ROLES = frozenset({Role.WEREWOLF})

#: 玩家在发言中可以宣称的身份（含"隐藏"）。
CLAIMABLE = [r.value for r in Role] + ["隐藏"]


def camp_of(role: Role) -> Camp:
    return Camp.WOLF if role in WOLF_ROLES else Camp.GOOD


def is_god(role: Role) -> bool:
    return role in GOD_ROLES


def is_villager(role: Role) -> bool:
    return role is Role.VILLAGER


ROLE_BRIEF = {
    Role.VILLAGER: "你没有任何技能，只有一张嘴和一票。你的价值在于分析发言、找出狼人、把票投对。",
    Role.SEER: "每晚可以查验一名玩家，得知其为「好人」或「狼人」。你是好人阵营的核心信息源。",
    Role.WITCH: "你有一瓶解药和一瓶毒药，各只能用一次。每晚你会得知今晚被狼人刀的是谁。",
    Role.HUNTER: "当你被狼人刀杀或被投票放逐出局时，可以开枪带走一名玩家；被女巫毒死则无法开枪。",
    Role.GUARD: "每晚可以守护一名玩家（含自己），使其免于狼人刀杀。不能连续两晚守护同一人。",
    Role.WEREWOLF: "每晚与狼队友商议后共同刀杀一名玩家。白天你需要伪装成好人，混淆视听、带偏节奏。",
}


@dataclass(frozen=True)
class Board:
    """一副板子（座位数 + 角色配置 + 规则）。"""

    name: str
    seats: int
    role_counts: dict[Role, int]
    rules: "Rules"

    def role_pool(self) -> list[Role]:
        pool: list[Role] = []
        for role, n in self.role_counts.items():
            pool.extend([role] * n)
        if len(pool) != self.seats:
            raise ValueError(
                f"板子 {self.name} 角色数({len(pool)})与座位数({self.seats})不符"
            )
        return pool

    def describe(self) -> str:
        parts = [f"{r.value}×{n}" for r, n in self.role_counts.items() if n]
        return f"{self.name}：{self.seats} 人局，{'、'.join(parts)}"


@dataclass(frozen=True)
class Rules:
    victory: str = "屠边"  # 屠边 | 屠城
    witch_self_save_day: int = 1  # 女巫可自救的最后一个夜晚（0=永不可自救）
    witch_sees_kill: str = "always"  # always | first_night
    witch_same_night_both: bool = False  # 同一晚是否可以既用解药又用毒药
    guard_repeat: bool = False  # 守卫是否可以连守同一人
    guard_save_conflict_dies: bool = True  # 同守同救是否致死（奶死）
    first_night_last_words: bool = True  # 首夜死者是否有遗言
    night_death_last_words: bool = False  # 非首夜的夜间死者是否有遗言
    tie_rule: str = "revote"  # revote | none
    max_days: int = 8

    def describe(self) -> str:
        lines = [
            f"- 胜利条件：{self.victory}"
            + ("（狼人杀光所有神职 或 杀光所有平民即获胜）" if self.victory == "屠边" else "（狼人数量≥好人数量即获胜）"),
            "- 女巫：解药与毒药各一瓶，"
            + (
                f"仅第 {self.witch_self_save_day} 夜可以自救"
                if self.witch_self_save_day
                else "不可自救"
            )
            + ("；同一晚不可同时使用解药和毒药" if not self.witch_same_night_both else ""),
            "- 女巫每晚都能得知刀口" if self.witch_sees_kill == "always" else "- 女巫仅首夜能得知刀口",
            "- 守卫" + ("可以" if self.guard_repeat else "不可以") + "连续两晚守护同一人"
            + ("；同守同救会导致目标死亡（奶死）" if self.guard_save_conflict_dies else ""),
            "- 平票处理：" + ("重新投票一次，仍平票则本轮无人出局" if self.tie_rule == "revote" else "本轮无人出局"),
            "- 死亡不公布死因（不知道是被刀还是被毒）",
        ]
        return "\n".join(lines)


DEFAULT_RULES = Rules()

BUILTIN_BOARDS: dict[str, Board] = {
    "board_9": Board(
        name="9人局(预女猎)",
        seats=9,
        role_counts={
            Role.WEREWOLF: 3,
            Role.SEER: 1,
            Role.WITCH: 1,
            Role.HUNTER: 1,
            Role.VILLAGER: 3,
        },
        rules=DEFAULT_RULES,
    ),
    "board_12": Board(
        name="12人标准局(预女猎守)",
        seats=12,
        role_counts={
            Role.WEREWOLF: 4,
            Role.SEER: 1,
            Role.WITCH: 1,
            Role.HUNTER: 1,
            Role.GUARD: 1,
            Role.VILLAGER: 4,
        },
        rules=DEFAULT_RULES,
    ),
}
