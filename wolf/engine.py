"""对局引擎：状态机 + 可见性控制。"""

from __future__ import annotations

import random
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

from . import prompts
from .agents.base import ActionRequest, Decision
from .archive import Archive
from .events import Visibility
from .roles import Board, Camp, Role, camp_of, is_god, is_villager
from .state import GameState, Player


@dataclass
class GameResult:
    game_id: str
    winner: str
    end_reason: str
    days: int
    players: list[dict]
    board: str
    votes: list[dict] = field(default_factory=list)
    errors: int = 0


class Engine:
    def __init__(
        self,
        *,
        game_id: str,
        board: Board,
        agent_factory: Callable[[int, str], Any],
        model_by_seat: dict[int, str],
        archive: Archive,
        seed: int | None = None,
        parallel: int = 1,
        on_progress: Callable[[str], None] | None = None,
    ) -> None:
        self.board = board
        self.rules = board.rules
        self.rng = random.Random(seed)
        self.archive = archive
        self.parallel = max(1, parallel)
        self.on_progress = on_progress or (lambda _msg: None)
        self.errors = 0
        self.vote_history: list[dict] = []

        pool = board.role_pool()
        self.rng.shuffle(pool)
        players = {
            seat: Player(seat=seat, role=pool[i], model_key=model_by_seat[seat])
            for i, seat in enumerate(sorted(model_by_seat))
        }
        self.state = GameState(game_id=game_id, board=board, players=players)
        self.state.log.subscribe(archive.on_event)
        self.agents = {seat: agent_factory(seat, p.model_key) for seat, p in players.items()}

    # ================================================================
    # 工具
    # ================================================================
    def _emit(self, **kw):
        kw.setdefault("day", self.state.day)
        kw.setdefault("phase", self.state.phase)
        return self.state.log.add(**kw)

    def _observation(self, seat: int) -> str:
        return prompts.render_events(self.state.log.visible(seat), seat)

    def _dead_list(self) -> list[tuple[int, int]]:
        return [
            (s, p.death_day or 0)
            for s, p in sorted(self.state.players.items())
            if not p.alive
        ]

    def _ask(
        self,
        seat: int,
        kind: str,
        *,
        options: list[int],
        extra: str = "",
        allow_zero: bool = False,
        observation: str | None = None,
    ) -> Decision:
        req = ActionRequest(
            kind=kind,
            day=self.state.day,
            phase=self.state.phase,
            observation=observation if observation is not None else self._observation(seat),
            alive=self.state.alive_seats(),
            dead=self._dead_list(),
            options=options,
            extra=extra,
            allow_zero=allow_zero,
        )
        try:
            decision = self.agents[seat].act(req)
        except Exception as exc:  # noqa: BLE001 - 单点失败不应炸掉整局
            self.errors += 1
            decision = Decision(
                thinking="", meta={"error": f"{type(exc).__name__}: {exc}", "fatal": True}
            )
        if decision.meta.get("error"):
            self.errors += 1
        return decision

    def _log_mind(self, seat: int, kind: str, decision: Decision, event_idx: int) -> None:
        p = self.state.players[seat]
        meta = dict(decision.meta)
        meta["event_idx"] = event_idx
        self.archive.record_mind(
            day=self.state.day,
            phase=self.state.phase,
            kind=kind,
            seat=seat,
            role=p.role.value,
            camp=p.camp.value,
            model_key=p.model_key,
            model=str(meta.get("model", p.model_key)),
            thinking=decision.thinking,
            notes=decision.notes,
            beliefs=decision.beliefs,
            scheme=decision.scheme,
            action=decision.data,
            meta=meta,
        )

    def _gather(self, tasks: list[tuple[Any, Callable[[], Any]]]) -> dict:
        """并发执行互不影响的决策（同一时刻的投票、夜间独立技能）。"""
        if self.parallel == 1 or len(tasks) <= 1:
            return {key: fn() for key, fn in tasks}
        with ThreadPoolExecutor(max_workers=min(self.parallel, len(tasks))) as ex:
            futures = {key: ex.submit(fn) for key, fn in tasks}
        return {key: f.result() for key, f in futures.items()}

    # ================================================================
    # 主流程
    # ================================================================
    def run(self) -> GameResult:
        self._setup()
        while True:
            self.state.day += 1
            self._night()
            if self._check_end():
                break
            self._day()
            if self._check_end():
                break
            if self.state.day >= self.rules.max_days:
                self.state.winner = None
                self.state.end_reason = f"达到最大天数 {self.rules.max_days}，平局"
                break
        return self._finish()

    def _setup(self) -> None:
        self.state.phase = "setup"
        st = self.state
        wolves = st.wolf_seats()
        self._emit(
            type="setup",
            text=f"游戏开始。{self.board.describe()}",
            visibility=Visibility.PUBLIC,
        )
        for seat, p in st.players.items():
            mates = [w for w in wolves if w != seat] if p.role is Role.WEREWOLF else []
            self.agents[seat].on_game_start(
                seat=seat,
                role=p.role,
                board=self.board,
                teammates=mates,
                all_seats=st.seats(),
            )
            text = f"你的身份是【{p.role.value}】。"
            if mates:
                text += "你的狼队友是：" + "、".join(f"{m}号" for m in mates) + "。"
            self._emit(
                type="role_assign",
                text=text,
                visibility=Visibility.PRIVATE,
                actor=seat,
                audience={seat},
                data={"role": p.role.value},
            )
        self._emit(
            type="god_view",
            text="上帝视角身份表：" + "，".join(f"{s}号={p.role.value}" for s, p in st.players.items()),
            visibility=Visibility.GOD,
            data={"roles": {str(s): p.role.value for s, p in st.players.items()}},
        )
        st.speech_start_seat = self.rng.choice(st.seats())

    # ----------------------------------------------------------------
    # 夜晚
    # ----------------------------------------------------------------
    def _night(self) -> None:
        st = self.state
        st.phase = "night"
        self._emit(type="night_start", text=f"天黑请闭眼（第 {st.day} 夜）。", visibility=Visibility.PUBLIC)
        self.on_progress(f"第{st.day}夜")

        guard_target = self._guard_phase()
        kill_target = self._wolf_phase()
        antidote_used, poison_target = self._witch_phase(kill_target)
        self._seer_phase()

        deaths = self._resolve_night(guard_target, kill_target, antidote_used, poison_target)
        self._night_deaths = deaths

    def _guard_phase(self) -> int | None:
        st = self.state
        seat = st.role_seat(Role.GUARD)
        if seat is None:
            return None
        options = [s for s in st.alive_seats()]
        if not self.rules.guard_repeat and st.last_guard_target in options:
            options = [s for s in options if s != st.last_guard_target]
        extra = ""
        if st.last_guard_target:
            extra = f"你昨晚守护的是 {st.last_guard_target} 号，今晚不能再守他。"
        d = self._ask(seat, "guard_protect", options=options, extra=extra, allow_zero=True)
        target = d.data.get("target", 0) or 0
        ev = self._emit(
            type="guard_protect",
            text=(f"你守护了 {target} 号。" if target else "你今晚空守。"),
            visibility=Visibility.PRIVATE,
            actor=seat,
            targets=[target] if target else [],
            audience={seat},
        )
        self._log_mind(seat, "guard_protect", d, ev.idx)
        st.last_guard_target = target or None
        return target or None

    def _wolf_phase(self) -> int | None:
        st = self.state
        wolves = st.wolf_seats(alive_only=True)
        if not wolves:
            return None
        wolf_set = frozenset(wolves)
        options = [s for s in st.alive_seats() if s not in wolf_set] or st.alive_seats()
        votes: dict[int, int] = {}
        for seat in wolves:  # 顺序进行，后手能看到先手在狼队频道的发言
            d = self._ask(seat, "wolf_kill", options=options)
            target = d.data.get("target", 0) or 0
            talk = d.data.get("wolf_talk", "")
            votes[seat] = target
            ev = self._emit(
                type="wolf_talk",
                text=f"{seat}号（狼）说：{talk}　【主张刀 {target} 号】",
                visibility=Visibility.WOLF,
                actor=seat,
                targets=[target],
                audience=wolf_set,
            )
            self._log_mind(seat, "wolf_kill", d, ev.idx)

        tally: dict[int, int] = {}
        for t in votes.values():
            if t:
                tally[t] = tally.get(t, 0) + 1
        if tally:
            top = max(tally.values())
            finalists = sorted(t for t, c in tally.items() if c == top)
            # 平局由座位最小的存活狼人拍板
            decider = votes.get(min(wolves))
            target = decider if decider in finalists else finalists[0]
        else:
            target = None
        self._emit(
            type="wolf_decision",
            text=(f"狼队今晚决定刀 {target} 号。" if target else "狼队今晚放弃刀人。"),
            visibility=Visibility.WOLF,
            targets=[target] if target else [],
            audience=wolf_set,
            data={"votes": {str(k): v for k, v in votes.items()}},
        )
        return target

    def _witch_phase(self, kill_target: int | None) -> tuple[bool, int | None]:
        st = self.state
        seat = st.role_seat(Role.WITCH)
        if seat is None or (not st.witch_antidote and not st.witch_poison):
            return False, None

        sees = self.rules.witch_sees_kill == "always" or st.day == 1
        if sees and kill_target:
            extra = f"今晚被狼人刀的是 {kill_target} 号。"
        elif sees:
            extra = "今晚狼人没有刀人（平安夜）。"
        else:
            extra = "你今晚看不到刀口。"

        can_save = bool(
            st.witch_antidote
            and kill_target
            and sees
            and (kill_target != seat or st.day <= self.rules.witch_self_save_day)
        )
        if st.witch_antidote and not can_save and kill_target == seat:
            extra += "（按规则你今晚不能自救。）"
        elif not st.witch_antidote:
            extra += "（你的解药已经用掉了。）"
        if not st.witch_poison:
            extra += "（你的毒药已经用掉了。）"
        if not self.rules.witch_same_night_both:
            extra += "（同一晚不能既用解药又用毒药。）"

        options = [s for s in st.alive_seats() if s != seat] if st.witch_poison else []
        d = self._ask(seat, "witch_action", options=options, extra=extra, allow_zero=True)

        use_antidote = bool(d.data.get("use_antidote")) and can_save
        poison_target = d.data.get("poison_target", 0) or 0
        if poison_target not in options:
            poison_target = 0
        if use_antidote and poison_target and not self.rules.witch_same_night_both:
            poison_target = 0

        if use_antidote:
            st.witch_antidote = False
        if poison_target:
            st.witch_poison = False

        desc = []
        if use_antidote:
            desc.append(f"你对 {kill_target} 号使用了解药。")
        if poison_target:
            desc.append(f"你对 {poison_target} 号使用了毒药。")
        ev = self._emit(
            type="witch_action",
            text=(" ".join(desc) if desc else "你今晚没有用药。") + f"（{extra}）",
            visibility=Visibility.PRIVATE,
            actor=seat,
            audience={seat},
            data={"antidote": use_antidote, "poison": poison_target},
        )
        self._log_mind(seat, "witch_action", d, ev.idx)
        return use_antidote, (poison_target or None)

    def _seer_phase(self) -> None:
        st = self.state
        seat = st.role_seat(Role.SEER)
        if seat is None:
            return
        options = [s for s in st.alive_seats() if s != seat]
        d = self._ask(seat, "seer_check", options=options)
        target = d.data.get("target", 0) or 0
        if target not in options:
            target = options[0] if options else 0
        if not target:
            return
        verdict = "狼人" if st.players[target].camp is Camp.WOLF else "好人"
        ev = self._emit(
            type="seer_check",
            text=f"你查验了 {target} 号，结果是【{verdict}】。",
            visibility=Visibility.PRIVATE,
            actor=seat,
            targets=[target],
            audience={seat},
            data={"target": target, "verdict": verdict},
        )
        self._log_mind(seat, "seer_check", d, ev.idx)

    def _resolve_night(
        self,
        guard_target: int | None,
        kill_target: int | None,
        antidote_used: bool,
        poison_target: int | None,
    ) -> list[tuple[int, str]]:
        deaths: list[tuple[int, str]] = []
        if kill_target:
            guarded = guard_target == kill_target
            if guarded and antidote_used:
                survives = not self.rules.guard_save_conflict_dies
            else:
                survives = guarded or antidote_used
            if not survives:
                deaths.append((kill_target, "狼杀"))
        if poison_target and poison_target not in [d[0] for d in deaths]:
            deaths.append((poison_target, "毒杀"))
        self._emit(
            type="night_resolution",
            text="夜间结算："
            + ("；".join(f"{s}号 {c}" for s, c in deaths) if deaths else "平安夜"),
            visibility=Visibility.GOD,
            data={"deaths": [{"seat": s, "cause": c} for s, c in deaths]},
        )
        return deaths

    # ----------------------------------------------------------------
    # 白天
    # ----------------------------------------------------------------
    def _day(self) -> None:
        st = self.state
        st.phase = "day"
        deaths = getattr(self, "_night_deaths", [])
        self.on_progress(f"第{st.day}天")

        for seat, cause in deaths:
            st.kill(seat, cause)
        if deaths:
            dead_seats = sorted(s for s, _ in deaths)
            self._emit(
                type="announce",
                text="天亮了。昨晚 " + "、".join(f"{s}号" for s in dead_seats) + " 出局（死因不公布）。",
                visibility=Visibility.PUBLIC,
                targets=dead_seats,
            )
        else:
            self._emit(type="announce", text="天亮了。昨晚是平安夜，无人出局。", visibility=Visibility.PUBLIC)

        allow_lw = self.rules.first_night_last_words if st.day == 1 else self.rules.night_death_last_words
        if allow_lw:
            for seat, _cause in sorted(deaths):
                self._last_words(seat)
        for seat, cause in sorted(deaths):
            if st.players[seat].role is Role.HUNTER and cause != "毒杀":
                self._hunter_shot(seat)

        if self._check_end():
            return

        start = st.next_seat_from(sorted(s for s, _ in deaths)[0]) if deaths else st.next_seat_from(
            st.speech_start_seat
        )
        st.speech_start_seat = start
        self._emit(
            type="speech_order",
            text=f"从 {start} 号开始，按座位顺序依次发言。",
            visibility=Visibility.PUBLIC,
        )
        for seat in st.speech_order(start):
            if not st.players[seat].alive:
                continue
            self._speech(seat)

        self._vote_round()

    def _speech(self, seat: int) -> None:
        st = self.state
        options = [s for s in st.alive_seats() if s != seat]
        d = self._ask(seat, "speech", options=options, allow_zero=True)
        claim = d.data.get("claim") or "隐藏"
        speech = d.data.get("speech") or "（跳过发言）"
        intent = d.data.get("vote_intent", 0) or 0
        suffix = ""
        if claim and claim != "隐藏":
            suffix += f"【宣称身份：{claim}】"
        if intent:
            suffix += f"【当前票型：{intent}号】"
        ev = self._emit(
            type="speech",
            text=f"{speech} {suffix}".strip(),
            visibility=Visibility.PUBLIC,
            actor=seat,
            targets=[intent] if intent else [],
            data={"claim": claim, "vote_intent": intent},
        )
        self._log_mind(seat, "speech", d, ev.idx)

    def _vote_round(self) -> None:
        st = self.state
        exiled = self._one_vote(round_no=1)
        if exiled == "tie" and self.rules.tie_rule == "revote":
            self._emit(
                type="vote_tie", text="平票，进入第二轮投票。", visibility=Visibility.PUBLIC
            )
            exiled = self._one_vote(round_no=2)
        if isinstance(exiled, int):
            st.kill(exiled, "放逐")
            self._emit(
                type="exile",
                text=f"{exiled} 号被放逐出局。",
                visibility=Visibility.PUBLIC,
                targets=[exiled],
            )
            self._last_words(exiled)
            if st.players[exiled].role is Role.HUNTER:
                self._hunter_shot(exiled)
        else:
            self._emit(type="no_exile", text="本轮无人被放逐。", visibility=Visibility.PUBLIC)

    def _one_vote(self, round_no: int) -> int | str:
        st = self.state
        voters = st.alive_seats()
        snapshots = {s: self._observation(s) for s in voters}
        tasks = [
            (
                s,
                (
                    lambda s=s: self._ask(
                        s,
                        "vote",
                        options=[x for x in voters if x != s],
                        allow_zero=True,
                        observation=snapshots[s],
                        extra=("这是第二轮投票（上一轮平票）。" if round_no == 2 else ""),
                    )
                ),
            )
            for s in voters
        ]
        decisions: dict[int, Decision] = self._gather(tasks)

        tally: dict[int, int] = {}
        detail: dict[int, int] = {}
        for seat in voters:
            d = decisions[seat]
            target = d.data.get("target", 0) or 0
            if target == seat or target not in voters:
                target = 0
            detail[seat] = target
            if target:
                tally[target] = tally.get(target, 0) + 1
            ev = self._emit(
                type="vote_declare",
                text=(f"投票给 {target} 号。" if target else "弃票。")
                + (f"（{d.data.get('one_liner')}）" if d.data.get("one_liner") else ""),
                visibility=Visibility.PUBLIC,
                actor=seat,
                targets=[target] if target else [],
                data={"target": target, "round": round_no},
            )
            self._log_mind(seat, "vote", d, ev.idx)
            for other in voters:
                fn = getattr(self.agents[other], "observe_vote", None)
                if fn:
                    fn(seat, target)

        self.vote_history.append(
            {"day": st.day, "round": round_no, "votes": {str(k): v for k, v in detail.items()}}
        )
        summary = "、".join(f"{t}号 {c}票" for t, c in sorted(tally.items(), key=lambda kv: -kv[1]))
        self._emit(
            type="vote_result",
            text=f"第 {round_no} 轮投票结果：{summary or '全场弃票'}。",
            visibility=Visibility.PUBLIC,
            data={"tally": {str(k): v for k, v in tally.items()}},
        )
        if not tally:
            return "none"
        top = max(tally.values())
        finalists = [t for t, c in tally.items() if c == top]
        if len(finalists) > 1:
            return "tie"
        return finalists[0]

    def _last_words(self, seat: int) -> None:
        d = self._ask(seat, "last_words", options=self.state.alive_seats(), allow_zero=True)
        ev = self._emit(
            type="last_words",
            text=(d.data.get("speech") or "（无遗言）")
            + (f"【宣称身份：{d.data.get('claim')}】" if d.data.get("claim") and d.data.get("claim") != "隐藏" else ""),
            visibility=Visibility.PUBLIC,
            actor=seat,
        )
        self._log_mind(seat, "last_words", d, ev.idx)

    def _hunter_shot(self, seat: int) -> None:
        st = self.state
        options = st.alive_seats()
        if not options:
            return
        d = self._ask(seat, "hunter_shot", options=options, allow_zero=True)
        target = d.data.get("target", 0) or 0
        if target not in options:
            target = 0
        ev = self._emit(
            type="hunter_shot",
            text=(d.data.get("speech") or "")
            + (f"【猎人开枪带走 {target} 号】" if target else "【猎人放弃开枪】"),
            visibility=Visibility.PUBLIC,
            actor=seat,
            targets=[target] if target else [],
        )
        self._log_mind(seat, "hunter_shot", d, ev.idx)
        if target:
            st.kill(target, "枪杀")
            self._last_words(target)

    # ----------------------------------------------------------------
    def _check_end(self) -> bool:
        st = self.state
        if st.winner is not None:
            return True
        c = st.counts()
        if c["wolf"] == 0:
            st.winner = Camp.GOOD
            st.end_reason = "狼人全部出局"
            return True
        if self.rules.victory == "屠边":
            if c["god"] == 0:
                st.winner = Camp.WOLF
                st.end_reason = "神职全部出局（屠神）"
                return True
            if c["villager"] == 0:
                st.winner = Camp.WOLF
                st.end_reason = "平民全部出局（屠民）"
                return True
        else:
            if c["wolf"] >= c["good"]:
                st.winner = Camp.WOLF
                st.end_reason = "狼人数量不少于好人"
                return True
        return False

    def _finish(self) -> GameResult:
        st = self.state
        winner = st.winner.value if st.winner else "平局"
        self._emit(
            type="game_over",
            text=f"游戏结束：{winner}获胜（{st.end_reason}）。身份公布："
            + "，".join(f"{s}号={p.role.value}" for s, p in st.players.items()),
            visibility=Visibility.PUBLIC,
            phase="day",
        )
        result = GameResult(
            game_id=st.game_id,
            winner=winner,
            end_reason=st.end_reason,
            days=st.day,
            players=[p.to_dict() for p in st.players.values()],
            board=self.board.describe(),
            votes=self.vote_history,
            errors=self.errors,
        )
        meta = {
            "game_id": st.game_id,
            "board": self.board.describe(),
            "winner": winner,
            "end_reason": st.end_reason,
            "days": st.day,
            "players": result.players,
            "votes": self.vote_history,
            "errors": self.errors,
        }
        self.archive.write_meta(meta)
        self.archive.write_transcript(meta)
        self.archive.close()
        return result
