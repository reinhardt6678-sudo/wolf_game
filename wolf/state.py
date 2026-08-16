"""对局状态。"""

from __future__ import annotations

from dataclasses import dataclass, field

from .events import EventLog
from .roles import Board, Camp, Role, camp_of, is_god, is_pack_wolf, is_villager


@dataclass
class Player:
    seat: int
    role: Role
    model_key: str
    alive: bool = True
    death_day: int | None = None
    death_cause: str | None = None  # 狼杀 | 毒杀 | 放逐 | 枪杀

    @property
    def camp(self) -> Camp:
        return camp_of(self.role)

    def to_dict(self) -> dict:
        return {
            "seat": self.seat,
            "role": self.role.value,
            "camp": self.camp.value,
            "model": self.model_key,
            "alive": self.alive,
            "death_day": self.death_day,
            "death_cause": self.death_cause,
        }


@dataclass
class GameState:
    game_id: str
    board: Board
    players: dict[int, Player]
    log: EventLog = field(default_factory=EventLog)
    day: int = 0
    phase: str = "setup"

    # 技能状态
    witch_antidote: bool = True
    witch_poison: bool = True
    last_guard_target: int | None = None
    speech_start_seat: int = 1

    #: 白痴剩余的免疫放逐次数（座位 → 次数）
    idiot_immunity: dict[int, int] = field(default_factory=dict)
    #: 已经当众翻牌的身份（白痴翻牌后全场可见）
    revealed_roles: dict[int, Role] = field(default_factory=dict)
    #: 失去投票权的座位（白痴翻牌后）
    no_vote_seats: set[int] = field(default_factory=set)

    # ---- 假面舞会 ----
    #: 整局已经进过舞池的座位（每人只能进一次）
    danced_seats: set[int] = field(default_factory=set)
    #: 今晚的舞池
    dance_pool: list[int] = field(default_factory=list)
    #: 昨晚戴面具的座位（假面不能连续两晚给同一人）
    last_mask_target: int | None = None

    # ---- 机械狼 ----
    #: 机械狼学到的身份（None = 还没学）
    mechanic_learned: Role | None = None
    #: 学到的技能从第几夜起生效
    mechanic_skill_from: int | None = None
    #: 机械狼的双刀是否还没用过
    mechanic_double_kill_left: bool = True
    #: 机械狼学到守卫后昨晚守的人
    last_mechanic_guard: int | None = None
    #: 机械狼（学狼人）连续刀同一人的记录
    mechanic_last_kill: int | None = None

    winner: Camp | None = None
    end_reason: str = ""

    # ---- 查询 ----
    def seats(self) -> list[int]:
        return sorted(self.players)

    def alive_seats(self) -> list[int]:
        return [s for s in self.seats() if self.players[s].alive]

    def alive_players(self) -> list[Player]:
        return [self.players[s] for s in self.alive_seats()]

    def dead_seats(self) -> list[int]:
        return [s for s in self.seats() if not self.players[s].alive]

    def voter_seats(self) -> list[int]:
        """有投票权的存活玩家（白痴翻牌后会被移出）。"""
        return [s for s in self.alive_seats() if s not in self.no_vote_seats]

    def pack_wolf_seats(self, alive_only: bool = True) -> list[int]:
        """会面的狼（进狼队频道、参与刀人的那一批，不含机械狼/假面）。"""
        return [
            s
            for s in self.seats()
            if is_pack_wolf(self.players[s].role)
            and (not alive_only or self.players[s].alive)
        ]

    def lone_wolf_seats(self, alive_only: bool = True) -> list[int]:
        """不见面的功能狼（机械狼、假面）。"""
        return [
            s
            for s in self.seats()
            if self.players[s].camp is Camp.WOLF
            and not is_pack_wolf(self.players[s].role)
            and (not alive_only or self.players[s].alive)
        ]

    def wolf_seats(self, alive_only: bool = False) -> list[int]:
        return [
            s
            for s in self.seats()
            if self.players[s].camp is Camp.WOLF and (not alive_only or self.players[s].alive)
        ]

    def seats_with_role(self, role: Role, alive_only: bool = True) -> list[int]:
        return [
            s
            for s in self.seats()
            if self.players[s].role is role and (not alive_only or self.players[s].alive)
        ]

    def role_seat(self, role: Role) -> int | None:
        seats = self.seats_with_role(role)
        return seats[0] if seats else None

    def counts(self) -> dict[str, int]:
        alive = self.alive_players()
        return {
            "wolf": sum(1 for p in alive if p.camp is Camp.WOLF),
            "god": sum(1 for p in alive if is_god(p.role)),
            "villager": sum(1 for p in alive if is_villager(p.role)),
            "good": sum(1 for p in alive if p.camp is Camp.GOOD),
        }

    def kill(self, seat: int, cause: str) -> None:
        p = self.players[seat]
        if not p.alive:
            return
        p.alive = False
        p.death_day = self.day
        p.death_cause = cause

    def next_seat_from(self, seat: int) -> int:
        """从某个座位开始，顺时针找到第一个存活玩家（含自己）。"""
        order = self.seats()
        n = len(order)
        start = order.index(seat) if seat in order else 0
        for i in range(n):
            s = order[(start + i) % n]
            if self.players[s].alive:
                return s
        return seat

    def speech_order(self, start: int) -> list[int]:
        order = self.seats()
        n = len(order)
        start_idx = order.index(start) if start in order else 0
        out = []
        for i in range(n):
            s = order[(start_idx + i) % n]
            if self.players[s].alive:
                out.append(s)
        return out
