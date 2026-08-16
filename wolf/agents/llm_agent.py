"""由 LLM 驱动的玩家。"""

from __future__ import annotations

import random
from typing import Any

from .. import prompts
from ..llm import LLMClient, LLMError, extract_json
from ..roles import Board, Camp, Role, camp_of
from ..schemas import schema_for
from .base import ActionRequest, Decision, coerce_seat

#: 需要收敛到合法座位的字段。
SEAT_FIELDS = {
    "wolf_kill": ["target"],
    "seer_check": ["target"],
    "guard_protect": ["target"],
    "witch_action": ["poison_target"],
    "dancer_dance": ["target"],
    "psychic_check": ["target"],
    "mechanic_scan": ["target"],
    "speech": ["vote_intent"],
    "vote": ["target"],
    "hunter_shot": ["target"],
    "wolf_king_shot": ["target"],
    "last_words": [],
}


class LLMAgent:
    def __init__(self, client: LLMClient, *, model_key: str, rng: random.Random | None = None) -> None:
        self.client = client
        self.model_key = model_key
        self.rng = rng or random.Random()
        self.seat = 0
        self.role: Role = Role.VILLAGER
        self.notes = ""
        self.beliefs: list[dict] = []
        self._system = ""
        self._board: Board | None = None

    # ------------------------------------------------------------------
    def on_game_start(
        self, *, seat: int, role: Role, board: Board, teammates: list[int], all_seats: list[int]
    ) -> None:
        self.seat = seat
        self.role = role
        self.notes = ""
        self.beliefs = []
        self._board = board
        self._system = prompts.system_prompt(
            seat=seat, role=role, board=board, teammates=teammates, all_seats=all_seats
        )

    # ------------------------------------------------------------------
    def act(self, req: ActionRequest) -> Decision:
        is_wolf = camp_of(self.role) is Camp.WOLF
        schema = schema_for(req.kind, is_wolf, self._board)
        user = prompts.action_prompt(
            kind=req.kind,
            day=req.day,
            observation=req.observation,
            alive=req.alive,
            dead=req.dead,
            notes=self.notes,
            extra=req.extra,
            options=req.options if req.options else None,
        )
        meta: dict[str, Any] = {"model_key": self.model_key, "model": self.client.model}
        try:
            result = self.client.generate_json(system=self._system, user=user, schema=schema)
            payload = extract_json(result.text)
            meta.update(
                usage=result.usage,
                latency_ms=result.latency_ms,
                attempts=result.attempts,
                stop_reason=result.stop_reason,
                raw=result.text,
            )
        except (LLMError, Exception) as exc:  # noqa: BLE001 - 单个玩家出错不应中断整局
            meta["error"] = f"{type(exc).__name__}: {exc}"
            payload = {}

        return self._to_decision(req, payload, meta)

    # ------------------------------------------------------------------
    def _to_decision(self, req: ActionRequest, payload: dict, meta: dict) -> Decision:
        thinking = str(payload.get("thinking") or "").strip()
        notes = str(payload.get("notes") or "").strip()
        beliefs = payload.get("beliefs") or []
        if isinstance(beliefs, list):
            beliefs = [b for b in beliefs if isinstance(b, dict)]
        else:
            beliefs = []
        scheme = payload.get("scheme") if isinstance(payload.get("scheme"), dict) else None

        if notes:
            self.notes = notes
        if beliefs:
            self.beliefs = beliefs

        data: dict[str, Any] = {}
        coerced: list[str] = []
        fallback = self._fallback_target(req)

        for field in SEAT_FIELDS.get(req.kind, []):
            allow_zero = req.allow_zero or field in ("vote_intent", "poison_target")
            value, was_coerced = coerce_seat(
                payload.get(field), req.options, allow_zero=allow_zero, fallback=fallback
            )
            if field in ("poison_target", "vote_intent") and payload.get(field) in (None, 0, "0"):
                value, was_coerced = 0, False
            data[field] = value
            if was_coerced:
                coerced.append(field)

        for field in ("wolf_talk", "speech", "one_liner", "claim"):
            if field in payload:
                data[field] = str(payload[field] or "").strip()
        if req.kind == "witch_action":
            data["use_antidote"] = bool(payload.get("use_antidote"))

        data.setdefault("speech", "")
        if coerced:
            meta["coerced_fields"] = coerced
        if not payload:
            meta["fallback_decision"] = True
            data.update(self._blind_defaults(req, fallback))

        return Decision(
            thinking=thinking,
            notes=self.notes,
            beliefs=self.beliefs,
            scheme=scheme,
            data=data,
            meta=meta,
        )

    def _fallback_target(self, req: ActionRequest) -> int | None:
        pool = [s for s in req.options if s != self.seat] or list(req.options)
        if not pool:
            return 0 if req.allow_zero else None
        return self.rng.choice(pool)

    def _blind_defaults(self, req: ActionRequest, fallback: int | None) -> dict:
        """LLM 调用失败时的保底行为：尽量做无害的合法动作。"""
        if req.kind == "witch_action":
            return {"use_antidote": False, "poison_target": 0}
        if req.kind in ("vote", "hunter_shot", "wolf_king_shot"):
            return {"target": 0, "speech": "（本轮无有效输出）", "one_liner": ""}
        if req.kind in ("speech", "last_words"):
            return {"claim": "隐藏", "speech": "（本轮无有效输出）", "vote_intent": 0}
        if req.kind == "guard_protect":
            return {"target": self.seat if self.seat in req.options else (fallback or 0)}
        return {"target": fallback or 0, "wolf_talk": ""}
