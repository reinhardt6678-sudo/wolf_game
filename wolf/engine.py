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
from .roles import Board, Camp, Role, is_pack_wolf
from .state import GameState, Player

#: 出局时能开枪的角色 → (行动类型, 公开称呼)
GUN_ROLES: dict[Role, tuple[str, str]] = {
    Role.HUNTER: ("hunter_shot", "猎人"),
    Role.WOLF_KING: ("wolf_king_shot", "狼王"),
}


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
        self.state.idiot_immunity = {
            s: self.rules.idiot_exile_immunity
            for s, p in players.items()
            if p.role is Role.IDIOT
        }
        self.state.log.subscribe(archive.on_event)
        self.agents = {seat: agent_factory(seat, p.model_key) for seat, p in players.items()}
        #: 今晚戴着面具的座位（假面用，每晚重置）
        self.masked: int | None = None

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
        pack = st.pack_wolf_seats(alive_only=False)
        self._emit(
            type="setup",
            text=f"游戏开始。{self.board.describe()}",
            visibility=Visibility.PUBLIC,
        )
        for seat, p in st.players.items():
            # 机械狼与假面不与狼队见面，拿不到队友名单，也进不了狼队频道。
            mates = [w for w in pack if w != seat] if is_pack_wolf(p.role) else []
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
            elif p.camp is Camp.WOLF and not is_pack_wolf(p.role):
                text += "你不与狼队见面：你不知道谁是狼，狼也不知道你是谁。"
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
        st.dance_pool = []
        self.masked = None
        self._emit(type="night_start", text=f"天黑请闭眼（第 {st.day} 夜）。", visibility=Visibility.PUBLIC)
        self.on_progress(f"第{st.day}夜")

        self._mechanic_learn_phase()
        guard_target = self._guard_phase()
        kill_target = self._wolf_phase()
        mech_guard, mech_double = self._mechanic_skill_phase(kill_target)
        self._dancer_phase()  # 舞者先点池
        self._mask_phase()  # 假面在舞者之后行动
        antidote_used, poison_target = self._witch_phase(kill_target)
        self._seer_phase()
        self._psychic_phase()

        self._night_deaths = self._resolve_night(
            guard_target=guard_target,
            kill_target=kill_target,
            antidote_used=antidote_used,
            poison_target=poison_target,
            mech_guard=mech_guard,
            mech_double_kill=mech_double,
        )

    # ---------------- 假面舞会 ----------------
    def _dancer_phase(self) -> None:
        """舞者点 N 人进舞池；结算放在天亮时（见 _resolve_dance_pool）。"""
        st = self.state
        seat = st.role_seat(Role.DANCER)
        if seat is None or st.day < self.rules.dance_from_night:
            return
        size = self.rules.dance_size
        options = [
            s
            for s in st.alive_seats()
            if not (self.rules.dance_once_per_player and s in st.danced_seats)
        ]
        if len(options) < size:
            self._emit(
                type="dance_skipped",
                text=f"能进舞池的人只剩 {len(options)} 个，不足 {size} 人，今晚无法开舞。",
                visibility=Visibility.PRIVATE,
                actor=seat,
                audience={seat},
            )
            return
        extra = (
            f"你必须点满 {size} 个人。已经进过舞池、不能再点的有："
            + ("、".join(f"{s}号" for s in sorted(st.danced_seats)) if st.danced_seats else "（暂无）")
        )
        d = self._ask(seat, "dance_invite", options=options, extra=extra)
        pool = self._coerce_pool(d.data.get("targets"), options, size, seat)
        st.dance_pool = pool
        st.danced_seats.update(pool)
        ev = self._emit(
            type="dance_invite",
            text="你今晚点了 " + "、".join(f"{s}号" for s in pool) + " 进入舞池。",
            visibility=Visibility.PRIVATE,
            actor=seat,
            targets=pool,
            audience={seat},
            data={"pool": pool},
        )
        self._log_mind(seat, "dance_invite", d, ev.idx)

    def _coerce_pool(self, raw: Any, options: list[int], size: int, seat: int) -> list[int]:
        """把模型给出的舞池收敛成 size 个合法且互不重复的座位。"""
        pool: list[int] = []
        for v in raw or []:
            try:
                x = int(v)
            except (TypeError, ValueError):
                continue
            if x in options and x not in pool:
                pool.append(x)
        for cand in [seat, *options]:  # 补齐：优先带上自己，其余按座位序
            if len(pool) >= size:
                break
            if cand in options and cand not in pool:
                pool.append(cand)
        return sorted(pool[:size])

    def _mask_phase(self) -> None:
        st = self.state
        seat = st.role_seat(Role.MASK)
        if seat is None or st.day < self.rules.mask_from_night:
            return
        alive = st.alive_seats()
        options = list(alive)
        if not self.rules.mask_repeat and st.last_mask_target in options:
            options = [s for s in options if s != st.last_mask_target]
        extra = "你可以先打听一名玩家今晚在不在舞池里（probe_target），再决定给谁戴面具。"
        if st.last_mask_target:
            extra += f"你昨晚给 {st.last_mask_target} 号戴过面具，今晚不能再给他。"
        d = self._ask(seat, "mask_action", options=options, extra=extra, allow_zero=True)

        probe = d.data.get("probe_target", 0) or 0
        if probe not in alive:
            probe = 0
        probe_text = ""
        if probe:
            in_pool = probe in st.dance_pool
            probe_text = f"你打听了 {probe} 号：他今晚{'在' if in_pool else '不在'}舞池里。"

        target = d.data.get("target", 0) or 0
        if target not in options:
            target = 0
        self.masked = target or None
        st.last_mask_target = target or None

        ev = self._emit(
            type="mask_action",
            text=(probe_text + (f"你给 {target} 号戴上了面具。" if target else "你今晚没有给任何人戴面具。")),
            visibility=Visibility.PRIVATE,
            actor=seat,
            targets=[target] if target else [],
            audience={seat},
            data={"probe": probe, "probe_in_pool": bool(probe and probe in st.dance_pool), "target": target},
        )
        self._log_mind(seat, "mask_action", d, ev.idx)

    def _pool_camp(self, seat: int) -> Camp:
        """舞池结算用的阵营：戴着面具的人阵营翻转。"""
        camp = self.state.players[seat].camp
        if self.masked == seat:
            return Camp.GOOD if camp is Camp.WOLF else Camp.WOLF
        return camp

    def _resolve_dance_pool(self) -> list[int]:
        """天亮时结算舞池：少数派阵营全部出局；三人同阵营则相安无事。"""
        st = self.state
        pool = [s for s in st.dance_pool if st.players[s].alive]
        if len(pool) < 2:
            return []
        camps = {s: self._pool_camp(s) for s in pool}
        wolves = [s for s in pool if camps[s] is Camp.WOLF]
        goods = [s for s in pool if camps[s] is Camp.GOOD]
        if not wolves or not goods:
            losers: list[int] = []
        else:
            losers = wolves if len(wolves) < len(goods) else goods if len(goods) < len(wolves) else []
        self._emit(
            type="dance_resolution",
            text="舞池结算："
            + "、".join(f"{s}号({camps[s].value})" for s in pool)
            + " → "
            + ("、".join(f"{s}号出局" for s in losers) if losers else "同阵营，无人出局"),
            visibility=Visibility.GOD,
            data={
                "pool": pool,
                "camps": {str(s): c.value for s, c in camps.items()},
                "masked": self.masked,
                "out": losers,
            },
        )
        dancer = st.role_seat(Role.DANCER)
        if dancer is not None and self.rules.dancer_learns_pool_result:
            self._emit(
                type="dance_feedback",
                text="舞池结算结果："
                + ("、".join(f"{s}号是少数派，出局" for s in losers) if losers else "三人同阵营，无人出局"),
                visibility=Visibility.PRIVATE,
                actor=dancer,
                audience={dancer},
            )
        return losers

    # ---------------- 机械狼 ----------------
    def _mechanic_learn_phase(self) -> None:
        st = self.state
        seat = st.role_seat(Role.MECHANIC_WOLF)
        if seat is None or st.mechanic_learned is not None:
            return
        if st.day != self.rules.mechanic_learn_night:
            return
        options = [s for s in st.alive_seats() if s != seat]
        d = self._ask(seat, "mechanic_learn", options=options)
        target = d.data.get("target", 0) or 0
        if target not in options:
            target = options[0]
        learned = st.players[target].role
        st.mechanic_learned = learned
        st.mechanic_skill_from = st.day + self.rules.mechanic_skill_delay
        ev = self._emit(
            type="mechanic_learn",
            text=f"你学习了 {target} 号，他的真实身份是【{learned.value}】。"
            f"从第 {st.mechanic_skill_from} 夜起你将拥有他的技能。"
            + ("（平民没有技能，你这一学等于白学。）" if learned is Role.VILLAGER else ""),
            visibility=Visibility.PRIVATE,
            actor=seat,
            targets=[target],
            audience={seat},
            data={"target": target, "learned": learned.value},
        )
        self._log_mind(seat, "mechanic_learn", d, ev.idx)

    def _mechanic_skill_active(self) -> Role | None:
        st = self.state
        seat = st.role_seat(Role.MECHANIC_WOLF)
        if seat is None or st.mechanic_learned is None:
            return None
        if st.mechanic_skill_from is None or st.day < st.mechanic_skill_from:
            return None
        return st.mechanic_learned

    def _mechanic_skill_phase(self, kill_target: int | None) -> tuple[int | None, int | None]:
        """机械狼学到的技能。返回 (守护目标, 双刀的第二刀)。"""
        st = self.state
        seat = st.role_seat(Role.MECHANIC_WOLF)
        learned = self._mechanic_skill_active()
        if seat is None or learned is None:
            return None, None

        if learned is Role.GUARD:
            options = [s for s in st.alive_seats() if s != st.last_mechanic_guard]
            d = self._ask(seat, "mechanic_guard", options=options, allow_zero=True)
            target = d.data.get("target", 0) or 0
            if target not in options:
                target = 0
            st.last_mechanic_guard = target or None
            ev = self._emit(
                type="mechanic_guard",
                text=(f"你（学到守卫）守护了 {target} 号，他今晚免疫狼刀与女巫毒。" if target else "你今晚没有守人。"),
                visibility=Visibility.PRIVATE,
                actor=seat,
                targets=[target] if target else [],
                audience={seat},
                data={"target": target},
            )
            self._log_mind(seat, "mechanic_guard", d, ev.idx)
            return (target or None), None

        if learned is Role.PSYCHIC:
            options = [s for s in st.alive_seats() if s != seat]
            d = self._ask(seat, "mechanic_psychic", options=options)
            target = d.data.get("target", 0) or 0
            if target not in options:
                target = options[0] if options else 0
            if target:
                verdict = self._psychic_verdict(target)
                ev = self._emit(
                    type="mechanic_psychic",
                    text=f"你（学到通灵师）查验了 {target} 号，他的身份是【{verdict}】。",
                    visibility=Visibility.PRIVATE,
                    actor=seat,
                    targets=[target],
                    audience={seat},
                    data={"target": target, "verdict": verdict},
                )
                self._log_mind(seat, "mechanic_psychic", d, ev.idx)
            return None, None

        if learned is Role.WEREWOLF and self.rules.mechanic_double_kill:
            # 双刀只有在机械狼自己带刀时才用得上（小狼全出局后）。
            if kill_target is None or not st.mechanic_double_kill_left:
                return None, None
            if st.pack_wolf_seats(alive_only=True):
                return None, None
            options = [s for s in st.alive_seats() if s != kill_target and s != seat]
            if not options:
                return None, None
            d = self._ask(
                seat,
                "mechanic_double_kill",
                options=options,
                extra=f"你今晚的第一刀砍向 {kill_target} 号。双刀整局只有一次。",
                allow_zero=True,
            )
            target = d.data.get("target", 0) or 0
            if target not in options:
                target = 0
            if target:
                st.mechanic_double_kill_left = False
            ev = self._emit(
                type="mechanic_double_kill",
                text=(f"你（学到狼人）打出双刀，第二刀砍向 {target} 号。" if target else "你今晚没有使用双刀。"),
                visibility=Visibility.PRIVATE,
                actor=seat,
                targets=[target] if target else [],
                audience={seat},
                data={"target": target},
            )
            self._log_mind(seat, "mechanic_double_kill", d, ev.idx)
            return None, (target or None)

        return None, None

    def _psychic_verdict(self, target: int) -> str:
        """通灵师看到的身份：机械狼未学习显示「狼人」，学习后显示它学来的牌。"""
        st = self.state
        role = st.players[target].role
        if not self.rules.psychic_sees_exact_role:
            return st.players[target].camp.value
        if role is Role.MECHANIC_WOLF:
            return (st.mechanic_learned or Role.WEREWOLF).value
        return role.value

    # ---------------- 守卫 / 狼队 ----------------
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
        """狼刀。普通狼商议决定；普通狼全部出局后，刀交给不见面的功能狼。"""
        st = self.state
        actors = st.pack_wolf_seats(alive_only=True)
        solo = False
        if not actors:
            lone = st.lone_wolf_seats(alive_only=True)
            if not lone:
                return None
            actors = lone[:1]
            solo = True
        wolf_set = frozenset(actors)
        options = [s for s in st.alive_seats() if st.players[s].camp is not Camp.WOLF]
        if not options:
            return None

        votes: dict[int, int] = {}
        for seat in actors:  # 顺序进行，后手能看到先手在狼队频道的发言
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
            decider = votes.get(min(actors))
            target = decider if decider in finalists else finalists[0]
        else:
            target = None
        self._emit(
            type="wolf_decision",
            text=(f"狼队今晚决定刀 {target} 号。" if target else "狼队今晚放弃刀人。")
            + ("（狼队友已全部出局，刀在你手上。）" if solo else ""),
            visibility=Visibility.WOLF,
            targets=[target] if target else [],
            audience=wolf_set,
            data={"votes": {str(k): v for k, v in votes.items()}, "solo": solo},
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

    def _psychic_phase(self) -> None:
        st = self.state
        seat = st.role_seat(Role.PSYCHIC)
        if seat is None:
            return
        options = [s for s in st.alive_seats() if s != seat]
        if not options:
            return
        d = self._ask(seat, "psychic_check", options=options)
        target = d.data.get("target", 0) or 0
        if target not in options:
            target = options[0]
        verdict = self._psychic_verdict(target)
        ev = self._emit(
            type="psychic_check",
            text=f"你通灵了 {target} 号，他的身份是【{verdict}】。",
            visibility=Visibility.PRIVATE,
            actor=seat,
            targets=[target],
            audience={seat},
            data={"target": target, "verdict": verdict},
        )
        self._log_mind(seat, "psychic_check", d, ev.idx)

    def _poison_immune(self, seat: int) -> bool:
        """舞者与假面免疫女巫毒；机械狼守到的人也免疫。"""
        role = self.state.players[seat].role
        if role is Role.DANCER and self.rules.dancer_poison_immune:
            return True
        if role is Role.MASK and self.rules.mask_poison_immune:
            return True
        return False

    def _resolve_night(
        self,
        *,
        guard_target: int | None,
        kill_target: int | None,
        antidote_used: bool,
        poison_target: int | None,
        mech_guard: int | None = None,
        mech_double_kill: int | None = None,
    ) -> list[tuple[int, str]]:
        st = self.state
        deaths: list[tuple[int, str]] = []
        dance_out = self._resolve_dance_pool()

        # 舞者在池中时，池内三人当晚免疫狼刀
        pool_immune = (
            self.rules.dance_pool_immune_to_kill
            and st.role_seat(Role.DANCER) in st.dance_pool
        )

        def knifed(target: int) -> bool:
            if pool_immune and target in st.dance_pool:
                return False
            if mech_guard == target:
                return False
            guarded = guard_target == target
            if guarded and antidote_used:
                return not self.rules.guard_save_conflict_dies
            return not (guarded or antidote_used)

        if kill_target and knifed(kill_target):
            deaths.append((kill_target, "狼杀"))
        if mech_double_kill and mech_double_kill != kill_target and knifed(mech_double_kill):
            deaths.append((mech_double_kill, "狼杀"))

        dead = {d[0] for d in deaths}
        for seat in dance_out:
            if seat not in dead:
                deaths.append((seat, "舞池"))
                dead.add(seat)
        if (
            poison_target
            and poison_target not in dead
            and mech_guard != poison_target
            and not self._poison_immune(poison_target)
        ):
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
            self._on_death(seat, cause)

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
            if self._idiot_survives_exile(exiled):
                return
            st.kill(exiled, "放逐")
            self._emit(
                type="exile",
                text=f"{exiled} 号被放逐出局。",
                visibility=Visibility.PUBLIC,
                targets=[exiled],
            )
            self._last_words(exiled)
            self._on_death(exiled, "放逐")
        else:
            self._emit(type="no_exile", text="本轮无人被放逐。", visibility=Visibility.PUBLIC)

    def _one_vote(self, round_no: int) -> int | str:
        st = self.state
        voters = st.voter_seats()
        candidates = st.alive_seats()
        if not voters:
            self._emit(
                type="vote_result",
                text="场上没有人还拥有投票权，本轮无人出局。",
                visibility=Visibility.PUBLIC,
            )
            return "none"
        snapshots = {s: self._observation(s) for s in voters}
        tasks = [
            (
                s,
                (
                    lambda s=s: self._ask(
                        s,
                        "vote",
                        options=[x for x in candidates if x != s],
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
            if target == seat or target not in candidates:
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
            for other in candidates:
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

    def _on_death(self, seat: int, cause: str) -> None:
        """结算出局者的死亡技能（猎人 / 狼王开枪）。"""
        role = self.state.players[seat].role
        if role not in GUN_ROLES:
            return
        if cause == "毒杀" and not (
            role is Role.WOLF_KING and self.rules.wolf_king_shoot_on_poison
        ):
            return  # 被毒死的枪口是哑的
        self._gun_shot(seat, *GUN_ROLES[role])

    def _gun_shot(self, seat: int, kind: str, label: str) -> None:
        st = self.state
        options = st.alive_seats()
        if not options:
            return
        d = self._ask(seat, kind, options=options, allow_zero=True)
        target = d.data.get("target", 0) or 0
        if target not in options:
            target = 0
        ev = self._emit(
            type=kind,
            text=(d.data.get("speech") or "")
            + (f"【{label}开枪带走 {target} 号】" if target else f"【{label}放弃开枪】"),
            visibility=Visibility.PUBLIC,
            actor=seat,
            targets=[target] if target else [],
        )
        self._log_mind(seat, kind, d, ev.idx)
        if target:
            st.kill(target, "枪杀")
            self._last_words(target)
            self._on_death(target, "枪杀")  # 枪响可能连环（猎人打狼王）

    def _idiot_survives_exile(self, seat: int) -> bool:
        """白痴被放逐：当场翻牌，本轮不出局。"""
        st = self.state
        if st.players[seat].role is not Role.IDIOT or st.idiot_immunity.get(seat, 0) <= 0:
            return False
        st.idiot_immunity[seat] -= 1
        st.revealed_roles[seat] = Role.IDIOT
        lost_vote = self.rules.idiot_loses_vote
        if lost_vote:
            st.no_vote_seats.add(seat)
        self._emit(
            type="idiot_reveal",
            text=f"{seat} 号被投票放逐，但他当场翻牌——他的真实身份是【白痴】，本轮不出局。"
            + ("从现在起他失去投票权。" if lost_vote else ""),
            visibility=Visibility.PUBLIC,
            actor=seat,
            targets=[seat],
            data={"seat": seat, "role": Role.IDIOT.value, "immunity_left": st.idiot_immunity[seat]},
        )
        return True

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
