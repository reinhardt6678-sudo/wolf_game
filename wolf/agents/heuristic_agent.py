"""规则基线玩家（不调用任何 LLM）。

用途：
1. 让整个项目在没有任何 API key 的情况下也能跑通、出档案、出复盘页；
2. 作为衡量 LLM 水平的对照组（"比随机策略强多少"）。

它同样会输出「心理活动」，只是内容由模板生成，用于验证档案格式。
"""

from __future__ import annotations

import random
from typing import Any

from ..roles import Board, Camp, Role, camp_of
from .base import ActionRequest, Decision


class HeuristicAgent:
    model_key = "heuristic"

    def __init__(self, *, model_key: str = "heuristic", rng: random.Random | None = None) -> None:
        self.model_key = model_key
        self.rng = rng or random.Random()
        self.seat = 0
        self.role: Role = Role.VILLAGER
        self.teammates: list[int] = []
        self.suspicion: dict[int, float] = {}
        self.notes = ""

    @property
    def is_wolf(self) -> bool:
        return camp_of(self.role) is Camp.WOLF

    def on_game_start(
        self, *, seat: int, role: Role, board: Board, teammates: list[int], all_seats: list[int]
    ) -> None:
        self.seat = seat
        self.role = role
        self.teammates = list(teammates)
        self.suspicion = {s: self.rng.random() * 0.2 for s in all_seats if s != seat}
        self.notes = ""

    # ------------------------------------------------------------------
    def _enemies(self, options: list[int]) -> list[int]:
        pool = [s for s in options if s != self.seat and s not in self.teammates]
        return pool or [s for s in options if s != self.seat] or list(options)

    def _most_suspicious(self, options: list[int]) -> int:
        pool = self._enemies(options)
        if not pool:
            return 0
        return max(pool, key=lambda s: self.suspicion.get(s, 0.0) + self.rng.random() * 0.3)

    # ------------------------------------------------------------------
    def act(self, req: ActionRequest) -> Decision:
        opts = req.options
        data: dict[str, Any] = {}
        thinking = ""
        scheme = None

        if req.kind == "wolf_kill":
            target = self._most_suspicious(opts)
            data = {"target": target, "wolf_talk": f"我倾向刀 {target} 号，他看起来像神。"}
            thinking = f"基线策略：优先刀非狼队友中怀疑度最高的 {target} 号，压缩好人神职空间。"
            scheme = {
                "stance": "深水(装平民)",
                "gold_water_target": 0,
                "kill_check_target": 0,
                "reason": "基线策略不做悍跳，保持低调等待好人自爆矛盾。",
            }
        elif req.kind in (
            "seer_check",
            "guard_protect",
            "psychic_check",
            "mechanic_guard",
            "mechanic_psychic",
            "mechanic_learn",
            "mechanic_double_kill",
        ):
            if req.kind in ("guard_protect", "mechanic_guard"):
                target = self.seat if self.seat in opts else (opts[0] if opts else 0)
                thinking = f"基线策略：守护 {target} 号（优先自守，保证信息源存活）。"
            elif req.kind == "mechanic_learn":
                target = opts[0] if opts else 0
                thinking = f"基线策略：学习 {target} 号，赌他是个有技能的神。"
            else:
                target = self._most_suspicious(opts)
                thinking = f"基线策略：查验/针对怀疑度最高的 {target} 号。"
            data = {"target": target}
        elif req.kind == "dance_invite":
            pool = sorted(opts)[:3]
            data = {"targets": pool}
            thinking = "基线策略：按座位序点满舞池，先把信息面铺开。"
        elif req.kind == "mask_action":
            probe = self._most_suspicious(opts) if opts else 0
            target = self.seat if self.seat in opts else (opts[0] if opts else 0)
            data = {"probe_target": probe, "target": target}
            thinking = f"基线策略：打听 {probe} 号在不在舞池，把面具戴在 {target} 号身上翻转结算。"
            scheme = {
                "stance": "深水(装平民)",
                "gold_water_target": 0,
                "kill_check_target": 0,
                "reason": "假面不与狼见面，白天保持低调，靠面具搅乱舞池结算。",
            }
        elif req.kind == "witch_action":
            use = req.day == 1 and "被刀" in req.extra
            data = {"use_antidote": bool(use), "poison_target": 0}
            thinking = "基线策略：首夜救人保神，毒药留到后期有明确目标时再用。"
        elif req.kind == "speech":
            target = self._most_suspicious(opts or req.alive)
            claim = "隐藏" if self.is_wolf else self.role.value
            data = {
                "claim": claim,
                "speech": f"我目前最怀疑 {target} 号，他的发言里没有给出明确的站边。我先把票压在 {target} 号。",
                "vote_intent": target,
            }
            thinking = (
                f"基线策略：公开对外宣称 {claim}，把矛头指向 {target} 号。"
                + ("（作为狼人，这是在把水搅浑。）" if self.is_wolf else "")
            )
            if self.is_wolf:
                scheme = {
                    "stance": "深水(装平民)",
                    "gold_water_target": 0,
                    "kill_check_target": target,
                    "reason": f"不悍跳，只是把票导向 {target} 号，保持低风险。",
                }
        elif req.kind == "vote":
            target = self._most_suspicious(opts)
            data = {"target": target, "one_liner": f"投 {target} 号。"}
            thinking = f"基线策略：投怀疑度最高的 {target} 号。"
        elif req.kind == "last_words":
            data = {"claim": "隐藏" if self.is_wolf else self.role.value, "speech": "我出局了，好人跟紧我的票。"}
            thinking = "基线策略：留下一句无信息量的遗言。"
        elif req.kind in ("hunter_shot", "wolf_king_shot"):
            target = self._most_suspicious(opts)
            label = "猎人" if req.kind == "hunter_shot" else "狼王"
            data = {"target": target, "speech": f"我是{label}，带走 {target} 号。"}
            thinking = f"基线策略：开枪带走{'怀疑度最高' if req.kind == 'hunter_shot' else '对狼队威胁最大'}的 {target} 号。"

        beliefs = [
            {
                "seat": s,
                "guess": "狼人" if v > 0.5 else "不确定",
                "confidence": round(min(v, 1.0), 2),
                "reason": "基线怀疑度累积",
            }
            for s, v in sorted(self.suspicion.items(), key=lambda kv: -kv[1])[:3]
        ]
        return Decision(
            thinking=thinking,
            notes=self.notes,
            beliefs=beliefs,
            scheme=scheme,
            data=data,
            meta={"model_key": self.model_key, "model": "heuristic", "latency_ms": 0},
        )

    def observe_vote(self, voter: int, target: int) -> None:
        """被引擎调用：有人投了我，就更怀疑他。"""
        if target == self.seat:
            self.suspicion[voter] = self.suspicion.get(voter, 0.0) + 0.35
